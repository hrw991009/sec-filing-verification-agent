"""Executable closeout contracts for SEC nightly bulk watermarks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

import httpx2
import pytest

from industry_platform.modules.disclosures.adapters.sec_bulk import (
    LiveSecBulkArchiveAdapter,
    MinioSecBulkSnapshotStore,
    extract_bulk_entry,
)
from industry_platform.modules.disclosures.bulk import (
    SEC_COMPANYFACTS_BULK_URL,
    SEC_SUBMISSIONS_BULK_URL,
    SecBulkArchiveDownload,
    SecBulkDatasetKind,
    SecBulkSyncReceipt,
    SecBulkSyncService,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecCanonicalFiling,
    SecFilingForm,
    SecFilingObservation,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceSnapshot,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    sec_submissions_source_version,
    sec_xbrl_source_version,
    sha256_hex,
)
from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    StoredObjectStat,
)

CIK = "0000320193"
PUBLISHED_AT = datetime(2023, 11, 1, 8, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2023, 11, 1, 9, 0, tzinfo=UTC)
INCREMENTAL_AT = datetime(2023, 11, 1, 8, 30, tzinfo=UTC)
LAST_MODIFIED = "Wed, 01 Nov 2023 08:00:00 GMT"
USER_AGENT = "IndustryIntelligencePlatform/0.1 edgar-ops@example.test"


@dataclass(slots=True)
class CountingBudget:
    calls: int = 0

    async def acquire(self) -> None:
        self.calls += 1


@dataclass(slots=True)
class MemoryBulkObjectStore:
    objects: dict[tuple[str, str], tuple[str, bytes]] = field(default_factory=dict)

    async def stat(self, *, bucket: str, object_key: str) -> StoredObjectStat:
        stored = self.objects.get((bucket, object_key))
        if stored is None:
            raise FileObjectNotFoundError
        content_type, content = stored
        return StoredObjectStat(size=len(content), etag="fixture-etag", content_type=content_type)

    async def put_private_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        stream: object,
        exact_size: int,
    ) -> None:
        assert hasattr(stream, "read")
        content = stream.read()
        assert isinstance(content, bytes)
        assert len(content) == exact_size
        self.objects[(bucket, object_key)] = (content_type, content)


@dataclass(slots=True)
class MemoryBulkRepository:
    receipts: dict[tuple[str, str, datetime], SecBulkSyncReceipt] = field(default_factory=dict)

    async def persist(self, receipt: SecBulkSyncReceipt) -> bool:
        key = (
            receipt.archive.source_version,
            receipt.entry.cik,
            receipt.gap_observed_through,
        )
        existing = self.receipts.get(key)
        if existing is not None:
            assert existing == receipt
            return False
        self.receipts[key] = receipt
        return True


@dataclass(frozen=True, slots=True)
class FrozenPostWatermarkSubmissions:
    snapshot: SecSubmissionSet

    async def fetch_submission_set_after(
        self,
        scope: FilingSelectionScope,
        *,
        watermark: datetime,
    ) -> SecSubmissionSet:
        assert scope.cik == self.snapshot.current.cik
        assert watermark == PUBLISHED_AT
        return self.snapshot


@dataclass(frozen=True, slots=True)
class FrozenPostWatermarkCompanyFacts:
    snapshot: SecXbrlSourceSnapshot

    async def fetch_after(
        self,
        filing: SecCanonicalFiling,
        *,
        watermark: datetime,
    ) -> SecXbrlSourceSnapshot:
        assert filing.cik == self.snapshot.cik
        assert watermark == PUBLISHED_AT
        return self.snapshot


def submissions_body() -> bytes:
    columns = {
        "accessionNumber": ["0000320193-23-000106"],
        "filingDate": ["2023-11-01"],
        "reportDate": ["2023-09-30"],
        "acceptanceDateTime": ["2023-11-01T07:30:00Z"],
        "form": ["10-K"],
        "primaryDocument": ["aapl-20230930.htm"],
    }
    return json.dumps(
        {"cik": 320193, "filings": {"recent": columns, "files": []}},
        separators=(",", ":"),
    ).encode()


def companyfacts_body() -> bytes:
    return json.dumps(
        {"cik": 320193, "entityName": "Apple Inc.", "facts": {}},
        separators=(",", ":"),
    ).encode()


def zip_body(entry_body: bytes, *, entry_name: str = f"CIK{CIK}.json") -> bytes:
    target = BytesIO()
    with ZipFile(target, mode="w") as archive:
        archive.writestr(entry_name, entry_body)
    return target.getvalue()


def scope() -> FilingSelectionScope:
    return FilingSelectionScope(
        cik=CIK,
        allowed_forms=(SecFilingForm.TEN_K,),
        report_period_start=date(2023, 1, 1),
        report_period_end=date(2023, 12, 31),
        as_of=RETRIEVED_AT,
        amendment_policy=SecAmendmentPolicy.AS_FILED,
    )


def filing() -> SecCanonicalFiling:
    return SecCanonicalFiling(
        id=UUID("10000000-0000-4000-8000-000000000001"),
        cik=CIK,
        accession="0000320193-23-000106",
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=date(2023, 11, 1),
        accepted_at=datetime(2023, 11, 1, 7, 30, tzinfo=UTC),
        public_available_at=datetime(2023, 11, 1, 7, 30, tzinfo=UTC),
        primary_document="aapl-20230930.htm",
        source_available_at=PUBLISHED_AT,
    )


def submission_set() -> SecSubmissionSet:
    body = submissions_body()
    content_hash = sha256_hex(body)
    current = SecSubmissionSourceSnapshot(
        cik=CIK,
        source_kind=SecSubmissionSourceKind.CURRENT,
        source_name=f"CIK{CIK}.json",
        source_url=f"https://data.sec.gov/submissions/CIK{CIK}.json",
        source_version=sec_submissions_source_version(
            SecSubmissionSourceKind.CURRENT,
            content_hash,
        ),
        content_sha256=content_hash,
        retrieved_at=INCREMENTAL_AT,
        source_available_at=INCREMENTAL_AT,
        body=body,
        filings=(
            SecFilingObservation(
                cik=CIK,
                accession="0000320193-23-000106",
                form=SecFilingForm.TEN_K,
                report_date=date(2023, 9, 30),
                filed_date=date(2023, 11, 1),
                accepted_at=datetime(2023, 11, 1, 7, 30, tzinfo=UTC),
                primary_document="aapl-20230930.htm",
            ),
        ),
    )
    return SecSubmissionSet(current=current, supplementals=(), required_supplemental_names=())


def companyfacts_snapshot() -> SecXbrlSourceSnapshot:
    body = companyfacts_body()
    content_hash = sha256_hex(body)
    return SecXbrlSourceSnapshot(
        source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
        cik=CIK,
        source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
        source_version=sec_xbrl_source_version(
            SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
            content_hash,
        ),
        content_type="application/json",
        content_sha256=content_hash,
        byte_size=len(body),
        retrieved_at=INCREMENTAL_AT,
        source_available_at=INCREMENTAL_AT,
        body=body,
    )


def live_archive_source(payloads: dict[str, bytes]) -> LiveSecBulkArchiveAdapter:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = payloads[str(request.url)]
        return httpx2.Response(
            200,
            headers={
                "content-type": "application/zip",
                "content-length": str(len(body)),
                "last-modified": LAST_MODIFIED,
            },
            content=body,
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    source = LiveSecBulkArchiveAdapter(
        client,
        CountingBudget(),
        user_agent=USER_AGENT,
        clock=lambda: RETRIEVED_AT,
        timeout_seconds=900.0,
    )
    source._test_client = client  # type: ignore[attr-defined]
    return source


@pytest.mark.asyncio
async def test_submissions_bulk_watermark_persists_snapshot_and_closes_post_watermark_gap() -> None:
    source = live_archive_source({SEC_SUBMISSIONS_BULK_URL: zip_body(submissions_body())})
    object_store = MemoryBulkObjectStore()
    repository = MemoryBulkRepository()
    service = SecBulkSyncService(
        archive_source=source,
        snapshot_store=MinioSecBulkSnapshotStore(object_store, bucket="private"),
        repository=repository,
        submissions_source=FrozenPostWatermarkSubmissions(submission_set()),
        companyfacts_source=FrozenPostWatermarkCompanyFacts(companyfacts_snapshot()),
    )
    try:
        first = await service.sync_submissions(scope())
        second = await service.sync_submissions(scope())
    finally:
        await source._test_client.aclose()  # type: ignore[attr-defined]

    assert first.committed is True
    assert second.committed is False
    assert first.duplicate_commits == second.duplicate_commits == 0
    assert first.receipt.archive.bulk_published_at == PUBLISHED_AT
    assert first.receipt.archive.coverage_through == datetime(2023, 11, 1, 7, 59, 59, tzinfo=UTC)
    assert first.receipt.gap_observed_through == INCREMENTAL_AT
    assert first.receipt.entry.content_sha256 == sha256_hex(submissions_body())
    assert len(object_store.objects) == 1
    assert len(repository.receipts) == 1


@pytest.mark.asyncio
async def test_companyfacts_bulk_watermark_persists_snapshot_and_closes_post_watermark_gap() -> (
    None
):
    source = live_archive_source({SEC_COMPANYFACTS_BULK_URL: zip_body(companyfacts_body())})
    object_store = MemoryBulkObjectStore()
    repository = MemoryBulkRepository()
    service = SecBulkSyncService(
        archive_source=source,
        snapshot_store=MinioSecBulkSnapshotStore(object_store, bucket="private"),
        repository=repository,
        submissions_source=FrozenPostWatermarkSubmissions(submission_set()),
        companyfacts_source=FrozenPostWatermarkCompanyFacts(companyfacts_snapshot()),
    )
    try:
        result = await service.sync_companyfacts(filing())
    finally:
        await source._test_client.aclose()  # type: ignore[attr-defined]

    assert result.committed is True
    assert result.duplicate_commits == 0
    assert result.receipt.archive.dataset_kind is SecBulkDatasetKind.COMPANYFACTS
    assert result.receipt.archive.bulk_published_at == PUBLISHED_AT
    assert result.receipt.archive.coverage_through == datetime(2023, 11, 1, 7, 59, 59, tzinfo=UTC)
    assert result.receipt.gap_observed_through == INCREMENTAL_AT
    assert result.receipt.entry.content_sha256 == sha256_hex(companyfacts_body())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        (
            {"content-type": "application/zip", "content-length": "9999"},
            SecSourceErrorCode.BULK_WATERMARK_INVALID,
        ),
        (
            {
                "content-type": "application/zip",
                "content-length": "9999",
                "last-modified": LAST_MODIFIED,
            },
            SecSourceErrorCode.BULK_ARCHIVE_PARTIAL,
        ),
    ],
)
async def test_bulk_download_fails_closed_on_missing_watermark_or_partial_body(
    headers: dict[str, str],
    expected_code: SecSourceErrorCode,
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(200, headers=headers, content=b"truncated")
        )
    ) as client:
        source = LiveSecBulkArchiveAdapter(
            client,
            CountingBudget(),
            user_agent=USER_AGENT,
            clock=lambda: RETRIEVED_AT,
            maximum_attempts=1,
        )
        with pytest.raises(SecSourceError) as caught:
            await source.fetch(SecBulkDatasetKind.SUBMISSIONS)

    assert caught.value.code is expected_code


@pytest.mark.parametrize("entry_name", ["../CIK0000320193.json", "CIK0000789019.json"])
def test_bulk_archive_fails_closed_on_unsafe_or_missing_cik_member(entry_name: str) -> None:
    body = zip_body(submissions_body(), entry_name=entry_name)
    download = SecBulkArchiveDownload(
        dataset_kind=SecBulkDatasetKind.SUBMISSIONS,
        source_url=SEC_SUBMISSIONS_BULK_URL,
        source_version=f"sec-submissions-bulk-v1-{sha256_hex(body)[:24]}",
        content_sha256=sha256_hex(body),
        byte_size=len(body),
        retrieved_at=RETRIEVED_AT,
        bulk_published_at=PUBLISHED_AT,
        coverage_through=datetime(2023, 11, 1, 7, 59, 59, tzinfo=UTC),
        stream=BytesIO(body),
    )
    try:
        with pytest.raises(SecSourceError) as caught:
            extract_bulk_entry(download, cik=CIK)
    finally:
        download.close()

    assert caught.value.code in {
        SecSourceErrorCode.BULK_ARCHIVE_INVALID,
        SecSourceErrorCode.BULK_ENTRY_MISSING,
    }
