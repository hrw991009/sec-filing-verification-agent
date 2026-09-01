"""Nightly SEC bulk snapshots and post-watermark gap closure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import BinaryIO, Protocol

from industry_platform.modules.agent_runtime.domain import require_utc
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecCanonicalFiling,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSet,
    SecXbrlSourceSnapshot,
    sha256_hex,
)

SEC_SUBMISSIONS_BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
SEC_COMPANYFACTS_BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SEC_BULK_ADAPTER_VERSION = "sec-bulk-v1"
SEC_BULK_WATERMARK_POLICY_VERSION = "sec-bulk-last-modified-v1"
SEC_MAX_BULK_ARCHIVE_BYTES = 4 * 1_024 * 1_024 * 1_024
SEC_MAX_BULK_ARCHIVE_ENTRIES = 25_000
SEC_MAX_BULK_COMPRESSION_RATIO = 1_000

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class SecBulkDatasetKind(StrEnum):
    SUBMISSIONS = "submissions"
    COMPANYFACTS = "companyfacts"

    @property
    def source_url(self) -> str:
        return {
            SecBulkDatasetKind.SUBMISSIONS: SEC_SUBMISSIONS_BULK_URL,
            SecBulkDatasetKind.COMPANYFACTS: SEC_COMPANYFACTS_BULK_URL,
        }[self]


@dataclass(slots=True)
class SecBulkArchiveDownload:
    """One bounded, seekable download; callers own and close the stream."""

    dataset_kind: SecBulkDatasetKind
    source_url: str
    source_version: str
    content_sha256: str
    byte_size: int
    retrieved_at: datetime
    bulk_published_at: datetime
    coverage_through: datetime
    stream: BinaryIO = field(repr=False)
    adapter_version: str = SEC_BULK_ADAPTER_VERSION
    watermark_policy_version: str = SEC_BULK_WATERMARK_POLICY_VERSION

    def __post_init__(self) -> None:
        _validate_archive_identity(
            dataset_kind=self.dataset_kind,
            source_url=self.source_url,
            source_version=self.source_version,
            content_sha256=self.content_sha256,
            byte_size=self.byte_size,
            retrieved_at=self.retrieved_at,
            bulk_published_at=self.bulk_published_at,
            coverage_through=self.coverage_through,
            adapter_version=self.adapter_version,
            watermark_policy_version=self.watermark_policy_version,
        )
        if not all(hasattr(self.stream, method) for method in ("read", "seek", "close")):
            raise ValueError("SEC bulk download stream is invalid")

    def close(self) -> None:
        self.stream.close()


@dataclass(frozen=True, slots=True)
class SecBulkArchiveSnapshot:
    dataset_kind: SecBulkDatasetKind
    source_url: str
    source_version: str
    content_sha256: str
    byte_size: int
    object_bucket: str
    object_key: str
    retrieved_at: datetime
    bulk_published_at: datetime
    coverage_through: datetime
    adapter_version: str = SEC_BULK_ADAPTER_VERSION
    watermark_policy_version: str = SEC_BULK_WATERMARK_POLICY_VERSION

    def __post_init__(self) -> None:
        _validate_archive_identity(
            dataset_kind=self.dataset_kind,
            source_url=self.source_url,
            source_version=self.source_version,
            content_sha256=self.content_sha256,
            byte_size=self.byte_size,
            retrieved_at=self.retrieved_at,
            bulk_published_at=self.bulk_published_at,
            coverage_through=self.coverage_through,
            adapter_version=self.adapter_version,
            watermark_policy_version=self.watermark_policy_version,
        )
        if not self.object_bucket.strip() or not self.object_key.strip():
            raise ValueError("SEC bulk object locator is invalid")


@dataclass(frozen=True, slots=True)
class SecBulkEntrySnapshot:
    dataset_kind: SecBulkDatasetKind
    cik: str
    entry_name: str
    content_sha256: str
    byte_size: int
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC bulk entry CIK is invalid")
        if self.entry_name != f"CIK{self.cik}.json":
            raise ValueError("SEC bulk entry name is invalid")
        snapshot = bytes(self.body)
        if (
            not snapshot
            or self.byte_size != len(snapshot)
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or sha256_hex(snapshot) != self.content_sha256
        ):
            raise ValueError("SEC bulk entry bytes are invalid")
        object.__setattr__(self, "body", snapshot)


@dataclass(frozen=True, slots=True)
class SecBulkIncrementalSource:
    source_kind: str
    source_url: str
    source_version: str
    content_sha256: str
    source_available_at: datetime
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not self.source_kind.strip() or len(self.source_kind) > 64:
            raise ValueError("SEC bulk gap source kind is invalid")
        if not self.source_url.startswith("https://data.sec.gov/"):
            raise ValueError("SEC bulk gap source URL is invalid")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version):
            raise ValueError("SEC bulk gap source version is invalid")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("SEC bulk gap source hash is invalid")
        require_utc(self.source_available_at, field_name="SEC bulk gap source available_at")
        require_utc(self.retrieved_at, field_name="SEC bulk gap source retrieved_at")
        if self.source_available_at > self.retrieved_at:
            raise ValueError("SEC bulk gap source availability is invalid")


@dataclass(frozen=True, slots=True)
class SecBulkSyncReceipt:
    archive: SecBulkArchiveSnapshot
    entry: SecBulkEntrySnapshot
    gap_observed_through: datetime
    incremental_sources: tuple[SecBulkIncrementalSource, ...]

    def __post_init__(self) -> None:
        if self.entry.dataset_kind is not self.archive.dataset_kind:
            raise ValueError("SEC bulk receipt dataset is inconsistent")
        require_utc(self.gap_observed_through, field_name="SEC bulk gap observed_through")
        sources = tuple(self.incremental_sources)
        if not sources or len({item.source_url for item in sources}) != len(sources):
            raise ValueError("SEC bulk gap sources are invalid")
        if any(item.retrieved_at < self.archive.bulk_published_at for item in sources):
            raise ValueError("SEC bulk gap source predates the bulk watermark")
        if self.gap_observed_through != min(item.retrieved_at for item in sources):
            raise ValueError("SEC bulk gap observation boundary is invalid")
        expected_prefix = {
            SecBulkDatasetKind.SUBMISSIONS: "https://data.sec.gov/submissions/",
            SecBulkDatasetKind.COMPANYFACTS: "https://data.sec.gov/api/xbrl/companyfacts/",
        }[self.archive.dataset_kind]
        if any(not item.source_url.startswith(expected_prefix) for item in sources):
            raise ValueError("SEC bulk gap source does not match the dataset")
        object.__setattr__(self, "incremental_sources", sources)


@dataclass(frozen=True, slots=True)
class SecBulkSyncResult:
    receipt: SecBulkSyncReceipt
    committed: bool

    @property
    def duplicate_commits(self) -> int:
        return 0


class SecBulkArchivePort(Protocol):
    async def fetch(self, dataset_kind: SecBulkDatasetKind) -> SecBulkArchiveDownload: ...


class SecBulkSnapshotStore(Protocol):
    async def persist(self, download: SecBulkArchiveDownload) -> SecBulkArchiveSnapshot: ...


class SecBulkSyncRepository(Protocol):
    async def persist(self, receipt: SecBulkSyncReceipt) -> bool: ...


class SecPostWatermarkSubmissionsPort(Protocol):
    async def fetch_submission_set_after(
        self,
        scope: FilingSelectionScope,
        *,
        watermark: datetime,
    ) -> SecSubmissionSet: ...


class SecPostWatermarkCompanyFactsPort(Protocol):
    async def fetch_after(
        self,
        filing: SecCanonicalFiling,
        *,
        watermark: datetime,
    ) -> SecXbrlSourceSnapshot: ...


@dataclass(frozen=True, slots=True)
class SecBulkSyncService:
    archive_source: SecBulkArchivePort
    snapshot_store: SecBulkSnapshotStore
    repository: SecBulkSyncRepository
    submissions_source: SecPostWatermarkSubmissionsPort
    companyfacts_source: SecPostWatermarkCompanyFactsPort

    async def sync_submissions(self, scope: FilingSelectionScope) -> SecBulkSyncResult:
        download = await self.archive_source.fetch(SecBulkDatasetKind.SUBMISSIONS)
        try:
            entry = _extract_entry(download, scope.cik)
            from industry_platform.modules.disclosures.adapters.sec_submissions import (
                validate_submissions_bulk_entry,
            )

            validate_submissions_bulk_entry(entry.body, cik=scope.cik)
            archive = await self.snapshot_store.persist(download)
            incremental = await self.submissions_source.fetch_submission_set_after(
                scope,
                watermark=archive.bulk_published_at,
            )
            current = incremental.current
            if current.cik != scope.cik or current.retrieved_at < archive.bulk_published_at:
                raise SecSourceError(
                    SecSourceErrorCode.COVERAGE_INCOMPLETE,
                    retryable=True,
                )
            receipt = SecBulkSyncReceipt(
                archive=archive,
                entry=entry,
                gap_observed_through=current.retrieved_at,
                incremental_sources=(
                    SecBulkIncrementalSource(
                        source_kind=current.source_kind.value,
                        source_url=current.source_url,
                        source_version=current.source_version,
                        content_sha256=current.content_sha256,
                        source_available_at=current.source_available_at,
                        retrieved_at=current.retrieved_at,
                    ),
                ),
            )
            return SecBulkSyncResult(
                receipt=receipt,
                committed=await self.repository.persist(receipt),
            )
        finally:
            download.close()

    async def sync_companyfacts(self, filing: SecCanonicalFiling) -> SecBulkSyncResult:
        download = await self.archive_source.fetch(SecBulkDatasetKind.COMPANYFACTS)
        try:
            entry = _extract_entry(download, filing.cik)
            from industry_platform.modules.disclosures.adapters.xbrl import (
                validate_companyfacts_bulk_entry,
            )

            validate_companyfacts_bulk_entry(entry.body, cik=filing.cik)
            archive = await self.snapshot_store.persist(download)
            incremental = await self.companyfacts_source.fetch_after(
                filing,
                watermark=archive.bulk_published_at,
            )
            if (
                incremental.cik != filing.cik
                or incremental.retrieved_at < archive.bulk_published_at
            ):
                raise SecSourceError(
                    SecSourceErrorCode.COVERAGE_INCOMPLETE,
                    retryable=True,
                )
            receipt = SecBulkSyncReceipt(
                archive=archive,
                entry=entry,
                gap_observed_through=incremental.retrieved_at,
                incremental_sources=(
                    SecBulkIncrementalSource(
                        source_kind=incremental.source_kind.value,
                        source_url=incremental.source_url,
                        source_version=incremental.source_version,
                        content_sha256=incremental.content_sha256,
                        source_available_at=incremental.source_available_at,
                        retrieved_at=incremental.retrieved_at,
                    ),
                ),
            )
            return SecBulkSyncResult(
                receipt=receipt,
                committed=await self.repository.persist(receipt),
            )
        finally:
            download.close()


def _extract_entry(download: SecBulkArchiveDownload, cik: str) -> SecBulkEntrySnapshot:
    from industry_platform.modules.disclosures.adapters.sec_bulk import extract_bulk_entry

    return extract_bulk_entry(download, cik=cik)


def _validate_archive_identity(
    *,
    dataset_kind: SecBulkDatasetKind,
    source_url: str,
    source_version: str,
    content_sha256: str,
    byte_size: int,
    retrieved_at: datetime,
    bulk_published_at: datetime,
    coverage_through: datetime,
    adapter_version: str,
    watermark_policy_version: str,
) -> None:
    if source_url != dataset_kind.source_url:
        raise ValueError("SEC bulk source URL is invalid")
    if not _SOURCE_VERSION_PATTERN.fullmatch(source_version):
        raise ValueError("SEC bulk source version is invalid")
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("SEC bulk source hash is invalid")
    if isinstance(byte_size, bool) or not 1 <= byte_size <= SEC_MAX_BULK_ARCHIVE_BYTES:
        raise ValueError("SEC bulk source size is invalid")
    require_utc(retrieved_at, field_name="SEC bulk retrieved_at")
    require_utc(bulk_published_at, field_name="SEC bulk published_at")
    require_utc(coverage_through, field_name="SEC bulk coverage_through")
    if not coverage_through < bulk_published_at <= retrieved_at:
        raise ValueError("SEC bulk watermark order is invalid")
    if not _SOURCE_VERSION_PATTERN.fullmatch(adapter_version):
        raise ValueError("SEC bulk adapter version is invalid")
    if not _SOURCE_VERSION_PATTERN.fullmatch(watermark_policy_version):
        raise ValueError("SEC bulk watermark policy version is invalid")
