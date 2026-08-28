"""Comparable SEC filing diff and Tool lineage contracts."""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.diff import (
    SecFilingComparisonIdentity,
    SecFilingComparisonPreparation,
    SecFilingDiffRelationship,
    SecFilingDiffService,
    SecFilingDiffStatus,
    resolve_filing_diff_relationship,
)
from industry_platform.modules.disclosures.domain import (
    SecAmendmentRelationStatus,
    SecFilingContentStatus,
    SecFilingForm,
    SecFilingSearchHit,
    SecFilingSearchResult,
    SecXbrlFact,
    SecXbrlFactResult,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingContentService
from industry_platform.modules.disclosures.ports import SecFilingContentRepository
from industry_platform.modules.disclosures.tool import SecDiffFilingsInput, SecDiffFilingsTool
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import sec_xbrl_evidence_ref

from .test_filing_content_tool import (
    ACCESSION,
    KNOWLEDGE_BASE_ID,
    NOW,
    SOURCE_URL,
    WORKSPACE_ID,
    context,
)
from .test_xbrl_service_tool import aggregate_fact

COMPARISON_ACCESSION = "0000320193-22-000108"
COMPARISON_FACT_ID = UUID("30303030-3030-4030-8030-303030303030")
COMPARISON_IMPORT_ID = UUID("40404040-4040-4040-8040-404040404040")
TARGET_IMPORT_ID = UUID("50505050-5050-4050-8050-505050505050")
COMPARISON_CHUNK_ID = UUID("60606060-6060-4060-8060-606060606060")
TARGET_CHUNK_ID = UUID("70707070-7070-4070-8070-707070707070")
LOW_SCORE_CHUNK_ID = UUID("71717171-7171-4171-8171-717171717171")
COMPARISON_VERSION_ID = UUID("80808080-8080-4080-8080-808080808080")
TARGET_VERSION_ID = UUID("90909090-9090-4090-8090-909090909090")
COMPARISON_SNAPSHOT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TARGET_SNAPSHOT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def identity(accession: str) -> SecFilingComparisonIdentity:
    target = accession == ACCESSION
    return SecFilingComparisonIdentity(
        import_id=TARGET_IMPORT_ID if target else COMPARISON_IMPORT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        cik="0000320193",
        accession=accession,
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30) if target else date(2022, 10, 1),
        filed_date=date(2023, 11, 3) if target else date(2022, 10, 28),
        public_available_at=datetime(2023, 11, 3, tzinfo=UTC)
        if target
        else datetime(2022, 10, 28, tzinfo=UTC),
        amendment_relation_status=SecAmendmentRelationStatus.NOT_AMENDMENT,
        base_accession=None,
    )


def fact(accession: str) -> SecXbrlFact:
    target = accession == ACCESSION
    base = aggregate_fact()
    return replace(
        base,
        id=base.id if target else COMPARISON_FACT_ID,
        accession=accession,
        value="120" if target else "100",
        period=SecXbrlPeriod(
            SecXbrlPeriodKind.DURATION,
            start_date=date(2022, 9, 25) if target else date(2021, 9, 26),
            end_date=date(2023, 9, 30) if target else date(2022, 10, 1),
        ),
        filed_date=date(2023, 11, 3) if target else date(2022, 10, 28),
        locator_key=f"aggregate:revenue:{accession}",
    )


def hit(accession: str) -> SecFilingSearchHit:
    target = accession == ACCESSION
    return SecFilingSearchHit(
        chunk_id=TARGET_CHUNK_ID if target else COMPARISON_CHUNK_ID,
        document_version_id=TARGET_VERSION_ID if target else COMPARISON_VERSION_ID,
        snapshot_id=TARGET_SNAPSHOT_ID if target else COMPARISON_SNAPSHOT_ID,
        accession=accession,
        title=f"10-K {accession}",
        excerpt="Net sales increased." if target else "Net sales were stable.",
        score=0.91 if target else 0.89,
        section="Net Sales",
        page_number=12,
        content_sha256=("c" if target else "d") * 64,
        source_content_sha256=("e" if target else "f") * 64,
        source_url=SOURCE_URL,
        source_version=f"sec-filing-{accession}",
    )


@dataclass(slots=True)
class MemoryComparisonRepository:
    comparison_cik: str = "0000320193"

    async def prepare_comparison_identity(
        self,
        _scope: object,
        **values: object,
    ) -> SecFilingComparisonPreparation:
        accession = str(values["accession"])
        selected = identity(accession)
        if accession == COMPARISON_ACCESSION and self.comparison_cik != selected.cik:
            selected = replace(selected, cik=self.comparison_cik)
        return SecFilingComparisonPreparation(
            status=SecFilingDiffStatus.OK,
            accession=accession,
            identity=selected,
        )


