"""Prove the SEC bulk watermark ledger against real PostgreSQL."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.disclosures.adapters.bulk_sqlalchemy import (
    SqlAlchemySecBulkSyncRepository,
)
from industry_platform.modules.disclosures.bulk import (
    SEC_SUBMISSIONS_BULK_URL,
    SecBulkArchiveSnapshot,
    SecBulkDatasetKind,
    SecBulkEntrySnapshot,
    SecBulkIncrementalSource,
    SecBulkSyncReceipt,
)
from industry_platform.modules.disclosures.domain import (
    SecSubmissionSourceKind,
    sec_submissions_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.models import (
    SecBulkEntryRecord,
    SecBulkGapClosureRecord,
    SecBulkGapSourceRecord,
    SecBulkSourceRecord,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

CIK = "0000320193"
PUBLISHED_AT = datetime(2023, 11, 1, 8, 0, tzinfo=UTC)
COVERAGE_THROUGH = datetime(2023, 11, 1, 7, 59, 59, tzinfo=UTC)
RETRIEVED_AT = datetime(2023, 11, 1, 8, 30, tzinfo=UTC)


def receipt() -> SecBulkSyncReceipt:
    archive_hash = "a" * 64
    entry_body = b'{"cik":320193,"filings":{"recent":{},"files":[]}}'
    entry_hash = sha256_hex(entry_body)
    incremental_hash = "b" * 64
    return SecBulkSyncReceipt(
        archive=SecBulkArchiveSnapshot(
            dataset_kind=SecBulkDatasetKind.SUBMISSIONS,
            source_url=SEC_SUBMISSIONS_BULK_URL,
            source_version=f"sec-submissions-bulk-v1-{archive_hash[:24]}",
            content_sha256=archive_hash,
            byte_size=1_024,
            object_bucket="integration-private",
            object_key=f"sec/bulk/submissions/{archive_hash}.zip",
            retrieved_at=RETRIEVED_AT,
            bulk_published_at=PUBLISHED_AT,
            coverage_through=COVERAGE_THROUGH,
        ),
        entry=SecBulkEntrySnapshot(
            dataset_kind=SecBulkDatasetKind.SUBMISSIONS,
            cik=CIK,
            entry_name=f"CIK{CIK}.json",
            content_sha256=entry_hash,
            byte_size=len(entry_body),
            body=entry_body,
        ),
        gap_observed_through=RETRIEVED_AT,
        incremental_sources=(
            SecBulkIncrementalSource(
                source_kind=SecSubmissionSourceKind.CURRENT.value,
                source_url=f"https://data.sec.gov/submissions/CIK{CIK}.json",
                source_version=sec_submissions_source_version(
                    SecSubmissionSourceKind.CURRENT,
                    incremental_hash,
                ),
                content_sha256=incremental_hash,
                source_available_at=RETRIEVED_AT,
                retrieved_at=RETRIEVED_AT,
            ),
        ),
    )


def test_bulk_watermark_receipt_is_append_only_and_idempotent(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        repository = SqlAlchemySecBulkSyncRepository(session_factory)
        snapshot = receipt()
        try:
            assert await repository.persist(snapshot) is True
            assert await repository.persist(snapshot) is False
            async with session_factory() as session:
                counts: list[int | None] = []
                for model in (
                    SecBulkSourceRecord,
                    SecBulkEntryRecord,
                    SecBulkGapClosureRecord,
                    SecBulkGapSourceRecord,
                ):
                    counts.append(await session.scalar(select(func.count()).select_from(model)))
                archive = await session.scalar(select(SecBulkSourceRecord))
                closure = await session.scalar(select(SecBulkGapClosureRecord))
            assert counts == [1, 1, 1, 1]
            assert archive is not None
            assert archive.content_sha256.hex() == snapshot.archive.content_sha256
            assert archive.bulk_published_at.astimezone(UTC) == PUBLISHED_AT
            assert archive.coverage_through.astimezone(UTC) == COVERAGE_THROUGH
            assert closure is not None
            assert closure.gap_observed_through.astimezone(UTC) == RETRIEVED_AT
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
