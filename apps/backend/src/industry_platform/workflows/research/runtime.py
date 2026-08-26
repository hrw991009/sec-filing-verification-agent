"""Research L3/L4 Runtime: LangGraph outside, the shared bounded Tool loop inside."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_runtime.checkpoints import (
    CheckpointEnvelope,
    SaveCheckpointCommand,
)
from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.execution import RecoverableAgentRunInterruption
from industry_platform.modules.agent_runtime.ports import (
    AgentEventCommitter,
    CancellationProbe,
    CheckpointStore,
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
    RESEARCH_NODE_ORDER,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchApprovalStatus,
    ResearchDraft,
    ResearchDraftStatus,
    ResearchNode,
    ResearchPlan,
    ResearchPlanAction,
)
from industry_platform.modules.research.durability import ResearchDurabilityService
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
    ResearchResumeKind,
    initial_graph_state,
)
from industry_platform.workflows.research.graph import build_research_graph


class ResearchHardStopError(RecoverableAgentRunInterruption):
    """Fault-injection boundary used to prove recovery from a committed Checkpoint."""


class ResearchL3Runtime(ToolL2Runtime):
    """Execute the only Research graph, optionally with durable L4 checkpoints."""

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
        checkpoint_store: CheckpointStore | None = None,
        durability_service: ResearchDurabilityService | None = None,
        hard_stop_after_node: ResearchNode | None = None,
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
        self._checkpoint_store = checkpoint_store
        self._durability_service = durability_service
        self._hard_stop_after_node = hard_stop_after_node
        if (checkpoint_store is None) != (durability_service is None):
            raise ValueError("Research L4 durability dependencies must be configured together")

    async def run(
        self,
        command: ToolL1RunCommand | ToolL2RunCommand | ResearchL3RunCommand,
        runtime_context: TrustedRuntimeContext,
    ) -> AsyncGenerator[AgentEvent]:
        if not isinstance(command, ResearchL3RunCommand):
            raise TypeError("Research Runtime requires a Research command")
        run = command.run
        state = command.state
        if (
            runtime_context.principal.user_id != run.user_id
            or runtime_context.workspace_scope.workspace_id != run.workspace_id
            or runtime_context.budget != run.budget
        ):
            raise ValueError("Trusted Runtime Context does not match the Research Run")

        resume_snapshot = command.resume
        resumed = resume_snapshot is not None
        events = [] if resume_snapshot is None else list(resume_snapshot.event_history)
        steps = [] if resume_snapshot is None else list(resume_snapshot.steps)
        if resumed:
            for committed in events:
                yield committed
        if not resumed:
            queued = self._event(
                run,
                events,
                event_type=AgentEventType.RUN_QUEUED,
                occurred_at=run.created_at,
                payload={
                    "run_type": run.run_type.value,
                    "runtime_version": run.runtime_version,
                    "harness_version": run.harness_version,
                    "loop_level": "l4" if self._checkpoint_store is not None else "l3",
                    "graph_version": RESEARCH_GRAPH_VERSION,
                    "tool_call_limit": command.loop_command.policy.tool_call_limit,
                },
            )
            await self._commit(events, queued)
            yield queued

        initial_at = self._time(not_before=events[-1].occurred_at if events else run.created_at)
        if initial_at >= run.budget.deadline:
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=tuple(steps),
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
                steps=tuple(steps),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=initial_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if resumed:
            if resume_snapshot is None:
                raise AssertionError("Resumed Research requires a snapshot")
            revision = state.revision + 1
            resumed_state = replace(
                state,
                revision=revision,
                status=AgentRunStatus.RUNNING,
                event_count=len(events) + 1,
                updated_at=initial_at,
            )
            resumed_run = replace(run, status=AgentRunStatus.RUNNING, state_revision=revision)
            resumed_event = self._event(
                resumed_run,
                events,
                event_type=AgentEventType.RUN_RESUMED,
                occurred_at=initial_at,
                payload={
                    "state_revision": revision,
                    "checkpoint_revision": resume_snapshot.checkpoint_revision,
                    "resume_kind": resume_snapshot.kind.value,
                    "resume_node": (
                        None
                        if resume_snapshot.next_node is None
                        else resume_snapshot.next_node.value
                    ),
                },
            )
            validate_state_transition(state, resumed_state, expected_revision=state.revision)
            validate_run_state(resumed_run, resumed_state)
            await self._commit(events, resumed_event)
            run, state = resumed_run, resumed_state
            yield resumed_event
        else:
            run, state, started = self._start_run(run, state, events, initial_at)
            await self._commit(events, started)
            yield started

        restored = resume_snapshot
        execution_state = ResearchExecutionState(initial_graph_state(command))
        if restored is not None:
            execution_state.observations = list(restored.observations)
            execution_state.final_decision = restored.final_decision
            execution_state.final_response = restored.final_response
            execution_state.final_markdown = restored.final_markdown
            execution_state.outline = restored.outline
            if restored.kind is ResearchResumeKind.APPROVAL:
                execution_state.graph["approval_status"] = ResearchApprovalStatus.ALLOWED.value
            execution_state.graph["stop_reason"] = None
            execution_state.graph["status"] = AgentRunStatus.RUNNING.value

        execution = _ResearchExecution(
            runtime=self,
            command=command,
            runtime_context=runtime_context,
            run=run,
            state=state,
            events=events,
            steps=steps,
            graph_state=execution_state,
            scope=runtime_context.workspace_scope,
            checkpoint_revision=(
                None if command.resume is None else command.resume.checkpoint_revision
            ),
        )
        emitted = len(events)
        next_node = (
            ResearchNode.CLARIFY_SCOPE if command.resume is None else command.resume.next_node
        )
        if next_node is not None:
            graph = build_research_graph(execution, start_node=next_node)
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
        checkpoint_revision: int | None,
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
        self.checkpoint_revision = checkpoint_revision

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
            checkpoint = await self._save_checkpoint(node)
            if (
                checkpoint is not None
                and node is ResearchNode.PLAN
                and self.command.resume is None
                and self.command.brief.input.approval_reason is not None
            ):
                await self._pause(checkpoint)
        except Exception:
            await self._fail_node(node, "research_node_failed")
            return self.graph_state.graph
        if not self.graph_state.terminated and self.runtime._hard_stop_after_node is node:
            raise ResearchHardStopError(f"Research hard stop after {node.value}")
        return self.graph_state.graph

    async def _save_checkpoint(self, node: ResearchNode) -> CheckpointEnvelope | None:
        store = self.runtime._checkpoint_store
        if store is None:
            return None
        next_node = _next_node(node)
        financial_scope = self.command.brief.input.financial_scope
        payload = {
            "kind": "research_l4_v1",
            "graph_version": RESEARCH_GRAPH_VERSION,
            "research_state_schema_version": RESEARCH_STATE_SCHEMA_VERSION,
            "research_run_id": str(self.command.research_run_id),
            "financial_scope": (
                None if financial_scope is None else dict(financial_scope.to_mapping())
            ),
            "node": node.value,
            "next_node": None if next_node is None else next_node.value,
            "graph_state": dict(self.graph_state.graph),
            "execution": _execution_checkpoint_payload(self.graph_state, self.steps),
        }
        checkpoint = await store.save(
            SaveCheckpointCommand(
                run_id=self.run.run_id,
                workspace_id=self.run.workspace_id,
                expected_revision=self.checkpoint_revision,
                checkpoint_revision=(
                    0 if self.checkpoint_revision is None else self.checkpoint_revision + 1
                ),
                state=self.state,
                payload=payload,
            )
        )
        durability = self.runtime._durability_service
        if durability is None:
            raise ValueError("Research durability service is not configured")
        effects = (
            tuple(
                (
                    "tool_call",
                    str(observation.tool_call_id),
                    observation.envelope_sha256,
                )
                for observation in self.graph_state.observations
            )
            + tuple(
                (
                    "evidence",
                    reference,
                    hashlib.sha256(reference.encode("ascii")).hexdigest(),
                )
                for reference in self.graph_state.graph["evidence_refs"]
            )
            + tuple(
                (
                    "artifact",
                    reference,
                    hashlib.sha256(reference.encode("ascii")).hexdigest(),
                )
                for reference in self.graph_state.graph["artifact_refs"]
            )
        )
        await durability.record_completed_effects(
            self.scope,
            run_id=self.run.run_id,
            effects=effects,
        )
        saved_event = self.runtime._event(
            self.run,
            self.events,
            event_type=AgentEventType.CHECKPOINT_SAVED,
            occurred_at=self.runtime._time(not_before=self.events[-1].occurred_at),
            payload={
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "revision": checkpoint.revision,
                "run_state_revision": checkpoint.state.revision,
                "node": node.value,
                "next_node": None if next_node is None else next_node.value,
            },
        )
        await self.runtime._commit(self.events, saved_event)
        self.state = replace(
            self.state,
            event_count=len(self.events),
            updated_at=saved_event.occurred_at,
        )
        self.checkpoint_revision = checkpoint.revision
        return checkpoint

    async def _pause(self, checkpoint: CheckpointEnvelope) -> None:
        service = self.runtime._durability_service
        reason = self.command.brief.input.approval_reason
        if service is None or reason is None:
            raise ValueError("Research approval interrupt is not configured")
        request, _token = await service.interrupt(
            self.scope,
            checkpoint=checkpoint,
            reason=reason,
        )
        requested_at = self.runtime._time(not_before=self.events[-1].occurred_at)
        requested = self.runtime._event(
            self.run,
            self.events,
            event_type=AgentEventType.APPROVAL_REQUESTED,
            occurred_at=requested_at,
            payload={
                "approval_request_id": str(request.approval_request_id),
                "checkpoint_revision": request.checkpoint_revision,
                "reason": request.reason.value,
                "expires_at": request.expires_at.isoformat(),
            },
        )
        paused_at = self.runtime._time(not_before=requested_at)
        revision = self.state.revision + 1
        paused_state = replace(
            self.state,
            revision=revision,
            status=AgentRunStatus.PAUSED,
            event_count=len(self.events) + 2,
            updated_at=paused_at,
        )
        paused_run = replace(
            self.run,
            status=AgentRunStatus.PAUSED,
            state_revision=revision,
        )
        paused = self.runtime._event(
            paused_run,
            [*self.events, requested],
            event_type=AgentEventType.RUN_PAUSED,
            occurred_at=paused_at,
            payload={
                "state_revision": revision,
                "checkpoint_revision": checkpoint.revision,
                "approval_request_id": str(request.approval_request_id),
                "pause_reason": reason.value,
            },
        )
        validate_state_transition(self.state, paused_state, expected_revision=self.state.revision)
        validate_run_state(paused_run, paused_state)
        await self.runtime._commit_batch(self.events, (requested, paused))
        self.run, self.state = paused_run, paused_state
        self.graph_state.graph["status"] = AgentRunStatus.PAUSED.value
        self.graph_state.graph["approval_status"] = ResearchApprovalStatus.PENDING.value
        self.graph_state.graph["stop_reason"] = RunStopReason.APPROVAL_REQUIRED.value
        self.graph_state.terminated = True

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


def _next_node(node: ResearchNode) -> ResearchNode | None:
    index = RESEARCH_NODE_ORDER.index(node)
    return None if index + 1 == len(RESEARCH_NODE_ORDER) else RESEARCH_NODE_ORDER[index + 1]


def _execution_checkpoint_payload(
    state: ResearchExecutionState,
    steps: list[AgentStep],
) -> dict[str, object]:
    response = state.final_response
    decision = state.final_decision
    return {
        "observations": [
            dict(observation.to_model_visible_envelope()) for observation in state.observations
        ],
        "final_decision": (
            None
            if decision is None
            else {
                "schema_version": decision.schema_version,
                "content_markdown": decision.content_markdown,
            }
        ),
        "final_response": (
            None
            if response is None
            else {
                "schema_version": response.schema_version,
                "model": response.model,
                "finish_reason": response.finish_reason.value,
                "output_text": response.output_text,
                "provider_request_id": response.provider_request_id,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cached_input_tokens": response.usage.cached_input_tokens,
                    "cost_micro_usd": response.usage.cost_micro_usd,
                    "pricing_version": response.usage.pricing_version,
                },
            }
        ),
        "final_markdown": state.final_markdown,
        "outline": list(state.outline),
        "steps": [
            {
                "schema_version": step.schema_version,
                "step_id": str(step.step_id),
                "run_id": str(step.run_id),
                "workspace_id": str(step.workspace_id),
                "sequence": step.sequence,
                "kind": step.kind.value,
                "status": step.status.value,
                "state_revision": step.state_revision,
                "started_at": step.started_at.isoformat(),
                "completed_at": (
                    None if step.completed_at is None else step.completed_at.isoformat()
                ),
                "input_summary": dict(step.input_summary),
                "output_summary": dict(step.output_summary),
                "input_artifact_ids": [str(value) for value in step.input_artifact_ids],
                "output_artifact_ids": [str(value) for value in step.output_artifact_ids],
                "input_tokens": step.input_tokens,
                "output_tokens": step.output_tokens,
                "cost_micro_usd": step.cost_micro_usd,
                "latency_ms": step.latency_ms,
                "error_code": step.error_code,
            }
            for step in steps
        ],
    }


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
