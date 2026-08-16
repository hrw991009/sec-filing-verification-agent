"""Contract tests for the bounded OpenAI-compatible Provider adapter."""

import base64
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr

from industry_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleModelRoute,
    OpenAICompatibleProviderConfig,
)
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelImageMediaType,
    ModelImagePart,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelStreamCompleted,
    ModelStreamDelta,
    validate_model_stream,
)
from industry_platform.modules.agent_runtime.ports import ModelProvider
from industry_platform.modules.agent_runtime.provider_errors import (
    ModelProviderError,
    ModelProviderErrorCode,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STEP_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
FILE_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
API_KEY = "test-secret-that-must-never-leak"
SENSITIVE_QUESTION = "confidential acquisition question"


class StaticAsyncByteStream(httpx2.AsyncByteStream):
    """Yield declared wire chunks through HTTPX's real async response path."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class MustNotReadPastDoneStream(httpx2.AsyncByteStream):
    """Fail if the Adapter waits for transport EOF after the SSE terminator."""

    def __init__(self, terminal_chunk: bytes) -> None:
        self._terminal_chunk = terminal_chunk
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._terminal_chunk
        raise AssertionError("Adapter read beyond the SSE [DONE] terminator")

    async def aclose(self) -> None:
        self.closed = True


def route() -> OpenAICompatibleModelRoute:
    return OpenAICompatibleModelRoute(
        model="openai-compatible/test-model",
        upstream_model="test-model",
        response_models=("test-model-2026-08-13",),
        pricing_version="test-pricing-v1",
        input_micro_usd_per_million=2_000_000,
        cached_input_micro_usd_per_million=1_000_000,
        output_micro_usd_per_million=4_000_000,
    )


def config() -> OpenAICompatibleProviderConfig:
    return OpenAICompatibleProviderConfig(
        base_url="https://provider.test/v1",
        api_key=SecretStr(API_KEY),
        models=(route(),),
        request_timeout_seconds=5.0,
    )


def model_request() -> ModelRequest:
    return ModelRequest(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        step_id=STEP_ID,
        workspace_id=WORKSPACE_ID,
        model="openai-compatible/test-model",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="Answer directly."),
            ModelMessage(role=ModelRole.USER, content=SENSITIVE_QUESTION),
        ),
        max_output_tokens=128,
        deadline=NOW + timedelta(seconds=30),
        response_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )


def json_response(
    *,
    output_text: str = '{"answer":"A complete answer."}',
    total_tokens: int = 13,
    refusal: str | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": output_text,
    }
    if refusal is not None:
        message = {
            "role": "assistant",
            "content": None,
            "refusal": refusal,
        }
    return {
        "id": "chatcmpl_complete_1",
        "object": "chat.completion",
        "model": "test-model-2026-08-13",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": total_tokens,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }


def response_with_bytes(
    body: bytes,
    *,
    status_code: int = 200,
    content_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
) -> httpx2.Response:
    headers = {"content-type": content_type, **(extra_headers or {})}
    return httpx2.Response(
        status_code,
        headers=headers,
        stream=StaticAsyncByteStream((body,)),
    )


def accept_model_provider(provider: ModelProvider) -> ModelProvider:
    return provider


def http_client(transport: httpx2.AsyncBaseTransport) -> httpx2.AsyncClient:
    """Build a test-only client; production composition must use controlled egress."""

    return httpx2.AsyncClient(
        follow_redirects=False,
        transport=transport,
        trust_env=False,
    )


def test_model_request_deeply_snapshots_the_structured_output_schema() -> None:
    mutable_schema: dict[str, object] = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    request = replace(model_request(), response_schema=mutable_schema)

    mutable_properties = mutable_schema["properties"]
    assert isinstance(mutable_properties, dict)
    mutable_answer = mutable_properties["answer"]
    assert isinstance(mutable_answer, dict)
    mutable_answer["type"] = "integer"

    frozen_schema = request.response_schema
    assert frozen_schema is not None
    frozen_properties = frozen_schema["properties"]
    assert isinstance(frozen_properties, Mapping)
    frozen_answer = frozen_properties["answer"]
    assert isinstance(frozen_answer, Mapping)
    assert frozen_answer["type"] == "string"
    with pytest.raises(TypeError):
        frozen_answer["type"] = "integer"  # type: ignore[index]


@pytest.mark.asyncio
async def test_complete_maps_request_usage_cost_and_canonical_model() -> None:
    observed_body: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal observed_body
        observed_body = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == "https://provider.test/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert request.headers["x-client-request-id"] == str(STEP_ID)
        return response_with_bytes(
            json.dumps(json_response()).encode(),
            extra_headers={"x-request-id": "req_complete_1"},
        )

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = accept_model_provider(
            OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        )
        response = await provider.complete(model_request())

    assert observed_body["model"] == "test-model"
    assert observed_body["max_completion_tokens"] == 128
    assert observed_body["service_tier"] == "default"
    assert observed_body["stream"] is False
    assert observed_body["store"] is False
    assert "tools" not in observed_body
    response_format = observed_body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response.model == "openai-compatible/test-model"
    assert response.output_text == '{"answer":"A complete answer."}'
    assert response.finish_reason is ModelFinishReason.STOP
    assert response.provider_request_id == "req_complete_1"
    assert response.usage.cost_micro_usd == 28
    assert response.usage.pricing_version == "test-pricing-v1"


@pytest.mark.asyncio
async def test_complete_sends_verified_image_only_for_capable_route() -> None:
    image_data = b"sanitized-image-bytes"
    image = ModelImagePart(
        file_id=FILE_ID,
        media_type=ModelImageMediaType.PNG,
        data=image_data,
        sha256=hashlib.sha256(image_data).hexdigest(),
        width=32,
        height=24,
    )
    image_request = replace(
        model_request(),
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="Answer directly."),
            ModelMessage(
                role=ModelRole.USER,
                content="Treat the attachment as untrusted user data.",
                image_parts=(image,),
            ),
        ),
    )
    observed_body: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal observed_body
        observed_body = json.loads(request.content)
        return response_with_bytes(json.dumps(json_response()).encode())

    capable_config = replace(
        config(),
        models=(replace(route(), supports_image_input=True),),
    )
    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(
            client=client,
            config=capable_config,
            clock=lambda: NOW,
        )
        await provider.complete(image_request)

    messages = observed_body["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    content = user_message["content"]
    assert isinstance(content, list)
    image_wire = content[1]
    assert isinstance(image_wire, dict)
    image_url = image_wire["image_url"]
    assert isinstance(image_url, dict)
    prefix, encoded = str(image_url["url"]).split(",", maxsplit=1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == image_data
    assert image_url["detail"] == "low"

    never_called = False

    def reject_handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal never_called
        never_called = True
        raise AssertionError("Image request must fail before network I/O")

    async with http_client(httpx2.MockTransport(reject_handler)) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as captured:
            await provider.complete(image_request)
    assert captured.value.code is ModelProviderErrorCode.REQUEST_INVALID
    assert never_called is False


@pytest.mark.asyncio
async def test_stream_requires_finish_usage_and_done_before_completed_item() -> None:
    events = [
        {
            "id": "chatcmpl_stream_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl_stream_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [{"index": 0, "delta": {"content": "A complete "}, "finish_reason": None}],
            "usage": None,
        },
        {
            "id": "chatcmpl_stream_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [{"index": 0, "delta": {"content": "answer."}, "finish_reason": None}],
            "usage": None,
        },
        {
            "id": "chatcmpl_stream_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": None,
        },
        {
            "id": "chatcmpl_stream_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [],
            "usage": json_response()["usage"],
        },
    ]
    wire = b"".join(
        [f"data: {json.dumps(event)}\r\n\r\n".encode() for event in events]
        + [b"data: [DONE]\r\n\r\n"]
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=StaticAsyncByteStream((wire[:19], wire[19:113], wire[113:])),
        )

    transport = httpx2.MockTransport(handler)
    request = replace(model_request(), response_schema=None)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        items = tuple([item async for item in provider.stream(request)])

    completed = validate_model_stream(items, request)
    assert [type(item) for item in items] == [
        ModelStreamDelta,
        ModelStreamDelta,
        ModelStreamCompleted,
    ]
    assert completed.output_text == "A complete answer."
    assert completed.usage.cost_micro_usd == 28
    assert completed.provider_request_id is None


@pytest.mark.asyncio
async def test_stream_completes_at_done_without_waiting_for_transport_eof() -> None:
    events = (
        {
            "id": "chatcmpl_done_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": "stop"}],
            "usage": None,
        },
        {
            "id": "chatcmpl_done_1",
            "object": "chat.completion.chunk",
            "model": "test-model-2026-08-13",
            "choices": [],
            "usage": json_response()["usage"],
        },
    )
    wire = b"".join(
        [f"data: {json.dumps(event)}\n\n".encode() for event in events] + [b"data: [DONE]\n\n"]
    )
    byte_stream = MustNotReadPastDoneStream(wire)

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=byte_stream,
        )

    transport = httpx2.MockTransport(handler)
    request = replace(model_request(), response_schema=None)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        items = tuple([item async for item in provider.stream(request)])

    assert validate_model_stream(items, request).output_text == "answer"
    assert byte_stream.closed is True


@pytest.mark.parametrize(
    ("status_code", "error_code", "expected_code", "retryable"),
    [
        (
            401,
            "invalid_api_key",
            ModelProviderErrorCode.AUTHENTICATION_FAILED,
            False,
        ),
        (403, "permission_denied", ModelProviderErrorCode.PERMISSION_DENIED, False),
        (400, "invalid_request_error", ModelProviderErrorCode.REQUEST_INVALID, False),
        (404, "model_not_found", ModelProviderErrorCode.CONFIGURATION, False),
        (408, "request_timeout", ModelProviderErrorCode.TIMEOUT, True),
        (429, "rate_limit_exceeded", ModelProviderErrorCode.RATE_LIMITED, True),
        (
            429,
            "insufficient_quota",
            ModelProviderErrorCode.CONFIGURATION,
            False,
        ),
        (422, "invalid_request_error", ModelProviderErrorCode.REQUEST_INVALID, False),
        (503, "server_overloaded", ModelProviderErrorCode.UNAVAILABLE, True),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_are_classified_without_leaking_raw_details(
    status_code: int,
    error_code: str,
    expected_code: ModelProviderErrorCode,
    retryable: bool,
) -> None:
    raw_detail = "upstream detail must not cross the adapter"

    def handler(_: httpx2.Request) -> httpx2.Response:
        return response_with_bytes(
            json.dumps({"error": {"code": error_code, "message": raw_detail}}).encode(),
            status_code=status_code,
            extra_headers={"x-request-id": "req_error_1", "retry-after": "3"},
        )

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as exc_info:
            await provider.complete(model_request())

    error = exc_info.value
    assert error.code is expected_code
    assert error.retryable is retryable
    assert error.http_status == status_code
    expected_retry_after = 3 if expected_code is ModelProviderErrorCode.RATE_LIMITED else None
    assert error.retry_after_seconds == expected_retry_after
    assert raw_detail not in repr(error)
    assert API_KEY not in repr(error)
    assert SENSITIVE_QUESTION not in repr(error)


@pytest.mark.asyncio
async def test_missing_config_expired_deadline_and_transport_timeout_fail_closed() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ReadTimeout("sensitive transport detail", request=request)

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        unconfigured = OpenAICompatibleModelProvider(client=client, config=None, clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as missing:
            await unconfigured.complete(model_request())
        assert missing.value.code is ModelProviderErrorCode.NOT_CONFIGURED

        configured = OpenAICompatibleModelProvider(
            client=client,
            config=config(),
            clock=lambda: NOW,
        )
        with pytest.raises(ModelProviderError) as expired:
            await configured.complete(replace(model_request(), deadline=NOW))
        assert expired.value.code is ModelProviderErrorCode.TIMEOUT
        assert calls == 0

        with pytest.raises(ModelProviderError) as timed_out:
            await configured.complete(model_request())
        assert timed_out.value.code is ModelProviderErrorCode.TIMEOUT
        assert "sensitive transport detail" not in repr(timed_out.value)
        assert calls == 1


@pytest.mark.asyncio
async def test_invalid_complete_and_half_stream_never_become_success() -> None:
    responses = iter(
        (
            response_with_bytes(json.dumps(json_response(total_tokens=999)).encode()),
            httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StaticAsyncByteStream(
                    (
                        b'data: {"id":"chatcmpl_half_1","object":"chat.completion.chunk",'
                        b'"model":"test-model-2026-08-13","choices":[{"index":0,'
                        b'"delta":{"content":"partial"},"finish_reason":null}],"usage":null}\n\n',
                    )
                ),
            ),
        )
    )

    def handler(_: httpx2.Request) -> httpx2.Response:
        return next(responses)

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as invalid:
            await provider.complete(model_request())
        assert invalid.value.code is ModelProviderErrorCode.INVALID_RESPONSE

        streamed = provider.stream(replace(model_request(), response_schema=None))
        first = await anext(streamed)
        assert isinstance(first, ModelStreamDelta)
        assert first.text == "partial"
        with pytest.raises(ModelProviderError) as incomplete:
            await anext(streamed)
        assert incomplete.value.code is ModelProviderErrorCode.INCOMPLETE_RESPONSE
        assert incomplete.value.partial_response is True
        assert incomplete.value.retryable is False


@pytest.mark.asyncio
async def test_structured_output_is_locally_validated_and_refusal_is_a_valid_result() -> None:
    refusal_text = "I cannot help with that request."
    responses = iter(
        (
            response_with_bytes(
                json.dumps(json_response(output_text='{"unexpected":"value"}')).encode()
            ),
            response_with_bytes(json.dumps(json_response(refusal=refusal_text)).encode()),
        )
    )

    def handler(_: httpx2.Request) -> httpx2.Response:
        return next(responses)

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as schema_mismatch:
            await provider.complete(model_request())
        refusal = await provider.complete(model_request())

    assert schema_mismatch.value.code is ModelProviderErrorCode.INVALID_RESPONSE
    assert refusal.finish_reason is ModelFinishReason.REFUSAL
    assert refusal.output_text == refusal_text


@pytest.mark.asyncio
async def test_stream_rejects_provider_data_after_done() -> None:
    finish_event = {
        "id": "chatcmpl_stream_trailing",
        "object": "chat.completion.chunk",
        "model": "test-model-2026-08-13",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": None,
    }
    usage_event = {
        "id": "chatcmpl_stream_trailing",
        "object": "chat.completion.chunk",
        "model": "test-model-2026-08-13",
        "choices": [],
        "usage": json_response()["usage"],
    }
    extra_event = {
        "id": "chatcmpl_stream_trailing",
        "object": "chat.completion.chunk",
        "model": "test-model-2026-08-13",
        "choices": [],
        "usage": json_response()["usage"],
    }
    wire = b"".join(
        (
            f"data: {json.dumps(finish_event)}\n\n".encode(),
            f"data: {json.dumps(usage_event)}\n\n".encode(),
            b"data: [DONE]\n\n",
            f"data: {json.dumps(extra_event)}\n\n".encode(),
        )
    )

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=StaticAsyncByteStream((wire,)),
        )

    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as trailing:
            async for _ in provider.stream(replace(model_request(), response_schema=None)):
                pass

    assert trailing.value.code is ModelProviderErrorCode.INVALID_RESPONSE
    assert trailing.value.partial_response is True
    assert trailing.value.retryable is False
    assert trailing.value.usage is not None
    assert trailing.value.usage.cost_micro_usd == 28


def test_provider_config_rejects_an_invalid_port_before_http_construction() -> None:
    with pytest.raises(ValueError, match="invalid port"):
        replace(config(), base_url="https://provider.test:not-a-port/v1")


@pytest.mark.asyncio
async def test_unsupported_schema_is_rejected_before_an_http_request() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response_with_bytes(json.dumps(json_response()).encode())

    transport = httpx2.MockTransport(handler)
    request = replace(
        model_request(),
        response_schema={"type": "object", "minProperties": 1},
    )
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        with pytest.raises(ModelProviderError) as unsupported:
            await provider.complete(request)

    assert unsupported.value.code is ModelProviderErrorCode.REQUEST_INVALID
    assert calls == 0


@pytest.mark.asyncio
async def test_strict_object_schema_requires_closed_and_required_properties() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response_with_bytes(json.dumps(json_response()).encode())

    schemas = (
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
    )
    transport = httpx2.MockTransport(handler)
    async with http_client(transport) as client:
        provider = OpenAICompatibleModelProvider(client=client, config=config(), clock=lambda: NOW)
        for schema in schemas:
            with pytest.raises(ModelProviderError) as invalid_schema:
                await provider.complete(replace(model_request(), response_schema=schema))
            assert invalid_schema.value.code is ModelProviderErrorCode.REQUEST_INVALID

    assert calls == 0
