"""Streaming adapter and immutable object storage for SEC nightly bulk ZIPs."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast
from zipfile import BadZipFile, ZipFile

import httpx2

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    SecRequestBudget,
    Sleep,
)
from industry_platform.modules.disclosures.bulk import (
    SEC_BULK_ADAPTER_VERSION,
    SEC_BULK_WATERMARK_POLICY_VERSION,
    SEC_MAX_BULK_ARCHIVE_BYTES,
    SEC_MAX_BULK_ARCHIVE_ENTRIES,
    SEC_MAX_BULK_COMPRESSION_RATIO,
    SecBulkArchiveDownload,
    SecBulkArchiveSnapshot,
    SecBulkDatasetKind,
    SecBulkEntrySnapshot,
)
from industry_platform.modules.disclosures.domain import (
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    SEC_MAX_XBRL_RESPONSE_BYTES,
    SecSourceError,
    SecSourceErrorCode,
    sha256_hex,
)
from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    FileObjectStoreError,
)

_ENTRY_PATTERN = re.compile(r"^CIK[0-9]{10}\.json$")
_SPOOL_MEMORY_BYTES = 8 * 1_024 * 1_024
_CHUNK_BYTES = 1 * 1_024 * 1_024


class SecBulkObjectStore(Protocol):
    async def stat(self, *, bucket: str, object_key: str) -> object: ...

    async def put_private_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        stream: BinaryIO,
        exact_size: int,
    ) -> None: ...


class LiveSecBulkArchiveAdapter:
    """Download one official bulk ZIP without buffering gigabytes in memory."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        budget: SecRequestBudget,
        *,
        user_agent: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        timeout_seconds: float = 60.0,
        maximum_attempts: int = 3,
    ) -> None:
        if (
            not user_agent.strip()
            or "@" not in user_agent
            or any(character in user_agent for character in "\r\n")
        ):
            raise ValueError("SEC User-Agent must identify the application and contact email")
        if not 0 < timeout_seconds <= 3_600 or not 1 <= maximum_attempts <= 5:
            raise ValueError("SEC bulk request policy is invalid")
        self._client = client
        self._budget = budget
        self._user_agent = user_agent
        self._clock = clock
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds
        self._maximum_attempts = maximum_attempts

    async def fetch(self, dataset_kind: SecBulkDatasetKind) -> SecBulkArchiveDownload:
        last_error: SecSourceError | None = None
        for attempt in range(self._maximum_attempts):
            try:
                return await self._request(dataset_kind)
            except SecSourceError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._maximum_attempts:
                    raise
                delay = error.retry_after_seconds
                if delay is None:
                    delay = min(0.5 * (2**attempt), 4.0)
                await self._sleep(delay)
        if last_error is None:
            raise AssertionError("SEC bulk retry loop terminated without an outcome")
        raise last_error

    async def _request(self, dataset_kind: SecBulkDatasetKind) -> SecBulkArchiveDownload:
        await self._budget.acquire()
        # Ownership transfers to SecBulkArchiveDownload and the service closes it.
        stream = cast(
            BinaryIO,
            SpooledTemporaryFile(  # noqa: SIM115
                max_size=_SPOOL_MEMORY_BYTES,
                mode="w+b",
            ),
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "GET",
                    dataset_kind.source_url,
                    headers={
                        "Accept": "application/zip",
                        "Accept-Encoding": "identity",
                        "User-Agent": self._user_agent,
                    },
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as response:
                    _raise_for_status(response)
                    content_type = response.headers.get("content-type", "").partition(";")[0]
                    if content_type.strip().lower() not in {
                        "application/zip",
                        "application/octet-stream",
                    }:
                        raise SecSourceError(
                            SecSourceErrorCode.CONTENT_TYPE_INVALID,
                            retryable=False,
                        )
                    declared_size = _required_content_length(response.headers.get("content-length"))
                    published_at = _required_last_modified(response.headers.get("last-modified"))
                    digest = hashlib.sha256()
                    observed_size = 0
                    async for chunk in response.aiter_bytes():
                        observed_size += len(chunk)
                        if observed_size > declared_size:
                            raise SecSourceError(
                                SecSourceErrorCode.BULK_ARCHIVE_PARTIAL,
                                retryable=True,
                            )
                        digest.update(chunk)
                        stream.write(chunk)
                    if observed_size != declared_size:
                        raise SecSourceError(
                            SecSourceErrorCode.BULK_ARCHIVE_PARTIAL,
                            retryable=True,
                        )
                    retrieved_at = _utc_now(self._clock)
                    if published_at > retrieved_at:
                        raise SecSourceError(
                            SecSourceErrorCode.BULK_WATERMARK_INVALID,
                            retryable=False,
                        )
                    content_sha256 = digest.hexdigest()
                    stream.seek(0)
                    return SecBulkArchiveDownload(
                        dataset_kind=dataset_kind,
                        source_url=dataset_kind.source_url,
                        source_version=(f"sec-{dataset_kind.value}-bulk-v1-{content_sha256[:24]}"),
                        content_sha256=content_sha256,
                        byte_size=observed_size,
                        retrieved_at=retrieved_at,
                        bulk_published_at=published_at,
                        coverage_through=published_at - timedelta(seconds=1),
                        stream=stream,
                    )
        except SecSourceError:
            stream.close()
            raise
        except (TimeoutError, httpx2.TimeoutException):
            stream.close()
            raise SecSourceError(SecSourceErrorCode.TIMEOUT, retryable=True) from None
        except (httpx2.RequestError, httpx2.InvalidURL, OSError):
            stream.close()
            raise SecSourceError(SecSourceErrorCode.UPSTREAM_ERROR, retryable=True) from None


class UnavailableSecBulkArchiveAdapter:
    async def fetch(self, dataset_kind: SecBulkDatasetKind) -> SecBulkArchiveDownload:
        del dataset_kind
        raise SecSourceError(SecSourceErrorCode.NOT_CONFIGURED, retryable=False)


class MinioSecBulkSnapshotStore:
    def __init__(self, object_store: SecBulkObjectStore, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("SEC bulk snapshot bucket is invalid")
        self._object_store = object_store
        self._bucket = bucket

    async def persist(self, download: SecBulkArchiveDownload) -> SecBulkArchiveSnapshot:
        object_key = (
            f"sec/bulk/{download.dataset_kind.value}/{download.source_version}/"
            f"{download.content_sha256}.zip"
        )
        try:
            try:
                stat = await self._object_store.stat(
                    bucket=self._bucket,
                    object_key=object_key,
                )
                if getattr(stat, "size", None) != download.byte_size:
                    raise FileObjectStoreError from None
            except FileObjectNotFoundError:
                download.stream.seek(0)
                await self._object_store.put_private_stream(
                    bucket=self._bucket,
                    object_key=object_key,
                    content_type="application/zip",
                    stream=download.stream,
                    exact_size=download.byte_size,
                )
                stat = await self._object_store.stat(
                    bucket=self._bucket,
                    object_key=object_key,
                )
                if getattr(stat, "size", None) != download.byte_size:
                    raise FileObjectStoreError from None
        except (FileObjectStoreError, OSError, ValueError):
            raise SecSourceError(
                SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                retryable=True,
            ) from None
        return SecBulkArchiveSnapshot(
            dataset_kind=download.dataset_kind,
            source_url=download.source_url,
            source_version=download.source_version,
            content_sha256=download.content_sha256,
            byte_size=download.byte_size,
            object_bucket=self._bucket,
            object_key=object_key,
            retrieved_at=download.retrieved_at,
            bulk_published_at=download.bulk_published_at,
            coverage_through=download.coverage_through,
            adapter_version=SEC_BULK_ADAPTER_VERSION,
            watermark_policy_version=SEC_BULK_WATERMARK_POLICY_VERSION,
        )


class UnavailableSecBulkSnapshotStore:
    async def persist(self, download: SecBulkArchiveDownload) -> SecBulkArchiveSnapshot:
        del download
        raise SecSourceError(
            SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
            retryable=False,
        )


def extract_bulk_entry(
    download: SecBulkArchiveDownload,
    *,
    cik: str,
) -> SecBulkEntrySnapshot:
    entry_name = f"CIK{cik}.json"
    maximum_bytes = {
        SecBulkDatasetKind.SUBMISSIONS: SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
        SecBulkDatasetKind.COMPANYFACTS: SEC_MAX_XBRL_RESPONSE_BYTES,
    }[download.dataset_kind]
    try:
        download.stream.seek(0)
        with ZipFile(download.stream) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= SEC_MAX_BULK_ARCHIVE_ENTRIES:
                raise SecSourceError(
                    SecSourceErrorCode.BULK_ARCHIVE_INVALID,
                    retryable=False,
                )
            names: set[str] = set()
            selected = None
            for item in entries:
                if (
                    item.is_dir()
                    or _ENTRY_PATTERN.fullmatch(item.filename) is None
                    or item.filename in names
                    or item.flag_bits & 0x1
                ):
                    raise SecSourceError(
                        SecSourceErrorCode.BULK_ARCHIVE_INVALID,
                        retryable=False,
                    )
                names.add(item.filename)
                if item.filename == entry_name:
                    selected = item
            if selected is None:
                raise SecSourceError(
                    SecSourceErrorCode.BULK_ENTRY_MISSING,
                    retryable=False,
                )
            if not 1 <= selected.file_size <= maximum_bytes:
                raise SecSourceError(
                    SecSourceErrorCode.RESPONSE_TOO_LARGE,
                    retryable=False,
                )
            if (
                selected.compress_size == 0
                or selected.file_size / selected.compress_size > SEC_MAX_BULK_COMPRESSION_RATIO
            ):
                raise SecSourceError(
                    SecSourceErrorCode.BULK_ARCHIVE_INVALID,
                    retryable=False,
                )
            with archive.open(selected) as source:
                body = source.read(maximum_bytes + 1)
            if len(body) != selected.file_size:
                raise SecSourceError(
                    SecSourceErrorCode.BULK_ARCHIVE_PARTIAL,
                    retryable=False,
                )
    except SecSourceError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError):
        raise SecSourceError(
            SecSourceErrorCode.BULK_ARCHIVE_INVALID,
            retryable=False,
        ) from None
    return SecBulkEntrySnapshot(
        dataset_kind=download.dataset_kind,
        cik=cik,
        entry_name=entry_name,
        content_sha256=sha256_hex(body),
        byte_size=len(body),
        body=body,
    )


def _raise_for_status(response: httpx2.Response) -> None:
    if 300 <= response.status_code < 400:
        raise SecSourceError(SecSourceErrorCode.REDIRECT_REJECTED, retryable=False)
    if response.status_code == 429:
        raise SecSourceError(
            SecSourceErrorCode.RATE_LIMITED,
            retryable=True,
            retry_after_seconds=_retry_after(response.headers.get("retry-after")),
        )
    if response.status_code >= 500:
        raise SecSourceError(SecSourceErrorCode.UPSTREAM_ERROR, retryable=True)
    if response.status_code >= 400:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)


def _required_content_length(value: str | None) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        raise SecSourceError(
            SecSourceErrorCode.BULK_ARCHIVE_PARTIAL,
            retryable=True,
        ) from None
    if not 1 <= parsed <= SEC_MAX_BULK_ARCHIVE_BYTES:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_TOO_LARGE, retryable=False)
    return parsed


def _required_last_modified(value: str | None) -> datetime:
    try:
        parsed = parsedate_to_datetime(value or "")
    except (TypeError, ValueError):
        raise SecSourceError(
            SecSourceErrorCode.BULK_WATERMARK_INVALID,
            retryable=False,
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecSourceError(
            SecSourceErrorCode.BULK_WATERMARK_INVALID,
            retryable=False,
        )
    return parsed.astimezone(UTC)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), 60.0)


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SEC bulk clock must return an aware datetime")
    return value.astimezone(UTC)
