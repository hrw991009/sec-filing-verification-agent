"""Prove SEC filing source and coverage facts against real PostgreSQL."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.disclosures.adapters.filings_sqlalchemy import (
    SqlAlchemySecFilingRepository,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFilingForm,
    SecFilingObservation,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceSnapshot,
    sec_submissions_current_url,
    sec_submissions_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.models import (
    SecFilingCoverageRecord,
    SecFilingCoverageSourceRecord,
    SecFilingObservationRecord,
    SecFilingRecord,
    SecSubmissionSourceRecord,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

CIK = "0000320193"
NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)


def _observation(
    accession: str,
    form: SecFilingForm,
    accepted_at: datetime,
) -> SecFilingObservation:
    return SecFilingObservation(
        cik=CIK,
        accession=accession,
        form=form,
        report_date=date(2024, 9, 28),
        filed_date=accepted_at.date(),
        accepted_at=accepted_at,
        primary_document=f"{accession}.htm",
    )


def _submission_set() -> SecSubmissionSet:
    body = b'{"fixture":"postgres-point-in-time"}'
    content_hash = sha256_hex(body)
    source = SecSubmissionSourceSnapshot(
        cik=CIK,
        source_kind=SecSubmissionSourceKind.CURRENT,
        source_name=f"CIK{CIK}.json",
        source_url=sec_submissions_current_url(CIK),
        source_version=sec_submissions_source_version(
            SecSubmissionSourceKind.CURRENT,
            content_hash,
        ),
        content_sha256=content_hash,
        retrieved_at=NOW,
        source_available_at=NOW - timedelta(minutes=10),
        body=body,
        filings=(
            _observation(
                "0000320193-24-000001",
                SecFilingForm.TEN_K,
                datetime(2024, 11, 1, 18, 0, tzinfo=UTC),
            ),
            _observation(
                "0000320193-24-000002",
                SecFilingForm.TEN_K_AMENDMENT,
                datetime(2024, 11, 10, 18, 0, tzinfo=UTC),
            ),
        ),
    )
    return SecSubmissionSet(current=source, supplementals=(), required_supplemental_names=())


def _scope() -> FilingSelectionScope:
    return FilingSelectionScope(
        cik=CIK,
        allowed_forms=(SecFilingForm.TEN_K, SecFilingForm.TEN_K_AMENDMENT),
        report_period_start=date(2024, 1, 1),
        report_period_end=date(2024, 12, 31),
        as_of=NOW,
        amendment_policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF,
    )


def test_submission_versions_and_coverage_are_idempotent_and_reconstructable(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        repository = SqlAlchemySecFilingRepository(
            session_factory,
            object_bucket="integration-private",
        )
        submission_set = _submission_set()
        scope = _scope()
        source = submission_set.current
        object_keys = {
            source.source_version: (f"sec/submissions/{CIK}/current/{source.content_sha256}.json")
        }
        try:
            first_version = await repository.replace_submission_set(
                submission_set,
                object_keys=object_keys,
                scope=scope,
            )
            second_version = await repository.replace_submission_set(
                submission_set,
                object_keys=object_keys,
                scope=scope,
            )
            dataset = await repository.load_dataset(
                coverage_version=first_version,
                scope=scope,
            )

            assert first_version == second_version
            assert [filing.accession for filing in dataset.filings] == [
                "0000320193-24-000001",
                "0000320193-24-000002",
            ]
            assert dataset.filings[1].amendment_relation_status is (
                SecAmendmentRelationStatus.RESOLVED
            )
            assert dataset.filings[1].base_accession == "0000320193-24-000001"

            async with session_factory() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecSubmissionSourceRecord)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecFilingObservationRecord)
                    )
                    == 2
                )
                assert await session.scalar(select(func.count()).select_from(SecFilingRecord)) == 2
                assert (
                    await session.scalar(select(func.count()).select_from(SecFilingCoverageRecord))
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecFilingCoverageSourceRecord)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()
