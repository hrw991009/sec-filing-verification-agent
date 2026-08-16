"""Provider-neutral Tool, approval, and Observation contracts for Agent Runtime."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, cast
from urllib.parse import urlsplit
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction

TOOL_ACTION_SCHEMA_VERSION: Final = 1
TOOL_OBSERVATION_NORMALIZER_VERSION: Final = "tool-observation-v1"
TOOL_SOURCE_LOCATOR_SCHEMES: Final = frozenset({"fixture", "https"})
MAX_TOOL_ACTION_BYTES: Final = 32_768
MAX_TOOL_SCHEMA_BYTES: Final = 100_000
MAX_TOOL_DESCRIPTION_LENGTH: Final = 1_000
MAX_TOOL_OBSERVATION_TEXT_LENGTH: Final = 50_000
MAX_TOOL_SOURCES: Final = 16
MAX_TOOL_COST_MICRO_USD: Final = 1_000_000_000
MAX_TOOL_IDEMPOTENCY_KEY_BYTES: Final = 512

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_REASON_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ToolCostClass(StrEnum):
    """Coarse server-owned cost class used before execution."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolSideEffectClass(StrEnum):
    """Whether retrying a Tool can change external state."""

    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"


class ToolApprovalPolicy(StrEnum):
    """Static Day 3 policy declared by trusted Tool configuration."""

    AUTO_ALLOW = "auto_allow"
    AUTO_DENY = "auto_deny"
    REQUIRE_APPROVAL = "require_approval"


class ToolRetryClassification(StrEnum):
    """Trusted retry eligibility metadata; it never authorizes an implicit retry."""

    NEVER = "never"
    SAFE_READ_ONLY = "safe_read_only"
    IDEMPOTENT_WRITE = "idempotent_write"


