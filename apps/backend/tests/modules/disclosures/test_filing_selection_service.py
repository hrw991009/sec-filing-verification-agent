"""Point-in-time filing selection behavior independent of persistence technology."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFilingCandidate,
    SecFilingDataset,
    SecFilingForm,
    SecFilingObservation,
    SecFilingSelectionStatus,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceReference,
    SecSubmissionSourceSnapshot,
    sec_submissions_current_url,
    sec_submissions_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.service import SecFilingSelectionService
from industry_platform.modules.workspaces.domain import WorkspaceScope

CIK = "0000320193"
NOW = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
AVAILABLE_AT = NOW - timedelta(hours=1)
WORKSPACE_SCOPE = WorkspaceScope(
    workspace_id=UUID("11111111-1111-4111-8111-111111111111"),
    user_id=UUID("22222222-2222-4222-8222-222222222222"),
    role="viewer",
)


def selection_scope(
    *,
    policy: SecAmendmentPolicy,
    as_of: datetime = NOW,
    forms: tuple[SecFilingForm, ...] = (
        SecFilingForm.TEN_K,
        SecFilingForm.TEN_K_AMENDMENT,
    ),
) -> FilingSelectionScope:
    return FilingSelectionScope(
        cik=CIK,
        allowed_forms=forms,
        report_period_start=date(2024, 1, 1),
        report_period_end=date(2024, 12, 31),
        as_of=as_of,
        amendment_policy=policy,
    )


def observation(accession: str, form: SecFilingForm, accepted_at: datetime) -> SecFilingObservation:
    return SecFilingObservation(
        cik=CIK,
        accession=accession,
        form=form,
        report_date=date(2024, 9, 28),
        filed_date=accepted_at.date(),
        accepted_at=accepted_at,
        primary_document=f"{accession}.htm",
    )


def test_filing_observation_accepts_utc_evening_rollover_but_rejects_later_acceptance() -> None:
    rollover = SecFilingObservation(
        cik=CIK,
        accession="0000320193-23-000106",
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=date(2023, 11, 3),
        accepted_at=datetime(2023, 11, 2, 22, 8, 27, tzinfo=UTC),
        primary_document="aapl-20230930.htm",
    )
    assert rollover.filed_date == date(2023, 11, 3)

    with pytest.raises(ValueError, match="acceptance time"):
        SecFilingObservation(
            cik=CIK,
            accession="0000320193-23-000107",
            form=SecFilingForm.TEN_K,
            report_date=date(2023, 9, 30),
            filed_date=date(2023, 11, 3),
            accepted_at=datetime(2023, 11, 4, 1, tzinfo=UTC),
            primary_document="aapl-20230930-late.htm",
        )


def source_snapshot() -> SecSubmissionSourceSnapshot:
    body = b'{"fixture":"point-in-time"}'
    content_hash = sha256_hex(body)
    return SecSubmissionSourceSnapshot(
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
        source_available_at=AVAILABLE_AT,
        body=body,
        filings=(
            observation(
                "0000320193-24-000001",
                SecFilingForm.TEN_K,
                datetime(2024, 11, 1, 18, 0, tzinfo=UTC),
            ),
            observation(
                "0000320193-24-000002",
                SecFilingForm.TEN_K_AMENDMENT,
                datetime(2024, 11, 10, 18, 0, tzinfo=UTC),
            ),
        ),
    )


def candidate(
    filing: SecFilingObservation,
    source: SecSubmissionSourceSnapshot,
    *,
    relation: SecAmendmentRelationStatus,
    base_accession: str | None,
) -> SecFilingCandidate:
    return SecFilingCandidate(
        cik=filing.cik,
        accession=filing.accession,
        form=filing.form,
        report_date=filing.report_date,
        filed_date=filing.filed_date,
        accepted_at=filing.accepted_at,
        public_available_at=filing.accepted_at,
        primary_document=filing.primary_document,
        amendment_relation_status=relation,
        base_accession=base_accession,
        source_version=source.source_version,
        source_url=source.source_url,
        content_sha256=source.content_sha256,
        source_available_at=source.source_available_at,
    )


def dataset(
    scope: FilingSelectionScope,
    *,
    unresolved_amendment: bool = False,
    source_available_at: datetime = AVAILABLE_AT,
) -> tuple[SecSubmissionSet, SecFilingDataset]:
    source = source_snapshot()
    filings = source.filings
    source_reference = SecSubmissionSourceReference(
        source_kind=source.source_kind,
        source_version=source.source_version,
        source_url=source.source_url,
        content_sha256=source.content_sha256,
        source_available_at=source_available_at,
        retrieved_at=max(source.retrieved_at, source_available_at),
    )
    candidates: tuple[SecFilingCandidate, ...] = (
        candidate(
            filings[0],
            source,
            relation=SecAmendmentRelationStatus.NOT_AMENDMENT,
            base_accession=None,
        ),
        candidate(
            filings[1],
            source,
            relation=(
                SecAmendmentRelationStatus.UNRESOLVED
                if unresolved_amendment
                else SecAmendmentRelationStatus.RESOLVED
            ),
            base_accession=None if unresolved_amendment else filings[0].accession,
        ),
    )
    if source_available_at != source.source_available_at:
        candidates = tuple(
            SecFilingCandidate(
                cik=item.cik,
                accession=item.accession,
                form=item.form,
                report_date=item.report_date,
                filed_date=item.filed_date,
                accepted_at=item.accepted_at,
                public_available_at=item.public_available_at,
                primary_document=item.primary_document,
                amendment_relation_status=item.amendment_relation_status,
                base_accession=item.base_accession,
                source_version=item.source_version,
                source_url=item.source_url,
                content_sha256=item.content_sha256,
                source_available_at=source_available_at,
            )
            for item in candidates
        )
    return (
        SecSubmissionSet(current=source, supplementals=(), required_supplemental_names=()),
        SecFilingDataset(
            coverage_version="sec-filings-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            scope=scope,
            filings=candidates,
            sources=(source_reference,),
        ),
    )


@dataclass(slots=True)
class FrozenSubmissionSource:
    value: SecSubmissionSet

    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet:
        assert scope.cik == self.value.current.cik
        return self.value


@dataclass(slots=True)
class MemorySnapshotStore:
    versions: list[str] = field(default_factory=list)

    async def persist(self, source: SecSubmissionSourceSnapshot) -> str:
        self.versions.append(source.source_version)
        return f"sec/submissions/{source.content_sha256}.json"


@dataclass(slots=True)
class FrozenFilingRepository:
    value: SecFilingDataset
    writes: int = 0

    async def replace_submission_set(
        self,
        snapshot: SecSubmissionSet,
        *,
        object_keys: dict[str, str],
        scope: FilingSelectionScope,
    ) -> str:
        assert set(object_keys) == {source.source_version for source in snapshot.sources}
        assert scope == self.value.scope
        self.writes += 1
        return self.value.coverage_version

    async def load_dataset(
        self,
        *,
        coverage_version: str,
        scope: FilingSelectionScope,
    ) -> SecFilingDataset:
        assert coverage_version == self.value.coverage_version
        assert scope == self.value.scope
        return self.value


def service(
    scope: FilingSelectionScope,
    *,
    unresolved_amendment: bool = False,
    source_available_at: datetime = AVAILABLE_AT,
) -> tuple[SecFilingSelectionService, FrozenFilingRepository, MemorySnapshotStore]:
    submission_set, filing_dataset = dataset(
        scope,
        unresolved_amendment=unresolved_amendment,
        source_available_at=source_available_at,
    )
    repository = FrozenFilingRepository(filing_dataset)
    store = MemorySnapshotStore()
    return (
        SecFilingSelectionService(
            repository=repository,
            source=FrozenSubmissionSource(submission_set),
            snapshot_store=store,
            clock=lambda: NOW,
        ),
        repository,
        store,
    )


@pytest.mark.asyncio
async def test_latest_policy_selects_amendment_after_persisting_source_coverage() -> None:
    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)
    application, repository, store = service(scope)

    result = await application.select(WORKSPACE_SCOPE, selection_scope=scope)

    assert result.status is SecFilingSelectionStatus.OK
    assert [filing.accession for filing in result.filings] == ["0000320193-24-000002"]
    assert repository.writes == 1
    assert store.versions


@pytest.mark.asyncio
async def test_as_filed_policy_retains_base_and_amendment() -> None:
    scope = selection_scope(policy=SecAmendmentPolicy.AS_FILED)
    application, _, _ = service(scope)

    result = await application.select(WORKSPACE_SCOPE, selection_scope=scope)

    assert result.status is SecFilingSelectionStatus.OK
    assert [filing.accession for filing in result.filings] == [
        "0000320193-24-000001",
        "0000320193-24-000002",
    ]


@pytest.mark.asyncio
async def test_unresolved_amendment_never_becomes_no_result() -> None:
    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)
    application, _, _ = service(
        scope,
        unresolved_amendment=True,
    )

    result = await application.select(WORKSPACE_SCOPE, selection_scope=scope)

    assert result.status is SecFilingSelectionStatus.INCOMPLETE
    assert result.error_code == "amendment_relation_unresolved"
    assert result.filings == ()


@pytest.mark.asyncio
async def test_source_version_after_cutoff_never_becomes_no_result() -> None:
    scope = selection_scope(
        policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF,
        as_of=NOW - timedelta(minutes=30),
    )
    application, _, _ = service(scope, source_available_at=NOW)

    result = await application.select(WORKSPACE_SCOPE, selection_scope=scope)

    assert result.status is SecFilingSelectionStatus.INCOMPLETE
    assert result.error_code == "source_version_not_visible_at_as_of"
    assert result.filings == ()


@pytest.mark.asyncio
async def test_complete_coverage_with_no_matching_form_is_no_result() -> None:
    scope = selection_scope(
        policy=SecAmendmentPolicy.AS_FILED,
        forms=(SecFilingForm.TEN_Q,),
    )
    application, _, _ = service(scope)

    result = await application.select(WORKSPACE_SCOPE, selection_scope=scope)

    assert result.status is SecFilingSelectionStatus.NO_RESULT
    assert result.error_code is None
    assert result.filings == ()
