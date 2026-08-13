"""Tests for the strict deterministic ModelProvider boundary."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_harness.fakes import (
    FakeModelOperation,
    FakeModelScriptExhaustedError,
    FakeModelScriptMismatchError,
    ModelRequestExpectation,
    ScriptedModelExchange,
    ScriptedModelProvider,
    UnconsumedFakeModelScriptError,
)
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.ports import ModelProvider

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STEP_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)


def request(*, question: str = "What changed?") -> ModelRequest:
    return ModelRequest(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        step_id=STEP_ID,
        workspace_id=WORKSPACE_ID,
        model="openai-compatible/fake-model",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="Answer directly."),
            ModelMessage(role=ModelRole.USER, content=question),
        ),
        max_output_tokens=128,
        deadline=NOW + timedelta(seconds=30),
    )


def response() -> ModelResponse:
    return ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/fake-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=8,
            output_tokens=2,
            cached_input_tokens=0,
            cost_micro_usd=10,
        ),
        output_text="It changed.",
        provider_request_id="fake-request-1",
    )


def stream_items() -> tuple[ModelStreamItem, ...]:
    return (
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=1,
            text="It ",
        ),
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=2,
            text="changed.",
        ),
        ModelStreamCompleted(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=3,
            response=response(),
        ),
    )


def accept_model_provider(provider: ModelProvider) -> ModelProvider:
    return provider


@pytest.mark.asyncio
async def test_fake_implements_model_port_and_consumes_operations_in_order() -> None:
    model_request = request()
    expectation = ModelRequestExpectation.from_request(model_request)
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.COMPLETE,
                expectation=expectation,
                response=response(),
            ),
            ScriptedModelExchange(
                operation=FakeModelOperation.STREAM,
                expectation=expectation,
                stream_items=stream_items(),
            ),
        )
    )
    provider = accept_model_provider(fake)

    completed = await provider.complete(model_request)
    streamed = [item async for item in provider.stream(model_request)]
    fake.assert_exhausted()

    assert completed.output_text == "It changed."
    assert tuple(streamed) == stream_items()
    assert fake.requests == (model_request, model_request)


@pytest.mark.asyncio
async def test_mismatch_is_fail_closed_does_not_consume_or_leak_prompt() -> None:
    sensitive_question = "confidential acquisition question"
    expected_request = request(question=sensitive_question)
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.COMPLETE,
                expectation=ModelRequestExpectation.from_request(expected_request),
                response=response(),
            ),
        )
    )

    with pytest.raises(FakeModelScriptMismatchError) as mismatch:
        await fake.complete(replace(expected_request, workspace_id=OTHER_WORKSPACE_ID))

    assert sensitive_question not in str(mismatch.value)
    assert fake.remaining_exchange_count == 1
    assert await fake.complete(expected_request) == response()


def test_request_expectation_deeply_freezes_schema_and_can_be_rebuilt() -> None:
    model_request = replace(
        request(),
        response_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string", "enum": ["yes", "no"]},
            },
        },
    )
    expectation = ModelRequestExpectation.from_request(model_request)
    response_schema = expectation.response_schema
    assert response_schema is not None
    properties = response_schema["properties"]

    assert isinstance(properties, Mapping)
    assert replace(expectation, model=expectation.model) == expectation
    assert expectation.matches(model_request)
    with pytest.raises(TypeError):
        properties["answer"] = {}  # type: ignore[index]


@pytest.mark.asyncio
async def test_wrong_operation_extra_call_and_unconsumed_script_are_explicit() -> None:
    model_request = request()
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.COMPLETE,
                expectation=ModelRequestExpectation.from_request(model_request),
                response=response(),
            ),
        )
    )

    with pytest.raises(FakeModelScriptMismatchError):
        await anext(fake.stream(model_request))
    with pytest.raises(UnconsumedFakeModelScriptError):
        fake.assert_exhausted()

    await fake.complete(model_request)
    with pytest.raises(FakeModelScriptExhaustedError):
        await fake.complete(model_request)


@pytest.mark.asyncio
async def test_incomplete_stream_is_replayed_without_becoming_fake_success() -> None:
    model_request = request()
    incomplete: tuple[ModelStreamItem, ...] = (
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=1,
            text="partial",
        ),
    )
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.STREAM,
                expectation=ModelRequestExpectation.from_request(model_request),
                stream_items=incomplete,
            ),
        )
    )

    replayed: AsyncIterator[ModelStreamItem] = fake.stream(model_request)

    assert [item async for item in replayed] == list(incomplete)
    fake.assert_exhausted()


@pytest.mark.asyncio
async def test_partially_read_stream_remains_unconsumed() -> None:
    model_request = request()
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.STREAM,
                expectation=ModelRequestExpectation.from_request(model_request),
                stream_items=stream_items(),
            ),
        )
    )
    streamed = fake.stream(model_request)

    assert await anext(streamed) == stream_items()[0]
    with pytest.raises(UnconsumedFakeModelScriptError):
        fake.assert_exhausted()

    await streamed.aclose()
    with pytest.raises(UnconsumedFakeModelScriptError):
        fake.assert_exhausted()


@pytest.mark.asyncio
async def test_last_item_is_not_consumed_until_stream_reaches_stop_iteration() -> None:
    model_request = request()
    fake = ScriptedModelProvider(
        (
            ScriptedModelExchange(
                operation=FakeModelOperation.STREAM,
                expectation=ModelRequestExpectation.from_request(model_request),
                stream_items=stream_items(),
            ),
        )
    )
    streamed = fake.stream(model_request)

    for expected_item in stream_items():
        assert await anext(streamed) == expected_item
    with pytest.raises(UnconsumedFakeModelScriptError):
        fake.assert_exhausted()
    with pytest.raises(StopAsyncIteration):
        await anext(streamed)

    fake.assert_exhausted()
