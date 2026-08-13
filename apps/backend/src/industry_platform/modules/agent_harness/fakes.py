"""Strict deterministic ModelProvider test boundary used by the Agent Harness."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from industry_platform.modules.agent_harness.scenarios import snapshot_harness_json_mapping
from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.model import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
)


class FakeModelOperation(StrEnum):
    """The ModelProvider method expected by one scripted exchange."""

    STREAM = "stream"
    COMPLETE = "complete"


class FakeModelScriptError(RuntimeError):
    """Base class for deterministic Fake Model contract failures."""


class FakeModelScriptExhaustedError(FakeModelScriptError):
    """Raised when Runtime performs more model calls than the fixture allows."""


class FakeModelScriptMismatchError(FakeModelScriptError):
    """Raised when the next operation or sanitized request projection differs."""


class UnconsumedFakeModelScriptError(FakeModelScriptError):
    """Raised when an execution stopped before consuming its declared fixtures."""


@dataclass(frozen=True, slots=True)
class ModelRequestExpectation:
    """Stable request fields that exclude dynamic IDs and absolute deadlines."""

    model: str
    workspace_id: UUID = field(repr=False)
    messages: tuple[ModelMessage, ...] = field(repr=False)
    max_output_tokens: int
    response_schema: Mapping[str, object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.model.strip() or len(self.model) > 200:
            raise ValueError("Fake Model expected model is invalid")
        require_non_nil_uuid(self.workspace_id, field_name="Fake Model expected workspace ID")
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("Fake Model expectation requires messages")
        object.__setattr__(self, "messages", messages)
        if (
            isinstance(self.max_output_tokens, bool)
            or not 1 <= self.max_output_tokens <= MAX_RUN_TOKENS
        ):
            raise ValueError("Fake Model expected output budget is invalid")
        if self.response_schema is not None:
            object.__setattr__(
                self,
                "response_schema",
                snapshot_harness_json_mapping(
                    self.response_schema,
                    error_message="Fake Model response schema must be canonical JSON data",
                ),
            )

    @classmethod
    def from_request(cls, request: ModelRequest) -> ModelRequestExpectation:
        """Capture only fields that should remain stable across repeated Runs."""

        return cls(
            model=request.model,
            workspace_id=request.workspace_id,
            messages=request.messages,
            max_output_tokens=request.max_output_tokens,
            response_schema=request.response_schema,
        )

    def matches(self, request: ModelRequest) -> bool:
        """Compare without exposing prompt content in an error message."""

        response_schema = (
            None
            if request.response_schema is None
            else snapshot_harness_json_mapping(
                request.response_schema,
                error_message="Model response schema must be canonical JSON data",
            )
        )
        return (
            self.model == request.model
            and self.workspace_id == request.workspace_id
            and self.messages == request.messages
            and self.max_output_tokens == request.max_output_tokens
            and self.response_schema == response_schema
        )


@dataclass(frozen=True, slots=True)
class ScriptedModelExchange:
    """Exactly one expected operation and exactly one normalized outcome."""

    operation: FakeModelOperation
    expectation: ModelRequestExpectation
    response: ModelResponse | None = field(default=None, repr=False)
    stream_items: tuple[ModelStreamItem, ...] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.operation is FakeModelOperation.COMPLETE:
            if self.response is None or self.stream_items is not None:
                raise ValueError("A complete Fake exchange requires only a response")
            return
        if self.stream_items is None or self.response is not None:
            raise ValueError("A streaming Fake exchange requires only stream items")
        object.__setattr__(self, "stream_items", tuple(self.stream_items))


class ScriptedModelProvider:
    """Consume a per-execution script in order, with no retry or success fallback."""

    def __init__(self, exchanges: Sequence[ScriptedModelExchange]) -> None:
        self._exchanges = tuple(exchanges)
        if not self._exchanges:
            raise ValueError("A Scripted Model Provider requires at least one exchange")
        self._cursor = 0
        self._requests: list[ModelRequest] = []
        self._stream_active = False
        self._stream_abandoned = False

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        """Return requests accepted by the scripted external boundary."""

        return tuple(self._requests)

    @property
    def remaining_exchange_count(self) -> int:
        """Return how many declared external interactions have not occurred."""

        return len(self._exchanges) - self._cursor

    def _next_exchange(
        self,
        operation: FakeModelOperation,
        request: ModelRequest,
    ) -> ScriptedModelExchange:
        if self._stream_active or self._stream_abandoned:
            raise FakeModelScriptMismatchError(
                "The previous Fake Model stream was not fully consumed"
            )
        if self._cursor >= len(self._exchanges):
            raise FakeModelScriptExhaustedError("Fake Model script is exhausted")
        exchange = self._exchanges[self._cursor]
        if exchange.operation is not operation:
            raise FakeModelScriptMismatchError("Fake Model operation does not match the script")
        if not exchange.expectation.matches(request):
            raise FakeModelScriptMismatchError(
                "Fake Model request does not match the scripted projection"
            )
        return exchange

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
        """Yield the declared stream verbatim, including malformed fault fixtures."""

        exchange = self._next_exchange(FakeModelOperation.STREAM, request)
        if exchange.stream_items is None:
            raise AssertionError("Validated streaming exchange lost its items")
        self._stream_active = True
        self._requests.append(request)
        fully_delivered = False
        try:
            for item in exchange.stream_items:
                yield item
            self._cursor += 1
            fully_delivered = True
        finally:
            self._stream_active = False
            if not fully_delivered:
                self._stream_abandoned = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the declared normalized response without implicit retries."""

        exchange = self._next_exchange(FakeModelOperation.COMPLETE, request)
        if exchange.response is None:
            raise AssertionError("Validated complete exchange lost its response")
        self._cursor += 1
        self._requests.append(request)
        return exchange.response

    def assert_exhausted(self) -> None:
        """Fail an evaluation whose expected external calls were not all made."""

        if self._stream_active or self._stream_abandoned or self.remaining_exchange_count:
            raise UnconsumedFakeModelScriptError("Fake Model script has unconsumed exchanges")
