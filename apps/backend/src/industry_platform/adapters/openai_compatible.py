"""Strict OpenAI-compatible Chat Completions ModelProvider adapter."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import math
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, cast
from urllib.parse import urlsplit

import httpx2
from pydantic import SecretStr

from industry_platform.adapters.openai_compatible_schema import (
    InvalidProviderRequest as _InvalidProviderRequest,
)
from industry_platform.adapters.openai_compatible_schema import (
    InvalidProviderResponse as _InvalidProviderResponse,
)
from industry_platform.adapters.openai_compatible_schema import (
    decode_json_object as _decode_json_object,
)
from industry_platform.adapters.openai_compatible_schema import (
    thaw_json_value as _thaw_json_value,
)
from industry_platform.adapters.openai_compatible_schema import (
    validate_structured_output as _validate_structured_output,
)
from industry_platform.adapters.openai_compatible_schema import (
    validate_supported_schema as _validate_supported_schema,
)
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION, require_utc
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.provider_errors import (
    MAX_PROVIDER_RETRY_AFTER_SECONDS,
    ModelProviderError,
    ModelProviderErrorCode,
)

MAX_PROVIDER_RESPONSE_BYTES: Final = 4_000_000
MAX_PROVIDER_STREAM_BYTES: Final = 8_000_000
MAX_PROVIDER_SSE_EVENT_BYTES: Final = 1_000_000
MAX_PROVIDER_ERROR_BODY_BYTES: Final = 64_000
MAX_PROVIDER_PRICE_MICRO_USD_PER_MILLION: Final = 1_000_000_000_000

_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_NON_RETRYABLE_LIMIT_CODES: Final = frozenset(
    {
        "credit_balance_exhausted",
        "billing_hard_limit_reached",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "project_spend_limit_exceeded",
    }
)


class _IncompleteProviderResponse(RuntimeError):
    """Internal sentinel for a syntactically valid stream without a terminal frame."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_model_name(value: str, *, field_name: str) -> None:
    if not _MODEL_NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_price(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not 0 <= value <= MAX_PROVIDER_PRICE_MICRO_USD_PER_MILLION:
        raise ValueError(f"{field_name} is invalid")


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelRoute:
    """Versioned canonical-to-upstream model and trusted price mapping."""

    model: str
    upstream_model: str
    response_models: tuple[str, ...]
    pricing_version: str
    input_micro_usd_per_million: int
    cached_input_micro_usd_per_million: int
    output_micro_usd_per_million: int
    supports_image_input: bool = False

    def __post_init__(self) -> None:
        _require_model_name(self.model, field_name="Canonical model name")
        _require_model_name(self.upstream_model, field_name="Upstream model name")
        response_models = tuple(self.response_models)
        if not response_models or len(response_models) != len(set(response_models)):
            raise ValueError("Accepted Provider response models are invalid")
        for response_model in response_models:
            _require_model_name(response_model, field_name="Provider response model name")
        if not _VERSION_PATTERN.fullmatch(self.pricing_version):
            raise ValueError("Model pricing version is invalid")
        for value, field_name in (
            (self.input_micro_usd_per_million, "Input token price"),
            (self.cached_input_micro_usd_per_million, "Cached input token price"),
            (self.output_micro_usd_per_million, "Output token price"),
        ):
            _require_price(value, field_name=field_name)
        if not isinstance(self.supports_image_input, bool):
            raise ValueError("Model image-input capability is invalid")
        object.__setattr__(self, "response_models", response_models)

    def calculate_cost(
        self,
        *,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> int:
        """Calculate one auditable integer micro-USD charge, rounded up once."""

        for value in (input_tokens, cached_input_tokens, output_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("Model token counts must be non-negative integers")
        if cached_input_tokens > input_tokens:
            raise ValueError("Cached input tokens cannot exceed all input tokens")
        numerator = (
            (input_tokens - cached_input_tokens) * self.input_micro_usd_per_million
            + cached_input_tokens * self.cached_input_micro_usd_per_million
            + output_tokens * self.output_micro_usd_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProviderConfig:
    """Server-owned endpoint, credentials, limits, and model routes."""

    base_url: str
    api_key: SecretStr = field(repr=False)
    models: tuple[OpenAICompatibleModelRoute, ...]
    request_timeout_seconds: float = 30.0
    max_response_bytes: int = MAX_PROVIDER_RESPONSE_BYTES
    max_stream_bytes: int = MAX_PROVIDER_STREAM_BYTES
    max_sse_event_bytes: int = MAX_PROVIDER_SSE_EVENT_BYTES
    allow_test_loopback: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("Provider base URL has an invalid port") from None
        if not isinstance(self.allow_test_loopback, bool):
            raise ValueError("Provider test loopback flag is invalid")
        common_invalid = (
            self.base_url != self.base_url.strip()
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        )
        loopback = (
            self.allow_test_loopback
            and parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and port is not None
        )
        if common_invalid or (
            not loopback and (parsed.scheme != "https" or port not in {None, 443})
        ):
            raise ValueError("Provider base URL must be one fixed HTTPS origin or path")
        hostname_value = parsed.hostname
        if hostname_value is None:
            raise ValueError("Provider base URL must include a DNS hostname")
        hostname = hostname_value.casefold()
        if not loopback and (hostname == "localhost" or hostname.endswith(".localhost")):
            raise ValueError("Provider base URL cannot target localhost")
        if not loopback:
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError("Provider base URL must use a controlled DNS hostname")
        try:
            httpx2.URL(self.base_url)
        except (httpx2.InvalidURL, TypeError, ValueError):
            raise ValueError("Provider base URL is invalid") from None
        normalized_base_url = self.base_url.rstrip("/")
        if not normalized_base_url or normalized_base_url.endswith("/chat/completions"):
            raise ValueError("Provider base URL must not include the Chat Completions endpoint")

        secret = self.api_key.get_secret_value()
        if not secret or secret != secret.strip() or len(secret) > 4_096:
            raise ValueError("Provider API key is invalid")

        models = tuple(self.models)
        model_names = {route.model for route in models}
        if not models or len(model_names) != len(models):
            raise ValueError("Provider model routes must be non-empty and unique")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not math.isfinite(self.request_timeout_seconds)
            or not 0 < self.request_timeout_seconds <= 300
        ):
            raise ValueError("Provider request timeout is invalid")
        for value, maximum, field_name in (
            (self.max_response_bytes, MAX_PROVIDER_RESPONSE_BYTES, "Provider response limit"),
            (self.max_stream_bytes, MAX_PROVIDER_STREAM_BYTES, "Provider stream limit"),
            (
                self.max_sse_event_bytes,
                MAX_PROVIDER_SSE_EVENT_BYTES,
                "Provider SSE event limit",
            ),
        ):
            if isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f"{field_name} is invalid")
        if self.max_sse_event_bytes > self.max_stream_bytes:
            raise ValueError("Provider SSE event limit cannot exceed its stream limit")

        object.__setattr__(self, "base_url", normalized_base_url)
        object.__setattr__(self, "models", models)

    def route_for(self, model: str) -> OpenAICompatibleModelRoute | None:
        """Resolve one fixed canonical model without accepting user-selected endpoints."""

        return next((route for route in self.models if route.model == model), None)


class _SseDataDecoder:
    """Incrementally frame bounded UTF-8 SSE data without unbounded line buffering."""

    def __init__(self, *, max_stream_bytes: int, max_event_bytes: int) -> None:
        self._max_stream_bytes = max_stream_bytes
        self._max_event_bytes = max_event_bytes
        self._total_bytes = 0
        self._line = bytearray()
        self._data_lines: list[bytes] = []
        self._event_bytes = 0
        self._skip_lf = False

    def feed(self, chunk: bytes) -> tuple[str, ...]:
        self._total_bytes += len(chunk)
        if self._total_bytes > self._max_stream_bytes:
            raise _InvalidProviderResponse

        events: list[str] = []
        for byte in chunk:
            if self._skip_lf:
                self._skip_lf = False
                if byte == 10:
                    continue
            if byte == 13:
                event = self._finish_line()
                self._skip_lf = True
            elif byte == 10:
                event = self._finish_line()
            else:
                self._line.append(byte)
                if len(self._line) > self._max_event_bytes:
                    raise _InvalidProviderResponse
                continue
            if event is not None:
                events.append(event)
        return tuple(events)

    def finish(self) -> tuple[str, ...]:
        """Flush a final unterminated line while preserving SSE dispatch rules."""

        events: list[str] = []
        if self._line:
            event = self._finish_line()
            if event is not None:
                events.append(event)
        if self._data_lines:
            event = self._finish_line()
            if event is not None:
                events.append(event)
        return tuple(events)

    def _finish_line(self) -> str | None:
        line = bytes(self._line)
        self._line.clear()
        if not line:
            if not self._data_lines:
                return None
            payload = b"\n".join(self._data_lines)
            self._data_lines.clear()
            self._event_bytes = 0
            try:
                return payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise _InvalidProviderResponse from None
        if line.startswith(b":"):
            return None
        field_name, separator, value = line.partition(b":")
        if field_name != b"data":
            return None
        if separator and value.startswith(b" "):
            value = value[1:]
        self._event_bytes += len(value) + (1 if self._data_lines else 0)
        if self._event_bytes > self._max_event_bytes:
            raise _InvalidProviderResponse
        self._data_lines.append(value)
        return None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _InvalidProviderResponse
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _InvalidProviderResponse
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise _InvalidProviderResponse
    return value


def _non_negative_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _InvalidProviderResponse
    return value


def _safe_provider_request_id(value: object) -> str | None:
    if isinstance(value, str) and _PROVIDER_REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return None


def _finish_reason(value: object) -> ModelFinishReason:
    try:
        return ModelFinishReason(_string(value))
    except ValueError:
        raise _InvalidProviderResponse from None


def _validate_response_identity(
    document: Mapping[str, object],
    *,
    expected_object: str,
    route: OpenAICompatibleModelRoute,
) -> tuple[str, str]:
    if document.get("object") != expected_object:
        raise _InvalidProviderResponse
    service_tier = document.get("service_tier")
    if service_tier is not None and service_tier != "default":
        raise _InvalidProviderResponse
    response_id = _string(document.get("id"))
    response_model = _string(document.get("model"))
    if (
        not _PROVIDER_REQUEST_ID_PATTERN.fullmatch(response_id)
        or response_model not in route.response_models
    ):
        raise _InvalidProviderResponse
    return response_id, response_model


def _parse_usage(
    value: object,
    *,
    route: OpenAICompatibleModelRoute,
) -> ModelUsage:
    document = _mapping(value)
    input_tokens = _non_negative_integer(document.get("prompt_tokens"))
    output_tokens = _non_negative_integer(document.get("completion_tokens"))
    total_tokens = _non_negative_integer(document.get("total_tokens"))
    if total_tokens != input_tokens + output_tokens:
        raise _InvalidProviderResponse

    details_value = document.get("prompt_tokens_details")
    if details_value is None:
        cached_input_tokens = 0
    else:
        details = _mapping(details_value)
        cached_input_tokens = _non_negative_integer(details.get("cached_tokens", 0))
    if cached_input_tokens > input_tokens:
        raise _InvalidProviderResponse

    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cost_micro_usd=route.calculate_cost(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        ),
        pricing_version=route.pricing_version,
    )


def _parse_complete_response(
    document: Mapping[str, object],
    *,
    request: ModelRequest,
    route: OpenAICompatibleModelRoute,
    header_request_id: str | None,
) -> ModelResponse:
    _validate_response_identity(
        document,
        expected_object="chat.completion",
        route=route,
    )
    choices = _list(document.get("choices"))
    if len(choices) != 1:
        raise _InvalidProviderResponse
    choice = _mapping(choices[0])
    if choice.get("index") != 0:
        raise _InvalidProviderResponse
    finish_reason = _finish_reason(choice.get("finish_reason"))
    if finish_reason is ModelFinishReason.REFUSAL:
        raise _InvalidProviderResponse
    message = _mapping(choice.get("message"))
    if message.get("role") != "assistant":
        raise _InvalidProviderResponse
    if message.get("tool_calls") is not None or message.get("function_call") is not None:
        raise _InvalidProviderResponse
    content = message.get("content")
    refusal = message.get("refusal")
    if refusal is not None:
        if content is not None or finish_reason is not ModelFinishReason.STOP:
            raise _InvalidProviderResponse
        output_text = _string(refusal)
        if not output_text.strip():
            raise _InvalidProviderResponse
        finish_reason = ModelFinishReason.REFUSAL
    elif content is None and finish_reason is ModelFinishReason.CONTENT_FILTER:
        output_text = ""
    else:
        output_text = _string(content)

    if request.response_schema is not None and finish_reason is ModelFinishReason.STOP:
        _validate_structured_output(output_text, request.response_schema)

    try:
        return ModelResponse(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            model=request.model,
            finish_reason=finish_reason,
            usage=_parse_usage(document.get("usage"), route=route),
            output_text=output_text,
            provider_request_id=header_request_id,
        )
    except ValueError:
        raise _InvalidProviderResponse from None


async def _read_bounded_response(response: httpx2.Response, *, maximum: int) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > maximum:
            raise _InvalidProviderResponse
        content.extend(chunk)
    return bytes(content)


async def _read_error_codes(response: httpx2.Response) -> frozenset[str]:
    try:
        content = await _read_bounded_response(
            response,
            maximum=MAX_PROVIDER_ERROR_BODY_BYTES,
        )
        document = _decode_json_object(content)
        error = _mapping(document.get("error"))
    except (ModelProviderError, _InvalidProviderResponse, httpx2.RequestError):
        return frozenset()
    candidates = (error.get("code"), error.get("type"))
    return frozenset(value for value in candidates if isinstance(value, str) and len(value) <= 128)


def _retry_after_seconds(response: httpx2.Response) -> int | None:
    raw_value = response.headers.get("retry-after")
    if raw_value is None or not raw_value.isascii() or not raw_value.isdecimal():
        return None
    if len(raw_value) > len(str(MAX_PROVIDER_RETRY_AFTER_SECONDS)):
        return None
    value = int(raw_value)
    return value if value <= MAX_PROVIDER_RETRY_AFTER_SECONDS else None


async def _raise_for_provider_status(
    response: httpx2.Response,
    *,
    provider_request_id: str | None,
) -> None:
    if response.status_code == 200:
        return
    error_codes = await _read_error_codes(response)
    if response.status_code == 429 and error_codes & _NON_RETRYABLE_LIMIT_CODES:
        code = ModelProviderErrorCode.CONFIGURATION
    elif response.status_code == 401:
        code = ModelProviderErrorCode.AUTHENTICATION_FAILED
    elif response.status_code == 403:
        code = ModelProviderErrorCode.PERMISSION_DENIED
    elif response.status_code == 408:
        code = ModelProviderErrorCode.TIMEOUT
    elif response.status_code == 429:
        code = ModelProviderErrorCode.RATE_LIMITED
    elif response.status_code in {400, 409, 422}:
        code = ModelProviderErrorCode.REQUEST_INVALID
    elif response.status_code == 404:
        code = ModelProviderErrorCode.CONFIGURATION
    elif 500 <= response.status_code <= 599:
        code = ModelProviderErrorCode.UNAVAILABLE
    else:
        code = ModelProviderErrorCode.REJECTED
    raise ModelProviderError(
        code,
        provider_request_id=provider_request_id,
        http_status=response.status_code,
        retry_after_seconds=(
            _retry_after_seconds(response) if code is ModelProviderErrorCode.RATE_LIMITED else None
        ),
    )


class OpenAICompatibleModelProvider:
    """Use a controlled egress client to map Chat Completions onto the model Port.

    The injected client is process-owned and must come from the unified WebFetch/egress
    composition; this Adapter neither creates a general-purpose client nor owns its lifetime.
    """

    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        config: OpenAICompatibleProviderConfig | None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._config = config
        self._clock = clock

    def _resolve(
        self,
        request: ModelRequest,
    ) -> tuple[OpenAICompatibleProviderConfig, OpenAICompatibleModelRoute]:
        if self._config is None:
            raise ModelProviderError(ModelProviderErrorCode.NOT_CONFIGURED)
        route = self._config.route_for(request.model)
        if route is None:
            raise ModelProviderError(ModelProviderErrorCode.NOT_CONFIGURED)
        return self._config, route

    def _remaining_seconds(self, request: ModelRequest) -> float:
        now = self._clock()
        require_utc(now, field_name="Provider clock")
        remaining = (request.deadline - now).total_seconds()
        if remaining <= 0:
            raise ModelProviderError(ModelProviderErrorCode.TIMEOUT)
        return remaining

    def _stream_io_timeout_seconds(
        self,
        request: ModelRequest,
        *,
        provider_deadline: float,
    ) -> float:
        """Bound one network wait without cancelling a consumer between yielded deltas."""

        now = self._clock()
        require_utc(now, field_name="Provider clock")
        run_remaining = (request.deadline - now).total_seconds()
        provider_remaining = provider_deadline - asyncio.get_running_loop().time()
        remaining = min(run_remaining, provider_remaining)
        if remaining <= 0:
            raise TimeoutError
        return remaining

    @staticmethod
    def _headers(
        config: OpenAICompatibleProviderConfig,
        request: ModelRequest,
        *,
        streaming: bool,
    ) -> dict[str, str]:
        return {
            "Accept": "text/event-stream" if streaming else "application/json",
            "Authorization": f"Bearer {config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "X-Client-Request-Id": str(request.step_id),
        }

    @staticmethod
    def _body(
        request: ModelRequest,
        route: OpenAICompatibleModelRoute,
        *,
        streaming: bool,
    ) -> dict[str, object]:
        messages: list[dict[str, object]] = []
        for message in request.messages:
            if not message.image_parts:
                content: object = message.content
            else:
                if not route.supports_image_input:
                    raise _InvalidProviderRequest
                content_parts: list[dict[str, object]] = [{"type": "text", "text": message.content}]
                for image in message.image_parts:
                    encoded = base64.b64encode(image.data).decode("ascii")
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image.media_type.value};base64,{encoded}",
                                "detail": "low",
                            },
                        }
                    )
                content = content_parts
            messages.append({"role": message.role.value, "content": content})

        body: dict[str, object] = {
            "model": route.upstream_model,
            "messages": messages,
            "max_completion_tokens": request.max_output_tokens,
            "n": 1,
            "service_tier": "default",
            "store": False,
            "stream": streaming,
        }
        if streaming:
            body["stream_options"] = {"include_usage": True}
        if request.response_schema is not None:
            _validate_supported_schema(request.response_schema)
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_response",
                    "strict": True,
                    "schema": _thaw_json_value(request.response_schema),
                },
            }
        return body

    @staticmethod
    def _provider_request_id(response: httpx2.Response) -> str | None:
        return _safe_provider_request_id(response.headers.get("x-request-id"))

    @staticmethod
    def _require_content_type(response: httpx2.Response, expected: str) -> None:
        content_type = response.headers.get("content-type", "").partition(";")[0]
        if content_type.strip().lower() != expected:
            raise _InvalidProviderResponse

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Perform one bounded non-streaming invocation without implicit retries."""

        config, route = self._resolve(request)
        remaining = self._remaining_seconds(request)
        effective_timeout = min(config.request_timeout_seconds, remaining)
        response_started = False
        provider_request_id: str | None = None
        try:
            async with asyncio.timeout(effective_timeout):
                async with self._client.stream(
                    "POST",
                    f"{config.base_url}/chat/completions",
                    headers=self._headers(config, request, streaming=False),
                    json=self._body(request, route, streaming=False),
                    follow_redirects=False,
                    timeout=effective_timeout,
                ) as response:
                    response_started = True
                    provider_request_id = self._provider_request_id(response)
                    await _raise_for_provider_status(
                        response,
                        provider_request_id=provider_request_id,
                    )
                    self._require_content_type(response, "application/json")
                    content = await _read_bounded_response(
                        response,
                        maximum=config.max_response_bytes,
                    )
                    document = _decode_json_object(content)
                    return _parse_complete_response(
                        document,
                        request=request,
                        route=route,
                        header_request_id=provider_request_id,
                    )
        except ModelProviderError:
            raise
        except _InvalidProviderRequest:
            raise ModelProviderError(ModelProviderErrorCode.REQUEST_INVALID) from None
        except httpx2.InvalidURL:
            raise ModelProviderError(ModelProviderErrorCode.CONFIGURATION) from None
        except TimeoutError:
            raise ModelProviderError(
                ModelProviderErrorCode.TIMEOUT,
                provider_request_id=provider_request_id,
            ) from None
        except httpx2.TimeoutException:
            raise ModelProviderError(
                ModelProviderErrorCode.TIMEOUT,
                provider_request_id=provider_request_id,
            ) from None
        except httpx2.RequestError:
            raise ModelProviderError(
                (
                    ModelProviderErrorCode.INCOMPLETE_RESPONSE
                    if response_started
                    else ModelProviderErrorCode.UNAVAILABLE
                ),
                provider_request_id=provider_request_id,
            ) from None
        except _InvalidProviderResponse:
            raise ModelProviderError(
                ModelProviderErrorCode.INVALID_RESPONSE,
                provider_request_id=provider_request_id,
            ) from None

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
        """Yield deltas, then complete only after finish, usage, and SSE [DONE]."""

        config, route = self._resolve(request)
        remaining = self._remaining_seconds(request)
        effective_timeout = min(config.request_timeout_seconds, remaining)
        provider_deadline = asyncio.get_running_loop().time() + effective_timeout
        response_started = False
        partial_response = False
        provider_request_id: str | None = None
        observed_usage: ModelUsage | None = None
        try:
            completed_item: ModelStreamCompleted | None = None
            async with AsyncExitStack() as response_stack:
                async with asyncio.timeout(
                    self._stream_io_timeout_seconds(
                        request,
                        provider_deadline=provider_deadline,
                    )
                ):
                    response = await response_stack.enter_async_context(
                        self._client.stream(
                            "POST",
                            f"{config.base_url}/chat/completions",
                            headers=self._headers(config, request, streaming=True),
                            json=self._body(request, route, streaming=True),
                            follow_redirects=False,
                            timeout=effective_timeout,
                        )
                    )
                response_started = True
                provider_request_id = self._provider_request_id(response)
                async with asyncio.timeout(
                    self._stream_io_timeout_seconds(
                        request,
                        provider_deadline=provider_deadline,
                    )
                ):
                    await _raise_for_provider_status(
                        response,
                        provider_request_id=provider_request_id,
                    )
                self._require_content_type(response, "text/event-stream")
                decoder = _SseDataDecoder(
                    max_stream_bytes=config.max_stream_bytes,
                    max_event_bytes=config.max_sse_event_bytes,
                )
                sequence = 0
                output_parts: list[str] = []
                stream_id: str | None = None
                response_model: str | None = None
                finish_reason: ModelFinishReason | None = None
                usage: ModelUsage | None = None
                output_kind: str | None = None
                saw_done = False

                chunks = response.aiter_bytes().__aiter__()
                while True:
                    try:
                        async with asyncio.timeout(
                            self._stream_io_timeout_seconds(
                                request,
                                provider_deadline=provider_deadline,
                            )
                        ):
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        break
                    decoded_events = decoder.feed(chunk)
                    for event_data in decoded_events:
                        if saw_done:
                            raise _InvalidProviderResponse
                        if event_data == "[DONE]":
                            saw_done = True
                            continue
                        (
                            stream_id,
                            response_model,
                            finish_reason,
                            usage,
                            output_kind,
                            delta,
                        ) = self._parse_stream_event(
                            event_data,
                            route=route,
                            stream_id=stream_id,
                            response_model=response_model,
                            finish_reason=finish_reason,
                            usage=usage,
                            output_kind=output_kind,
                        )
                        partial_response = True
                        observed_usage = usage
                        if delta:
                            output_parts.append(delta)
                            sequence += 1
                            yield ModelStreamDelta(
                                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                                sequence=sequence,
                                text=delta,
                            )
                    if saw_done:
                        if decoder.finish():
                            raise _InvalidProviderResponse
                        break

                if not saw_done and decoder.finish():
                    raise _IncompleteProviderResponse

                if (
                    not saw_done
                    or stream_id is None
                    or response_model is None
                    or finish_reason is None
                    or usage is None
                ):
                    raise _IncompleteProviderResponse
                output_text = "".join(output_parts)
                if request.response_schema is not None and finish_reason is ModelFinishReason.STOP:
                    _validate_structured_output(output_text, request.response_schema)
                try:
                    completed_response = ModelResponse(
                        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                        model=request.model,
                        finish_reason=finish_reason,
                        usage=usage,
                        output_text=output_text,
                        provider_request_id=provider_request_id,
                    )
                except ValueError:
                    raise _InvalidProviderResponse from None
                sequence += 1
                completed_item = ModelStreamCompleted(
                    schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                    sequence=sequence,
                    response=completed_response,
                )
            if completed_item is None:
                raise _IncompleteProviderResponse
            yield completed_item
        except ModelProviderError:
            raise
        except _InvalidProviderRequest:
            raise ModelProviderError(ModelProviderErrorCode.REQUEST_INVALID) from None
        except httpx2.InvalidURL:
            raise ModelProviderError(ModelProviderErrorCode.CONFIGURATION) from None
        except TimeoutError:
            raise ModelProviderError(
                ModelProviderErrorCode.TIMEOUT,
                provider_request_id=provider_request_id,
                partial_response=partial_response,
                usage=observed_usage,
            ) from None
        except httpx2.TimeoutException:
            raise ModelProviderError(
                ModelProviderErrorCode.TIMEOUT,
                provider_request_id=provider_request_id,
                partial_response=partial_response,
                usage=observed_usage,
            ) from None
        except httpx2.RequestError:
            raise ModelProviderError(
                (
                    ModelProviderErrorCode.INCOMPLETE_RESPONSE
                    if response_started
                    else ModelProviderErrorCode.UNAVAILABLE
                ),
                provider_request_id=provider_request_id,
                partial_response=partial_response,
                usage=observed_usage,
            ) from None
        except _IncompleteProviderResponse:
            raise ModelProviderError(
                ModelProviderErrorCode.INCOMPLETE_RESPONSE,
                provider_request_id=provider_request_id,
                partial_response=partial_response,
                usage=observed_usage,
            ) from None
        except _InvalidProviderResponse:
            raise ModelProviderError(
                ModelProviderErrorCode.INVALID_RESPONSE,
                provider_request_id=provider_request_id,
                partial_response=partial_response,
                usage=observed_usage,
            ) from None

    @staticmethod
    def _parse_stream_event(
        event_data: str,
        *,
        route: OpenAICompatibleModelRoute,
        stream_id: str | None,
        response_model: str | None,
        finish_reason: ModelFinishReason | None,
        usage: ModelUsage | None,
        output_kind: str | None,
    ) -> tuple[
        str,
        str,
        ModelFinishReason | None,
        ModelUsage | None,
        str | None,
        str | None,
    ]:
        document = _decode_json_object(event_data)
        current_id, current_model = _validate_response_identity(
            document,
            expected_object="chat.completion.chunk",
            route=route,
        )
        if stream_id is not None and current_id != stream_id:
            raise _InvalidProviderResponse
        if response_model is not None and current_model != response_model:
            raise _InvalidProviderResponse

        choices = _list(document.get("choices"))
        if not choices:
            if usage is not None or finish_reason is None or document.get("usage") is None:
                raise _InvalidProviderResponse
            return (
                current_id,
                current_model,
                finish_reason,
                _parse_usage(document.get("usage"), route=route),
                output_kind,
                None,
            )
        if len(choices) != 1 or usage is not None:
            raise _InvalidProviderResponse
        choice = _mapping(choices[0])
        if choice.get("index") != 0:
            raise _InvalidProviderResponse
        if finish_reason is not None:
            raise _InvalidProviderResponse
        delta_document = _mapping(choice.get("delta"))
        if delta_document.get("tool_calls") is not None or (
            delta_document.get("function_call") is not None
        ):
            raise _InvalidProviderResponse
        role = delta_document.get("role")
        if role is not None and role != "assistant":
            raise _InvalidProviderResponse
        content = delta_document.get("content")
        if content is not None and not isinstance(content, str):
            raise _InvalidProviderResponse
        refusal = delta_document.get("refusal")
        if refusal is not None and not isinstance(refusal, str):
            raise _InvalidProviderResponse
        if content is not None and refusal is not None:
            raise _InvalidProviderResponse

        delta = content or refusal or None
        next_output_kind = output_kind
        if delta:
            current_output_kind = "refusal" if refusal is not None else "content"
            if output_kind is not None and output_kind != current_output_kind:
                raise _InvalidProviderResponse
            next_output_kind = current_output_kind

        raw_finish_reason = choice.get("finish_reason")
        next_finish_reason = finish_reason
        if raw_finish_reason is not None:
            parsed_finish_reason = _finish_reason(raw_finish_reason)
            if parsed_finish_reason is ModelFinishReason.REFUSAL:
                raise _InvalidProviderResponse
            if next_output_kind == "refusal":
                if parsed_finish_reason is not ModelFinishReason.STOP:
                    raise _InvalidProviderResponse
                parsed_finish_reason = ModelFinishReason.REFUSAL
            next_finish_reason = parsed_finish_reason
        if document.get("usage") is not None:
            raise _InvalidProviderResponse
        return (
            current_id,
            current_model,
            next_finish_reason,
            usage,
            next_output_kind,
            delta,
        )
