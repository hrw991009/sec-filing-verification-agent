"""The single concrete Day 2 L0 Runtime used by production and Harness callers."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_runtime.context import (
    ContextBudgetExceededError,
    ContextCompilationInput,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    AgentStepStatus,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.final_output import DirectAnswerFinalOutput
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelResponse,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelStreamItem,
    validate_model_stream,
)
from industry_platform.modules.agent_runtime.ports import (
    AgentEventCommitter,
    CancellationProbe,
    ContextCompiler,
    ContextManifestStore,
    ContextManifestStoreError,
    ModelProvider,
)
from industry_platform.modules.agent_runtime.provider_errors import (
    ModelProviderError,
    ModelProviderErrorCode,
)
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRunCommand
from industry_platform.modules.agent_runtime.runtime_support import (
    RuntimeDeadlineExceeded,
    RuntimeTransitionSupport,
    utc_now,
)
from industry_platform.modules.agent_runtime.state import (
    exhausted_budget_reason,
    validate_run_state,
    validate_state_transition,
)

MODEL_STREAM_CANCEL_POLL_SECONDS = 0.1
MODEL_STREAM_CLOSE_TIMEOUT_SECONDS = 1.0


async def _next_model_item(iterator: AsyncIterator[ModelStreamItem]) -> ModelStreamItem:
    return await iterator.__anext__()


async def _close_model_stream(stream: AsyncIterator[ModelStreamItem] | None) -> bool:
    """Close Provider resources without allowing cleanup to hang the Runtime."""

    close = None if stream is None else getattr(stream, "aclose", None)
    if close is None:
        return True
    try:
        await asyncio.wait_for(close(), timeout=MODEL_STREAM_CLOSE_TIMEOUT_SECONDS)
    except Exception:
        return False
    return True


class DirectAnswerRuntime(RuntimeTransitionSupport):
    """Run one no-tool model stream; production must consume it outside SSE requests."""

    def __init__(
        self,
        *,
        context_compiler: ContextCompiler,
        context_manifest_store: ContextManifestStore,
        model_provider: ModelProvider,
        event_committer: AgentEventCommitter,
        cancellation_probe: CancellationProbe,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._context_compiler = context_compiler
        self._context_manifest_store = context_manifest_store
        self._model_provider = model_provider
        super().__init__(
            event_committer=event_committer,
            cancellation_probe=cancellation_probe,
            clock=clock,
        )

    async def run(
        self,
        command: DirectAnswerRunCommand,
        runtime_context: TrustedRuntimeContext,
    ) -> AsyncGenerator[AgentEvent]:
        """Execute one committed Event stream without tools or implicit retries."""

        run = command.run
        state = command.state
        if (
            runtime_context.principal.user_id != run.user_id
            or runtime_context.workspace_scope.workspace_id != run.workspace_id
            or runtime_context.budget != run.budget
        ):
            raise ValueError("Trusted Runtime Context does not match the Direct Answer Run")
        initial_time = self._time(not_before=run.created_at)
        events: list[AgentEvent] = []
        queued = self._event(
            run,
            events,
            event_type=AgentEventType.RUN_QUEUED,
            occurred_at=run.created_at,
            payload={
                "run_type": run.run_type.value,
                "runtime_version": run.runtime_version,
                "harness_version": run.harness_version,
            },
        )
        await self._commit(events, queued)
        yield queued

        if initial_time >= run.budget.deadline:
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=initial_time,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if await self._cancel_requested(run):
            cancelled_at = self._time(not_before=events[-1].occurred_at)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=cancelled_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        try:
            started_at = self._before_deadline(
                run,
                not_before=events[-1].occurred_at,
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        run = replace(
            run,
            status=AgentRunStatus.RUNNING,
            state_revision=1,
            started_at=started_at,
        )
        state = replace(
            state,
            revision=1,
            status=AgentRunStatus.RUNNING,
            event_count=2,
            updated_at=started_at,
        )
        validate_state_transition(command.state, state, expected_revision=0)
        validate_run_state(run, state)
        started = self._event(
            run,
            events,
            event_type=AgentEventType.RUN_STARTED,
            occurred_at=started_at,
            payload={"state_revision": state.revision},
        )
        await self._commit(events, started)
        yield started

        try:
            model_started_at = self._before_deadline(
                run,
                not_before=events[-1].occurred_at,
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        model_step = AgentStep(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            step_id=command.model_step_id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            sequence=1,
            kind=AgentStepKind.MODEL,
            status=AgentStepStatus.RUNNING,
            state_revision=2,
            started_at=model_started_at,
            input_summary={
                "profile_version": command.policy.profile_version,
                "prompt_version": command.policy.prompt_version,
                "context_compiler_version": command.policy.context_compiler_version,
                "model": command.policy.model,
            },
        )
        step_started = self._event(
            run,
            events,
            event_type=AgentEventType.STEP_STARTED,
            occurred_at=model_started_at,
            payload={
                "step_id": str(model_step.step_id),
                "step_sequence": model_step.sequence,
                "step_kind": model_step.kind.value,
            },
        )
        state_before_model = state
        state = replace(
            state,
            revision=2,
            step_count=1,
            event_count=len(events) + 1,
            updated_at=model_started_at,
        )
        run = replace(run, state_revision=2)
        validate_state_transition(state_before_model, state, expected_revision=1)
        validate_run_state(run, state)
        await self._commit(events, step_started)
        yield step_started

        try:
            compiled_at = self._before_deadline(run, not_before=model_started_at)
            compiled = self._context_compiler.compile(
                ContextCompilationInput(
                    manifest_id=command.manifest_id,
                    run=run,
                    step=model_step,
                    state=state,
                    runtime_context=runtime_context,
                    compiler_version=command.policy.context_compiler_version,
                    prompt_version=command.policy.prompt_version,
                    model=command.policy.model,
                    system_instructions=command.policy.system_instructions,
                    user_question=command.user_question,
                    max_input_tokens=command.policy.max_input_tokens,
                    max_output_tokens=command.policy.max_output_tokens,
                    compiled_at=compiled_at,
                    conversation_summary=command.conversation_summary,
                    conversation_summary_version=command.conversation_summary_version,
                    attachments=command.attachments,
                    short_term_memory=command.memory_context.short_term,
                    long_term_memories=command.memory_context.long_term,
                )
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=RunStopReason.DEADLINE_EXCEEDED.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(model_step.step_id),
                    "error_code": RunStopReason.DEADLINE_EXCEEDED.value,
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        except ContextBudgetExceededError:
            failed_at = self._time(not_before=events[-1].occurred_at)
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=RunStopReason.TOKEN_BUDGET_EXCEEDED.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(model_step.step_id),
                    "error_code": RunStopReason.TOKEN_BUDGET_EXCEEDED.value,
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.TOKEN_BUDGET_EXCEEDED,
                occurred_at=failed_at,
                token_budget_preflight_rejected=True,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        except ValueError:
            failed_at = self._time(not_before=events[-1].occurred_at)
            error_code = "context_compile_error"
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=error_code,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={"step_id": str(model_step.step_id), "error_code": error_code},
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.RUNTIME_ERROR,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        try:
            await self._context_manifest_store.save(compiled.manifest)
        except ContextManifestStoreError:
            failed_at = self._time(not_before=events[-1].occurred_at)
            error_code = "context_manifest_error"
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=error_code,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={"step_id": str(model_step.step_id), "error_code": error_code},
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.RUNTIME_ERROR,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        if await self._cancel_requested(run):
            cancelled_at = self._time(not_before=events[-1].occurred_at)
            cancelled_step = self._settled_step(
                model_step,
                status=AgentStepStatus.CANCELLED,
                revision=3,
                completed_at=cancelled_at,
            )
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(cancelled_step,),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=cancelled_at,
                terminal_details={
                    "cancelled_step_id": str(model_step.step_id),
                    "cancelled_step_status": cancelled_step.status.value,
                },
            )
            await self._commit(events, terminal)
            yield terminal
            return

        try:
            provider_started_at = self._before_deadline(
                run,
                not_before=events[-1].occurred_at,
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=RunStopReason.DEADLINE_EXCEEDED.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(model_step.step_id),
                    "error_code": RunStopReason.DEADLINE_EXCEEDED.value,
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        model_started = self._event(
            run,
            events,
            event_type=AgentEventType.MODEL_STARTED,
            occurred_at=provider_started_at,
            payload={
                "step_id": str(model_step.step_id),
                "model": command.policy.model,
                "context_manifest_id": str(command.manifest_id),
            },
        )
        await self._commit(events, model_started)
        yield model_started

        model_items: list[ModelStreamItem] = []
        provider_error: ModelProviderError | None = None
        cancelled_during_stream = False
        deadline_exceeded_during_stream = False
        stream: AsyncIterator[ModelStreamItem] | None = None
        pending_item: asyncio.Task[ModelStreamItem] | None = None
        stream_closed_cleanly = True
        try:
            stream = self._model_provider.stream(compiled.request)
            iterator = stream.__aiter__()
            while True:
                pending_item = asyncio.create_task(_next_model_item(iterator))
                while not pending_item.done():
                    await asyncio.wait(
                        (pending_item,),
                        timeout=MODEL_STREAM_CANCEL_POLL_SECONDS,
                    )
                    if pending_item.done():
                        break
                    if await self._cancel_requested(run):
                        cancelled_during_stream = True
                        pending_item.cancel()
                        await asyncio.gather(pending_item, return_exceptions=True)
                        break
                    try:
                        self._before_deadline(run, not_before=events[-1].occurred_at)
                    except RuntimeDeadlineExceeded:
                        deadline_exceeded_during_stream = True
                        pending_item.cancel()
                        await asyncio.gather(pending_item, return_exceptions=True)
                        break
                if cancelled_during_stream or deadline_exceeded_during_stream:
                    break
                try:
                    item = pending_item.result()
                except StopAsyncIteration:
                    break
                try:
                    item_at = self._before_deadline(run, not_before=events[-1].occurred_at)
                except RuntimeDeadlineExceeded:
                    deadline_exceeded_during_stream = True
                    break
                model_items.append(item)
                if isinstance(item, ModelStreamDelta):
                    delta = self._event(
                        run,
                        events,
                        event_type=AgentEventType.MODEL_DELTA,
                        occurred_at=item_at,
                        payload={
                            "step_id": str(model_step.step_id),
                            "model_sequence": item.sequence,
                            "delta": item.text,
                        },
                    )
                    await self._commit(events, delta)
                    yield delta
                    if await self._cancel_requested(run):
                        cancelled_during_stream = True
                        break
        except ModelProviderError as error:
            provider_error = error
        finally:
            if pending_item is not None and not pending_item.done():
                pending_item.cancel()
                await asyncio.gather(pending_item, return_exceptions=True)
            stream_closed_cleanly = await _close_model_stream(stream)

        if deadline_exceeded_during_stream:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                error_code=RunStopReason.DEADLINE_EXCEEDED.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(model_step.step_id),
                    "error_code": RunStopReason.DEADLINE_EXCEEDED.value,
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if cancelled_during_stream:
            cancelled_at = self._time(not_before=events[-1].occurred_at)
            cancelled_step = self._settled_step(
                model_step,
                status=AgentStepStatus.CANCELLED,
                revision=3,
                completed_at=cancelled_at,
            )
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(cancelled_step,),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=cancelled_at,
                terminal_details={
                    "cancelled_step_id": str(model_step.step_id),
                    "cancelled_step_status": cancelled_step.status.value,
                },
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if not stream_closed_cleanly and provider_error is None:
            provider_error = ModelProviderError(ModelProviderErrorCode.INVALID_RESPONSE)

        response: ModelResponse | None = None
        if provider_error is None:
            try:
                response = validate_model_stream(model_items, compiled.request)
            except ValueError:
                completed_item = next(
                    (
                        item
                        for item in reversed(model_items)
                        if isinstance(item, ModelStreamCompleted)
                    ),
                    None,
                )
                incomplete = completed_item is None
                provider_error = ModelProviderError(
                    (
                        ModelProviderErrorCode.INCOMPLETE_RESPONSE
                        if incomplete
                        else ModelProviderErrorCode.INVALID_RESPONSE
                    ),
                    partial_response=any(
                        isinstance(item, ModelStreamDelta) for item in model_items
                    ),
                    usage=(None if completed_item is None else completed_item.response.usage),
                )

        if provider_error is not None:
            failed_at = self._time(not_before=events[-1].occurred_at)
            failed_step = self._settled_step(
                model_step,
                status=AgentStepStatus.FAILED,
                revision=3,
                completed_at=failed_at,
                usage=provider_error.usage,
                error_code=provider_error.code.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(model_step.step_id),
                    "error_code": provider_error.code.value,
                    "partial_response": provider_error.partial_response,
                    **(
                        {}
                        if provider_error.usage is None
                        else {
                            "input_tokens": provider_error.usage.input_tokens,
                            "output_tokens": provider_error.usage.output_tokens,
                            "cached_input_tokens": provider_error.usage.cached_input_tokens,
                            "cost_micro_usd": provider_error.usage.cost_micro_usd,
                        }
                    ),
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(failed_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=provider_error.stop_reason,
                occurred_at=failed_at,
                usage=provider_error.usage,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if response is None:
            raise AssertionError("A successful Provider stream requires its final response")
        completed_at = self._time(not_before=events[-1].occurred_at)
        model_completed = self._event(
            run,
            events,
            event_type=AgentEventType.MODEL_COMPLETED,
            occurred_at=completed_at,
            payload=self._model_completed_payload(model_step.step_id, response),
        )
        await self._commit(events, model_completed)
        yield model_completed
        completed_model_step = self._settled_step(
            model_step,
            status=AgentStepStatus.COMPLETED,
            revision=3,
            completed_at=completed_at,
            usage=response.usage,
            output_summary={
                "model": response.model,
                "finish_reason": response.finish_reason.value,
                "provider_request_id": response.provider_request_id,
            },
        )
        step_completed = self._event(
            run,
            events,
            event_type=AgentEventType.STEP_COMPLETED,
            occurred_at=completed_at,
            payload={
                "step_id": str(model_step.step_id),
                "step_kind": model_step.kind.value,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cached_input_tokens": response.usage.cached_input_tokens,
                "cost_micro_usd": response.usage.cost_micro_usd,
            },
        )
        await self._commit(events, step_completed)
        yield step_completed
        model_state = replace(
            state,
            revision=3,
            event_count=len(events),
            input_tokens_used=state.input_tokens_used + response.usage.input_tokens,
            output_tokens_used=state.output_tokens_used + response.usage.output_tokens,
            cost_micro_usd=state.cost_micro_usd + response.usage.cost_micro_usd,
            updated_at=completed_at,
        )
        run = replace(run, state_revision=3)
        validate_state_transition(state, model_state, expected_revision=2)
        state = model_state

        failure_reason = exhausted_budget_reason(state, run.budget)
        if response.finish_reason is not ModelFinishReason.STOP:
            failure_reason = RunStopReason.INCOMPLETE_PROVIDER_RESPONSE
        if failure_reason is not None:
            failed_at = self._time(not_before=events[-1].occurred_at)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(completed_model_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=failure_reason,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        if await self._cancel_requested(run):
            cancelled_at = self._time(not_before=events[-1].occurred_at)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(completed_model_step,),
                status=AgentRunStatus.CANCELLED,
                stop_reason=RunStopReason.CANCELLED,
                occurred_at=cancelled_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return

        try:
            final_started_at = self._before_deadline(
                run,
                not_before=events[-1].occurred_at,
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(completed_model_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        try:
            final_output = DirectAnswerFinalOutput.from_response(
                contract_version=command.policy.output_contract_version,
                run_id=run.run_id,
                step_id=command.final_step_id,
                workspace_id=run.workspace_id,
                response=response,
            )
        except ValueError:
            failed_at = final_started_at
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(completed_model_step,),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.INVALID_PROVIDER_RESPONSE,
                occurred_at=failed_at,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        running_final_step = AgentStep(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            step_id=command.final_step_id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            sequence=2,
            kind=AgentStepKind.FINAL,
            status=AgentStepStatus.RUNNING,
            state_revision=4,
            started_at=final_started_at,
            input_summary={
                "contract_version": final_output.contract_version,
                "format": final_output.format,
            },
        )
        final_started = self._event(
            run,
            events,
            event_type=AgentEventType.STEP_STARTED,
            occurred_at=final_started_at,
            payload={
                "step_id": str(command.final_step_id),
                "step_sequence": 2,
                "step_kind": AgentStepKind.FINAL.value,
            },
        )
        await self._commit(events, final_started)
        yield final_started
        try:
            final_completed_at = self._before_deadline(
                run,
                not_before=events[-1].occurred_at,
            )
        except RuntimeDeadlineExceeded:
            failed_at = max(events[-1].occurred_at, run.budget.deadline)
            failed_final_step = self._settled_step(
                running_final_step,
                status=AgentStepStatus.FAILED,
                revision=4,
                completed_at=failed_at,
                error_code=RunStopReason.DEADLINE_EXCEEDED.value,
            )
            step_failed = self._event(
                run,
                events,
                event_type=AgentEventType.STEP_FAILED,
                occurred_at=failed_at,
                payload={
                    "step_id": str(running_final_step.step_id),
                    "error_code": RunStopReason.DEADLINE_EXCEEDED.value,
                },
            )
            await self._commit(events, step_failed)
            yield step_failed
            terminal = self._terminal_event(
                run=run,
                state=state,
                events=events,
                steps=(completed_model_step, failed_final_step),
                status=AgentRunStatus.FAILED,
                stop_reason=RunStopReason.DEADLINE_EXCEEDED,
                occurred_at=failed_at,
                step_count=2,
            )
            await self._commit(events, terminal)
            yield terminal
            return
        final_step = replace(
            running_final_step,
            status=AgentStepStatus.COMPLETED,
            completed_at=final_completed_at,
            output_summary={
                "contract_version": final_output.contract_version,
                "format": final_output.format,
            },
            latency_ms=max(
                0,
                int((final_completed_at - final_started_at).total_seconds() * 1_000),
            ),
        )
        final_completed = self._event(
            run,
            events,
            event_type=AgentEventType.STEP_COMPLETED,
            occurred_at=final_completed_at,
            payload={
                "step_id": str(final_step.step_id),
                "step_kind": final_step.kind.value,
                **final_output.to_event_payload(),
            },
        )
        await self._commit(events, final_completed)
        yield final_completed
        terminal = self._terminal_event(
            run=run,
            state=state,
            events=events,
            steps=(completed_model_step, final_step),
            status=AgentRunStatus.COMPLETED,
            stop_reason=RunStopReason.FINAL,
            occurred_at=final_completed_at,
            step_count=2,
        )
        await self._commit(events, terminal)
        yield terminal

    @staticmethod
    def _model_completed_payload(step_id: UUID, response: ModelResponse) -> dict[str, object]:
        payload: dict[str, object] = {
            "step_id": str(step_id),
            "model": response.model,
            "finish_reason": response.finish_reason.value,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cached_input_tokens": response.usage.cached_input_tokens,
            "cost_micro_usd": response.usage.cost_micro_usd,
            "pricing_version": response.usage.pricing_version,
        }
        if response.provider_request_id is not None:
            payload["provider_request_id"] = response.provider_request_id
        return payload
