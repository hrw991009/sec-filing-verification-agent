"""Server-owned typed Tool Registry, static policy check, and bounded Executor."""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    require_utc,
)
from industry_platform.modules.tools.domain import (
    MAX_TOOL_COST_MICRO_USD,
    ToolAction,
    ToolApprovalOutcome,
    ToolApprovalPolicy,
    ToolCall,
    ToolDefinition,
    ToolExecutionResult,
    ToolObservation,
    ToolPolicyDecision,
    ToolReference,
    ToolSideEffectClass,
    canonical_mapping_sha256,
    sanitized_arguments_summary,
    side_effect_idempotency_key_sha256,
    tool_references,
)

_TOOL_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")
TOOL_ADAPTER_DRAIN_TIMEOUT_SECONDS = 1.0


def _require_stable_tool_error_code(code: str) -> str:
    if not isinstance(code, str) or not _TOOL_ERROR_CODE_PATTERN.fullmatch(code):
        raise ValueError("Tool error code is invalid")
    return code


def _auditable_tool_cost(value: object) -> int:
    """Keep a returned cost only when it is safe for durable failure accounting."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_TOOL_COST_MICRO_USD
    ):
        return 0
    return value


def _consume_adapter_task_result(
    task: asyncio.Task[tuple[ToolObservation, int]],
) -> None:
    """Observe a detached cancellation-resistant Adapter without leaking its exception."""

    with suppress(asyncio.CancelledError, Exception):
        task.result()


class ToolPreparationError(RuntimeError):
    """Stable fail-closed result of schema, surface, capability, or approval checks."""

    def __init__(
        self,
        code: str,
        *,
        outcome: ToolApprovalOutcome,
        definition: ToolDefinition | None = None,
    ) -> None:
        super().__init__("Tool request could not be prepared")
        self.code = _require_stable_tool_error_code(code)
        self.outcome = outcome
        self.definition = definition


class ToolExecutionError(RuntimeError):
    """Sanitized execution failure safe to map to a stable Runtime stop reason."""

    def __init__(self, code: str, *, actual_cost_micro_usd: int = 0) -> None:
        super().__init__("Tool execution failed")
        self.code = _require_stable_tool_error_code(code)
        if (
            isinstance(actual_cost_micro_usd, bool)
            or not isinstance(actual_cost_micro_usd, int)
            or not 0 <= actual_cost_micro_usd <= MAX_TOOL_COST_MICRO_USD
        ):
            raise ValueError("Tool execution error cost is invalid")
        self.actual_cost_micro_usd = actual_cost_micro_usd


class RegisteredToolAdapter(Protocol):
    """Erased heterogeneous boundary stored by ToolRegistry."""

    @property
    def definition(self) -> ToolDefinition: ...

    def validate_arguments(self, arguments: Mapping[str, object]) -> Mapping[str, object]: ...

    async def execute(
        self,
        arguments: Mapping[str, object],
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
        idempotency_key: str | None,
    ) -> tuple[ToolObservation, int]: ...


class PydanticToolAdapter[InputT: BaseModel, OutputT: BaseModel](ABC):
    """Reusable strict typed adapter: validate input, invoke, validate output, normalize."""

    def __init__(
        self,
        *,
        definition: ToolDefinition,
        input_model: type[InputT],
        output_model: type[OutputT],
    ) -> None:
        self._definition = definition
        self._input_model = input_model
        self._output_model = output_model
        for schema, model, field_name in (
            (definition.input_schema, input_model, "input"),
            (definition.output_schema, output_model, "output"),
        ):
            properties = schema.get("properties")
            if not isinstance(properties, Mapping) or set(properties) != set(model.model_fields):
                raise ValueError(f"Tool Definition {field_name} fields do not match its model")
            if model.model_config.get("extra") != "forbid":
                raise ValueError(f"Tool {field_name} model must reject extra fields")

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def validate_arguments(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        try:
            validated = self._input_model.model_validate(dict(arguments), strict=True)
        except ValidationError:
            raise ToolPreparationError(
                "tool_arguments_invalid",
                outcome=ToolApprovalOutcome.DENY,
                definition=self.definition,
            ) from None
        return cast(Mapping[str, object], validated.model_dump(mode="json"))

    async def execute(
        self,
        arguments: Mapping[str, object],
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
        idempotency_key: str | None,
    ) -> tuple[ToolObservation, int]:
        try:
            input_value = self._input_model.model_validate(dict(arguments), strict=True)
            raw_output, actual_cost_micro_usd = await self.invoke(
                input_value,
                runtime_context,
                idempotency_key=idempotency_key,
            )
        except ToolExecutionError:
            raise
        except (ValidationError, ValueError, TypeError):
            raise ToolExecutionError("tool_output_invalid") from None

        auditable_cost_micro_usd = _auditable_tool_cost(actual_cost_micro_usd)
        try:
            output_value = self._output_model.model_validate(raw_output, strict=True)
            encoded_output = json.dumps(
                output_value.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(encoded_output) > self.definition.max_result_bytes:
                raise ToolExecutionError(
                    "tool_result_too_large",
                    actual_cost_micro_usd=auditable_cost_micro_usd,
                )
            return (
                self.normalize(
                    output_value,
                    runtime_context,
                    call_id=call_id,
                    run_id=run_id,
                    observed_at=observed_at,
                ),
                actual_cost_micro_usd,
            )
        except ToolExecutionError as error:
            raise ToolExecutionError(
                error.code,
                actual_cost_micro_usd=auditable_cost_micro_usd,
            ) from None
        except Exception:
            raise ToolExecutionError(
                "tool_output_invalid",
                actual_cost_micro_usd=auditable_cost_micro_usd,
            ) from None

    @abstractmethod
    async def invoke(
        self,
        value: InputT,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[OutputT, int]:
        """Call one external or application capability using trusted dependencies."""

    @abstractmethod
    def normalize(
        self,
        value: OutputT,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        """Discard raw output and return the bounded Observation contract."""


@dataclass(frozen=True, slots=True)
class ToolRequestAudit:
    """Safe facts available immediately after decoding a model Action."""

    call_id: UUID
    action: ToolAction = field(repr=False)

    @property
    def arguments_sha256(self) -> str:
        return canonical_mapping_sha256(self.action.arguments)

    @property
    def sanitized_input_summary(self) -> Mapping[str, object]:
        return sanitized_arguments_summary(self.action.arguments)


class ToolRegistry:
    """Resolve exact allowlisted versions; never let an Action enlarge its surface."""

    def __init__(self, adapters: Sequence[RegisteredToolAdapter]) -> None:
        selected = tuple(adapters)
        by_key = {
            (adapter.definition.name, adapter.definition.version): adapter for adapter in selected
        }
        if not selected or len(by_key) != len(selected):
            raise ValueError("Tool Registry requires unique registered definitions")
        self._by_key = by_key

    def definition(self, reference: ToolReference) -> ToolDefinition | None:
        adapter = self._by_key.get((reference.name, reference.version))
        return None if adapter is None else adapter.definition

    def prepare(
        self,
        audit: ToolRequestAudit,
        *,
        allowed_tools: Sequence[ToolReference],
        run_id: UUID,
        requested_by_step_id: UUID,
        runtime_context: TrustedRuntimeContext,
        requested_at: datetime,
        idempotency_key: str | None = None,
    ) -> ToolCall:
        surface = tool_references(allowed_tools)
        requested = ToolReference(audit.action.name, audit.action.version)
        adapter = self._by_key.get((requested.name, requested.version))
        if requested not in surface or adapter is None:
            raise ToolPreparationError(
                "tool_not_allowed",
                outcome=ToolApprovalOutcome.DENY,
                definition=None if adapter is None else adapter.definition,
            )
        definition = adapter.definition
        if definition.capability not in runtime_context.capabilities:
            raise ToolPreparationError(
                "tool_capability_denied",
                outcome=ToolApprovalOutcome.DENY,
                definition=definition,
            )
        if runtime_context.workspace_scope.workspace_id.int == 0:
            raise ToolPreparationError(
                "tool_scope_invalid",
                outcome=ToolApprovalOutcome.DENY,
                definition=definition,
            )
        validated_arguments = adapter.validate_arguments(audit.action.arguments)
        if definition.approval_policy is ToolApprovalPolicy.AUTO_DENY:
            raise ToolPreparationError(
                "tool_policy_denied",
                outcome=ToolApprovalOutcome.DENY,
                definition=definition,
            )
        if definition.approval_policy is ToolApprovalPolicy.REQUIRE_APPROVAL:
            raise ToolPreparationError(
                "tool_approval_required",
                outcome=ToolApprovalOutcome.APPROVAL_REQUIRED,
                definition=definition,
            )
        if definition.side_effect_class is ToolSideEffectClass.READ_ONLY:
            if idempotency_key is not None:
                raise ToolPreparationError(
                    "tool_idempotency_key_unexpected",
                    outcome=ToolApprovalOutcome.DENY,
                    definition=definition,
                )
            idempotency_key_sha256 = None
        else:
            if idempotency_key is None:
                raise ToolPreparationError(
                    "tool_idempotency_key_required",
                    outcome=ToolApprovalOutcome.DENY,
                    definition=definition,
                )
            try:
                idempotency_key_sha256 = side_effect_idempotency_key_sha256(idempotency_key)
            except ValueError:
                raise ToolPreparationError(
                    "tool_idempotency_key_invalid",
                    outcome=ToolApprovalOutcome.DENY,
                    definition=definition,
                ) from None
        return ToolCall(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            call_id=audit.call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            requested_by_step_id=requested_by_step_id,
            requested_by_user_id=runtime_context.principal.user_id,
            definition=definition,
            arguments=validated_arguments,
            decision=ToolPolicyDecision(
                outcome=ToolApprovalOutcome.ALLOW,
                policy_version=definition.policy_version,
                reason_code="static_policy_allowed",
            ),
            requested_at=requested_at,
            side_effect_idempotency_key=idempotency_key,
            idempotency_key_sha256=idempotency_key_sha256,
        )

    def adapter_for(self, call: ToolCall) -> RegisteredToolAdapter:
        adapter = self._by_key.get((call.definition.name, call.definition.version))
        if adapter is None or adapter.definition != call.definition:
            raise ToolExecutionError("tool_registration_changed")
        return adapter


class RegistryToolExecutor:
    """Execute one already-authorized call with a hard timeout and output validation."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        adapter_drain_timeout_seconds: float = TOOL_ADAPTER_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(adapter_drain_timeout_seconds, bool)
            or not isinstance(adapter_drain_timeout_seconds, (int, float))
            or not 0 < adapter_drain_timeout_seconds <= TOOL_ADAPTER_DRAIN_TIMEOUT_SECONDS
        ):
            raise ValueError("Tool Adapter drain timeout is invalid")
        self._registry = registry
        self._clock = clock
        self._adapter_drain_timeout_seconds = float(adapter_drain_timeout_seconds)

    async def execute(
        self,
        call: ToolCall,
        runtime_context: TrustedRuntimeContext,
    ) -> ToolExecutionResult:
        if (
            call.workspace_id != runtime_context.workspace_scope.workspace_id
            or call.requested_by_user_id != runtime_context.principal.user_id
            or call.definition.capability not in runtime_context.capabilities
        ):
            raise ToolExecutionError("tool_scope_changed")
        adapter = self._registry.adapter_for(call)
        started_at = self._clock()
        require_utc(started_at, field_name="Tool Executor clock value")
        event_loop = asyncio.get_running_loop()
        timeout_at = event_loop.time() + call.definition.timeout_ms / 1_000
        adapter_finished_at: float | None = None

        async def invoke_adapter() -> tuple[ToolObservation, int]:
            nonlocal adapter_finished_at
            try:
                return await adapter.execute(
                    call.arguments,
                    runtime_context,
                    call_id=call.call_id,
                    run_id=call.run_id,
                    observed_at=started_at,
                    idempotency_key=call.side_effect_idempotency_key,
                )
            finally:
                adapter_finished_at = event_loop.time()

        task = asyncio.create_task(invoke_adapter())
        drain_deadline: float | None = None

        async def enforce_timeout() -> ToolExecutionResult:
            nonlocal drain_deadline
            done, _pending = await asyncio.wait(
                (task,),
                timeout=max(0.0, timeout_at - event_loop.time()),
            )
            completed_before_timeout = (
                task in done
                and adapter_finished_at is not None
                and adapter_finished_at < timeout_at
            )
            if completed_before_timeout:
                return self._completed_result(
                    task,
                    call=call,
                    started_at=started_at,
                )

            if not task.done():
                task.cancel()
            drain_deadline = event_loop.time() + self._adapter_drain_timeout_seconds
            done, _pending = await asyncio.wait(
                (task,),
                timeout=max(0.0, drain_deadline - event_loop.time()),
            )
            if task not in done:
                task.add_done_callback(_consume_adapter_task_result)
                raise ToolExecutionError("tool_outcome_unknown")
            if task.cancelled():
                raise ToolExecutionError("tool_timeout")
            try:
                result = self._completed_result(
                    task,
                    call=call,
                    started_at=started_at,
                )
            except ToolExecutionError as error:
                raise ToolExecutionError(
                    "tool_timeout_after_failure",
                    actual_cost_micro_usd=error.actual_cost_micro_usd,
                ) from None
            raise ToolExecutionError(
                "tool_timeout_after_completion",
                actual_cost_micro_usd=result.actual_cost_micro_usd,
            )

        try:
            return await enforce_timeout()
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            remaining_drain_seconds = (
                self._adapter_drain_timeout_seconds
                if drain_deadline is None
                else max(0.0, drain_deadline - event_loop.time())
            )
            done, _pending = await asyncio.wait(
                (task,),
                timeout=remaining_drain_seconds,
            )
            if task not in done:
                task.add_done_callback(_consume_adapter_task_result)
                raise ToolExecutionError("tool_outcome_unknown") from None
            if task.cancelled():
                raise
            return self._completed_result(
                task,
                call=call,
                started_at=started_at,
            )

    def _completed_result(
        self,
        task: asyncio.Task[tuple[ToolObservation, int]],
        *,
        call: ToolCall,
        started_at: datetime,
    ) -> ToolExecutionResult:
        try:
            observation, actual_cost_micro_usd = task.result()
        except ToolExecutionError:
            raise
        except Exception:
            raise ToolExecutionError("tool_adapter_error") from None
        if (
            isinstance(actual_cost_micro_usd, bool)
            or not isinstance(actual_cost_micro_usd, int)
            or not 0 <= actual_cost_micro_usd <= MAX_TOOL_COST_MICRO_USD
        ):
            raise ToolExecutionError("tool_cost_invalid")
        if actual_cost_micro_usd > call.definition.max_cost_micro_usd:
            raise ToolExecutionError(
                "tool_cost_limit_exceeded",
                actual_cost_micro_usd=actual_cost_micro_usd,
            )
        completed_at = self._clock()
        require_utc(completed_at, field_name="Tool Executor clock value")
        completed_at = max(completed_at, observation.observed_at, started_at)
        try:
            return ToolExecutionResult(
                call=call,
                observation=observation,
                actual_cost_micro_usd=actual_cost_micro_usd,
                completed_at=completed_at,
                duration_ms=max(0, int((completed_at - started_at).total_seconds() * 1_000)),
            )
        except (TypeError, ValueError):
            raise ToolExecutionError(
                "tool_output_invalid",
                actual_cost_micro_usd=actual_cost_micro_usd,
            ) from None
