"""Bounded official SEC archive access for one locked accession."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx2

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    SecRequestBudget,
    _retry_after,
)
from industry_platform.modules.disclosures.domain import (
    SEC_FILING_CONTENT_ADAPTER_VERSION,
    SEC_MAX_ARCHIVE_ATTACHMENTS,
    SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
    SEC_MAX_ARCHIVE_TOTAL_BYTES,
    SecCanonicalFiling,
    SecFilingArchive,
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    SecSourceError,
    SecSourceErrorCode,
    sec_complete_submission_url,
    sec_filing_document_url,
    sec_primary_document_url,
    sha256_hex,
)

type Sleep = Callable[[float], Awaitable[None]]

_XBRL_DOCUMENT_TYPES = {
    "EX-101.INS": SecFilingDocumentKind.XBRL_INSTANCE,
    "EX-101.SCH": SecFilingDocumentKind.XBRL_ATTACHMENT,
    "EX-101.CAL": SecFilingDocumentKind.XBRL_ATTACHMENT,
    "EX-101.DEF": SecFilingDocumentKind.XBRL_ATTACHMENT,
    "EX-101.LAB": SecFilingDocumentKind.XBRL_ATTACHMENT,
    "EX-101.PRE": SecFilingDocumentKind.XBRL_ATTACHMENT,
}


class FrozenSecFilingArchiveAdapter:
    def __init__(
        self,
        archive: SecFilingArchive | dict[str, SecFilingArchive],
    ) -> None:
        self._archives = (
            dict(archive) if isinstance(archive, dict) else {archive.filing.accession: archive}
        )

    async def fetch_archive(self, filing: SecCanonicalFiling) -> SecFilingArchive:
        archive = self._archives.get(filing.accession)
        if archive is None or not _same_filing_identity(filing, archive.filing):
            raise SecFilingArchiveNotFoundError
        return replace(archive, filing=filing)


def _same_filing_identity(left: SecCanonicalFiling, right: SecCanonicalFiling) -> bool:
    return (
        left.cik,
        left.accession,
        left.form,
        left.report_date,
        left.filed_date,
        left.accepted_at,
        left.public_available_at,
        left.primary_document,
    ) == (
        right.cik,
        right.accession,
        right.form,
        right.report_date,
        right.filed_date,
        right.accepted_at,
        right.public_available_at,
        right.primary_document,
    )


class SecFilingArchiveNotFoundError(SecSourceError):
    def __init__(self) -> None:
        super().__init__(SecSourceErrorCode.FILING_NOT_FOUND, retryable=False)


class UnavailableSecFilingArchiveAdapter:
    async def fetch_archive(self, filing: SecCanonicalFiling) -> SecFilingArchive:
        del filing
        raise SecSourceError(SecSourceErrorCode.NOT_CONFIGURED, retryable=False)


class LiveSecFilingArchiveAdapter:
    """Fetch a locked accession and only its bounded XBRL document set."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        budget: SecRequestBudget,
        *,
        user_agent: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        timeout_seconds: float = 30.0,
        maximum_attempts: int = 3,
    ) -> None:
        if (
            not user_agent.strip()
            or "@" not in user_agent
            or any(character in user_agent for character in "\r\n")
        ):
            raise ValueError("SEC User-Agent must identify the application and contact email")
        if not 0 < timeout_seconds <= 60 or not 1 <= maximum_attempts <= 5:
            raise ValueError("SEC request policy is invalid")
        self._client = client
        self._budget = budget
        self._user_agent = user_agent
        self._clock = clock
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds
        self._maximum_attempts = maximum_attempts

    async def fetch_archive(self, filing: SecCanonicalFiling) -> SecFilingArchive:
        complete_url = sec_complete_submission_url(filing.cik, filing.accession)
        complete = await self._fetch(
            filing,
            kind=SecFilingDocumentKind.COMPLETE_SUBMISSION,
            filename=f"{filing.accession}.txt",
            url=complete_url,
            maximum_bytes=min(
                SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
                SEC_MAX_ARCHIVE_TOTAL_BYTES,
            ),
        )
        descriptors = _xbrl_document_descriptors(complete.body)
        for _kind, filename in descriptors:
            try:
                sec_filing_document_url(filing.cik, filing.accession, filename)
            except ValueError:
                raise SecSourceError(
                    SecSourceErrorCode.RESPONSE_INVALID,
                    retryable=False,
                ) from None
            if filename == filing.primary_document:
                raise SecSourceError(
                    SecSourceErrorCode.RESPONSE_INVALID,
                    retryable=False,
                )
        primary_url = sec_primary_document_url(
            filing.cik,
            filing.accession,
            filing.primary_document,
        )
        documents = [complete]
        remaining_bytes = SEC_MAX_ARCHIVE_TOTAL_BYTES - complete.byte_size
        requested_documents = (
            (
                SecFilingDocumentKind.PRIMARY_DOCUMENT,
                filing.primary_document,
                primary_url,
            ),
            *(
                (
                    kind,
                    filename,
                    sec_filing_document_url(filing.cik, filing.accession, filename),
                )
                for kind, filename in descriptors
            ),
        )
        for kind, filename, url in requested_documents:
            if remaining_bytes <= 0:
                raise SecSourceError(
                    SecSourceErrorCode.RESPONSE_TOO_LARGE,
                    retryable=False,
                )
            document = await self._fetch(
                filing,
                kind=kind,
                filename=filename,
                url=url,
                maximum_bytes=min(SEC_MAX_ARCHIVE_DOCUMENT_BYTES, remaining_bytes),
            )
            documents.append(document)
            remaining_bytes -= document.byte_size
        return SecFilingArchive(filing=filing, documents=tuple(documents))

    async def _fetch(
        self,
        filing: SecCanonicalFiling,
        *,
        kind: SecFilingDocumentKind,
        filename: str,
        url: str,
        maximum_bytes: int,
    ) -> SecFilingDocumentSnapshot:
        last_error: SecSourceError | None = None
        for attempt in range(self._maximum_attempts):
            try:
                return await self._request(
                    filing,
                    kind=kind,
                    filename=filename,
                    url=url,
                    maximum_bytes=maximum_bytes,
                )
            except SecSourceError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._maximum_attempts:
                    raise
                delay = error.retry_after_seconds
                if delay is None:
                    delay = min(0.25 * (2**attempt), 2.0)
                await self._sleep(delay)
        if last_error is None:
            raise AssertionError("SEC archive retry loop terminated without an outcome")
        raise last_error

    async def _request(
        self,
        filing: SecCanonicalFiling,
        *,
        kind: SecFilingDocumentKind,
        filename: str,
        url: str,
        maximum_bytes: int,
    ) -> SecFilingDocumentSnapshot:
        await self._budget.acquire()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": (
                            "text/plain,text/html,application/xhtml+xml,application/xml,text/xml"
                        ),
                        "Accept-Encoding": "gzip",
                        "User-Agent": self._user_agent,
                    },
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as response:
                    retrieved_at = _utc(self._clock())
                    if 300 <= response.status_code < 400:
                        raise SecSourceError(
                            SecSourceErrorCode.REDIRECT_REJECTED,
                            retryable=False,
                        )
                    if response.status_code == 429:
                        raise SecSourceError(
                            SecSourceErrorCode.RATE_LIMITED,
                            retryable=True,
                            retry_after_seconds=_retry_after(response.headers.get("retry-after")),
                        )
                    if response.status_code >= 500:
                        raise SecSourceError(SecSourceErrorCode.UPSTREAM_ERROR, retryable=True)
                    if response.status_code == 404:
                        raise SecFilingArchiveNotFoundError
                    if response.status_code >= 400:
                        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
                    content_type = response.headers.get("content-type", "").partition(";")[0]
                    content_type = content_type.strip().lower()
                    if content_type not in _allowed_content_types(kind):
                        raise SecSourceError(
                            SecSourceErrorCode.CONTENT_TYPE_INVALID,
                            retryable=False,
                        )
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_INVALID,
                                retryable=False,
                            ) from None
                        if not 0 < declared_size <= maximum_bytes:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    async for chunk in response.aiter_bytes():
                        observed += len(chunk)
                        if observed > maximum_bytes:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not body:
                        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
                    content_sha256 = sha256_hex(body)
                    return SecFilingDocumentSnapshot(
                        kind=kind,
                        cik=filing.cik,
                        accession=filing.accession,
                        filename=filename,
                        source_url=url,
                        source_version=f"sec-filing-{kind.value}-{content_sha256[:24]}",
                        content_type=content_type,
                        content_sha256=content_sha256,
                        byte_size=len(body),
                        retrieved_at=retrieved_at,
                        source_available_at=_source_available_at(
                            response.headers.get("last-modified"),
                            lower_bound=filing.public_available_at,
                            retrieved_at=retrieved_at,
                        ),
                        body=body,
                        adapter_version=SEC_FILING_CONTENT_ADAPTER_VERSION,
                    )
        except SecSourceError:
            raise
        except (TimeoutError, httpx2.TimeoutException):
            raise SecSourceError(SecSourceErrorCode.TIMEOUT, retryable=True) from None
        except (httpx2.RequestError, httpx2.InvalidURL):
            raise SecSourceError(SecSourceErrorCode.UPSTREAM_ERROR, retryable=True) from None


