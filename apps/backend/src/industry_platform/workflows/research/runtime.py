"""Research L3 Runtime: LangGraph outside, the shared bounded Tool loop inside."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.ports import (
    AgentEventCommitter,
    CancellationProbe,
    ContextCompiler,
    ContextManifestStore,
    ModelProvider,
    ToolExecutor,
)
from industry_platform.modules.agent_runtime.runtime_support import utc_now
from industry_platform.modules.agent_runtime.state import (
    RunState,
    validate_run_state,
    validate_state_transition,
)
from industry_platform.modules.agent_runtime.tool_runtime import (
    ToolL2Runtime,
    _ToolLoopSegmentOutcome,
)
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    ToolL1RunCommand,
    ToolL2RunCommand,
    ToolLoopFinalDecision,
    tool_loop_decision_response_schema,
)
from industry_platform.modules.evidence.domain import (
    ClaimEvidenceInput,
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    CreateClaim,
    NormalizeObservation,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchDraft,
    ResearchDraftStatus,
    ResearchNode,
    ResearchPlan,
    ResearchPlanAction,
)
from industry_platform.modules.research.ports import ResearchWorkflowStore
from industry_platform.modules.tools.domain import (
    ToolCall,
    ToolDefinition,
    ToolExecutionResult,
)
from industry_platform.modules.tools.registry import ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.workflows.research.contracts import (
    ResearchExecutionState,
    ResearchGraphState,
    ResearchL3RunCommand,
    initial_graph_state,
)
from industry_platform.workflows.research.graph import build_research_graph


class ResearchL3Runtime(ToolL2Runtime):
    """Execute the only Research graph without owning a second model/tool loop."""

    def __init__(
        self,
        *,
        workflow_store: ResearchWorkflowStore,
        evidence_service: EvidenceUseCase,
        context_compiler: ContextCompiler,
        context_manifest_store: ContextManifestStore,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor[ToolCall, TrustedRuntimeContext, ToolExecutionResult],
        event_committer: AgentEventCommitter,
        cancellation_probe: CancellationProbe,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__(
            context_compiler=context_compiler,
            context_manifest_store=context_manifest_store,
            model_provider=model_provider,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            event_committer=event_committer,
            cancellation_probe=cancellation_probe,
            clock=clock,
        )
        self._workflow_store = workflow_store
        self._evidence_service = evidence_service

    async def run(
        self,
        command: ToolL1RunCommand | ToolL2RunCommand | ResearchL3RunCommand,
        runtime_context: TrustedRuntimeContext,
    ) -> AsyncGenerator[AgentEvent]:
        if not isinstance(command, ResearchL3RunCommand):
            raise TypeError("Research L3 Runtime requires a Research command")
        run = command.run
        state = command.state
        if (
            runtime_context.principal.user_id != run.user_id
            or runtime_context.workspace_scope.workspace_id != run.workspace_id
            or runtime_context.budget != run.budget
        ):
            raise ValueError("Trusted Runtime Context does not match the Research L3 Run")

        events: list[AgentEvent] = []
        steps: list[AgentStep] = []
        queued = self._event(
            run,
            events,
            event_type=AgentEventType.RUN_QUEUED,
            occurred_at=run.created_at,
            payload={
                "run_type": run.run_type.value,
                "runtime_version": run.runtime_version,
                "harness_version": run.harness_version,
                "loop_level": "l3",
                "graph_version": RESEARCH_GRAPH_VERSION,
                "tool_call_limit": command.loop_command.policy.tool_call_limit,
            },
        )
        await self._commit(events, queued)
        yield queued

        initial_at = self._time(not_before=run.created_at)
        if initial_at >= run.budget.deadline:
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=initial_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        if await self._cancel_requested(run):
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=initial_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        run, state, started = self._start_run(run, state, events, initial_at)
        await self._commit(events, started)
        yield started

        execution = _ResearchExecution(
            runtime=self,
            command=command,
            runtime_context=runtime_context,
            run=run,
            state=state,
            events=events,
            steps=steps,
            graph_state=ResearchExecutionState(initial_graph_state(command)),
            scope=runtime_context.workspace_scope,
        )
        graph = build_research_graph(execution)
        emitted = len(events)
        async for _update in graph.astream(execution.graph_state.graph, stream_mode="values"):
            while emitted < len(events):
                yield events[emitted]
                emitted += 1

        while emitted < len(events):
            yield events[emitted]
            emitted += 1
        if execution.graph_state.terminated:
            return
        final_decision = execution.graph_state.final_decision
        final_response = execution.graph_state.final_response
        final_markdown = execution.graph_state.final_markdown
        if final_decision is None or final_response is None or final_markdown is None:
            raise AssertionError("Research graph completed without an explainable draft")
        async for event in self._complete_final_decision(
            command=command.loop_command,
            run=execution.run,
            state=execution.state,
            events=events,
            steps=steps,
            decision=ToolLoopFinalDecision(
                schema_version=final_decision.schema_version,
                content_markdown=final_markdown,
            ),
            response=final_response,
        ):
            yield event


class _ResearchExecution:
    """Run-local graph executor; every node calls an existing Runtime or application port."""

    def __init__(
        self,
        *,
        runtime: ResearchL3Runtime,
        command: ResearchL3RunCommand,
        runtime_context: TrustedRuntimeContext,
        run: AgentRun,
        state: RunState,
        events: list[AgentEvent],
        steps: list[AgentStep],
        graph_state: ResearchExecutionState,
        scope: WorkspaceScope,
    ) -> None:
        self.runtime = runtime
        self.command = command
        self.runtime_context = runtime_context
        self.run = run
        self.state = state
        self.events = events
        self.steps = steps
        self.graph_state = graph_state
        self.scope = scope

    async def execute(
        self,
        node: ResearchNode,
        graph: ResearchGraphState,
    ) -> ResearchGraphState:
        if graph != self.graph_state.graph:
            raise ValueError("Research graph state diverged from the Runtime-owned state")
        if self.graph_state.terminated:
            return self.graph_state.graph
        stop = await self._preflight(node)
        if stop is not None:
            return stop
        await self._node_event(node, AgentEventType.RESEARCH_NODE_STARTED)
        try:
            await self._execute_node(node)
            if self.events[-1].event_type in {
                AgentEventType.RUN_COMPLETED,
                AgentEventType.RUN_FAILED,
                AgentEventType.RUN_CANCELLED,
            }:
                return self.graph_state.graph
            self.graph_state.graph["current_node"] = node.value
            self.graph_state.graph["status"] = self.state.status.value
            self.graph_state.graph["step_count"] = self.state.step_count
            self.graph_state.graph["input_tokens_used"] = self.state.input_tokens_used
            self.graph_state.graph["output_tokens_used"] = self.state.output_tokens_used
            self.graph_state.graph["cost_micro_usd"] = self.state.cost_micro_usd
            await self.runtime._workflow_store.save_state(
                self.scope,
                self.command.research_run_id,
                node=node,
                state=self.graph_state.graph,
                updated_at=self.events[-1].occurred_at,
            )
            await self._node_event(node, AgentEventType.RESEARCH_NODE_COMPLETED)
        except Exception:
            await self._fail_node(node, "research_node_failed")
            return self.graph_state.graph
        return self.graph_state.graph

    async def _preflight(self, node: ResearchNode) -> ResearchGraphState | None:
        at = self.runtime._time(not_before=self.events[-1].occurred_at)
        if await self.runtime._cancel_requested(self.run):
            await self._terminal(
                status=AgentRunStatus.CANCELLED,
                reason=RunStopReason.CANCELLED,
                occurred_at=at,
                node=node,
            )
            return self.graph_state.graph
        if at >= self.run.budget.deadline:
            await self._terminal(
                status=AgentRunStatus.FAILED,
                reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=at,
                node=node,
            )
            return self.graph_state.graph
        return None

    async def _execute_node(self, node: ResearchNode) -> None:
        if node is ResearchNode.CLARIFY_SCOPE:
            self._clarify_scope()
        elif node is ResearchNode.WRITE_RESEARCH_BRIEF:
            self._write_brief()
        elif node is ResearchNode.PLAN:
            await self._plan()
        elif node is ResearchNode.RESEARCH_LOOP:
            await self._research_loop()
        elif node is ResearchNode.NORMALIZE_EVIDENCE:
            await self._normalize_evidence()
        elif node is ResearchNode.SYNTHESIZE_CLAIMS:
            await self._synthesize_claims()
        elif node is ResearchNode.OUTLINE:
            self._outline()
        elif node is ResearchNode.DRAFT:
            await self._draft()
        else:
            raise ValueError("Research graph contains an unsupported node")

    def _clarify_scope(self) -> None:
        brief = self.command.brief
        if brief.input.original_question != self.command.loop_command.user_question:
            raise ValueError("Research Planner attempted to rewrite the original question")
        if brief.revision != self.graph_state.graph["brief_revision"]:
            raise ValueError("Research graph loaded a stale Brief revision")

    def _write_brief(self) -> None:
        if self.command.brief.confirmed_by_user_id != self.run.user_id:
            raise ValueError("Research Brief was not confirmed by the Run owner")

    async def _plan(self) -> None:
        references = self.command.loop_command.policy.available_tools
        tool_names = tuple(reference.name for reference in references)
        plan = ResearchPlan(
            plan_id=self.command.plan_id,
            research_run_id=self.command.research_run_id,
            workspace_id=self.run.workspace_id,
            brief_revision=self.command.brief.revision,
            revision=1,
            actions=tuple(
                ResearchPlanAction(
                    ordinal=index,
                    objective=(
                        f"Use {reference.name}:{reference.version} only within confirmed scope"
                    ),
                    allowed_tool_names=(reference.name,),
                )
                for index, reference in enumerate(references, start=1)
            ),
            planner_summary=(
                "The plan preserves the original question and confirmed scope; "
                f"available Tool names: {', '.join(tool_names)}."
            ),
            created_at=self.events[-1].occurred_at,
        )
        await self.runtime._workflow_store.save_plan(self.scope, plan)
        self.graph_state.graph["plan_id"] = str(plan.plan_id)
        self.graph_state.graph["pending_actions"] = [action.ordinal for action in plan.actions]

    async def _research_loop(self) -> None:
        command = self.command.loop_command
        selected = tuple(
            self.runtime._tool_registry.definition(reference)
            for reference in command.policy.available_tools
        )
        if any(definition is None for definition in selected):
            await self._terminal(
                status=AgentRunStatus.FAILED,
                reason=RunStopReason.TOOL_DENIED,
                occurred_at=self.runtime._time(not_before=self.events[-1].occurred_at),
                node=ResearchNode.RESEARCH_LOOP,
                details={"error_code": "tool_registry_missing"},
            )
            return
        definitions = tuple(item for item in selected if isinstance(item, ToolDefinition))
        outcome = _ToolLoopSegmentOutcome(
            run=self.run,
            state=self.state,
            steps=self.steps,
            observations=[],
        )
        async for _event in self.runtime._run_loop_segment(
            command=command,
            runtime_context=self.runtime_context,
            events=self.events,
            definitions=definitions,
            decision_schema=tool_loop_decision_response_schema(definitions),
            seen_actions=set(),
            seen_observation_content=set(),
            outcome=outcome,
        ):
            pass
        self.run, self.state = outcome.run, outcome.state
        self.graph_state.observations = outcome.observations
        self.graph_state.final_decision = outcome.final_decision
        self.graph_state.final_response = outcome.final_response
        if outcome.terminated:
            terminal = self.events[-1]
            reason = terminal.payload.get("stop_reason")
            self.graph_state.graph["stop_reason"] = reason if isinstance(reason, str) else None
            self.graph_state.terminated = True
            return
        if outcome.final_decision is None or outcome.final_response is None:
            raise ValueError("Research Tool loop did not return a final decision")
        self.graph_state.graph["pending_actions"] = []

    async def _normalize_evidence(self) -> None:
        evidence_refs: list[str] = []
        for observation in self.graph_state.observations:
            result = await self.runtime._evidence_service.normalize_observation(
                self.scope,
                NormalizeObservation(
                    tool_call_id=observation.tool_call_id,
                    observation_id=observation.observation_id,
                    trace_id=self.run.trace_id,
                ),
            )
            for item in result.items:
                if item.evidence is not None:
                    evidence_refs.append(str(item.evidence.evidence_id))
        self.graph_state.graph["evidence_refs"] = list(dict.fromkeys(evidence_refs))

    async def _synthesize_claims(self) -> None:
        decision = self.graph_state.final_decision
        if decision is None:
            raise ValueError("Research Claim synthesis requires the Tool loop decision")
        origin = next(
            (step for step in reversed(self.steps) if step.kind is AgentStepKind.MODEL),
            None,
        )
        if origin is None:
            raise ValueError("Research Claim synthesis requires an origin Model Step")
        evidence_ids = tuple(UUID(value) for value in self.graph_state.graph["evidence_refs"])
        statement = _claim_statement(decision.content_markdown)
        claim = await self.runtime._evidence_service.create_claim(
            self.scope,
            CreateClaim(
                research_run_id=self.command.research_run_id,
                statement=statement,
                confidence=0.75 if evidence_ids else 0.25,
                relations=tuple(
                    ClaimEvidenceInput(
                        evidence_id=evidence_id,
                        relation=ClaimEvidenceRelation.SUPPORTS,
                    )
                    for evidence_id in evidence_ids
                ),
                origin_run_id=self.run.run_id,
                origin_step_id=origin.step_id,
                trace_id=self.run.trace_id,
            ),
        )
        self.graph_state.graph["claim_refs"] = [str(claim.claim_id)]
        if claim.verification_status in {
            ClaimVerificationStatus.UNCERTAIN,
            ClaimVerificationStatus.CONFLICTED,
        }:
            self.graph_state.graph["error_summary"] = claim.verification_status.value

    def _outline(self) -> None:
        self.graph_state.outline = (
            "问题与确认范围",
            "研究发现与证据",
            "冲突、限制与未确定项",
        )

    async def _draft(self) -> None:
        decision = self.graph_state.final_decision
        if decision is None or not self.graph_state.outline:
            raise ValueError("Research Draft requires findings and an outline")
        evidence_ids = tuple(UUID(value) for value in self.graph_state.graph["evidence_refs"])
        claim_ids = tuple(UUID(value) for value in self.graph_state.graph["claim_refs"])
        uncertainty = self.graph_state.graph["error_summary"]
        status = (
            ResearchDraftStatus.UNCERTAIN_DRAFT
            if uncertainty is not None or not evidence_ids
            else ResearchDraftStatus.EXPLAINABLE_DRAFT
        )
        markdown = _draft_markdown(
            question=self.command.brief.input.original_question,
            confirmed_scope=self.command.brief.input.confirmed_scope,
            findings=decision.content_markdown,
            evidence_ids=evidence_ids,
            claim_ids=claim_ids,
            uncertainty=uncertainty,
        )
        draft = ResearchDraft(
            draft_id=self.command.draft_id,
            research_run_id=self.command.research_run_id,
            workspace_id=self.run.workspace_id,
            plan_id=self.command.plan_id,
            status=status,
            content_markdown=markdown,
            outline=self.graph_state.outline,
            evidence_refs=evidence_ids,
            claim_refs=claim_ids,
            uncertainty_summary=uncertainty,
            created_at=self.events[-1].occurred_at,
            updated_at=self.events[-1].occurred_at,
        )
        await self.runtime._workflow_store.save_draft(self.scope, draft)
        self.graph_state.final_markdown = draft.content_markdown

    async def _node_event(self, node: ResearchNode, event_type: AgentEventType) -> None:
        occurred_at = self.runtime._time(not_before=self.events[-1].occurred_at)
        revision = self.state.revision + 1
        next_state = replace(
            self.state,
            revision=revision,
            event_count=len(self.events) + 1,
            updated_at=occurred_at,
        )
        next_run = replace(self.run, state_revision=revision)
        event = self.runtime._event(
            next_run,
            self.events,
            event_type=event_type,
            occurred_at=occurred_at,
            payload={
                "node": node.value,
                "graph_version": RESEARCH_GRAPH_VERSION,
                "research_state_schema_version": RESEARCH_STATE_SCHEMA_VERSION,
                "state_revision": revision,
            },
        )
        validate_state_transition(self.state, next_state, expected_revision=self.state.revision)
        validate_run_state(next_run, next_state)
        await self.runtime._commit(self.events, event)
        self.run, self.state = next_run, next_state

    async def _fail_node(self, node: ResearchNode, error_code: str) -> None:
        occurred_at = self.runtime._time(not_before=self.events[-1].occurred_at)
        revision = self.state.revision + 1
        next_state = replace(
            self.state,
            revision=revision,
            event_count=len(self.events) + 1,
            updated_at=occurred_at,
        )
        next_run = replace(self.run, state_revision=revision)
        failed = self.runtime._event(
            next_run,
            self.events,
            event_type=AgentEventType.RESEARCH_NODE_FAILED,
            occurred_at=occurred_at,
            payload={
                "node": node.value,
                "error_code": error_code,
                "graph_version": RESEARCH_GRAPH_VERSION,
                "state_revision": revision,
            },
        )
        validate_state_transition(self.state, next_state, expected_revision=self.state.revision)
        validate_run_state(next_run, next_state)
        await self.runtime._commit(self.events, failed)
        self.run, self.state = next_run, next_state
        await self._terminal(
            status=AgentRunStatus.FAILED,
            reason=RunStopReason.RUNTIME_ERROR,
            occurred_at=self.runtime._time(not_before=occurred_at),
            node=node,
            details={"error_code": error_code},
        )

    async def _terminal(
        self,
        *,
        status: AgentRunStatus,
        reason: RunStopReason,
        occurred_at: datetime,
        node: ResearchNode,
        details: dict[str, object] | None = None,
    ) -> None:
        terminal = self.runtime._terminal_event(
            run=self.run,
            state=self.state,
            events=self.events,
            steps=tuple(self.steps),
            status=status,
            stop_reason=reason,
            occurred_at=occurred_at,
            terminal_details={"research_node": node.value, **(details or {})},
        )
        await self.runtime._commit(self.events, terminal)
        self.graph_state.graph["current_node"] = node.value
        self.graph_state.graph["status"] = status.value
        self.graph_state.graph["stop_reason"] = reason.value
        self.graph_state.graph["cancel_requested"] = reason is RunStopReason.CANCELLED
        self.graph_state.graph["approval_status"] = (
            "required" if reason is RunStopReason.APPROVAL_REQUIRED else "not_required"
        )
        self.graph_state.graph["error_summary"] = (
            None if details is None else str(details.get("error_code"))
        )
        self.graph_state.terminated = True


def _claim_statement(markdown: str) -> str:
    normalized = " ".join(
        line.lstrip("#- ").strip() for line in markdown.splitlines() if line.strip()
    )
    return normalized[:4_000].rstrip() or "Research findings require review"


def _draft_markdown(
    *,
    question: str,
    confirmed_scope: tuple[str, ...],
    findings: str,
    evidence_ids: tuple[UUID, ...],
    claim_ids: tuple[UUID, ...],
    uncertainty: str | None,
) -> str:
    evidence_lines = (
        "\n".join(f"- Evidence `{value}`" for value in evidence_ids)
        if evidence_ids
        else "- 暂无通过规范化与授权校验的 Evidence。"
    )
    claim_lines = (
        "\n".join(f"- Claim `{value}`" for value in claim_ids) if claim_ids else "- 暂无 Claim。"
    )
    uncertainty_text = uncertainty or "未发现结构化冲突; 本草稿仍未经过 Day 6 Verifier。"
    scope_lines = "\n".join(f"- {value}" for value in confirmed_scope)
    return (
        "# L3 可解释研究草稿\n\n"
        "> 这是 Evidence Research L3 草稿, 不是已核验的最终报告。\n\n"
        f"## 原始问题\n\n{question}\n\n"
        f"## 确认范围\n\n{scope_lines}\n\n"
        f"## 研究发现\n\n{findings}\n\n"
        f"## Evidence\n\n{evidence_lines}\n\n"
        f"## Claims\n\n{claim_lines}\n\n"
        f"## 限制与未确定项\n\n{uncertainty_text}"
    )
