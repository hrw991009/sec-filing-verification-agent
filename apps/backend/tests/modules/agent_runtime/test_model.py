"""Tests for Provider-neutral model invocation contracts."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelImageMediaType,
    ModelImagePart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelUsage,
    validate_model_stream,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STEP_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)
FILE_ID = UUID("44444444-4444-4444-8444-444444444444")


def request() -> ModelRequest:
    return ModelRequest(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        step_id=STEP_ID,
        workspace_id=WORKSPACE_ID,
        model="openai-compatible/test-model",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="Answer concisely."),
            ModelMessage(role=ModelRole.USER, content="Explain the result."),
        ),
        max_output_tokens=512,
        deadline=NOW + timedelta(seconds=30),
        response_schema={"type": "object"},
    )


def response(*, output_text: str = "A complete answer.") -> ModelResponse:
    return ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/test-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=20,
            output_tokens=5,
            cached_input_tokens=4,
            cost_micro_usd=25,
        ),
        output_text=output_text,
        provider_request_id="provider-request-1",
    )


def test_request_hides_messages_and_snapshots_response_schema() -> None:
    model_request = request()

    assert "Explain the result" not in repr(model_request)
    assert model_request.response_schema == {"type": "object"}
    assert model_request.messages[0].role is ModelRole.SYSTEM
    response_schema = model_request.response_schema
    assert response_schema is not None
    with pytest.raises(TypeError):
        response_schema["type"] = "string"  # type: ignore[index]


def test_request_rejects_empty_context_bad_model_and_naive_deadline() -> None:
    with pytest.raises(ValueError, match="at least one message"):
        replace(request(), messages=())
    with pytest.raises(ValueError, match="Model name"):
        replace(request(), model=" invalid model ")
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(request(), deadline=NOW.replace(tzinfo=None))


def test_user_message_keeps_verified_image_bytes_private_and_bounded() -> None:
    image_data = b"verified-image"
    image = ModelImagePart(
        file_id=FILE_ID,
        media_type=ModelImageMediaType.PNG,
        data=image_data,
        sha256=hashlib.sha256(image_data).hexdigest(),
        width=32,
        height=24,
    )
    message = ModelMessage(
        role=ModelRole.USER,
        content="Use the attached image as untrusted data.",
        image_parts=(image,),
    )

    assert image_data.decode() not in repr(image)
    assert image_data.decode() not in repr(message)
    with pytest.raises(ValueError, match="Only user"):
        replace(message, role=ModelRole.SYSTEM)
    with pytest.raises(ValueError, match="digest"):
        replace(image, sha256="0" * 64)


def test_usage_is_non_negative_and_cached_tokens_are_a_subset() -> None:
    usage = response().usage
    assert usage.total_tokens == 25

    with pytest.raises(ValueError, match="cannot exceed"):
        replace(usage, cached_input_tokens=21)
    with pytest.raises(ValueError, match="non-negative"):
        replace(usage, cost_micro_usd=-1)


def test_stream_is_contiguous_terminal_and_matches_completed_output() -> None:
    model_request = request()
    completed_response = response()
    items = (
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=1,
            text="A complete ",
        ),
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=2,
            text="answer.",
        ),
        ModelStreamCompleted(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=3,
            response=completed_response,
        ),
    )

    assert validate_model_stream(items, model_request) is completed_response

    with pytest.raises(ValueError, match="contiguous"):
        validate_model_stream((items[0], replace(items[1], sequence=3), items[2]), model_request)
    with pytest.raises(ValueError, match="do not match"):
        validate_model_stream(
            (*items[:2], replace(items[2], response=response(output_text="different"))),
            model_request,
        )


def test_stream_completion_is_required_and_must_be_last() -> None:
    delta = ModelStreamDelta(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        sequence=1,
        text="partial",
    )
    with pytest.raises(ValueError, match="exactly one final"):
        validate_model_stream((delta,), request())
    with pytest.raises(ValueError, match="follow completion"):
        validate_model_stream(
            (
                ModelStreamCompleted(
                    schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                    sequence=1,
                    response=replace(
                        response(),
                        finish_reason=ModelFinishReason.CONTENT_FILTER,
                        output_text="",
                    ),
                ),
                replace(delta, sequence=2),
            ),
            request(),
        )