class ToolApprovalOutcome(StrEnum):
    """Persisted result of evaluating trusted policy context."""

    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class ToolCallStatus(StrEnum):
    """Audit lifecycle; Agent State remains owned by Run/Step/Event."""

    REQUESTED = "requested"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _require_reference(value: str, *, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_enum_instance(value: object, enum_type: type[StrEnum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__} instance")


def _normalize_description(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_TOOL_DESCRIPTION_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Tool description is invalid")
    return value


def _validate_side_effect_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Tool Call idempotency key is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Tool Call idempotency key is invalid") from None
    if not 16 <= len(encoded) <= MAX_TOOL_IDEMPOTENCY_KEY_BYTES or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("Tool Call idempotency key is invalid")
    return value


def side_effect_idempotency_key_sha256(value: str) -> str:
    """Validate one in-memory side-effect key and return only its durable digest."""

    validated = _validate_side_effect_idempotency_key(value)
    return hashlib.sha256(validated.encode("utf-8")).hexdigest()


def _canonical_mapping(value: Mapping[str, object], *, field_name: str) -> Mapping[str, object]:
    return snapshot_json_mapping(value, error_message=f"{field_name} must be canonical JSON data")


def canonical_mapping_sha256(value: Mapping[str, object]) -> str:
    """Hash canonical JSON without retaining the original value in an audit summary."""

    encoded = json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitized_arguments_summary(arguments: Mapping[str, object]) -> Mapping[str, object]:
    """Return structural metadata only, never model-supplied argument values."""

    encoded_size = len(
        json.dumps(
            dict(arguments),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return _canonical_mapping(
        {
            "argument_count": len(arguments),
            "canonical_bytes": encoded_size,
        },
        field_name="Sanitized Tool argument summary",
    )


@dataclass(frozen=True, slots=True)
class ToolReference:
    """One exact Tool version exposed by a trusted profile surface."""

    name: str
    version: str

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("Tool reference name is invalid")
        _require_reference(self.version, field_name="Tool reference version")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Versioned schema, capability, budget, side-effect, and approval declaration."""

    schema_version: int
    name: str
    version: str
    description: str
    input_schema_version: str
    output_schema_version: str
    input_schema: Mapping[str, object] = field(repr=False)
    output_schema: Mapping[str, object] = field(repr=False)
    capability: WorkspaceAction
    timeout_ms: int
    max_result_bytes: int
    max_cost_micro_usd: int
    cost_class: ToolCostClass
    side_effect_class: ToolSideEffectClass
    retry_classification: ToolRetryClassification
    approval_policy: ToolApprovalPolicy
    policy_version: str

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        ToolReference(self.name, self.version)
        object.__setattr__(self, "description", _normalize_description(self.description))
        for enum_value, enum_type, field_name in (
            (self.capability, WorkspaceAction, "Tool capability"),
            (self.cost_class, ToolCostClass, "Tool cost class"),
            (self.side_effect_class, ToolSideEffectClass, "Tool side-effect class"),
            (
                self.retry_classification,
                ToolRetryClassification,
                "Tool retry classification",
            ),
            (self.approval_policy, ToolApprovalPolicy, "Tool approval policy"),
        ):
            _require_enum_instance(enum_value, enum_type, field_name=field_name)
        for reference_value, field_name in (
            (self.input_schema_version, "Tool input schema version"),
            (self.output_schema_version, "Tool output schema version"),
            (self.policy_version, "Tool policy version"),
        ):
            _require_reference(reference_value, field_name=field_name)
        if isinstance(self.timeout_ms, bool) or not 1 <= self.timeout_ms <= 300_000:
            raise ValueError("Tool timeout is invalid")
        for numeric_value, maximum, field_name in (
            (self.max_result_bytes, 10_000_000, "Tool result byte limit"),
            (self.max_cost_micro_usd, MAX_TOOL_COST_MICRO_USD, "Tool cost limit"),
        ):
            if isinstance(numeric_value, bool) or not 1 <= numeric_value <= maximum:
                raise ValueError(f"{field_name} is invalid")
        for attribute, schema, field_name in (
            ("input_schema", self.input_schema, "Tool input schema"),
            ("output_schema", self.output_schema, "Tool output schema"),
        ):
            snapshot = _canonical_mapping(schema, field_name=field_name)
            encoded = json.dumps(dict(snapshot), separators=(",", ":")).encode("utf-8")
            if (
                len(encoded) > MAX_TOOL_SCHEMA_BYTES
                or snapshot.get("type") != "object"
                or snapshot.get("additionalProperties") is not False
            ):
                raise ValueError(f"{field_name} must be a bounded strict object schema")
            object.__setattr__(self, attribute, snapshot)
        if (
            self.retry_classification is ToolRetryClassification.SAFE_READ_ONLY
            and self.side_effect_class is not ToolSideEffectClass.READ_ONLY
        ):
            raise ValueError("Read-only retry classification requires a read-only Tool")
        if (
            self.retry_classification is ToolRetryClassification.IDEMPOTENT_WRITE
            and self.side_effect_class is not ToolSideEffectClass.IDEMPOTENT_WRITE
        ):
            raise ValueError("Idempotent retry classification requires an idempotent write Tool")

    @property
    def reference(self) -> ToolReference:
        return ToolReference(self.name, self.version)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Tool Action contains duplicate object keys")
        document[key] = value
    return document


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Tool Action contains a non-finite number: {value}")


@dataclass(frozen=True, slots=True)
class ToolAction:
    """The only structured L1 action a model may request."""

    schema_version: int
    name: str
    version: str
    arguments: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        ToolReference(self.name, self.version)
        arguments = _canonical_mapping(self.arguments, field_name="Tool Action arguments")
        if len(json.dumps(dict(arguments), separators=(",", ":")).encode("utf-8")) > (
            MAX_TOOL_ACTION_BYTES
        ):
            raise ValueError("Tool Action arguments exceed the size limit")
        object.__setattr__(self, "arguments", arguments)

    @classmethod
    def from_json(cls, serialized: str) -> ToolAction:
        """Decode one strict bounded object; do not accept prose or extra fields."""

        if len(serialized.encode("utf-8")) > MAX_TOOL_ACTION_BYTES:
            raise ValueError("Tool Action exceeds the size limit")
        try:
            loaded = json.loads(
                serialized,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except (json.JSONDecodeError, RecursionError):
            raise ValueError("Tool Action is not valid JSON") from None
        if not isinstance(loaded, dict) or set(loaded) != {
            "schema_version",
            "kind",
            "name",
            "version",
            "arguments",
        }:
            raise ValueError("Tool Action fields are invalid")
        if loaded["kind"] != "tool_call":
            raise ValueError("Tool Action kind is unsupported")
        schema_version = loaded["schema_version"]
        name = loaded["name"]
        version = loaded["version"]
        arguments = loaded["arguments"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(arguments, dict)
        ):
            raise ValueError("Tool Action field types are invalid")
        return cls(
            schema_version=schema_version,
            name=name,
            version=version,
            arguments=cast(dict[str, object], arguments),
        )


def tool_action_response_schema(definition: ToolDefinition) -> Mapping[str, object]:
    """Bind structured output to the one exact L1 Tool selected by trusted policy.

    The schema intentionally uses the project's existing provider-compatible strict
    subset. Pydantic performs a second, richer typed validation after decoding.
    """

    return _canonical_mapping(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "kind", "name", "version", "arguments"],
            "properties": {
                "schema_version": {"type": "integer", "const": TOOL_ACTION_SCHEMA_VERSION},
                "kind": {"type": "string", "const": "tool_call"},
                "name": {"type": "string", "const": definition.name},
                "version": {"type": "string", "const": definition.version},
                "arguments": dict(definition.input_schema),
            },
        },
        field_name="Tool Action response schema",
    )


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    """Static decision derived from trusted policy, never from model text."""

    outcome: ToolApprovalOutcome
    policy_version: str
    reason_code: str

    def __post_init__(self) -> None:
        _require_reference(self.policy_version, field_name="Tool policy decision version")
        if not _REASON_CODE_PATTERN.fullmatch(self.reason_code):
            raise ValueError("Tool policy decision reason is invalid")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Frozen request contract; durable interrupt/resume remains a Day 5 concern."""

    schema_version: int
    approval_request_id: UUID
    call_id: UUID
    run_id: UUID
    workspace_id: UUID
    requested_by_user_id: UUID
    tool: ToolReference
    policy_version: str
    reason_code: str
    requested_at: datetime

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.approval_request_id, "Approval request ID"),
            (self.call_id, "Approval request Tool Call ID"),
            (self.run_id, "Approval request Run ID"),
            (self.workspace_id, "Approval request Workspace ID"),
            (self.requested_by_user_id, "Approval request user ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        _require_reference(self.policy_version, field_name="Approval request policy version")
        if not _REASON_CODE_PATTERN.fullmatch(self.reason_code):
            raise ValueError("Approval request reason is invalid")
        require_utc(self.requested_at, field_name="Approval request time")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Typed future decision record; Step 1 only emits approval_required and stops."""

    schema_version: int
    approval_request_id: UUID
    decided_by_user_id: UUID
    outcome: ToolApprovalOutcome
    policy_version: str
    reason_code: str
    decided_at: datetime

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        require_non_nil_uuid(self.approval_request_id, field_name="Approval decision request ID")
        require_non_nil_uuid(self.decided_by_user_id, field_name="Approval decision user ID")
        if self.outcome is ToolApprovalOutcome.APPROVAL_REQUIRED:
            raise ValueError("An Approval Decision must be allow or deny")
        _require_reference(self.policy_version, field_name="Approval decision policy version")
        if not _REASON_CODE_PATTERN.fullmatch(self.reason_code):
            raise ValueError("Approval decision reason is invalid")
        require_utc(self.decided_at, field_name="Approval decision time")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A server-validated call ready for one exact registered Tool implementation."""

    schema_version: int
    call_id: UUID
    run_id: UUID
    workspace_id: UUID
    requested_by_step_id: UUID
    requested_by_user_id: UUID
    definition: ToolDefinition
    arguments: Mapping[str, object] = field(repr=False)
    decision: ToolPolicyDecision
    requested_at: datetime
    side_effect_idempotency_key: str | None = field(default=None, repr=False)
    idempotency_key_sha256: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.call_id, "Tool Call ID"),
            (self.run_id, "Tool Call Run ID"),
            (self.workspace_id, "Tool Call Workspace ID"),
            (self.requested_by_step_id, "Tool Call requesting Step ID"),
            (self.requested_by_user_id, "Tool Call user ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        arguments = _canonical_mapping(self.arguments, field_name="Validated Tool arguments")
        object.__setattr__(self, "arguments", arguments)
        require_utc(self.requested_at, field_name="Tool Call request time")
        if self.decision.outcome is not ToolApprovalOutcome.ALLOW:
            raise ValueError("Only an allowed Tool Call can be prepared for execution")
        requires_key = self.definition.side_effect_class is not ToolSideEffectClass.READ_ONLY
        has_raw_key = self.side_effect_idempotency_key is not None
        has_hash = self.idempotency_key_sha256 is not None
        if requires_key != has_raw_key or requires_key != has_hash:
            raise ValueError("Write Tool Calls require exactly one idempotency key hash")
        if self.side_effect_idempotency_key is not None:
            raw_key = _validate_side_effect_idempotency_key(self.side_effect_idempotency_key)
            if (
                self.idempotency_key_sha256 is None
                or not _SHA256_PATTERN.fullmatch(self.idempotency_key_sha256)
                or side_effect_idempotency_key_sha256(raw_key) != self.idempotency_key_sha256
            ):
                raise ValueError("Tool Call idempotency key hash is invalid")

    @property
    def arguments_sha256(self) -> str:
        return canonical_mapping_sha256(self.arguments)

    @property
    def sanitized_input_summary(self) -> Mapping[str, object]:
        return sanitized_arguments_summary(self.arguments)


@dataclass(frozen=True, slots=True)
class ToolSource:
    """A normalized source locator carried by an untrusted Observation."""

    source_type: str
    source_version: str
    locator: str = field(repr=False)
    observed_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        _require_reference(self.source_type, field_name="Tool source type")
        _require_reference(self.source_version, field_name="Tool source version")
        locator = self.locator
        if (
            not isinstance(locator, str)
            or not locator.strip()
            or locator != locator.strip()
            or len(locator) > 2_048
            or "\\" in locator
            or any(
                ord(character) < 32 or character.isspace() or ord(character) == 127
                for character in locator
            )
        ):
            raise ValueError("Tool source locator is invalid")
        try:
            parsed = urlsplit(locator)
            has_userinfo = parsed.username is not None or parsed.password is not None
            _port = parsed.port
        except ValueError:
            raise ValueError("Tool source locator is invalid") from None
        if (
            parsed.scheme not in TOOL_SOURCE_LOCATOR_SCHEMES
            or parsed.hostname is None
            or has_userinfo
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Tool source locator is invalid")
        require_utc(self.observed_at, field_name="Tool source observation time")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Tool source content hash is invalid")


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """Bounded normalized Tool output; still untrusted and not Evidence."""

    schema_version: int
    observation_id: UUID
    call_id: UUID
    run_id: UUID
    workspace_id: UUID
    tool: ToolReference
    normalizer_version: str
    model_text: str = field(repr=False)
    sources: tuple[ToolSource, ...]
    observed_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.observation_id, "Tool Observation ID"),
            (self.call_id, "Tool Observation Call ID"),
            (self.run_id, "Tool Observation Run ID"),
            (self.workspace_id, "Tool Observation Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        _require_reference(self.normalizer_version, field_name="Tool Observation normalizer")
        normalized = self.model_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if (
            not normalized
            or len(normalized) > MAX_TOOL_OBSERVATION_TEXT_LENGTH
            or any(
                ord(character) < 32 and character not in {"\n", "\t"} for character in normalized
            )
        ):
            raise ValueError("Tool Observation model text is invalid")
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("Tool Observation content hash is invalid")
        require_utc(self.observed_at, field_name="Tool Observation time")
        sources = tuple(self.sources)
        if (
            not 1 <= len(sources) <= MAX_TOOL_SOURCES
            or len({(item.source_type, item.locator, item.content_sha256) for item in sources})
            != len(sources)
            or any(item.observed_at > self.observed_at for item in sources)
        ):
            raise ValueError("Tool Observation sources are invalid")
        object.__setattr__(self, "model_text", normalized)
        object.__setattr__(self, "sources", sources)

    @property
    def sanitized_output_summary(self) -> Mapping[str, object]:
        return _canonical_mapping(
            {
                "normalizer_version": self.normalizer_version,
                "source_count": len(self.sources),
                "text_characters": len(self.model_text),
                "content_sha256": self.content_sha256,
            },
            field_name="Sanitized Tool output summary",
        )

    def to_model_visible_envelope(self) -> Mapping[str, object]:
        """Return the exact normalized payload whose digest binds content and provenance."""

        return _canonical_mapping(
            {
                "observation_id": str(self.observation_id),
                "tool_call_id": str(self.call_id),
                "tool": {"name": self.tool.name, "version": self.tool.version},
                "source": {
                    "name": "normalized_tool_result",
                    "version": self.normalizer_version,
                },
                "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
                "locator": {
                    "sources": [
                        {
                            "source_type": source.source_type,
                            "source_version": source.source_version,
                            "locator": source.locator,
                            "observed_at": source.observed_at.isoformat().replace("+00:00", "Z"),
                            "content_sha256": source.content_sha256,
                        }
                        for source in self.sources
                    ]
                },
                "content_sha256": self.content_sha256,
                "content": self.model_text,
            },
            field_name="Tool Observation model-visible envelope",
        )

    @property
    def model_visible_envelope_sha256(self) -> str:
        """Bind all model-visible Observation content and provenance with one digest."""

        return tool_observation_envelope_sha256(self)

    def to_persistence_payload(self) -> Mapping[str, object]:
        """Persist only the bounded normalized envelope, never the raw adapter response."""

        return _canonical_mapping(
            {
                "schema_version": self.schema_version,
                "observation_id": str(self.observation_id),
                "call_id": str(self.call_id),
                "tool_name": self.tool.name,
                "tool_version": self.tool.version,
                "normalizer_version": self.normalizer_version,
                "model_text": self.model_text,
                "content_sha256": self.content_sha256,
                "observed_at": self.observed_at.isoformat(),
                "sources": [
                    {
                        "source_type": source.source_type,
                        "source_version": source.source_version,
                        "locator": source.locator,
                        "observed_at": source.observed_at.isoformat(),
                        "content_sha256": source.content_sha256,
                    }
                    for source in self.sources
                ],
            },
            field_name="Tool Observation persistence envelope",
        )


def tool_observation_envelope_sha256(observation: ToolObservation) -> str:
    """Hash the canonical model-visible Observation envelope, including provenance."""

    return canonical_mapping_sha256(observation.to_model_visible_envelope())


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """One successful validated execution with its normalized Observation."""

    call: ToolCall = field(repr=False)
    observation: ToolObservation = field(repr=False)
    actual_cost_micro_usd: int
    completed_at: datetime
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            self.observation.call_id != self.call.call_id
            or self.observation.run_id != self.call.run_id
            or self.observation.workspace_id != self.call.workspace_id
            or self.observation.tool != self.call.definition.reference
        ):
            raise ValueError("Tool execution result does not match its Call")
        require_utc(self.completed_at, field_name="Tool execution completion time")
        if self.completed_at < self.call.requested_at:
            raise ValueError("Tool execution completion is out of order")
        if isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise ValueError("Tool execution duration is invalid")
        if (
            isinstance(self.actual_cost_micro_usd, bool)
            or not isinstance(self.actual_cost_micro_usd, int)
            or not 0 <= self.actual_cost_micro_usd <= MAX_TOOL_COST_MICRO_USD
        ):
            raise ValueError("Tool execution actual cost is invalid")
        if self.actual_cost_micro_usd > self.call.definition.max_cost_micro_usd:
            raise ValueError("Tool execution actual cost exceeds its declared limit")


def tool_references(values: Sequence[ToolReference]) -> tuple[ToolReference, ...]:
    """Freeze an exact allowlist and reject ambiguous duplicate references."""

    references = tuple(values)
    if len(references) != len(set(references)):
        raise ValueError("Tool surface contains duplicate references")
    return references
