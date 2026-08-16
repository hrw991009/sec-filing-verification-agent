"""Registry execution boundaries for idempotency, cost, and retry metadata."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import pytest

from industry_platform.modules.agent_harness.tool_fakes import (
    FakeIndustryLookupTool,
    FakeLookupInput,
    FakeLookupOutput,
    FakeLookupRecord,
    fake_lookup_definition,
)
from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.tools.domain import (
    MAX_TOOL_COST_MICRO_USD,
    TOOL_OBSERVATION_NORMALIZER_VERSION,
    ToolAction,
    ToolApprovalOutcome,
    ToolCall,
    ToolDefinition,
    ToolObservation,
    ToolRetryClassification,
    ToolSideEffectClass,
    ToolSource,
)
from industry_platform.modules.tools.registry import (
    PydanticToolAdapter,
    RegistryToolExecutor,
    ToolExecutionError,
    ToolPreparationError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
STEP_ID = UUID("44444444-4444-4444-8444-444444444444")
USER_ID = UUID("55555555-5555-4555-8555-555555555555")
OBSERVATION_ID = UUID("66666666-6666-4666-8666-666666666666")
NOW = datetime(2026, 8, 16, 4, 0, tzinfo=UTC)
RAW_IDEMPOTENCY_KEY = "server-owned-write-key-v1"


def runtime_context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Tool Registry Workspace",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            role="member",
        ),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=4,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
    )


def prepare_call(
    registry: ToolRegistry,
    definition: ToolDefinition,
    *,
    idempotency_key: str | None,
) -> ToolCall:
    return registry.prepare(
        ToolRequestAudit(
            call_id=CALL_ID,
            action=ToolAction(
                schema_version=1,
                name=definition.name,
                version=definition.version,
                arguments={"query": "steel"},
            ),
        ),
        allowed_tools=(definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=runtime_context(),
        requested_at=NOW,
        idempotency_key=idempotency_key,
    )


@dataclass(slots=True)
class RecordingAdapter:
    definition: ToolDefinition
    actual_cost_micro_usd: int
    invocations: int = 0
    received_idempotency_keys: list[str | None] = field(default_factory=list)

    def validate_arguments(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return dict(arguments)

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
        self.invocations += 1
        self.received_idempotency_keys.append(idempotency_key)
        text = str(arguments["query"])
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return (
            ToolObservation(
                schema_version=1,
                observation_id=OBSERVATION_ID,
                call_id=call_id,
                run_id=run_id,
                workspace_id=runtime_context.workspace_scope.workspace_id,
                tool=self.definition.reference,
                normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
                model_text=text,
                sources=(
                    ToolSource(
                        source_type="test_fixture",
                        source_version="v1",
                        locator="fixture://registry/steel",
                        observed_at=observed_at,
                        content_sha256=content_sha256,
                    ),
                ),
                observed_at=observed_at,
                content_sha256=content_sha256,
            ),
            self.actual_cost_micro_usd,
        )


@dataclass(slots=True)
class CancellationResistantAdapter(RecordingAdapter):
    cancel_return_delay_seconds: float = 0.01
    release_after_cancel: asyncio.Event | None = None
    cancellation_requests: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)

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
            self.started.set()
            try:
                await asyncio.sleep(3_600)
            except asyncio.CancelledError:
                self.cancellation_requests += 1
                if self.release_after_cancel is None:
                    await asyncio.sleep(self.cancel_return_delay_seconds)
                else:
                    await self.release_after_cancel.wait()
            return await RecordingAdapter.execute(
                self,
                arguments,
                runtime_context,
                call_id=call_id,
                run_id=run_id,
                observed_at=observed_at,
                idempotency_key=idempotency_key,
            )
        finally:
            self.finished.set()


class PostInvokeFailureTool(PydanticToolAdapter[FakeLookupInput, FakeLookupOutput]):
    def __init__(self, failure: Literal["schema", "size", "normalize"]) -> None:
        definition = fake_lookup_definition(timeout_ms=1_000)
        if failure == "size":
            definition = replace(definition, max_result_bytes=1)
        super().__init__(
            definition=definition,
            input_model=FakeLookupInput,
            output_model=FakeLookupOutput,
        )
        self._failure = failure

    async def invoke(
        self,
        value: FakeLookupInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[FakeLookupOutput, int]:
        del value, runtime_context, idempotency_key
        if self._failure == "schema":
            return cast(
                FakeLookupOutput,
                {
                    "text": {"sensitive": "raw-provider-output"},
                    "locator": "fixture://registry/post-invoke",
                    "source_version": "v1",
                },
            ), 23
        return FakeLookupOutput(
            text="Steel demand rose.",
            locator="fixture://registry/post-invoke",
            source_version="v1",
        ), 23

    def normalize(
        self,
        value: FakeLookupOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        del value, runtime_context, call_id, run_id, observed_at
        raise ValueError("sensitive-normalizer-payload")


@dataclass(slots=True)
class MismatchedObservationAdapter(RecordingAdapter):
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
        observation, actual_cost_micro_usd = await RecordingAdapter.execute(
            self,
            arguments,
            runtime_context,
            call_id=call_id,
            run_id=run_id,
            observed_at=observed_at,
            idempotency_key=idempotency_key,
        )
        mismatched_observation = replace(
            observation,
            call_id=UUID("77777777-7777-4777-8777-777777777777"),
        )
        return mismatched_observation, actual_cost_micro_usd


def write_definition(*, max_cost_micro_usd: int = 10) -> ToolDefinition:
    return replace(
        fake_lookup_definition(),
        name="test.write_lookup",
        description="Record one deterministic idempotent test write.",
        side_effect_class=ToolSideEffectClass.IDEMPOTENT_WRITE,
        retry_classification=ToolRetryClassification.NEVER,
        max_cost_micro_usd=max_cost_micro_usd,
    )


@pytest.mark.asyncio
async def test_write_tool_receives_raw_idempotency_key_without_repr_leakage() -> None:
    definition = write_definition()
    adapter = RecordingAdapter(definition=definition, actual_cost_micro_usd=7)
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        call,
        runtime_context(),
    )

    assert call.side_effect_idempotency_key == RAW_IDEMPOTENCY_KEY
    assert (
        call.idempotency_key_sha256
        == hashlib.sha256(RAW_IDEMPOTENCY_KEY.encode("utf-8")).hexdigest()
    )
    assert RAW_IDEMPOTENCY_KEY not in repr(call)
    assert RAW_IDEMPOTENCY_KEY not in repr(result)
    assert adapter.received_idempotency_keys == [RAW_IDEMPOTENCY_KEY]
    assert result.actual_cost_micro_usd == 7


def test_read_only_tool_rejects_an_idempotency_key() -> None:
    tool = FakeIndustryLookupTool(
        {
            "steel": FakeLookupRecord(
                text="Steel demand rose.",
                locator="fixture://registry/steel",
                source_version="v1",
            )
        }
    )
    registry = ToolRegistry((tool,))

    with pytest.raises(ToolPreparationError) as exc_info:
        prepare_call(registry, tool.definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    assert exc_info.value.code == "tool_idempotency_key_unexpected"


@pytest.mark.parametrize(
    "idempotency_key",
    [
        "short",
        "valid-key-prefix-\n",
        "valid-key-prefix-\ud800",
        "界" * 171,
    ],
)
def test_write_tool_maps_invalid_idempotency_keys_to_a_stable_preparation_error(
    idempotency_key: str,
) -> None:
    definition = write_definition()
    registry = ToolRegistry((RecordingAdapter(definition=definition, actual_cost_micro_usd=0),))

    with pytest.raises(ToolPreparationError) as exc_info:
        prepare_call(registry, definition, idempotency_key=idempotency_key)

    assert exc_info.value.code == "tool_idempotency_key_invalid"
    assert exc_info.value.outcome.value == "deny"
    assert exc_info.value.definition == definition


def test_write_tool_accepts_a_multibyte_idempotency_key_within_the_byte_limit() -> None:
    definition = write_definition()
    registry = ToolRegistry((RecordingAdapter(definition=definition, actual_cost_micro_usd=0),))
    idempotency_key = "界" * 170

    call = prepare_call(registry, definition, idempotency_key=idempotency_key)

    assert call.idempotency_key_sha256 == hashlib.sha256(idempotency_key.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_cost_micro_usd", "expected_code"),
    [
        (11, "tool_cost_limit_exceeded"),
        (-1, "tool_cost_invalid"),
        (cast(int, True), "tool_cost_invalid"),
        (MAX_TOOL_COST_MICRO_USD + 1, "tool_cost_invalid"),
    ],
)
async def test_registry_rejects_invalid_or_over_limit_actual_cost_without_retry(
    actual_cost_micro_usd: int,
    expected_code: str,
) -> None:
    definition = write_definition(max_cost_micro_usd=10)
    adapter = RecordingAdapter(
        definition=definition,
        actual_cost_micro_usd=actual_cost_micro_usd,
    )
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
            call,
            runtime_context(),
        )

    assert exc_info.value.code == expected_code
    assert exc_info.value.actual_cost_micro_usd == (
        actual_cost_micro_usd if expected_code == "tool_cost_limit_exceeded" else 0
    )
    assert adapter.invocations == 1
    assert adapter.received_idempotency_keys == [RAW_IDEMPOTENCY_KEY]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("schema", "tool_output_invalid"),
        ("size", "tool_result_too_large"),
        ("normalize", "tool_output_invalid"),
    ],
)
async def test_post_invoke_failures_preserve_known_cost_without_leaking_output(
    failure: Literal["schema", "size", "normalize"],
    expected_code: str,
) -> None:
    adapter = PostInvokeFailureTool(failure)
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, adapter.definition, idempotency_key=None)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(call, runtime_context())

    assert exc_info.value.code == expected_code
    assert exc_info.value.actual_cost_micro_usd == 23
    assert "sensitive" not in str(exc_info.value)
    assert "sensitive" not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_mismatched_observation_is_a_stable_cost_preserving_execution_error() -> None:
    definition = write_definition()
    adapter = MismatchedObservationAdapter(definition=definition, actual_cost_micro_usd=7)
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(call, runtime_context())

    assert exc_info.value.code == "tool_output_invalid"
    assert exc_info.value.actual_cost_micro_usd == 7


@pytest.mark.asyncio
async def test_hard_timeout_rejects_a_late_success_and_preserves_its_known_cost() -> None:
    definition = replace(write_definition(), timeout_ms=5)
    adapter = CancellationResistantAdapter(
        definition=definition,
        actual_cost_micro_usd=7,
        cancel_return_delay_seconds=0.01,
    )
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(
            registry,
            clock=lambda: NOW,
            adapter_drain_timeout_seconds=0.1,
        ).execute(call, runtime_context())

    assert exc_info.value.code == "tool_timeout_after_completion"
    assert exc_info.value.actual_cost_micro_usd == 7
    assert adapter.cancellation_requests == 1
    assert adapter.invocations == 1


@pytest.mark.asyncio
async def test_hard_timeout_returns_unknown_when_adapter_outlives_the_bounded_drain() -> None:
    definition = replace(write_definition(), timeout_ms=5)
    release = asyncio.Event()
    adapter = CancellationResistantAdapter(
        definition=definition,
        actual_cost_micro_usd=7,
        release_after_cancel=release,
    )
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(
            registry,
            clock=lambda: NOW,
            adapter_drain_timeout_seconds=0.01,
        ).execute(call, runtime_context())

    assert exc_info.value.code == "tool_outcome_unknown"
    assert exc_info.value.actual_cost_micro_usd == 0
    assert adapter.cancellation_requests == 1
    assert adapter.invocations == 0

    release.set()
    await asyncio.wait_for(adapter.finished.wait(), timeout=0.1)
    assert adapter.invocations == 1


@pytest.mark.asyncio
async def test_external_cancellation_returns_a_known_drained_result_for_runtime_accounting() -> (
    None
):
    definition = replace(write_definition(), timeout_ms=1_000)
    adapter = CancellationResistantAdapter(
        definition=definition,
        actual_cost_micro_usd=7,
        cancel_return_delay_seconds=0.01,
    )
    registry = ToolRegistry((adapter,))
    call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)
    task = asyncio.create_task(
        RegistryToolExecutor(
            registry,
            clock=lambda: NOW,
            adapter_drain_timeout_seconds=0.1,
        ).execute(call, runtime_context())
    )
    await adapter.started.wait()

    task.cancel()
    result = await task

    assert result.actual_cost_micro_usd == 7
    assert adapter.cancellation_requests == 1
    assert adapter.invocations == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ToolExecutionError("token=secret"),
        lambda: ToolPreparationError(
            "token=secret",
            outcome=ToolApprovalOutcome.DENY,
        ),
    ],
)
def test_tool_errors_reject_noncanonical_audit_codes(
    factory: Callable[[], Exception],
) -> None:
    with pytest.raises(ValueError, match="error code"):
        factory()
