"""Agent Tool contracts for server-locked imported SEC filing content."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.disclosures.domain import (
    SecFilingContentStatus,
    SecFilingSearchHit,
    SecFilingSearchResult,
    SecFilingSection,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingContentService
from industry_platform.modules.disclosures.tool import (
    SecReadFilingSectionTool,
    SecSearchFilingTool,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.tools.domain import ToolAction, ToolCall, ToolReference
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolPreparationError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
KNOWLEDGE_BASE_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
STEP_ID = UUID("55555555-5555-4555-8555-555555555555")
CALL_ID = UUID("66666666-6666-4666-8666-666666666666")
VERSION_ID = UUID("77777777-7777-4777-8777-777777777777")
CHUNK_ID = UUID("88888888-8888-4888-8888-888888888888")
SNAPSHOT_ID = UUID("99999999-9999-4999-8999-999999999999")
IMPORT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ACCESSION = "0000320193-23-000106"
SOURCE_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"


def context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "SEC Research", "member"),),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=8,
            max_total_tokens=4_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=FinancialScope(
            cik="0000320193",
            accession=ACCESSION,
            form=FinancialForm.TEN_K,
            report_period=date(2023, 9, 30),
            as_of=NOW,
            unit="USD",
            scale=0,
        ),
    )


def search_hit() -> SecFilingSearchHit:
    return SecFilingSearchHit(
        chunk_id=CHUNK_ID,
        document_version_id=VERSION_ID,
        snapshot_id=SNAPSHOT_ID,
        accession=ACCESSION,
        title="10-K filing",
        excerpt="Net sales increased.",
        score=0.91,
        section="Net sales",
        page_number=1,
        content_sha256="a" * 64,
        source_content_sha256="b" * 64,
        source_url=SOURCE_URL,
        source_version="sec-filing-primary-v1",
    )


@dataclass(slots=True)
class MemoryContentService:
    search_calls: int = 0
    read_calls: int = 0

    async def search(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        query: str,
    ) -> SecFilingSearchResult:
        assert scope.workspace_id == WORKSPACE_ID
        assert knowledge_base_ids == (KNOWLEDGE_BASE_ID,)
        assert financial_scope.accession == ACCESSION
        assert query == "net sales"
        self.search_calls += 1
        return SecFilingSearchResult(
            status=SecFilingContentStatus.OK,
            accession=ACCESSION,
            hits=(search_hit(),),
        )

    async def read_section(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        document_version_id: UUID,
        chunk_id: UUID,
    ) -> SecFilingSection:
        assert scope.workspace_id == WORKSPACE_ID
        assert knowledge_base_ids == (KNOWLEDGE_BASE_ID,)
        assert financial_scope.accession == ACCESSION
        assert document_version_id == VERSION_ID
        assert chunk_id == CHUNK_ID
        self.read_calls += 1
        hit = search_hit()
        return SecFilingSection(
            import_id=IMPORT_ID,
            snapshot_id=SNAPSHOT_ID,
            accession=ACCESSION,
            document_version_id=VERSION_ID,
            chunk_id=CHUNK_ID,
            title=hit.title,
            section=hit.section,
            text=hit.excerpt,
            page_number=hit.page_number,
            content_sha256=hit.content_sha256,
            source_content_sha256=hit.source_content_sha256,
            source_url=hit.source_url,
            source_version=hit.source_version,
        )


def prepare(registry: ToolRegistry, action: ToolAction) -> ToolCall:
    definition = registry.definition(ToolReference(action.name, action.version))
    assert definition is not None
    return registry.prepare(
        ToolRequestAudit(call_id=CALL_ID, action=action),
        allowed_tools=(definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=context(),
        requested_at=NOW,
    )


@pytest.mark.asyncio
async def test_search_tool_uses_trusted_scope_and_emits_snapshot_lineage() -> None:
    service = MemoryContentService()
    registry = ToolRegistry((SecSearchFilingTool(cast(SecFilingContentService, service)),))
    action = ToolAction(1, "sec.search_filing", "v1", {"query": "net sales"})

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action),
        context(),
    )

    assert service.search_calls == 1
    assert '"retrieval_profile_version":"dense-v1"' in result.observation.model_text
    assert result.observation.sources[0].source_type == "sec_filing_text"
    assert result.observation.sources[0].locator == f"sec://filing-chunks/{CHUNK_ID}"
    assert result.observation.sources[0].content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_read_tool_returns_only_the_authorized_chunk() -> None:
    service = MemoryContentService()
    registry = ToolRegistry((SecReadFilingSectionTool(cast(SecFilingContentService, service)),))
    action = ToolAction(
        1,
        "sec.read_filing_section",
        "v1",
        {"document_version_id": str(VERSION_ID), "chunk_id": str(CHUNK_ID)},
    )

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        prepare(registry, action),
        context(),
    )

    assert service.read_calls == 1
    assert '"text":"Net sales increased."' in result.observation.model_text
    assert result.observation.sources[0].locator == f"sec://filing-chunks/{CHUNK_ID}"


def test_model_cannot_override_the_server_locked_accession() -> None:
    registry = ToolRegistry(
        (SecSearchFilingTool(cast(SecFilingContentService, MemoryContentService())),)
    )
    action = ToolAction(
        1,
        "sec.search_filing",
        "v1",
        {"query": "net sales", "accession": "0000789019-23-000001"},
    )

    with pytest.raises(ToolPreparationError) as caught:
        prepare(registry, action)

    assert caught.value.code == "tool_arguments_invalid"
