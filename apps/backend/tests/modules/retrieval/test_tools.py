"""Registry-level tests for the local financial Tool surface."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.financial_verification.domain import (
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialPeriodKind,
    FinancialScope,
    sec_xbrl_evidence_ref,
)
from industry_platform.modules.financial_verification.ports import (
    FinancialOperandResolution,
    FinancialOperandResolutionStatus,
)
from industry_platform.modules.financial_verification.tool import FinanceCalculateTool
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.retrieval.domain import (
    KnowledgeSearchHit,
    KnowledgeSearchResult,
    KnowledgeSearchStatus,
    knowledge_evidence_ref,
)
from industry_platform.modules.retrieval.fixtures import load_sec_fixture_catalog
from industry_platform.modules.retrieval.tool import KnowledgeSearchTool
from industry_platform.modules.tools.domain import ToolAction, ToolCall, ToolReference
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolPreparationError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
MANIFEST = REPOSITORY_ROOT / "evals" / "fixtures" / "sec" / "sec-fixture-v1" / "manifest.json"
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
KNOWLEDGE_BASE_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
CHUNK_ID = UUID("66666666-6666-4666-8666-666666666666")
CALL_ID = UUID("77777777-7777-4777-8777-777777777777")
RUN_ID = UUID("88888888-8888-4888-8888-888888888888")
STEP_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = datetime(2023, 11, 3, 12, tzinfo=UTC)
CHUNK_HASH = "a" * 64
FIRST_FACT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_FACT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FIRST_XBRL_EVIDENCE_ID = sec_xbrl_evidence_ref(
    workspace_id=WORKSPACE_ID,
    fact_id=FIRST_FACT_ID,
    as_of=NOW,
    authorization_role="member",
)
SECOND_XBRL_EVIDENCE_ID = sec_xbrl_evidence_ref(
    workspace_id=WORKSPACE_ID,
    fact_id=SECOND_FACT_ID,
    as_of=NOW,
    authorization_role="member",
)


def financial_scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=NOW,
        unit="USD",
        scale=6,
    )


def runtime_context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Financial Research", "member"),),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=12,
            max_total_tokens=4_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
    )


def prepare(
    registry: ToolRegistry,
    action: ToolAction,
    *,
    call_id: UUID = CALL_ID,
) -> ToolCall:
    definition = registry.definition(ToolReference(action.name, action.version))
    assert definition is not None
    return registry.prepare(
        ToolRequestAudit(call_id=call_id, action=action),
        allowed_tools=(definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=runtime_context(),
        requested_at=NOW,
        idempotency_key=None,
    )


@dataclass(slots=True)
class KnowledgeServiceStub:
    result: KnowledgeSearchResult
    received_queries: list[str] = field(default_factory=list)

    async def search(self, scope: WorkspaceScope, **kwargs: object) -> KnowledgeSearchResult:
        assert scope.workspace_id == WORKSPACE_ID
        assert kwargs["knowledge_base_ids"] == (KNOWLEDGE_BASE_ID,)
        assert kwargs["financial_scope"] == financial_scope()
        self.received_queries.append(str(kwargs["query"]))
        return self.result


@dataclass(slots=True)
class OperandRepositoryStub:
    status: KnowledgeSearchStatus = KnowledgeSearchStatus.OK
    received: list[tuple[tuple[UUID, str], ...]] = field(default_factory=list)

    async def validate_operands(
        self,
        scope: WorkspaceScope,
        **kwargs: object,
    ) -> KnowledgeSearchStatus:
        assert scope.workspace_id == WORKSPACE_ID
        values = kwargs["evidence_values"]
        assert isinstance(values, tuple)
        self.received.append(values)
        return self.status


@dataclass(slots=True)
class FormalOperandRepositoryStub:
    resolution: FinancialOperandResolution
    call_count: int = 0

    async def resolve(self, scope: WorkspaceScope, **kwargs: object) -> FinancialOperandResolution:
        assert scope.workspace_id == WORKSPACE_ID
        assert kwargs["knowledge_base_ids"] == (KNOWLEDGE_BASE_ID,)
        assert kwargs["financial_scope"] == financial_scope()
        self.call_count += 1
        return self.resolution


def xbrl_operand(
    *,
    evidence_ref: UUID,
    fact_id: UUID,
    value: str,
    concept: str,
    scale: int,
    dimensions: tuple[tuple[str, str], ...] = (),
) -> FinancialEvidenceOperand:
    return FinancialEvidenceOperand(
        evidence_ref=evidence_ref,
        source_fact_id=fact_id,
        value=value,
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        unit="USD",
        scale=scale,
        period_kind=FinancialPeriodKind.DURATION,
        instant=None,
        start_date=date(2022, 10, 1),
        end_date=date(2023, 9, 30),
        context_id="D2023",
        dimensions=dimensions,
        taxonomy="us-gaap",
        concept=concept,
        is_custom=False,
        source_kind="raw_instance",
        source_version="sec-xbrl-raw-instance-v1",
        source_available_at=NOW,
        amendment_relation_status="not_amendment",
        base_accession=None,
    )


@pytest.mark.asyncio
async def test_knowledge_search_uses_only_query_from_the_model_and_emits_filing_source() -> None:
    fixture = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT).filings[0]
    evidence_ref = knowledge_evidence_ref(
        workspace_id=WORKSPACE_ID,
        accession=fixture.accession,
        document_version_id=VERSION_ID,
        chunk_id=CHUNK_ID,
        content_sha256=CHUNK_HASH,
    )
    service = KnowledgeServiceStub(
        KnowledgeSearchResult(
            status=KnowledgeSearchStatus.OK,
            hits=(
                KnowledgeSearchHit(
                    evidence_ref=evidence_ref,
                    knowledge_base_id=KNOWLEDGE_BASE_ID,
                    document_id=DOCUMENT_ID,
                    document_version_id=VERSION_ID,
                    chunk_id=CHUNK_ID,
                    title="Apple 2023 Form 10-K",
                    excerpt="Total net sales 383285 394328",
                    score=0.95,
                    page_number=29,
                    section="Item 8. Consolidated Statements of Operations",
                    content_sha256=CHUNK_HASH,
                    parser_version="1.0.0",
                    chunker_version="1.0.0",
                    index_version="knowledge-index-v1",
                    fixture=fixture,
                ),
            ),
        )
    )
    tool = KnowledgeSearchTool(service)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(1, "knowledge_search", "v1", {"query": "net sales change"})

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action), runtime_context()
    )

    assert service.received_queries == ["net sales change"]
    assert f"evidence_ref={evidence_ref}" in result.observation.model_text
    assert result.observation.sources[0].locator == (
        f"fixture://sec-filings/{fixture.accession}/{VERSION_ID}/{CHUNK_ID}"
    )

    untrusted_scope = ToolAction(
        1,
        "knowledge_search",
        "v1",
        {"query": "net sales", "cik": fixture.cik},
    )
    with pytest.raises(ToolPreparationError):
        prepare(registry, untrusted_scope)


@pytest.mark.asyncio
async def test_finance_tool_accepts_shared_evidence_and_emits_recomputable_observation() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)
    repository = OperandRepositoryStub()
    tool = FinanceCalculateTool(repository, catalog)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "finance.calculate",
        "v1",
        {
            "operator": "percent_change",
            "operands": [
                {"value": "383285", "evidence_ref": str(CHUNK_ID)},
                {"value": "394328", "evidence_ref": str(CHUNK_ID)},
            ],
            "decimal_places": 2,
            "rounding_mode": "half_even",
        },
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action), runtime_context()
    )

    assert repository.received == [((CHUNK_ID, "383285"), (CHUNK_ID, "394328"))]
    assert '"result":"-2.80"' in result.observation.model_text
    assert '"formula":"((383285 - 394328) / 394328) * 100"' in (result.observation.model_text)
    assert result.observation.sources[0].source_type == "finance_calculation"


@pytest.mark.asyncio
async def test_finance_tool_returns_stable_error_for_invalid_operator_arity() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)
    tool = FinanceCalculateTool(OperandRepositoryStub(), catalog)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "finance.calculate",
        "v1",
        {
            "operator": "ratio",
            "operands": [
                {"value": "383285", "evidence_ref": str(CHUNK_ID)},
                {"value": "394328", "evidence_ref": str(CHUNK_ID)},
                {"value": "1", "evidence_ref": str(CHUNK_ID)},
            ],
            "decimal_places": 2,
            "rounding_mode": "half_even",
        },
    )

    with pytest.raises(ToolExecutionError) as caught:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
            prepare(registry, action), runtime_context()
        )

    assert caught.value.code == "calculation_invalid"


@pytest.mark.asyncio
async def test_finance_tool_calculates_from_authorized_xbrl_evidence_with_reconciliation() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)
    legacy = OperandRepositoryStub()
    formal = FormalOperandRepositoryStub(
        FinancialOperandResolution(
            FinancialOperandResolutionStatus.OK,
            operands=(
                xbrl_operand(
                    evidence_ref=FIRST_XBRL_EVIDENCE_ID,
                    fact_id=FIRST_FACT_ID,
                    value="100",
                    concept="Revenue",
                    scale=6,
                ),
                xbrl_operand(
                    evidence_ref=SECOND_XBRL_EVIDENCE_ID,
                    fact_id=SECOND_FACT_ID,
                    value="50000000",
                    concept="OtherIncome",
                    scale=0,
                ),
            ),
        )
    )
    tool = FinanceCalculateTool(legacy, catalog, formal)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "finance.calculate",
        "v1",
        {
            "operator": "add",
            "operands": [
                {
                    "value": "100",
                    "evidence_ref": str(FIRST_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(FIRST_FACT_ID),
                },
                {
                    "value": "50000000",
                    "evidence_ref": str(SECOND_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(SECOND_FACT_ID),
                },
            ],
            "decimal_places": 2,
            "rounding_mode": "half_even",
        },
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action), runtime_context()
    )

    assert formal.call_count == 1
    assert legacy.received == []
    assert '"operand_source":"sec_xbrl_evidence"' in result.observation.model_text
    assert '"status":"consistent"' in result.observation.model_text
    assert '"result":"150.00"' in result.observation.model_text
    assert '"formula":"100 + 50.000000"' in result.observation.model_text
    assert result.observation.sources[0].locator.startswith("sec://financial-calculations/")


@pytest.mark.asyncio
async def test_finance_tool_returns_typed_not_comparable_without_calculating() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)
    legacy = OperandRepositoryStub()
    formal = FormalOperandRepositoryStub(
        FinancialOperandResolution(
            FinancialOperandResolutionStatus.OK,
            operands=(
                xbrl_operand(
                    evidence_ref=FIRST_XBRL_EVIDENCE_ID,
                    fact_id=FIRST_FACT_ID,
                    value="100",
                    concept="Revenue",
                    scale=6,
                ),
                xbrl_operand(
                    evidence_ref=SECOND_XBRL_EVIDENCE_ID,
                    fact_id=SECOND_FACT_ID,
                    value="90",
                    concept="Revenue",
                    scale=6,
                    dimensions=(("dei:LegalEntityAxis", "aapl:AppleIncMember"),),
                ),
            ),
        )
    )
    tool = FinanceCalculateTool(legacy, catalog, formal)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "finance.calculate",
        "v1",
        {
            "operator": "ratio",
            "operands": [
                {
                    "value": "100",
                    "evidence_ref": str(FIRST_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(FIRST_FACT_ID),
                },
                {
                    "value": "90",
                    "evidence_ref": str(SECOND_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(SECOND_FACT_ID),
                },
            ],
            "decimal_places": 2,
            "rounding_mode": "half_even",
        },
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action), runtime_context()
    )

    assert legacy.received == []
    assert '"status":"no_result"' in result.observation.model_text
    assert '"error_code":"financial_reconciliation_not_comparable"' in (
        result.observation.model_text
    )
    assert result.observation.sources == ()


@pytest.mark.asyncio
async def test_finance_tool_does_not_downgrade_unresolved_formal_operands_to_fixture() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)
    legacy = OperandRepositoryStub()
    formal = FormalOperandRepositoryStub(
        FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
    )
    tool = FinanceCalculateTool(legacy, catalog, formal)  # type: ignore[arg-type]
    registry = ToolRegistry((tool,))
    action = ToolAction(
        1,
        "finance.calculate",
        "v1",
        {
            "operator": "add",
            "operands": [
                {
                    "value": "100",
                    "evidence_ref": str(FIRST_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(FIRST_FACT_ID),
                },
                {
                    "value": "50",
                    "evidence_ref": str(SECOND_XBRL_EVIDENCE_ID),
                    "source_fact_id": str(SECOND_FACT_ID),
                },
            ],
            "decimal_places": 2,
            "rounding_mode": "half_even",
        },
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action), runtime_context()
    )

    assert formal.call_count == 1
    assert legacy.received == []
    assert '"status":"no_result"' in result.observation.model_text
    assert '"error_code":"financial_operand_not_authorized"' in result.observation.model_text
    assert result.observation.sources == ()
