"""Typed Memory candidates, policy decisions, revisions, and commands."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.identity.domain import TraceId

MEMORY_SCHEMA_VERSION: Final = 1
MAX_MEMORY_CONTENT_LENGTH: Final = 4_000
MAX_MEMORY_SOURCE_MESSAGES: Final = 8
MAX_MEMORY_LIST_SIZE: Final = 100
MAX_IDEMPOTENCY_KEY_LENGTH: Final = 200

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,200}$")
_SENSITIVE_PATTERNS: Final = (
    re.compile(
        r"(?i)(?:password|passwd|api[_ -]?key|client[_ -]?secret|access[_ -]?token|"
        r"refresh[_ -]?token|cookie)\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/]+=*"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:密码|密钥|令牌|凭据)\s*[:：=]\s*\S+"),  # noqa: RUF001
)


class MemoryScope(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    INSTRUCTION = "instruction"
    NOTE = "note"


class MemoryStatus(StrEnum):
    CONFIRMED = "confirmed"
    DISABLED = "disabled"
    EXPIRED = "expired"
    DELETED = "deleted"


class MemoryCandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class MemoryPolicyDecision(StrEnum):
    ALLOWED = "allowed"
    REQUIRES_EDIT = "requires_edit"
    REJECTED = "rejected"


class MemoryPolicyReason(StrEnum):
    USER_AUTHORED = "user_authored"
    MIXED_SOURCES = "mixed_sources"
    ASSISTANT_ONLY_REQUIRES_EDIT = "assistant_only_requires_edit"
    SENSITIVE_CONTENT = "sensitive_content"


class MemoryWriteAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MERGE = "merge"


class MemoryRevisionValidity(StrEnum):
    VALID = "valid"
    WITHDRAWN = "withdrawn"


class MemoryFeedbackValue(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class MemoryError(Exception):
    """Base class for safe Memory failures."""


class MemoryNotFoundError(MemoryError):
    pass


class MemoryCandidateNotFoundError(MemoryError):
    pass


class MemorySourceNotFoundError(MemoryError):
    pass


class MemoryConflictError(MemoryError):
    pass


class MemoryIdempotencyConflictError(MemoryConflictError):
    pass


class MemoryCandidateEditRequiredError(MemoryError):
    pass


class MemoryRequestRejectedError(MemoryError):
    pass


class MemoryPersistenceError(MemoryError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Memory persistence is unavailable")
        self.sqlstate = sqlstate


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_memory_content(value: str) -> str:
    normalized = "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()
    if not normalized or len(normalized) > MAX_MEMORY_CONTENT_LENGTH or "\x00" in normalized:
        raise ValueError("Memory content is invalid")
    return normalized


def memory_content_is_sensitive(value: str) -> bool:
    """Recheck current content before recall; write-time policy is not enough."""

    content = require_memory_content(value)
    return any(pattern.search(content) is not None for pattern in _SENSITIVE_PATTERNS)


def require_source_message_ids(value: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if not 1 <= len(value) <= MAX_MEMORY_SOURCE_MESSAGES:
        raise ValueError("Memory source message count is invalid")
    if len(set(value)) != len(value):
        raise ValueError("Memory source messages must be unique")
    for message_id in value:
        require_non_nil_uuid(message_id, field_name="message_id")
    return value


def hash_idempotency_key(value: str) -> bytes:
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ValueError("Memory idempotency key is invalid")
    return hashlib.sha256(value.encode("ascii")).digest()


def canonical_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MemorySourceMessage:
    message_id: UUID
    conversation_id: UUID
    role: str
    content_markdown: str

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.message_id, field_name="message_id")
        require_non_nil_uuid(self.conversation_id, field_name="conversation_id")
        if self.role not in {"user", "assistant"}:
            raise ValueError("Memory source role is invalid")
        require_memory_content(self.content_markdown)


@dataclass(frozen=True, slots=True)
class MemoryPolicyAssessment:
    decision: MemoryPolicyDecision
    reason: MemoryPolicyReason
    confidence: float

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("Memory confidence is invalid")


def assess_memory_candidate(
    sources: tuple[MemorySourceMessage, ...],
    suggested_content: str,
) -> MemoryPolicyAssessment:
    content = require_memory_content(suggested_content)
    if memory_content_is_sensitive(content):
        return MemoryPolicyAssessment(
            decision=MemoryPolicyDecision.REJECTED,
            reason=MemoryPolicyReason.SENSITIVE_CONTENT,
            confidence=0,
        )
    roles = {source.role for source in sources}
    if roles == {"assistant"}:
        return MemoryPolicyAssessment(
            decision=MemoryPolicyDecision.REQUIRES_EDIT,
            reason=MemoryPolicyReason.ASSISTANT_ONLY_REQUIRES_EDIT,
            confidence=0.6,
        )
    if roles == {"user"}:
        return MemoryPolicyAssessment(
            decision=MemoryPolicyDecision.ALLOWED,
            reason=MemoryPolicyReason.USER_AUTHORED,
            confidence=0.95,
        )
    return MemoryPolicyAssessment(
        decision=MemoryPolicyDecision.ALLOWED,
        reason=MemoryPolicyReason.MIXED_SOURCES,
        confidence=0.8,
    )


def build_candidate_content(sources: tuple[MemorySourceMessage, ...]) -> str:
    if not sources:
        raise ValueError("Memory candidate requires sources")
    if len(sources) == 1:
        return require_memory_content(sources[0].content_markdown)
    labels = {"user": "用户", "assistant": "助手"}
    content = "\n\n".join(
        f"{labels[source.role]}：{source.content_markdown.strip()}"  # noqa: RUF001
        for source in sources
    )
    return require_memory_content(content)


@dataclass(frozen=True, slots=True)
class CreateMemoryCandidate:
    conversation_id: UUID
    message_ids: tuple[UUID, ...]
    scope: MemoryScope
    idempotency_key: str
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.conversation_id, field_name="conversation_id")
        require_source_message_ids(self.message_ids)
        hash_idempotency_key(self.idempotency_key)


@dataclass(frozen=True, slots=True)
class ResolveMemoryCandidate:
    candidate_id: UUID
    expected_candidate_revision: int
    action: MemoryWriteAction
    content: str
    scope: MemoryScope
    kind: MemoryKind
    expires_at: datetime | None
    target_memory_id: UUID | None
    expected_target_revision: int | None
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.candidate_id, field_name="candidate_id")
        if self.expected_candidate_revision < 1:
            raise ValueError("Candidate revision is invalid")
        object.__setattr__(self, "content", require_memory_content(self.content))
        if self.expires_at is not None:
            require_utc(self.expires_at, field_name="expires_at")
        requires_target = self.action in {MemoryWriteAction.UPDATE, MemoryWriteAction.MERGE}
        if requires_target != (self.target_memory_id is not None):
            raise ValueError("Memory target does not match the write action")
        if requires_target != (self.expected_target_revision is not None):
            raise ValueError("Memory target revision does not match the write action")
        if self.target_memory_id is not None:
            require_non_nil_uuid(self.target_memory_id, field_name="target_memory_id")
        if self.expected_target_revision is not None and self.expected_target_revision < 1:
            raise ValueError("Target Memory revision is invalid")


@dataclass(frozen=True, slots=True)
class RejectMemoryCandidate:
    candidate_id: UUID
    expected_candidate_revision: int
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.candidate_id, field_name="candidate_id")
        if self.expected_candidate_revision < 1:
            raise ValueError("Candidate revision is invalid")


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: UUID
    conversation_id: UUID
    source_message_ids: tuple[UUID, ...]
    suggested_content: str | None
    suggested_scope: MemoryScope
    suggested_expires_at: datetime | None
    confidence: float
    write_reason: str
    policy_decision: MemoryPolicyDecision
    policy_reason: MemoryPolicyReason
    status: MemoryCandidateStatus
    revision: int
    resolved_memory_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    revision_id: UUID
    version: int
    content: str
    scope: MemoryScope
    kind: MemoryKind
    write_action: MemoryWriteAction
    write_reason: str
    policy_decision: MemoryPolicyDecision
    editor_user_id: UUID
    source_message_ids: tuple[UUID, ...]
    validity: MemoryRevisionValidity
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Memory:
    memory_id: UUID
    owner_user_id: UUID
    source_conversation_id: UUID
    scope: MemoryScope
    kind: MemoryKind
    confidence: float
    status: MemoryStatus
    current_revision_id: UUID
    current_version: int
    revision: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryDetail:
    memory: Memory
    current_revision: MemoryRevision
    revisions: tuple[MemoryRevision, ...]


@dataclass(frozen=True, slots=True)
class UpdateMemory:
    memory_id: UUID
    expected_revision: int
    content: str
    scope: MemoryScope
    kind: MemoryKind
    expires_at: datetime | None
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.memory_id, field_name="memory_id")
        if self.expected_revision < 1:
            raise ValueError("Memory revision is invalid")
        object.__setattr__(self, "content", require_memory_content(self.content))
        if self.expires_at is not None:
            require_utc(self.expires_at, field_name="expires_at")


@dataclass(frozen=True, slots=True)
class ChangeMemoryStatus:
    memory_id: UUID
    expected_revision: int
    status: MemoryStatus
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.memory_id, field_name="memory_id")
        if self.expected_revision < 1 or self.status not in {
            MemoryStatus.CONFIRMED,
            MemoryStatus.DISABLED,
        }:
            raise ValueError("Memory status transition is invalid")


@dataclass(frozen=True, slots=True)
class DeleteMemory:
    memory_id: UUID
    expected_revision: int
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.memory_id, field_name="memory_id")
        if self.expected_revision < 1:
            raise ValueError("Memory revision is invalid")


@dataclass(frozen=True, slots=True)
class RecordMemoryFeedback:
    memory_id: UUID
    expected_revision: int
    memory_revision_id: UUID
    value: MemoryFeedbackValue
    reason: str | None
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.memory_id, field_name="memory_id")
        require_non_nil_uuid(self.memory_revision_id, field_name="memory_revision_id")
        if self.expected_revision < 1:
            raise ValueError("Memory revision is invalid")
        if self.reason is not None:
            reason = self.reason.strip()
            if not reason or len(reason) > 500:
                raise ValueError("Memory feedback reason is invalid")
            object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class MemoryFeedback:
    feedback_id: UUID
    memory_id: UUID
    memory_revision_id: UUID
    actor_user_id: UUID
    value: MemoryFeedbackValue
    reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CandidateCreationResult:
    candidate: MemoryCandidate
    created: bool


@dataclass(frozen=True, slots=True)
class MemoryResolutionResult:
    detail: MemoryDetail
    action: MemoryWriteAction
    created: bool
