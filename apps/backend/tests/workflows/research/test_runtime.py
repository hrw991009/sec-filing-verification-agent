"""Acceptance tests for the single Day 4 Research L3 execution chain."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from industry_platform.modules.agent_harness.tool_fakes import (
    FAKE_LOOKUP_TOOL_NAME,
    FAKE_LOOKUP_TOOL_VERSION,
    FakeIndustryLookupTool,
    FakeLookupRecord,
)
from industry_platform.modules.agent_runtime.context import (
    ContextManifest,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.context_compiler import ContextCompilerV1
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.ports import CancellationProbe, ModelProvider
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime import UnifiedAgentRuntime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    ToolL2RunCommand,
    ToolL2RuntimePolicy,
)
from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimVerificationStatus,
    CreateClaim,
    Evidence,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceNormalizationItem,
    EvidenceNormalizationResult,
    EvidenceStatus,
    IndustrySourceLocatorV1,
    NormalizeObservation,
    ResearchClaim,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_HARNESS_VERSION,
    RESEARCH_NODE_ORDER,
    RESEARCH_RUNTIME_VERSION,
    ResearchBrief,
    ResearchBriefInput,
    ResearchDraft,
    ResearchDraftStatus,
    ResearchNode,
    ResearchPlan,
)
from industry_platform.modules.research.ports import ResearchWorkflowStore
from industry_platform.modules.tools.domain import ToolReference
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
from industry_platform.workflows.research.contracts import ResearchL3RunCommand
from industry_platform.workflows.research.runtime import ResearchL3Runtime

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-4000-8000-000000000001")
STREAM_ID = UUID("10000000-0000-4000-8000-000000000002")
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000003")
USER_ID = UUID("10000000-0000-4000-8000-000000000004")
SESSION_ID = UUID("10000000-0000-4000-8000-000000000005")
RESEARCH_RUN_ID = UUID("10000000-0000-4000-8000-000000000006")
BRIEF_ID = UUID("10000000-0000-4000-8000-000000000007")
PLAN_ID = UUID("10000000-0000-4000-8000-000000000008")
DRAFT_ID = UUID("10000000-0000-4000-8000-000000000009")
EVIDENCE_ID = UUID("10000000-0000-4000-8000-000000000010")
CLAIM_ID = UUID("10000000-0000-4000-8000-000000000011")
QUESTION = "Compare steel and copper market changes."


def stable_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"industry-platform:research-l3:{name}")


class FixedTokenCounter:
    version = "fixed-token-counter-v1"

    def count(self, *, model: str, messages: tuple[object, ...]) -> int:
        del model
        return len(messages) * 10


class RecordingManifestStore:
    def __init__(self) -> None:
        self.manifests: list[ContextManifest] = []

    async def save(self, manifest: ContextManifest) -> None:
        self.manifests.append(manifest)


class RecordingCommitter:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def append(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def append_batch(self, events: tuple[AgentEvent, ...]) -> None:
        self.events.extend(events)


class NeverCancelled:
    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        del run_id, workspace_id
        return False


class AlwaysCancelled:
    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        del run_id, workspace_id
        return True


class QueueModelProvider:
    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise AssertionError(f"Research decisions must be structured: {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("Unexpected Research model call")
        return self._responses.pop(0)


@dataclass
class IncrementingClock:
    value: datetime = NOW
    increment: timedelta = timedelta(milliseconds=1)

    def __call__(self) -> datetime:
        current = self.value
        self.value += self.increment
        return current


@dataclass
class RecordingWorkflowStore:
    states: list[tuple[ResearchNode, dict[str, object]]] = field(default_factory=list)
    plans: list[ResearchPlan] = field(default_factory=list)
    drafts: list[ResearchDraft] = field(default_factory=list)

    async def save_state(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        node: ResearchNode,
        state: Mapping[str, object],
        updated_at: datetime,
    ) -> None:
        del updated_at
        assert scope.workspace_id == WORKSPACE_ID
        assert research_run_id == RESEARCH_RUN_ID
        self.states.append((node, dict(state)))

    async def save_plan(self, scope: WorkspaceScope, plan: ResearchPlan) -> None:
        assert scope.workspace_id == plan.workspace_id
        self.plans.append(plan)

    async def save_draft(self, scope: WorkspaceScope, draft: ResearchDraft) -> None:
        assert scope.workspace_id == draft.workspace_id
        self.drafts.append(draft)


@dataclass
class RecordingEvidenceService:
    normalizations: list[NormalizeObservation] = field(default_factory=list)
    claims: list[CreateClaim] = field(default_factory=list)

    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
    ) -> EvidenceNormalizationResult:
        assert scope.workspace_id == WORKSPACE_ID
        self.normalizations.append(command)
        evidence = Evidence(
            evidence_id=EVIDENCE_ID,
            workspace_id=WORKSPACE_ID,
            kind=EvidenceKind.NEWS,
            title="Steel fixture",
            canonical_url="https://example.test/steel",
            locator=IndustrySourceLocatorV1(
                source_item_id=stable_id("source-item"),
                source_kind="news",
                provider="test_provider",
                source_version="fixture-2026-08-v1",
                content_sha256="a" * 64,
            ),
            excerpt="Steel demand rose 3%.",
            content_sha256="a" * 64,
            source_published_at=NOW,
            retrieved_at=NOW,
            license_or_terms="Test fixture.",
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=RUN_ID,
            origin_step_id=stable_id("tool-step-origin"),
            origin_tool_call_id=command.tool_call_id,
            origin_observation_id=command.observation_id,
            origin_source_ordinal=1,
            normalizer_version="evidence-normalizer-v1",
            authorization_snapshot=AuthorizationSnapshot(
                workspace_id=WORKSPACE_ID,
                actor_user_id=USER_ID,
                role="member",
                action="evidence.normalize",
                captured_at=NOW,
            ),
            source_resource_version="fixture-2026-08-v1:a",
            created_at=NOW,
            updated_at=NOW,
        )
        return EvidenceNormalizationResult(
            observation_id=command.observation_id,
            tool_call_id=command.tool_call_id,
            normalizer_version="evidence-normalizer-v1",
            items=(
                EvidenceNormalizationItem(
                    source_ordinal=1,
                    decision=EvidenceDecision.ACCEPTED,
                    reason=EvidenceDecisionReason.ACCEPTED,
                    evidence=evidence,
                ),
            ),
        )

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime | None = None,
    ) -> ResearchClaim:
        del created_at
        assert scope.workspace_id == WORKSPACE_ID
        self.claims.append(command)
        return ResearchClaim(
            claim_id=CLAIM_ID,
            workspace_id=WORKSPACE_ID,
            research_run_id=command.research_run_id,
            statement=command.statement,
            confidence=command.confidence,
            verification_status=ClaimVerificationStatus.SUPPORTED,
            coverage=1,
            conflict=False,
            revision=1,
            relations=(),
            created_at=NOW,
            updated_at=NOW,
        )


def model_response(output_text: str, request_id: str) -> ModelResponse:
    return ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/fake-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            cost_micro_usd=20,
            pricing_version="fake-pricing-v1",
        ),
        output_text=output_text,
        provider_request_id=request_id,
    )


def research_command(selected_budget: RunBudget) -> ResearchL3RunCommand:
    run = AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.RESEARCH,
        runtime_version=RESEARCH_RUNTIME_VERSION,
        harness_version=RESEARCH_HARNESS_VERSION,
        budget=selected_budget,
        trace_id=TraceId("trace-research-l3"),
        status=AgentRunStatus.QUEUED,
        state_revision=0,
        created_at=NOW,
        started_at=None,
        terminal_at=None,
        stop_reason=None,
    )
    state = RunState(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        revision=0,
        status=AgentRunStatus.QUEUED,
        step_count=0,
        event_count=1,
        input_tokens_used=0,
        output_tokens_used=0,
        cost_micro_usd=0,
        updated_at=NOW,
    )
    selected_policy = ToolL2RuntimePolicy(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        profile_version="research-l3-v1",
        prompt_version="research-l3-prompt-v1",
        context_compiler_version="context-v1",
        output_contract_version="final-markdown-v1",
        toolset_version="research-test-toolset-v1",
        model="openai-compatible/fake-model",
        max_input_tokens=2_048,
        max_decision_output_tokens=256,
        max_tool_calls=2,
        system_instructions="Use only the exact configured fixture Tool.",
        available_tools=(ToolReference(FAKE_LOOKUP_TOOL_NAME, FAKE_LOOKUP_TOOL_VERSION),),
    )
    loop_command = ToolL2RunCommand(
        run=run,
        state=state,
        policy=selected_policy,
        decision_model_step_ids=tuple(stable_id(f"model-step-{index}") for index in range(3)),
        tool_step_ids=tuple(stable_id(f"tool-step-{index}") for index in range(2)),
        decision_manifest_ids=tuple(stable_id(f"manifest-{index}") for index in range(3)),
        tool_call_ids=tuple(stable_id(f"tool-call-{index}") for index in range(2)),
        approval_request_ids=tuple(stable_id(f"approval-{index}") for index in range(2)),
        final_step_id=stable_id("final-step"),
        user_question=QUESTION,
        side_effect_idempotency_keys=(None, None),
        embedded_in_research=True,
    )
    return ResearchL3RunCommand(
        run=run,
        state=state,
        research_run_id=RESEARCH_RUN_ID,
        brief=ResearchBrief(
            brief_id=BRIEF_ID,
            research_run_id=RESEARCH_RUN_ID,
            workspace_id=WORKSPACE_ID,
            revision=1,
            input=ResearchBriefInput(
                original_question=QUESTION,
                confirmed_scope=("Public steel and copper market changes",),
                exclusions=("Investment advice",),
                completion_criteria=("Produce an attributable L3 draft",),
            ),
            budget=selected_budget,
            confirmed_by_user_id=USER_ID,
            confirmed_at=NOW,
            created_at=NOW,
        ),
        loop_command=loop_command,
        plan_id=PLAN_ID,
        draft_id=DRAFT_ID,
    )


def runtime_context(selected_budget: RunBudget) -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=AuthenticatedPrincipal(
            user_id=USER_ID,
            session_id=SESSION_ID,
            email=NormalizedEmail("research@example.test"),
            workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset(
            {WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL, WorkspaceAction.RUN_RESEARCH}
        ),
        budget=selected_budget,
        secret_references=("provider/research-test-key",),
    )


def build_runtime(
    provider: ModelProvider,
    workflow_store: RecordingWorkflowStore,
    evidence_service: RecordingEvidenceService,
    *,
    cancellation_probe: CancellationProbe | None = None,
) -> tuple[UnifiedAgentRuntime, FakeIndustryLookupTool, RecordingCommitter]:
    clock: Callable[[], datetime] = IncrementingClock()
    manifests = RecordingManifestStore()
    committer = RecordingCommitter()
    cancellation = cancellation_probe or NeverCancelled()
    compiler = ContextCompilerV1(token_counter=FixedTokenCounter())
    tool = FakeIndustryLookupTool(
        {
            "steel": FakeLookupRecord(
                text="Steel demand rose 3%.",
                locator="fixture://industry/steel/2026-08",
                source_version="fixture-2026-08-v1",
            )
        }
    )
    registry = ToolRegistry((tool,))
    executor = RegistryToolExecutor(registry, clock=clock)
    research_runtime = ResearchL3Runtime(
        workflow_store=cast(ResearchWorkflowStore, workflow_store),
        evidence_service=cast(EvidenceUseCase, evidence_service),
        context_compiler=compiler,
        context_manifest_store=manifests,
        model_provider=provider,
        tool_registry=registry,
        tool_executor=executor,
        event_committer=committer,
        cancellation_probe=cancellation,
        clock=clock,
    )
    direct_runtime = DirectAnswerRuntime(
        context_compiler=compiler,
        context_manifest_store=manifests,
        model_provider=provider,
        event_committer=committer,
        cancellation_probe=cancellation,
        clock=clock,
    )
    return (
        UnifiedAgentRuntime(
            direct_answer_runtime=direct_runtime,
            research_l3_runtime=research_runtime,
        ),
        tool,
        committer,
    )


@pytest.mark.asyncio
async def test_research_l3_completes_the_exact_graph_on_one_unified_run() -> None:
    provider = QueueModelProvider(
        (
            model_response(
                '{"decision":{"schema_version":1,"kind":"tool_call",'
                '"name":"fake.industry_lookup","version":"v1",'
                '"arguments":{"query":"steel"}}}',
                "decision-1",
            ),
            model_response(
                '{"decision":{"schema_version":1,"kind":"final",'
                '"content_markdown":"## Finding\\n\\nSteel demand rose 3% [S1]."}}',
                "decision-2",
            ),
        )
    )
    store = RecordingWorkflowStore()
    evidence = RecordingEvidenceService()
    runtime, tool, committer = build_runtime(provider, store, evidence)
    selected_budget = RunBudget(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        max_steps=20,
        max_total_tokens=5_000,
        max_cost_micro_usd=10_000,
        deadline=NOW + timedelta(minutes=10),
    )

    events = [
        event
        async for event in runtime.run(
            research_command(selected_budget),
            runtime_context(selected_budget),
        )
    ]

    assert events == committer.events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert {event.run_id for event in events} == {RUN_ID}
    assert {event.stream_id for event in events} == {STREAM_ID}
    assert events[-1].event_type is AgentEventType.RUN_COMPLETED
    assert events[-1].payload["stop_reason"] == RunStopReason.FINAL.value
    assert not any(
        event.event_type
        in {
            AgentEventType.RUN_PAUSED,
            AgentEventType.RUN_RESUMED,
            AgentEventType.CHECKPOINT_SAVED,
        }
        for event in events
    )
    started_nodes = tuple(
        ResearchNode(str(event.payload["node"]))
        for event in events
        if event.event_type is AgentEventType.RESEARCH_NODE_STARTED
    )
    completed_nodes = tuple(
        ResearchNode(str(event.payload["node"]))
        for event in events
        if event.event_type is AgentEventType.RESEARCH_NODE_COMPLETED
    )
    assert started_nodes == RESEARCH_NODE_ORDER
    assert completed_nodes == RESEARCH_NODE_ORDER
    assert tuple(node for node, _state in store.states) == RESEARCH_NODE_ORDER
    assert all(state["graph_version"] == RESEARCH_GRAPH_VERSION for _, state in store.states)
    assert all(state["revise_count"] == 0 for _, state in store.states)
    assert len(provider.requests) == 2
    assert [item.query for item in tool.invocations] == ["steel"]
    assert len(evidence.normalizations) == 1
    assert len(evidence.claims) == 1
    assert evidence.claims[0].origin_run_id == RUN_ID
    assert [plan.plan_id for plan in store.plans] == [PLAN_ID]
    assert [draft.draft_id for draft in store.drafts] == [DRAFT_ID]
    assert store.drafts[0].status is ResearchDraftStatus.EXPLAINABLE_DRAFT
    assert store.drafts[0].evidence_refs == (EVIDENCE_ID,)
    assert store.drafts[0].claim_refs == (CLAIM_ID,)
    assert "不是已核验的最终报告" in store.drafts[0].content_markdown


@pytest.mark.asyncio
async def test_research_l3_cancellation_stops_before_the_graph() -> None:
    provider = QueueModelProvider(())
    store = RecordingWorkflowStore()
    evidence = RecordingEvidenceService()
    runtime, tool, committer = build_runtime(
        provider,
        store,
        evidence,
        cancellation_probe=AlwaysCancelled(),
    )
    selected_budget = RunBudget(
        schema_version=1,
        max_steps=20,
        max_total_tokens=5_000,
        max_cost_micro_usd=10_000,
        deadline=NOW + timedelta(minutes=10),
    )

    events = [
        event
        async for event in runtime.run(
            research_command(selected_budget),
            runtime_context(selected_budget),
        )
    ]

    assert events == committer.events
    assert events[-1].event_type is AgentEventType.RUN_CANCELLED
    assert events[-1].payload["stop_reason"] == RunStopReason.CANCELLED.value
    assert not any(event.event_type is AgentEventType.RESEARCH_NODE_STARTED for event in events)
    assert tool.invocations == []
    assert store.states == []
    assert store.drafts == []


@pytest.mark.asyncio
async def test_research_l3_deadline_stops_at_the_first_node_safe_point() -> None:
    provider = QueueModelProvider(())
    store = RecordingWorkflowStore()
    evidence = RecordingEvidenceService()
    runtime, tool, _committer = build_runtime(provider, store, evidence)
    selected_budget = RunBudget(
        schema_version=1,
        max_steps=20,
        max_total_tokens=5_000,
        max_cost_micro_usd=10_000,
        deadline=NOW + timedelta(milliseconds=1),
    )

    events = [
        event
        async for event in runtime.run(
            research_command(selected_budget),
            runtime_context(selected_budget),
        )
    ]

    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == RunStopReason.DEADLINE_EXCEEDED.value
    assert tool.invocations == []
    assert store.states == []
    assert store.drafts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "selected_budget", "expected_reason"),
    [
        (
            (model_response("not-json", "invalid-response"),),
            RunBudget(
                schema_version=1,
                max_steps=20,
                max_total_tokens=5_000,
                max_cost_micro_usd=10_000,
                deadline=NOW + timedelta(minutes=10),
            ),
            RunStopReason.INVALID_PROVIDER_RESPONSE,
        ),
        (
            (
                model_response(
                    '{"decision":{"schema_version":1,"kind":"tool_call",'
                    '"name":"fake.industry_lookup","version":"v1",'
                    '"arguments":{"query":"steel"}}}',
                    "max-steps-action",
                ),
            ),
            RunBudget(
                schema_version=1,
                max_steps=2,
                max_total_tokens=5_000,
                max_cost_micro_usd=10_000,
                deadline=NOW + timedelta(minutes=10),
            ),
            RunStopReason.MAX_STEPS,
        ),
        (
            (
                model_response(
                    '{"decision":{"schema_version":1,"kind":"tool_call",'
                    '"name":"fake.industry_lookup","version":"v1",'
                    '"arguments":{"query":"steel"}}}',
                    "token-budget-action",
                ),
            ),
            RunBudget(
                schema_version=1,
                max_steps=20,
                max_total_tokens=10,
                max_cost_micro_usd=10_000,
                deadline=NOW + timedelta(minutes=10),
            ),
            RunStopReason.TOKEN_BUDGET_EXCEEDED,
        ),
        (
            (
                model_response(
                    '{"decision":{"schema_version":1,"kind":"tool_call",'
                    '"name":"fake.industry_lookup","version":"v1",'
                    '"arguments":{"query":"steel"}}}',
                    "cost-budget-action",
                ),
            ),
            RunBudget(
                schema_version=1,
                max_steps=20,
                max_total_tokens=5_000,
                max_cost_micro_usd=10,
                deadline=NOW + timedelta(minutes=10),
            ),
            RunStopReason.COST_BUDGET_EXCEEDED,
        ),
    ],
    ids=("invalid-provider-output", "max-steps", "token-budget", "cost-budget"),
)
async def test_research_l3_shared_loop_failures_have_one_terminal_event(
    responses: tuple[ModelResponse, ...],
    selected_budget: RunBudget,
    expected_reason: RunStopReason,
) -> None:
    provider = QueueModelProvider(responses)
    store = RecordingWorkflowStore()
    evidence = RecordingEvidenceService()
    runtime, _tool, committer = build_runtime(provider, store, evidence)

    events = [
        event
        async for event in runtime.run(
            research_command(selected_budget),
            runtime_context(selected_budget),
        )
    ]

    terminals = [
        event
        for event in events
        if event.event_type
        in {
            AgentEventType.RUN_COMPLETED,
            AgentEventType.RUN_FAILED,
            AgentEventType.RUN_CANCELLED,
        }
    ]
    assert events == committer.events
    assert terminals == [events[-1]]
    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == expected_reason.value
    assert store.drafts == []
