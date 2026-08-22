"""Prove a Web conversation reaches the production L2 loader and real Tool adapter."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.execution import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyAgentRunTerminalizer,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.adapters.trace_query import SqlAlchemyAgentTraceQuery
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    ContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentStepKind,
    RunBudget,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.execution import DirectAnswerRunExecutionService
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime, UnifiedAgentRuntime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L2_RUNTIME_VERSION,
    ToolL2RuntimePolicy,
)
from industry_platform.modules.conversations.adapters.management import (
    SqlAlchemyConversationManagementRepository,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn, TurnSearchMode
from industry_platform.modules.conversations.management import ConversationManagementService
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.evidence.adapters.sqlalchemy import SqlAlchemyEvidenceRepository
from industry_platform.modules.evidence.domain import (
    ClaimEvidenceInput,
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    CreateClaim,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceStatus,
    InvalidateEvidence,
    NormalizeObservation,
    RelationStatus,
)
from industry_platform.modules.evidence.models import (
    ClaimEvidenceRecord,
    EvidenceNormalizationDecisionRecord,
    EvidenceRecord,
    GraphEdgeRecord,
    ResearchClaimRecord,
)
from industry_platform.modules.evidence.normalizer import parse_persisted_observation
from industry_platform.modules.evidence.service import EvidenceApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.industry.domain import (
    SMART_TRANSPORT_INDUSTRY_ID,
    ProviderCode,
    ProviderItem,
    ProviderPage,
    ProviderQuery,
    ProviderReadiness,
    ProviderStatus,
    SourceKind,
    provider_for_kind,
)
from industry_platform.modules.industry.models import DataSourceRecord, SourceItemRecord
from industry_platform.modules.industry.tool import IndustryWebSearchTool
from industry_platform.modules.research.adapters.sqlalchemy import (
    SqlAlchemyResearchQueryRepository,
)
from industry_platform.modules.research.domain import (
    RESEARCH_NODE_ORDER,
    ResearchBriefInput,
    ResearchDraftStatus,
    ResearchRunStatus,
)
from industry_platform.modules.research.models import (
    ResearchDraftRecord,
    ResearchPlanRecord,
    ResearchRunRecord,
)
from industry_platform.modules.research.service import (
    ResearchSubmissionService,
    StartResearch,
)
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop
from industry_platform.workflows.research.runtime import ResearchL3Runtime

from .postgres import PostgresProbe


@dataclass(frozen=True, slots=True)
class FrozenNewsProvider:
    now: datetime

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            ProviderCode.WORLD_BANK_NEWS,
            SourceKind.NEWS,
            ProviderReadiness.READY,
            None,
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        assert query.industry.industry_id == SMART_TRANSPORT_INDUSTRY_ID
        assert query.query == "transport policy"
        return ProviderPage(
            definition=provider_for_kind(SourceKind.NEWS),
            items=(
                ProviderItem(
                    kind=SourceKind.NEWS,
                    provider=ProviderCode.WORLD_BANK_NEWS,
                    external_id="day3-production-web-1",
                    title="Public transport transition",
                    summary="A frozen official-source contract fixture.",
                    locator="https://www.worldbank.org/en/news/transport-transition",
                    published_at=self.now,
                    metadata={"category": "Feature Story"},
                ),
                ProviderItem(
                    kind=SourceKind.NEWS,
                    provider=ProviderCode.WORLD_BANK_NEWS,
                    external_id="day3-production-web-2",
                    title="Uncollected transport source",
                    summary="This source deliberately lacks an immutable local snapshot.",
                    locator="https://www.worldbank.org/en/news/uncollected-transport-source",
                    published_at=self.now,
                    metadata={"category": "Feature Story"},
                ),
            ),
            next_cursor=None,
            fetched_at=self.now,
        )


@dataclass(frozen=True, slots=True)
class FrozenProviderRegistry:
    provider_adapter: FrozenNewsProvider

    def provider(self, kind: SourceKind) -> FrozenNewsProvider:
        assert kind is SourceKind.NEWS
        return self.provider_adapter

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return (self.provider_adapter.status,)


@dataclass(slots=True)
class ScriptedModelProvider:
    responses: list[ModelResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise AssertionError(f"Web L2 must use structured complete calls: {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Web L2 model script exhausted")
        return self.responses.pop(0)


@dataclass(slots=True)
class IncrementingClock:
    value: datetime

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


def model_response(text: str, request_id: str) -> ModelResponse:
    return ModelResponse(
        schema_version=1,
        model="openai-compatible/frozen-web-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=20,
            output_tokens=10,
            cached_input_tokens=0,
            cost_micro_usd=40,
            pricing_version="frozen-pricing-v1",
        ),
        output_text=text,
        provider_request_id=request_id,
    )


def direct_policy() -> DirectAnswerRuntimePolicy:
    return DirectAnswerRuntimePolicy(
        schema_version=1,
        profile_version="direct-answer-v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/frozen-web-model",
        max_input_tokens=2_048,
        max_output_tokens=512,
        system_instructions="Answer directly without claiming Tool use.",
    )


def test_web_turn_executes_through_production_loader_unified_runtime_and_trace(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        now = datetime.now(UTC)
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        tool = IndustryWebSearchTool(FrozenProviderRegistry(FrozenNewsProvider(now)))
        tool_policy = ToolL2RuntimePolicy(
            schema_version=1,
            profile_version="conversation-web-l2-v1",
            prompt_version="conversation-web-l2-prompt-v1",
            context_compiler_version="context-v1",
            output_contract_version="final-markdown-v1",
            toolset_version="conversation-web-toolset-v1",
            model="openai-compatible/frozen-web-model",
            max_input_tokens=4_096,
            max_decision_output_tokens=768,
            max_tool_calls=2,
            system_instructions="Use only the exact public-source Tool catalog.",
            available_tools=(tool.definition.reference,),
        )
        provider = ScriptedModelProvider(
            [
                model_response(
                    '{"decision":{"schema_version":1,"kind":"tool_call",'
                    '"name":"industry.web_search","version":"v1","arguments":{'
                    '"industry_code":"smart_transport","source_kind":"news",'
                    '"query":"transport policy","limit":2}}}',
                    "production-web-action",
                ),
                model_response(
                    '{"decision":{"schema_version":1,"kind":"final",'
                    '"content_markdown":"## Transport brief\\n\\nPublic transition update [S1]."}}',
                    "production-web-final",
                ),
            ]
        )
        clock = IncrementingClock(now + timedelta(seconds=1))
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"production-web-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=now,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Production Web Tool",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )
            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: now,
            ).start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    trace_id=TraceId("production-web-tool-trace"),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=8,
                        max_total_tokens=8_192,
                        max_cost_micro_usd=250_000,
                        deadline=now + timedelta(minutes=5),
                    ),
                    runtime_version=TOOL_L2_RUNTIME_VERSION,
                    harness_version="harness-v1",
                    idempotency_key=f"production-web-{user_id}",
                    question="Find a public transport policy update.",
                    search_mode=TurnSearchMode.WEB,
                    industry_id=SMART_TRANSPORT_INDUSTRY_ID,
                )
            )
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            control = SqlAlchemyAgentRunControl(session_factory)
            manifests = SqlAlchemyContextManifestStore(session_factory)
            registry = ToolRegistry((tool,))
            l2_runtime = ToolL2Runtime(
                context_compiler=ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=manifests,
                model_provider=provider,
                tool_registry=registry,
                tool_executor=RegistryToolExecutor(registry, clock=clock),
                event_committer=committer,
                cancellation_probe=control,
                clock=clock,
            )
            runtime = UnifiedAgentRuntime(
                direct_answer_runtime=DirectAnswerRuntime(
                    context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
                    context_manifest_store=manifests,
                    model_provider=provider,
                    event_committer=committer,
                    cancellation_probe=control,
                    clock=clock,
                ),
                tool_l2_runtime=l2_runtime,
            )
            result = await DirectAnswerRunExecutionService(
                loader=SqlAlchemyDirectAnswerRunLoader(
                    session_factory,
                    direct_policy(),
                    tool_policy=tool_policy,
                ),
                runtime=runtime,
                terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
            ).execute_run(receipt.run_id)

            assert result.status is AgentRunStatus.COMPLETED
            assert len(provider.requests) == 2
            assert provider.requests[0].response_schema is not None
            async with session_factory() as session:
                assistant = await session.scalar(
                    select(Message).where(
                        Message.agent_run_id == receipt.run_id,
                        Message.role == MessageRole.ASSISTANT,
                        Message.status == MessageStatus.FINAL,
                    )
                )
                call = await session.scalar(
                    select(ToolCallRecord).where(ToolCallRecord.run_id == receipt.run_id)
                )
                audit = await session.scalar(
                    select(ToolRunRecord).where(ToolRunRecord.run_id == receipt.run_id)
                )
            assert assistant is not None
            assert assistant.content_markdown.endswith("[S1].")
            assert call is not None
            assert call.resolved_tool_name == "industry.web_search"
            assert audit is not None
            assert audit.status == "completed"

            assert call.observation is not None
            observation = parse_persisted_observation(
                call.observation,
                run_id=call.run_id,
                workspace_id=call.workspace_id,
            )
            collected_source = observation.sources[0]
            async with session_factory.begin() as session:
                data_source = await session.scalar(
                    select(DataSourceRecord).where(
                        DataSourceRecord.version == collected_source.source_version
                    )
                )
                final_step = await session.scalar(
                    select(AgentStepRecord).where(
                        AgentStepRecord.run_id == receipt.run_id,
                        AgentStepRecord.kind == AgentStepKind.FINAL,
                    )
                )
                assert data_source is not None
                assert final_step is not None
                session.add(
                    SourceItemRecord(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        industry_id=SMART_TRANSPORT_INDUSTRY_ID,
                        data_source_id=data_source.id,
                        source_kind=SourceKind.NEWS,
                        external_id="day4-evidence-snapshot-1",
                        title="Public transport transition",
                        summary="A frozen official-source contract fixture.",
                        locator=collected_source.locator,
                        published_at=now,
                        collected_at=now,
                        content_sha256=bytes.fromhex(collected_source.content_sha256),
                        source_metadata={"category": "Feature Story"},
                        usage_constraints=data_source.usage_constraints,
                    )
                )
                research_run_id = uuid4()
                session.add(
                    ResearchRunRecord(
                        id=research_run_id,
                        workspace_id=workspace_id,
                        owner_user_id=user_id,
                        agent_run_id=receipt.run_id,
                        status=ResearchRunStatus.DRAFT,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )

            evidence_service = EvidenceApplicationService(
                SqlAlchemyEvidenceRepository(session_factory),
                clock=lambda: now + timedelta(minutes=1),
            )
            normalized = await evidence_service.normalize_observation(
                WorkspaceScope(workspace_id, user_id, "owner"),
                NormalizeObservation(
                    tool_call_id=call.id,
                    observation_id=observation.observation_id,
                    trace_id=TraceId("day4-evidence-normalize"),
                ),
            )
            repeated_normalization = await evidence_service.normalize_observation(
                WorkspaceScope(workspace_id, user_id, "owner"),
                NormalizeObservation(
                    tool_call_id=call.id,
                    observation_id=observation.observation_id,
                    trace_id=TraceId("day4-evidence-normalize-retry"),
                ),
            )
            assert tuple(item.decision for item in normalized.items) == (
                EvidenceDecision.ACCEPTED,
                EvidenceDecision.REJECTED,
            )
            assert normalized.items[1].reason is EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
            accepted_evidence = normalized.items[0].evidence
            assert accepted_evidence is not None
            assert accepted_evidence.origin_run_id == receipt.run_id
            assert accepted_evidence.origin_tool_call_id == call.id
            assert repeated_normalization.items[0].evidence == accepted_evidence

            claim = await evidence_service.create_claim(
                WorkspaceScope(workspace_id, user_id, "owner"),
                CreateClaim(
                    research_run_id=research_run_id,
                    statement="Public transport is transitioning.",
                    confidence=0.8,
                    relations=(
                        ClaimEvidenceInput(
                            evidence_id=accepted_evidence.evidence_id,
                            relation=ClaimEvidenceRelation.SUPPORTS,
                        ),
                    ),
                    origin_run_id=receipt.run_id,
                    origin_step_id=final_step.id,
                    trace_id=TraceId("day4-claim-create"),
                ),
            )
            assert claim.verification_status is ClaimVerificationStatus.SUPPORTED
            assert claim.coverage == 1
            graph = await evidence_service.get_claim_graph(
                WorkspaceScope(workspace_id, user_id, "owner"), research_run_id
            )
            assert len(graph.nodes) == 2
            assert len(graph.edges) == 1

            invalidated = await evidence_service.invalidate_evidence(
                WorkspaceScope(workspace_id, user_id, "owner"),
                InvalidateEvidence(
                    evidence_id=accepted_evidence.evidence_id,
                    expected_revision=1,
                    status=EvidenceStatus.TOMBSTONED,
                    reason="Source withdrawn by an operator",
                    trace_id=TraceId("day4-evidence-invalidate"),
                ),
                invalidated_at=now + timedelta(minutes=2),
            )
            recomputed_claim = await evidence_service.get_claim(
                WorkspaceScope(workspace_id, user_id, "owner"), claim.claim_id
            )
            assert invalidated.excerpt is None
            assert invalidated.revision == 2
            assert recomputed_claim.verification_status is ClaimVerificationStatus.UNCERTAIN
            assert recomputed_claim.coverage == 0
            assert recomputed_claim.relations[0].status is RelationStatus.INVALIDATED

            async with session_factory() as session:
                assert await session.scalar(select(func.count()).select_from(EvidenceRecord)) == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(EvidenceNormalizationDecisionRecord)
                    )
                    == 2
                )
                assert (
                    await session.scalar(select(func.count()).select_from(ClaimEvidenceRecord)) == 1
                )
                assert await session.scalar(select(func.count()).select_from(GraphEdgeRecord)) == 1

            scope = WorkspaceScope(workspace_id, user_id, "owner")
            trace = await SqlAlchemyAgentTraceQuery(session_factory).get(
                scope=scope,
                run_id=receipt.run_id,
            )
            assert trace.run.status is AgentRunStatus.COMPLETED
            assert any(event.event_type.value == "agent.tool.completed" for event in trace.events)
            assert any(
                source.source_kind.value == "tool_observation"
                for manifest in trace.context_manifests
                for source in manifest.sources
            )
            assert "frozen official-source contract fixture" not in repr(trace)

            management = ConversationManagementService(
                repository=SqlAlchemyConversationManagementRepository(session_factory),
                clock=lambda: now + timedelta(minutes=2),
            )
            assert await management.delete(scope, receipt.conversation_id) is True
            assert (await management.list_conversations(scope)).items == ()
            async with session_factory() as session:
                assert (
                    await session.scalar(
                        select(ToolCallRecord).where(ToolCallRecord.run_id == receipt.run_id)
                    )
                    is not None
                )
                assert (
                    await session.scalar(
                        select(ToolRunRecord).where(ToolRunRecord.run_id == receipt.run_id)
                    )
                    is not None
                )
        finally:
            await engine.dispose()

    asyncio.run(exercise(), loop_factory=create_selector_event_loop)


def test_research_l3_executes_one_postgres_run_into_an_uncertain_draft(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        now = datetime.now(UTC)
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        tool = IndustryWebSearchTool(FrozenProviderRegistry(FrozenNewsProvider(now)))
        tool_policy = ToolL2RuntimePolicy(
            schema_version=1,
            profile_version="conversation-web-l2-v1",
            prompt_version="conversation-web-l2-prompt-v1",
            context_compiler_version="context-v1",
            output_contract_version="final-markdown-v1",
            toolset_version="conversation-web-toolset-v1",
            model="openai-compatible/frozen-web-model",
            max_input_tokens=4_096,
            max_decision_output_tokens=768,
            max_tool_calls=2,
            system_instructions="Use only the exact public-source Tool catalog.",
            available_tools=(tool.definition.reference,),
        )
        provider = ScriptedModelProvider(
            [
                model_response(
                    '{"decision":{"schema_version":1,"kind":"tool_call",'
                    '"name":"industry.web_search","version":"v1","arguments":{'
                    '"industry_code":"smart_transport","source_kind":"news",'
                    '"query":"transport policy","limit":2}}}',
                    "production-research-action",
                ),
                model_response(
                    '{"decision":{"schema_version":1,"kind":"final",'
                    '"content_markdown":"## Finding\\n\\nPublic transition update [S1]."}}',
                    "production-research-final",
                ),
            ]
        )
        clock = IncrementingClock(now + timedelta(seconds=1))
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"production-research-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=now,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Production Research L3",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )
            scope = WorkspaceScope(workspace_id, user_id, "owner")
            receipt = await ResearchSubmissionService(
                ConversationApplicationService(
                    transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(
                        session_factory
                    ),
                    clock=lambda: now,
                ),
                clock=lambda: now,
            ).start(
                scope,
                StartResearch(
                    trace_id=TraceId("production-research-l3-trace"),
                    industry_id=SMART_TRANSPORT_INDUSTRY_ID,
                    brief=ResearchBriefInput(
                        original_question="Find a public transport policy update.",
                        confirmed_scope=("Public smart transport news",),
                        exclusions=("Investment advice",),
                        completion_criteria=("Produce an attributable L3 draft",),
                    ),
                    idempotency_key=f"production-research-{user_id}",
                    max_steps=20,
                    max_total_tokens=12_000,
                    max_cost_micro_usd=300_000,
                    timeout_seconds=600,
                ),
            )
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            control = SqlAlchemyAgentRunControl(session_factory)
            manifests = SqlAlchemyContextManifestStore(session_factory)
            registry = ToolRegistry((tool,))
            compiler = ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter())
            executor = RegistryToolExecutor(registry, clock=clock)
            evidence_service = EvidenceApplicationService(
                SqlAlchemyEvidenceRepository(session_factory),
                clock=clock,
            )
            research_runtime = ResearchL3Runtime(
                workflow_store=SqlAlchemyResearchQueryRepository(session_factory),
                evidence_service=evidence_service,
                context_compiler=compiler,
                context_manifest_store=manifests,
                model_provider=provider,
                tool_registry=registry,
                tool_executor=executor,
                event_committer=committer,
                cancellation_probe=control,
                clock=clock,
            )
            runtime = UnifiedAgentRuntime(
                direct_answer_runtime=DirectAnswerRuntime(
                    context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
                    context_manifest_store=manifests,
                    model_provider=provider,
                    event_committer=committer,
                    cancellation_probe=control,
                    clock=clock,
                ),
                tool_l2_runtime=ToolL2Runtime(
                    context_compiler=compiler,
                    context_manifest_store=manifests,
                    model_provider=provider,
                    tool_registry=registry,
                    tool_executor=executor,
                    event_committer=committer,
                    cancellation_probe=control,
                    clock=clock,
                ),
                research_l3_runtime=research_runtime,
            )
            result = await DirectAnswerRunExecutionService(
                loader=SqlAlchemyDirectAnswerRunLoader(
                    session_factory,
                    direct_policy(),
                    tool_policy=tool_policy,
                ),
                runtime=runtime,
                terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
            ).execute_run(receipt.agent_run_id)
            trace = await SqlAlchemyAgentTraceQuery(session_factory).get(
                scope=scope,
                run_id=receipt.agent_run_id,
            )

            assert result.status is AgentRunStatus.COMPLETED
            assert len(provider.requests) == 2
            async with session_factory() as session:
                research_run = await session.get(ResearchRunRecord, receipt.research_run_id)
                plan = await session.scalar(
                    select(ResearchPlanRecord).where(
                        ResearchPlanRecord.research_run_id == receipt.research_run_id
                    )
                )
                draft = await session.scalar(
                    select(ResearchDraftRecord).where(
                        ResearchDraftRecord.research_run_id == receipt.research_run_id
                    )
                )
                claim = await session.scalar(
                    select(ResearchClaimRecord).where(
                        ResearchClaimRecord.research_run_id == receipt.research_run_id
                    )
                )
                steps = tuple(
                    await session.scalars(
                        select(AgentStepRecord)
                        .where(AgentStepRecord.run_id == receipt.agent_run_id)
                        .order_by(AgentStepRecord.sequence)
                    )
                )
                events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == receipt.agent_run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                assistant = await session.scalar(
                    select(Message).where(
                        Message.agent_run_id == receipt.agent_run_id,
                        Message.role == MessageRole.ASSISTANT,
                        Message.status == MessageStatus.FINAL,
                    )
                )
                run_count = await session.scalar(
                    select(func.count())
                    .select_from(AgentRunRecord)
                    .where(AgentRunRecord.workspace_id == workspace_id)
                )

            assert research_run is not None
            assert plan is not None
            assert draft is not None
            assert claim is not None
            assert assistant is not None
            assert research_run.status is ResearchRunStatus.COMPLETED
            assert research_run.current_node is not None
            assert research_run.current_node.value == "draft"
            assert research_run.state["status"] == "completed"
            assert research_run.state["step_count"] == 4
            assert research_run.state["input_tokens_used"] == 40
            assert research_run.state["output_tokens_used"] == 20
            assert research_run.state["cost_micro_usd"] == 80
            assert research_run.state["approval_status"] == "not_required"
            assert research_run.state["evidence_refs"] == []
            assert research_run.state["claim_refs"] == [str(claim.id)]
            assert plan.actions[0]["allowed_tool_names"] == ["industry.web_search"]
            assert draft.status is ResearchDraftStatus.UNCERTAIN_DRAFT
            assert draft.evidence_refs == []
            assert draft.claim_refs == [str(claim.id)]
            assert claim.verification_status is ClaimVerificationStatus.UNCERTAIN
            assert assistant.content_markdown == draft.content_markdown
            assert [step.kind for step in steps] == [
                AgentStepKind.MODEL,
                AgentStepKind.TOOL,
                AgentStepKind.MODEL,
                AgentStepKind.FINAL,
            ]
            assert tuple(
                event.payload["node"]
                for event in events
                if event.event_type is AgentEventType.RESEARCH_NODE_COMPLETED
            ) == tuple(node.value for node in RESEARCH_NODE_ORDER)
            assert tuple(
                event.details["node"]
                for event in trace.events
                if event.event_type is AgentEventType.RESEARCH_NODE_COMPLETED
            ) == tuple(node.value for node in RESEARCH_NODE_ORDER)
            assert all("error_summary" not in event.details for event in trace.events)
            assert run_count == 1
        finally:
            await engine.dispose()

    asyncio.run(exercise(), loop_factory=create_selector_event_loop)