def _source_available_at(
    value: str | None,
    *,
    lower_bound: datetime,
    retrieved_at: datetime,
) -> datetime:
    if lower_bound > retrieved_at:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    if value is None:
        return retrieved_at
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return retrieved_at
    parsed = _utc(parsed)
    return min(max(parsed, lower_bound), retrieved_at)


def _xbrl_document_descriptors(
    complete_submission: bytes,
) -> tuple[tuple[SecFilingDocumentKind, str], ...]:
    descriptors: list[tuple[SecFilingDocumentKind, str]] = []
    seen_filenames: set[str] = set()
    instance_count = 0
    for section in complete_submission.split(b"<DOCUMENT>")[1:]:
        header, separator, _body = section.partition(b"<TEXT>")
        if not separator:
            continue
        values: dict[bytes, bytes] = {}
        for raw_line in header.splitlines():
            line = raw_line.strip()
            if not line.startswith(b"<") or b">" not in line:
                continue
            raw_tag, value = line[1:].split(b">", 1)
            values[raw_tag.strip().upper()] = value.strip()
        try:
            document_type = values[b"TYPE"].decode("ascii").upper()
        except (KeyError, UnicodeDecodeError):
            continue
        kind = _XBRL_DOCUMENT_TYPES.get(document_type)
        if kind is None:
            continue
        try:
            filename = values[b"FILENAME"].decode("ascii")
        except (KeyError, UnicodeDecodeError):
            raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None
        if not filename or filename in seen_filenames:
            raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
        seen_filenames.add(filename)
        instance_count += kind is SecFilingDocumentKind.XBRL_INSTANCE
        descriptors.append((kind, filename))
    if instance_count > 1 or len(descriptors) > SEC_MAX_ARCHIVE_ATTACHMENTS:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    return tuple(sorted(descriptors, key=lambda item: (item[0].value, item[1])))


def _allowed_content_types(kind: SecFilingDocumentKind) -> frozenset[str]:
    if kind is SecFilingDocumentKind.COMPLETE_SUBMISSION:
        return frozenset({"text/plain"})
    if kind is SecFilingDocumentKind.PRIMARY_DOCUMENT:
        return frozenset({"text/html", "application/xhtml+xml"})
    return frozenset({"application/xml", "text/xml", "text/plain"})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SEC archive clock must return an aware datetime")
    return value.astimezone(UTC)
