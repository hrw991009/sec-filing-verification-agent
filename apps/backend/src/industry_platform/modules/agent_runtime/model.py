"""Provider-neutral model request, streaming, response, and usage contracts."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)

_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ModelRole(StrEnum):
    """Provider-independent roles visible to the model."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ModelFinishReason(StrEnum):
    """Normalized reasons why one model response stopped producing output."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


def _require_non_negative_integer(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One bounded message prepared by the Context Compiler."""

    role: ModelRole
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Model message content must not be blank")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One normalized model invocation independent of any vendor SDK."""

    schema_version: int
    run_id: UUID
    step_id: UUID
    workspace_id: UUID
    model: str
    messages: tuple[ModelMessage, ...] = field(repr=False)
    max_output_tokens: int
    deadline: datetime
    response_schema: Mapping[str, object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.run_id, "Model request run ID"),
            (self.step_id, "Model request step ID"),
            (self.workspace_id, "Model request workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if not _MODEL_NAME_PATTERN.fullmatch(self.model):
            raise ValueError("Model name is invalid")
        messages = tuple(self.messages)
        if not messages:
            raise ValueError("Model request requires at least one message")
        object.__setattr__(self, "messages", messages)
        if (
            isinstance(self.max_output_tokens, bool)
            or not 1 <= self.max_output_tokens <= MAX_RUN_TOKENS
        ):
            raise ValueError("Model max output tokens are invalid")
        require_utc(self.deadline, field_name="Model request deadline")
        if self.response_schema is not None:
            object.__setattr__(
                self,
                "response_schema",
                snapshot_json_mapping(
                    self.response_schema,
                    error_message="Model response schema must be canonical JSON data",
                ),
            )


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Normalized integer usage and cost accounting for one model call."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_micro_usd: int

    def __post_init__(self) -> None:
        for usage_value, field_name in (
            (self.input_tokens, "Model input tokens"),
            (self.output_tokens, "Model output tokens"),
            (self.cached_input_tokens, "Model cached input tokens"),
            (self.cost_micro_usd, "Model cost"),
        ):
            _require_non_negative_integer(usage_value, field_name=field_name)
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed all input tokens")

    @property
    def total_tokens(self) -> int:
        """Return the total units counted against the Run token budget."""

        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized completed response without exposing a vendor object."""

    schema_version: int
    model: str
    finish_reason: ModelFinishReason
    usage: ModelUsage
    output_text: str = field(repr=False)
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        if not _MODEL_NAME_PATTERN.fullmatch(self.model):
            raise ValueError("Response model name is invalid")
        if self.finish_reason is not ModelFinishReason.CONTENT_FILTER and (
            not self.output_text.strip()
        ):
            raise ValueError("A completed model response must contain output")
        if self.provider_request_id is not None and not _PROVIDER_REQUEST_ID_PATTERN.fullmatch(
            self.provider_request_id
        ):
            raise ValueError("Provider request ID is invalid")


@dataclass(frozen=True, slots=True)
class ModelStreamDelta:
    """One non-empty normalized text delta from a streaming response."""

    schema_version: int
    sequence: int
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Model stream sequence must be a positive integer")
        if not self.text:
            raise ValueError("Model stream delta must not be empty")


@dataclass(frozen=True, slots=True)
class ModelStreamCompleted:
    """The single terminal streaming item carrying normalized usage."""

    schema_version: int
    sequence: int
    response: ModelResponse = field(repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Model stream sequence must be a positive integer")


type ModelStreamItem = ModelStreamDelta | ModelStreamCompleted


def validate_model_stream(
    items: Sequence[ModelStreamItem],
    request: ModelRequest,
) -> ModelResponse:
    """Validate one complete stream and return its terminal response."""

    if not items:
        raise ValueError("Model stream must contain a completed item")

    deltas: list[str] = []
    completed: ModelStreamCompleted | None = None
    for expected_sequence, item in enumerate(items, start=1):
        if item.sequence != expected_sequence:
            raise ValueError("Model stream sequence must be contiguous and start at one")
        if completed is not None:
            raise ValueError("No model stream item may follow completion")
        if isinstance(item, ModelStreamDelta):
            deltas.append(item.text)
        else:
            completed = item

    if completed is None or completed is not items[-1]:
        raise ValueError("Model stream requires exactly one final completed item")
    if completed.response.model != request.model:
        raise ValueError("Model stream response does not match the requested model")
    if "".join(deltas) != completed.response.output_text:
        raise ValueError("Model stream deltas do not match the completed output")
    return completed.response
