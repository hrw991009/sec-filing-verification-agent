"""Deterministic SEC Monitor analysis and fail-closed watermark contracts."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.diff import (
    SecFilingChangeKind,
    SecFilingComparisonIdentity,
    SecFilingDiffRelationship,
    SecFilingDiffResult,
    SecFilingDiffService,
    SecFilingDiffStatus,
    SecFilingSectionChange,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFilingCandidate,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSearchHit,
    SecFilingSelection,
    SecFilingSelectionStatus,
    SecSubmissionSourceKind,
    SecSubmissionSourceReference,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingImportService
from industry_platform.modules.disclosures.monitor import (
    SEC_MONITOR_RULE_SET_VERSION,
    SecMonitorAnalysisService,
    SecMonitorDependencyError,
    SecMonitorExecutionRequest,
    SecMonitorRule,
    SecMonitorRuleKind,
    SecMonitorStateError,
    SecMonitorWatermark,
)
from industry_platform.modules.disclosures.service import SecFilingSelectionService
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OWNER_ID = UUID("22222222-2222-4222-8222-222222222222")
MONITOR_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
JOB_ID = UUID("55555555-5555-4555-8555-555555555555")
KNOWLEDGE_BASE_ID = UUID("66666666-6666-4666-8666-666666666666")
WATERMARK_ID = UUID("77777777-7777-4777-8777-777777777777")
RULE_ID = UUID("88888888-8888-4888-8888-888888888888")
BASE_ACCESSION = "0000320193-23-000001"
TARGET_ACCESSION = "0000320193-23-000002"
NOW = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/320193/filing.htm"


def candidate(*, amendment: bool) -> SecFilingCandidate:
    accession = TARGET_ACCESSION if amendment else BASE_ACCESSION
    accepted_at = datetime(2023, 11, 10 if amendment else 3, 18, 0, tzinfo=UTC)
    return SecFilingCandidate(
        cik="0000320193",
        accession=accession,
        form=SecFilingForm.TEN_K_AMENDMENT if amendment else SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=accepted_at.date(),
        accepted_at=accepted_at,
        public_available_at=accepted_at,
        primary_document=f"{accession}.htm",
        amendment_relation_status=(
            SecAmendmentRelationStatus.RESOLVED
            if amendment
            else SecAmendmentRelationStatus.NOT_AMENDMENT
        ),
        base_accession=BASE_ACCESSION if amendment else None,
        source_version=f"sec-filing-{accession}",
        source_url=SUBMISSIONS_URL,
        content_sha256=("b" if amendment else "a") * 64,
        source_available_at=accepted_at,
    )


def identity(*, amendment: bool) -> SecFilingComparisonIdentity:
    filing = candidate(amendment=amendment)
    return SecFilingComparisonIdentity(
        import_id=UUID("99999999-9999-4999-8999-999999999999")
        if amendment
        else UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        cik=filing.cik,
        accession=filing.accession,
        form=filing.form,
        report_date=filing.report_date,
        filed_date=filing.filed_date,
        public_available_at=filing.public_available_at,
        amendment_relation_status=filing.amendment_relation_status,
        base_accession=filing.base_accession,
    )


def hit(*, amendment: bool) -> SecFilingSearchHit:
    accession = TARGET_ACCESSION if amendment else BASE_ACCESSION
    return SecFilingSearchHit(
        chunk_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        if amendment
        else UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        document_version_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        if amendment
        else UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        snapshot_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
        if amendment
        else UUID("12121212-1212-4212-8212-121212121212"),
        accession=accession,
        title=f"10-K {accession}",
        excerpt="Updated risk disclosure." if amendment else "Original risk disclosure.",
        score=0.9,
        section="Risk Factors",
        page_number=8,
        content_sha256=("d" if amendment else "c") * 64,
        source_content_sha256=("f" if amendment else "e") * 64,
        source_url=FILING_URL,
        source_version=f"sec-filing-{accession}",
    )


def selection(status: SecFilingSelectionStatus) -> SecFilingSelection:
    scope = FilingSelectionScope(
        cik="0000320193",
        allowed_forms=(SecFilingForm.TEN_K, SecFilingForm.TEN_K_AMENDMENT),
        report_period_start=NOW.date() - timedelta(days=3_660),
        report_period_end=NOW.date(),
        as_of=NOW,
        amendment_policy=SecAmendmentPolicy.AS_FILED,
    )
    source = SecSubmissionSourceReference(
        source_kind=SecSubmissionSourceKind.CURRENT,
        source_version="sec-submissions-current-" + "a" * 64,
        source_url=SUBMISSIONS_URL,
        content_sha256="a" * 64,
        source_available_at=NOW - timedelta(hours=1),
        retrieved_at=NOW,
    )
    return SecFilingSelection(
        status=status,
        scope=scope,
        filings=(candidate(amendment=False), candidate(amendment=True))
        if status is SecFilingSelectionStatus.OK
        else (),
        coverage_version="sec-filings-" + "a" * 32,
        sources=(source,),
        error_code="sec_coverage_incomplete"
        if status is SecFilingSelectionStatus.INCOMPLETE
        else None,
    )


def request() -> SecMonitorExecutionRequest:
    return SecMonitorExecutionRequest(
        run_id=RUN_ID,
        job_id=JOB_ID,
        monitor_id=MONITOR_ID,
        scope=WorkspaceScope(WORKSPACE_ID, OWNER_ID, "owner"),
        owner_user_id=OWNER_ID,
        cik="0000320193",
        allowed_forms=(SecFilingForm.TEN_K, SecFilingForm.TEN_K_AMENDMENT),
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        rules=(
            SecMonitorRule(
                rule_id=RULE_ID,
                kind=SecMonitorRuleKind.AMENDMENT,
                rule_version=SEC_MONITOR_RULE_SET_VERSION,
                section_query="risk factors",
            ),
        ),
        watermark=SecMonitorWatermark(
            watermark_id=WATERMARK_ID,
            revision=1,
            coverage_version="sec-filings-" + "0" * 32,
            accepted_at=candidate(amendment=False).accepted_at,
            accession=BASE_ACCESSION,
        ),
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        trace_id=TraceId("sec-monitor-unit"),
    )


@dataclass(slots=True)
class FrozenSelection:
    result: SecFilingSelection
    calls: int = 0

    async def select(self, *_args: object, **_kwargs: object) -> SecFilingSelection:
        self.calls += 1
        return self.result


@dataclass(slots=True)
class RecordingImports:
    accessions: list[str] = field(default_factory=list)

    async def import_filing(self, _scope: object, *, accession: str, **_kwargs: object) -> object:
        self.accessions.append(accession)
        return SimpleNamespace(status=SecFilingImportStatus.READY)


@dataclass(slots=True)
class RecordingXbrl:
    accessions: list[str] = field(default_factory=list)

    async def sync(self, _scope: object, *, accession: str, **_kwargs: object) -> None:
        self.accessions.append(accession)


@dataclass(slots=True)
class FrozenDiff:
    calls: int = 0

    async def compare(self, *_args: object, **_kwargs: object) -> SecFilingDiffResult:
        self.calls += 1
        return SecFilingDiffResult(
            status=SecFilingDiffStatus.OK,
            requested_accession=TARGET_ACCESSION,
            comparison_accession=BASE_ACCESSION,
            relationship=SecFilingDiffRelationship.BASE_AMENDMENT,
            baseline=identity(amendment=False),
            target=identity(amendment=True),
            section_change=SecFilingSectionChange(
                section="Risk Factors",
                change_kind=SecFilingChangeKind.CHANGED,
                baseline=hit(amendment=False),
                target=hit(amendment=True),
            ),
        )


def service(
    selected: SecFilingSelection,
) -> tuple[SecMonitorAnalysisService, RecordingImports, RecordingXbrl, FrozenDiff]:
    imports = RecordingImports()
    xbrl = RecordingXbrl()
    diff = FrozenDiff()
    return (
        SecMonitorAnalysisService(
            selection=cast(SecFilingSelectionService, FrozenSelection(selected)),
            imports=cast(SecFilingImportService, imports),
            xbrl=cast(SecXbrlService, xbrl),
            diff=cast(SecFilingDiffService, diff),
        ),
        imports,
        xbrl,
        diff,
    )


@pytest.mark.asyncio
async def test_complete_amendment_advances_cursor_and_preserves_both_evidence_sides() -> None:
    analyzer, imports, xbrl, diff = service(selection(SecFilingSelectionStatus.OK))

    result = await analyzer.analyze(request())

    assert result.accepted_at == candidate(amendment=True).accepted_at
    assert result.accession == TARGET_ACCESSION
    assert len(result.findings) == 1
    evidence = result.findings[0].evidence
    assert evidence.baseline_text == hit(amendment=False)
    assert evidence.target_text == hit(amendment=True)
    assert imports.accessions == [BASE_ACCESSION, TARGET_ACCESSION]
    assert xbrl.accessions == [BASE_ACCESSION, TARGET_ACCESSION]
    assert diff.calls == 1


@pytest.mark.asyncio
async def test_incomplete_coverage_is_retryable_before_import_or_diff() -> None:
    analyzer, imports, xbrl, diff = service(selection(SecFilingSelectionStatus.INCOMPLETE))

    with pytest.raises(SecMonitorDependencyError) as captured:
        await analyzer.analyze(request())

    assert captured.value.code == "sec_coverage_incomplete"
    assert imports.accessions == []
    assert xbrl.accessions == []
    assert diff.calls == 0


@pytest.mark.asyncio
async def test_missing_baseline_is_permanent_and_cannot_produce_a_watermark() -> None:
    selected = selection(SecFilingSelectionStatus.OK)
    selected = SecFilingSelection(
        status=selected.status,
        scope=selected.scope,
        filings=(candidate(amendment=True),),
        coverage_version=selected.coverage_version,
        sources=selected.sources,
    )
    analyzer, imports, xbrl, diff = service(selected)

    with pytest.raises(SecMonitorStateError) as captured:
        await analyzer.analyze(request())

    assert captured.value.code == "monitor_baseline_missing"
    assert imports.accessions == []
    assert xbrl.accessions == []
    assert diff.calls == 0