@dataclass(slots=True)
class MemoryDiffXbrlService:
    async def get_imported_facts(self, _scope: object, **values: object) -> SecXbrlFactResult:
        accession = str(values["accession"])
        return SecXbrlFactResult(
            status=SecFilingContentStatus.OK,
            accession=accession,
            facts=(fact(accession),),
        )


@dataclass(slots=True)
class MemoryDiffContentService:
    async def search_imported(self, _scope: object, **values: object) -> SecFilingSearchResult:
        accession = str(values["accession"])
        best_hit = hit(accession)
        return SecFilingSearchResult(
            status=SecFilingContentStatus.OK,
            accession=accession,
            hits=(
                best_hit,
                replace(
                    best_hit,
                    chunk_id=LOW_SCORE_CHUNK_ID,
                    excerpt="Lower-ranked duplicate section.",
                    score=0.01,
                ),
            ),
        )


def service(repository: MemoryComparisonRepository | None = None) -> SecFilingDiffService:
    return SecFilingDiffService(
        repository=cast(SecFilingContentRepository, repository or MemoryComparisonRepository()),
        content_service=cast(SecFilingContentService, MemoryDiffContentService()),
        xbrl_service=cast(SecXbrlService, MemoryDiffXbrlService()),
        clock=lambda: NOW,
    )


def test_resolved_amendment_is_ordered_after_its_base_filing() -> None:
    base = identity(ACCESSION)
    amendment = replace(
        base,
        accession="0000320193-23-000120",
        form=SecFilingForm.TEN_K_AMENDMENT,
        filed_date=date(2023, 11, 10),
        public_available_at=datetime(2023, 11, 10, tzinfo=UTC),
        amendment_relation_status=SecAmendmentRelationStatus.RESOLVED,
        base_accession=ACCESSION,
    )

    assert resolve_filing_diff_relationship(amendment, base) == (
        SecFilingDiffRelationship.BASE_AMENDMENT,
        base,
        amendment,
    )


@pytest.mark.asyncio
async def test_adjacent_period_diff_preserves_fact_and_section_lineage() -> None:
    locked_scope = context().financial_scope
    assert locked_scope is not None

    result = await service().compare(
        context().workspace_scope,
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=locked_scope,
        comparison_accession=COMPARISON_ACCESSION,
        section_query="net sales",
    )

    assert result.status is SecFilingDiffStatus.OK
    assert result.relationship is SecFilingDiffRelationship.ADJACENT_PERIOD
    assert result.baseline is not None
    assert result.baseline.accession == COMPARISON_ACCESSION
    assert result.target is not None
    assert result.target.accession == ACCESSION
    assert result.fact_changes[0].baseline == fact(COMPARISON_ACCESSION)
    assert result.fact_changes[0].target == fact(ACCESSION)
    assert result.section_change is not None
    assert result.section_change.baseline.chunk_id == COMPARISON_CHUNK_ID
    assert result.section_change.target.chunk_id == TARGET_CHUNK_ID


@pytest.mark.asyncio
async def test_diff_fails_closed_for_a_different_filer() -> None:
    locked_scope = context().financial_scope
    assert locked_scope is not None

    result = await service(MemoryComparisonRepository(comparison_cik="0000789019")).compare(
        context().workspace_scope,
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=locked_scope,
        comparison_accession=COMPARISON_ACCESSION,
        section_query="net sales",
    )

    assert result.status is SecFilingDiffStatus.NOT_COMPARABLE
    assert result.error_code == "filing_scope_not_comparable"
    assert result.fact_changes == ()
    assert result.section_change is None


@pytest.mark.asyncio
async def test_diff_tool_returns_calculator_refs_and_underlying_sources() -> None:
    tool = SecDiffFilingsTool(service())

    output, cost = await tool.invoke(
        SecDiffFilingsInput(
            comparison_accession=COMPARISON_ACCESSION,
            section_query="net sales",
        ),
        context(),
        idempotency_key=None,
    )
    observation = tool.normalize(
        output,
        context(),
        call_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        observed_at=NOW,
    )

    assert cost == 0
    assert {item.fact_id for item in output.fact_evidence_refs} == {
        fact(COMPARISON_ACCESSION).id,
        fact(ACCESSION).id,
    }
    assert output.fact_evidence_refs[0].evidence_ref == sec_xbrl_evidence_ref(
        workspace_id=WORKSPACE_ID,
        fact_id=output.fact_evidence_refs[0].fact_id,
        as_of=NOW,
        authorization_role="member",
    )
    assert {source.source_type for source in observation.sources} == {
        "sec_xbrl_fact",
        "sec_filing_text",
    }
    assert len(observation.sources) == 4
