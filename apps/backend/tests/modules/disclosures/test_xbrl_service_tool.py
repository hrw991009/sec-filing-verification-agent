"""XBRL service and Tool contracts for one server-locked SEC filing."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingSnapshotReference,
    SecXbrlDataset,
    SecXbrlFact,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    SecXbrlSyncPreparation,
    SecXbrlSyncResult,
    sec_companyfacts_url,
    sec_xbrl_fact_content_sha256,
    sec_xbrl_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.ports import SecFilingContentRepository
from industry_platform.modules.disclosures.tool import SecGetXbrlFactsTool
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import (
    FinancialScope,
    sec_xbrl_evidence_ref,
)
from industry_platform.modules.tools.domain import ToolAction
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceScope

from .test_filing_content_service import (
    ACCESSION,
    KNOWLEDGE_BASE_ID,
    NOW,
    PRIMARY_SNAPSHOT_ID,
    WORKSPACE_ID,
    canonical_filing,
    snapshot_references,
    workspace_import,
)
from .test_filing_content_tool import context, prepare

SOURCE_ID = UUID("10101010-1010-4010-8010-101010101010")
FACT_ID = UUID("20202020-2020-4020-8020-202020202020")


def aggregate_source() -> SecXbrlSourceSnapshot:
    body = (
        b'{"cik":320193,"facts":{"us-gaap":{"Revenue":{"units":{"USD":['
        b'{"accn":"0000320193-23-000106","form":"10-K","filed":"2023-11-03",'
        b'"start":"2022-09-25","end":"2023-09-30","val":100}]}}}}}'
    )
    digest = sha256_hex(body)
    return SecXbrlSourceSnapshot(
        source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
        cik="0000320193",
        source_url=sec_companyfacts_url("0000320193"),
        source_version=sec_xbrl_source_version(
            SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
            digest,
        ),
        content_type="application/json",
        content_sha256=digest,
        byte_size=len(body),
        retrieved_at=NOW,
        source_available_at=datetime(2023, 11, 3, 6, 1, tzinfo=UTC),
        body=body,
    )


def raw_source() -> SecXbrlSourceSnapshot:
    body = b"<html></html>"
    digest = sha256_hex(body)
    reference = next(
        item for item in snapshot_references() if item.snapshot_id == PRIMARY_SNAPSHOT_ID
    )
    return SecXbrlSourceSnapshot(
        source_kind=SecXbrlSourceKind.RAW_INLINE,
        cik="0000320193",
        source_url=reference.source_url,
        source_version=sec_xbrl_source_version(SecXbrlSourceKind.RAW_INLINE, digest),
        content_type="text/html",
        content_sha256=digest,
        byte_size=len(body),
        retrieved_at=NOW,
        source_available_at=datetime(2023, 11, 3, 6, 1, tzinfo=UTC),
        body=body,
        filing_snapshot_id=reference.snapshot_id,
    )


def aggregate_fact() -> SecXbrlFact:
    source = aggregate_source()
    return SecXbrlFact(
        id=FACT_ID,
        filing_id=canonical_filing().id,
        source_id=SOURCE_ID,
        source_snapshot_id=None,
        source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
        cik="0000320193",
        accession=ACCESSION,
        taxonomy="us-gaap",
        concept="Revenue",
        value="100",
        unit="USD",
        period=SecXbrlPeriod(
            SecXbrlPeriodKind.DURATION,
            start_date=date(2022, 9, 25),
            end_date=date(2023, 9, 30),
        ),
        filed_date=date(2023, 11, 3),
        form=canonical_filing().form,
        context_id=None,
        dimensions=(),
        decimals=None,
        scale=None,
        format=None,
        is_custom=False,
        ordinal=0,
        locator_key="aggregate:revenue",
        source_url=source.source_url,
        source_version=source.source_version,
        source_content_sha256=source.content_sha256,
        source_available_at=source.source_available_at,
        retrieved_at=source.retrieved_at,
        unavailable_fields=("context_id", "decimals", "dimensions", "scale"),
    )


@dataclass(slots=True)
class MemoryXbrlRepository:
    query_result: SecXbrlFactResult = field(
        default_factory=lambda: SecXbrlFactResult(
            SecFilingContentStatus.OK,
            ACCESSION,
            (aggregate_fact(),),
        )
    )
    persisted: SecXbrlDataset | None = None

    async def prepare_sync(self, scope: WorkspaceScope, **values: object) -> SecXbrlSyncPreparation:
        assert scope.workspace_id == WORKSPACE_ID
        assert values == {"accession": ACCESSION, "knowledge_base_id": KNOWLEDGE_BASE_ID}
        return SecXbrlSyncPreparation(
            filing=canonical_filing(),
            import_record=workspace_import(),
            raw_sources=tuple(
                item for item in snapshot_references() if item.snapshot_id == PRIMARY_SNAPSHOT_ID
            ),
        )

    async def persist_dataset(
        self,
        dataset: SecXbrlDataset,
        *,
        aggregate_object_keys: dict[str, str],
    ) -> SecXbrlSyncResult:
        assert aggregate_object_keys == {aggregate_source().source_url: "xbrl/aggregate.json"}
        self.persisted = dataset
        return SecXbrlSyncResult(
            accession=ACCESSION,
            source_count=2,
            context_count=0,
            fact_count=1,
            source_versions=tuple(batch.source.source_version for batch in dataset.batches),
        )

    async def query_facts(self, scope: WorkspaceScope, **values: object) -> SecXbrlFactResult:
        assert scope.workspace_id == WORKSPACE_ID
        assert values["accession"] == ACCESSION
        return self.query_result


@dataclass(slots=True)
class MemoryFilingRepository:
    filing: SecCanonicalFiling = field(default_factory=canonical_filing)

    async def get_canonical_filing(self, accession: str) -> SecCanonicalFiling:
        assert accession == ACCESSION
        return self.filing


@dataclass(slots=True)
class MemoryCompanyFactsSource:
    async def fetch(self, filing: SecCanonicalFiling) -> SecXbrlSourceSnapshot:
        assert filing == canonical_filing()
        return aggregate_source()


@dataclass(slots=True)
class MemoryXbrlSnapshotStore:
    async def persist_aggregate(self, source: SecXbrlSourceSnapshot) -> str:
        assert source == aggregate_source()
        return "xbrl/aggregate.json"

    async def read_raw(
        self,
        source: SecFilingSnapshotReference,
        *,
        cik: str,
    ) -> SecXbrlSourceSnapshot:
        assert cik == "0000320193"
        assert source is not None
        return raw_source()


def service(repository: MemoryXbrlRepository | None = None) -> SecXbrlService:
    return SecXbrlService(
        repository=repository or MemoryXbrlRepository(),
        filing_repository=cast(SecFilingContentRepository, MemoryFilingRepository()),
        companyfacts_source=MemoryCompanyFactsSource(),
        snapshot_store=MemoryXbrlSnapshotStore(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_sync_combines_immutable_aggregate_and_raw_sources() -> None:
    repository = MemoryXbrlRepository()

    result = await service(repository).sync(
        WorkspaceScope(WORKSPACE_ID, context().workspace_scope.user_id, "member"),
        accession=ACCESSION,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
    )

    assert result.fact_count == 1
    assert repository.persisted is not None
    assert tuple(batch.source.source_kind for batch in repository.persisted.batches) == (
        SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
        SecXbrlSourceKind.RAW_INLINE,
    )


@pytest.mark.asyncio
async def test_service_rejects_future_cutoff_and_mismatched_financial_identity() -> None:
    with pytest.raises(SecFilingContentError):
        await service().get_imported_facts(
            context().workspace_scope,
            knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
            accession=ACCESSION,
            as_of=datetime(2026, 8, 26, 4, 0, 1, tzinfo=UTC),
            query=SecXbrlFactQuery(),
        )

    locked = context().financial_scope
    assert locked is not None
    mismatched = FinancialScope(
        cik="0000789019",
        accession=locked.accession,
        form=locked.form,
        report_period=locked.report_period,
        as_of=locked.as_of,
        unit=locked.unit,
        scale=locked.scale,
    )
    result = await service().get_facts(
        context().workspace_scope,
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=mismatched,
        query=SecXbrlFactQuery(),
    )

    assert result.status is SecFilingContentStatus.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_xbrl_tool_uses_trusted_scope_and_emits_typed_source_lineage() -> None:
    tool = SecGetXbrlFactsTool(service())
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "sec.get_xbrl_facts",
        "v1",
        {"taxonomy": "us-gaap", "concept": "Revenue", "limit": 5},
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action),
        context(),
    )

    evidence_ref = sec_xbrl_evidence_ref(
        workspace_id=WORKSPACE_ID,
        fact_id=FACT_ID,
        as_of=NOW,
        authorization_role="member",
    )
    assert '"source_kind":"companyfacts_aggregate"' in result.observation.model_text
    assert f'"evidence_ref":"{evidence_ref}"' in result.observation.model_text
    assert result.observation.sources[0].source_type == "sec_xbrl_fact"
    assert result.observation.sources[0].locator == f"sec://xbrl-facts/{FACT_ID}"
    assert result.observation.sources[0].content_sha256 == sec_xbrl_fact_content_sha256(
        aggregate_fact()
    )
