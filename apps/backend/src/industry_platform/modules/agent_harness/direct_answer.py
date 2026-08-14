"""Trusted materialization of Day 2 Scenarios for the unified Direct Answer Runtime."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_harness.profiles import DirectAnswerProfile
from industry_platform.modules.agent_harness.runner import MaterializedScenario
from industry_platform.modules.agent_harness.scenarios import Scenario
from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    require_non_nil_uuid,
    require_utc,
)
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRunCommand
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope


@dataclass(frozen=True, slots=True)
class HarnessExecutionIdentity:
    """Server-created IDs and time for one isolated Scenario execution."""

    run_id: UUID
    stream_id: UUID
    model_step_id: UUID
    final_step_id: UUID
    manifest_id: UUID
    trace_id: TraceId
    created_at: datetime

    def __post_init__(self) -> None:
        identifiers = (
            self.run_id,
            self.stream_id,
            self.model_step_id,
            self.final_step_id,
            self.manifest_id,
        )
        for identifier in identifiers:
            require_non_nil_uuid(identifier, field_name="Harness execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Harness execution IDs must be distinct")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Harness Trace ID is invalid")
        require_utc(self.created_at, field_name="Harness execution creation time")


@dataclass(frozen=True, slots=True)
class HarnessTrustedIdentity:
    """Trusted authorization fixture created by Harness composition, never Scenario JSON."""

    principal: AuthenticatedPrincipal = field(repr=False)
    workspace_scope: WorkspaceScope = field(repr=False)
    capabilities: frozenset[WorkspaceAction] = field(repr=False)
    secret_references: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        capabilities = frozenset(self.capabilities)
        references = tuple(self.secret_references)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "secret_references", references)


@dataclass(frozen=True, slots=True)
class DirectAnswerScenarioMaterializer:
    """Convert a serialized Scenario into trusted Runtime inputs without executing it."""

    profile: DirectAnswerProfile
    execution: HarnessExecutionIdentity
    identity: HarnessTrustedIdentity = field(repr=False)
    model_version: str
    harness_version: str

    def materialize(
        self,
        scenario: Scenario,
    ) -> MaterializedScenario[DirectAnswerRunCommand, TrustedRuntimeContext]:
        """Validate frozen versions and construct one fresh queued Runtime command."""

        self._validate_scenario_versions(scenario)
        budget = scenario.budget.materialize(started_at=self.execution.created_at)
        run = AgentRun(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            run_id=self.execution.run_id,
            event_stream_id=self.execution.stream_id,
            workspace_id=self.identity.workspace_scope.workspace_id,
            user_id=self.identity.workspace_scope.user_id,
            run_type=AgentRunType.DIRECT_ANSWER,
            runtime_version=scenario.runtime_version,
            harness_version=scenario.harness_version,
            budget=budget,
            trace_id=self.execution.trace_id,
            status=AgentRunStatus.QUEUED,
            state_revision=0,
            created_at=self.execution.created_at,
            started_at=None,
            terminal_at=None,
            stop_reason=None,
        )
        state = RunState(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            revision=0,
            status=AgentRunStatus.QUEUED,
            step_count=0,
            event_count=1,
            input_tokens_used=0,
            output_tokens_used=0,
            cost_micro_usd=0,
            updated_at=self.execution.created_at,
        )
        question = scenario.input["question"]
        if not isinstance(question, str):
            raise ValueError("Direct Answer Scenario question is invalid")
        summary = scenario.input.get("conversation_summary")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("Direct Answer Scenario summary is invalid")
        command = DirectAnswerRunCommand(
            run=run,
            state=state,
            policy=self.profile.to_runtime_policy(),
            model_step_id=self.execution.model_step_id,
            final_step_id=self.execution.final_step_id,
            manifest_id=self.execution.manifest_id,
            user_question=question,
            conversation_summary=summary,
            conversation_summary_version=(None if summary is None else "scenario-summary-v1"),
        )
        runtime_context = TrustedRuntimeContext(
            principal=self.identity.principal,
            workspace_scope=self.identity.workspace_scope,
            capabilities=self.identity.capabilities,
            budget=budget,
            secret_references=self.identity.secret_references,
        )
        return MaterializedScenario(command=command, runtime_context=runtime_context)

    def _validate_scenario_versions(self, scenario: Scenario) -> None:
        if scenario.run_type is not AgentRunType.DIRECT_ANSWER:
            raise ValueError("Direct Answer materializer received another Run type")
        if scenario.profile.name != self.profile.profile_name or (
            scenario.profile.version != self.profile.profile_version
        ):
            raise ValueError("Scenario profile version is not configured")
        if scenario.runtime_version not in {"runtime-v0", "direct-answer-runtime-v0"}:
            raise ValueError("Scenario Runtime version is unsupported")
        if scenario.harness_version != self.harness_version:
            raise ValueError("Scenario Harness version is unsupported")
        if scenario.model_version != self.model_version:
            raise ValueError("Scenario model fixture version is unsupported")
        if scenario.prompt_version != self.profile.prompt_version:
            raise ValueError("Scenario prompt version is unsupported")
        if scenario.context_version != self.profile.context_compiler_version:
            raise ValueError("Scenario Context version is unsupported")
        if (
            scenario.toolset_version != self.profile.toolset_version
            or scenario.available_tools != self.profile.available_tools
        ):
            raise ValueError("Direct Answer Scenario must use the empty toolset")
