"""Trusted materialization of Day 3 L1 Scenarios for the unified Runtime."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_harness.direct_answer import HarnessTrustedIdentity
from industry_platform.modules.agent_harness.profiles import ToolL1Profile, ToolL2Profile
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
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L1_RUNTIME_VERSION,
    TOOL_L2_RUNTIME_VERSION,
    ToolL1RunCommand,
    ToolL2RunCommand,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.tools.domain import ToolReference


@dataclass(frozen=True, slots=True)
class ToolL1HarnessExecutionIdentity:
    """Server-created deterministic IDs, never accepted from Scenario JSON."""

    run_id: UUID
    stream_id: UUID
    action_model_step_id: UUID
    tool_step_id: UUID
    answer_model_step_id: UUID
    final_step_id: UUID
    action_manifest_id: UUID
    answer_manifest_id: UUID
    tool_call_id: UUID
    approval_request_id: UUID
    trace_id: TraceId
    created_at: datetime

    def __post_init__(self) -> None:
        identifiers = (
            self.run_id,
            self.stream_id,
            self.action_model_step_id,
            self.tool_step_id,
            self.answer_model_step_id,
            self.final_step_id,
            self.action_manifest_id,
            self.answer_manifest_id,
            self.tool_call_id,
            self.approval_request_id,
        )
        for identifier in identifiers:
            require_non_nil_uuid(identifier, field_name="Tool L1 Harness execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Tool L1 Harness execution IDs must be distinct")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Tool L1 Harness Trace ID is invalid")
        require_utc(self.created_at, field_name="Tool L1 Harness creation time")


@dataclass(frozen=True, slots=True)
class ToolL1ScenarioMaterializer:
    """Resolve trusted profile/identity around a serialized L1 Scenario."""

    profile: ToolL1Profile
    execution: ToolL1HarnessExecutionIdentity
    identity: HarnessTrustedIdentity = field(repr=False)
    model_version: str
    harness_version: str

    def materialize(
        self,
        scenario: Scenario,
    ) -> MaterializedScenario[ToolL1RunCommand, TrustedRuntimeContext]:
        self._validate_scenario_versions(scenario)
        budget = scenario.budget.materialize(started_at=self.execution.created_at)
        run = AgentRun(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            run_id=self.execution.run_id,
            event_stream_id=self.execution.stream_id,
            workspace_id=self.identity.workspace_scope.workspace_id,
            user_id=self.identity.workspace_scope.user_id,
            run_type=AgentRunType.TOOL_LOOP,
            runtime_version=TOOL_L1_RUNTIME_VERSION,
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
        question = scenario.input["question"]
        summary = scenario.input.get("conversation_summary")
        if not isinstance(question, str) or (summary is not None and not isinstance(summary, str)):
            raise ValueError("Tool L1 Scenario input is invalid")
        command = ToolL1RunCommand(
            run=run,
            state=RunState(
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
            ),
            policy=self.profile.to_runtime_policy(),
            action_model_step_id=self.execution.action_model_step_id,
            tool_step_id=self.execution.tool_step_id,
            answer_model_step_id=self.execution.answer_model_step_id,
            final_step_id=self.execution.final_step_id,
            action_manifest_id=self.execution.action_manifest_id,
            answer_manifest_id=self.execution.answer_manifest_id,
            tool_call_id=self.execution.tool_call_id,
            approval_request_id=self.execution.approval_request_id,
            user_question=question,
            conversation_summary=summary,
            conversation_summary_version=(None if summary is None else "scenario-summary-v1"),
        )
        return MaterializedScenario(
            command=command,
            runtime_context=TrustedRuntimeContext(
                principal=self.identity.principal,
                workspace_scope=self.identity.workspace_scope,
                capabilities=self.identity.capabilities,
                budget=budget,
                secret_references=self.identity.secret_references,
            ),
        )

    def _validate_scenario_versions(self, scenario: Scenario) -> None:
        if scenario.run_type is not AgentRunType.TOOL_LOOP:
            raise ValueError("Tool L1 materializer received another Run type")
        if scenario.profile.name != self.profile.profile_name or (
            scenario.profile.version != self.profile.profile_version
        ):
            raise ValueError("Scenario Tool L1 profile version is not configured")
        if scenario.runtime_version != TOOL_L1_RUNTIME_VERSION:
            raise ValueError("Scenario Tool L1 Runtime version is unsupported")
        if scenario.harness_version != self.harness_version:
            raise ValueError("Scenario Harness version is unsupported")
        if scenario.model_version != self.model_version:
            raise ValueError("Scenario model fixture version is unsupported")
        if scenario.prompt_version != self.profile.prompt_version:
            raise ValueError("Scenario Tool L1 prompt version is unsupported")
        if scenario.context_version != self.profile.context_compiler_version:
            raise ValueError("Scenario Tool L1 Context version is unsupported")
        scenario_tools = tuple(
            ToolReference(reference.name, reference.version)
            for reference in scenario.available_tools
        )
        if (
            scenario.toolset_version != self.profile.toolset_version
            or scenario_tools != self.profile.available_tools
        ):
            raise ValueError("Scenario Tool surface is not the configured trusted surface")


@dataclass(frozen=True, slots=True)
class ToolL2HarnessExecutionIdentity:
    """Server-created bounded ID pool for one deterministic L2 Scenario."""

    run_id: UUID
    stream_id: UUID
    decision_model_step_ids: tuple[UUID, ...]
    tool_step_ids: tuple[UUID, ...]
    decision_manifest_ids: tuple[UUID, ...]
    tool_call_ids: tuple[UUID, ...]
    approval_request_ids: tuple[UUID, ...]
    final_step_id: UUID
    trace_id: TraceId
    created_at: datetime

    def __post_init__(self) -> None:
        identifiers = (
            self.run_id,
            self.stream_id,
            *self.decision_model_step_ids,
            *self.tool_step_ids,
            *self.decision_manifest_ids,
            *self.tool_call_ids,
            *self.approval_request_ids,
            self.final_step_id,
        )
        for identifier in identifiers:
            require_non_nil_uuid(identifier, field_name="Tool L2 Harness execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Tool L2 Harness execution IDs must be distinct")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Tool L2 Harness Trace ID is invalid")
        require_utc(self.created_at, field_name="Tool L2 Harness creation time")


@dataclass(frozen=True, slots=True)
class ToolL2ScenarioMaterializer:
    """Resolve trusted profile and bounded ID pools around one L2 Scenario."""

    profile: ToolL2Profile
    execution: ToolL2HarnessExecutionIdentity
    identity: HarnessTrustedIdentity = field(repr=False)
    model_version: str
    harness_version: str

    def materialize(
        self,
        scenario: Scenario,
    ) -> MaterializedScenario[ToolL2RunCommand, TrustedRuntimeContext]:
        self._validate_scenario_versions(scenario)
        budget = scenario.budget.materialize(started_at=self.execution.created_at)
        run = AgentRun(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            run_id=self.execution.run_id,
            event_stream_id=self.execution.stream_id,
            workspace_id=self.identity.workspace_scope.workspace_id,
            user_id=self.identity.workspace_scope.user_id,
            run_type=AgentRunType.TOOL_LOOP,
            runtime_version=TOOL_L2_RUNTIME_VERSION,
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
        question = scenario.input["question"]
        summary = scenario.input.get("conversation_summary")
        if not isinstance(question, str) or (summary is not None and not isinstance(summary, str)):
            raise ValueError("Tool L2 Scenario input is invalid")
        command = ToolL2RunCommand(
            run=run,
            state=RunState(
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
            ),
            policy=self.profile.to_runtime_policy(),
            decision_model_step_ids=self.execution.decision_model_step_ids,
            tool_step_ids=self.execution.tool_step_ids,
            decision_manifest_ids=self.execution.decision_manifest_ids,
            tool_call_ids=self.execution.tool_call_ids,
            approval_request_ids=self.execution.approval_request_ids,
            final_step_id=self.execution.final_step_id,
            user_question=question,
            conversation_summary=summary,
            conversation_summary_version=(None if summary is None else "scenario-summary-v1"),
            side_effect_idempotency_keys=(None,) * self.profile.max_tool_calls,
        )
        return MaterializedScenario(
            command=command,
            runtime_context=TrustedRuntimeContext(
                principal=self.identity.principal,
                workspace_scope=self.identity.workspace_scope,
                capabilities=self.identity.capabilities,
                budget=budget,
                secret_references=self.identity.secret_references,
            ),
        )

    def _validate_scenario_versions(self, scenario: Scenario) -> None:
        if scenario.run_type is not AgentRunType.TOOL_LOOP:
            raise ValueError("Tool L2 materializer received another Run type")
        if scenario.profile.name != self.profile.profile_name or (
            scenario.profile.version != self.profile.profile_version
        ):
            raise ValueError("Scenario Tool L2 profile version is not configured")
        if scenario.runtime_version != TOOL_L2_RUNTIME_VERSION:
            raise ValueError("Scenario Tool L2 Runtime version is unsupported")
        if scenario.harness_version != self.harness_version:
            raise ValueError("Scenario Harness version is unsupported")
        if scenario.model_version != self.model_version:
            raise ValueError("Scenario model fixture version is unsupported")
        if scenario.prompt_version != self.profile.prompt_version:
            raise ValueError("Scenario Tool L2 prompt version is unsupported")
        if scenario.context_version != self.profile.context_compiler_version:
            raise ValueError("Scenario Tool L2 Context version is unsupported")
        scenario_tools = tuple(
            ToolReference(reference.name, reference.version)
            for reference in scenario.available_tools
        )
        if (
            scenario.toolset_version != self.profile.toolset_version
            or scenario_tools != self.profile.available_tools
        ):
            raise ValueError("Scenario Tool L2 surface is not the configured trusted surface")
