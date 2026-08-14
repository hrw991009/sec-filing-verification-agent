"""Validated HTTP payloads and opaque cursors for conversation APIs."""

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.conversations.domain import (
    MAX_CONVERSATION_TITLE_LENGTH,
    MAX_TURN_ATTACHMENTS,
    MAX_USER_MESSAGE_LENGTH,
    TurnSearchMode,
)
from industry_platform.modules.conversations.management import (
    ConversationCursor,
    ConversationMessageRole,
    ConversationMessageStatus,
    MessageCursor,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)

MAX_CURSOR_LENGTH: Final = 512
_CURSOR_VERSION: Final = 1
type CursorKind = Literal["conversation", "message"]


class InvalidConversationCursorError(ValueError):
    """Raised when an untrusted pagination cursor cannot be decoded safely."""


def _non_nil_uuid(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("ID must not be nil")
    return value


type NonNilUuid = Annotated[UUID, AfterValidator(_non_nil_uuid)]


def _idempotency_key(value: str) -> str:
    if (
        not value
        or len(value) > 200
        or value != value.strip()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ValueError("Idempotency key is invalid")
    return value


type IdempotencyKey = Annotated[str, AfterValidator(_idempotency_key)]


class StrictConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validated_title(value: str) -> str:
    if (
        not value.strip()
        or value != value.strip()
        or len(value) > MAX_CONVERSATION_TITLE_LENGTH
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        raise ValueError("Conversation title is invalid")
    return value


class RenameConversationRequest(StrictConversationModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validated_title(value)


class StartConversationTurnRequest(StrictConversationModel):
    question: str
    conversation_id: NonNilUuid | None = None
    title: str | None = None
    mode: TurnSearchMode = TurnSearchMode.NONE
    industry_id: NonNilUuid | None = None
    knowledge_base_ids: list[NonNilUuid] = Field(default_factory=list, max_length=100)
    attachment_ids: list[NonNilUuid] = Field(default_factory=list, max_length=MAX_TURN_ATTACHMENTS)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip() or len(value) > MAX_USER_MESSAGE_LENGTH or "\x00" in value:
            raise ValueError("Question is invalid")
        return value

    @field_validator("title")
    @classmethod
    def validate_optional_title(cls, value: str | None) -> str | None:
        return _validated_title(value) if value is not None else None

    @model_validator(mode="after")
    def validate_turn_shape(self) -> Self:
        if self.conversation_id is not None and self.title is not None:
            raise ValueError("An existing conversation cannot declare a new title")
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("Knowledge-base IDs must be unique")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("Attachment IDs must be unique")
        if self.knowledge_base_ids and self.mode not in {
            TurnSearchMode.LOCAL,
            TurnSearchMode.BOTH,
        }:
            raise ValueError("Knowledge-base IDs require local search mode")
        return self


class StartConversationTurnResponse(StrictConversationModel):
    conversation_id: UUID
    turn_id: UUID
    user_message_id: UUID
    agent_run_id: UUID
    job_id: UUID
    created: bool


class ConversationSummaryResponse(StrictConversationModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationSummaryResponse):
    turn_count: int


class ConversationCollectionResponse(StrictConversationModel):
    conversations: list[ConversationSummaryResponse]
    next_cursor: str | None


class ConversationAttachmentResponse(StrictConversationModel):
    file_id: UUID
    original_name: str
    kind: AttachmentKind
    detected_media_type: AttachmentMediaType
    actual_size: int
    status: FileObjectStatus
    width: int | None
    height: int | None


class ConversationMessageResponse(StrictConversationModel):
    id: UUID
    turn_id: UUID
    agent_run_id: UUID
    role: ConversationMessageRole
    status: ConversationMessageStatus
    content_markdown: str
    created_at: datetime
    attachments: list[ConversationAttachmentResponse]


class ConversationMessageCollectionResponse(StrictConversationModel):
    messages: list[ConversationMessageResponse]
    next_cursor: str | None


def encode_conversation_cursor(cursor: ConversationCursor) -> str:
    return _encode_cursor("conversation", cursor.updated_at, cursor.conversation_id)


def decode_conversation_cursor(value: str) -> ConversationCursor:
    timestamp, identifier = _decode_cursor(value, expected_kind="conversation")
    return ConversationCursor(updated_at=timestamp, conversation_id=identifier)


def encode_message_cursor(cursor: MessageCursor) -> str:
    return _encode_cursor("message", cursor.created_at, cursor.message_id)


def decode_message_cursor(value: str) -> MessageCursor:
    timestamp, identifier = _decode_cursor(value, expected_kind="message")
    return MessageCursor(created_at=timestamp, message_id=identifier)


def _encode_cursor(kind: CursorKind, timestamp: datetime, identifier: UUID) -> str:
    document = {
        "id": str(identifier),
        "kind": kind,
        "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "version": _CURSOR_VERSION,
    }
    raw = json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str, *, expected_kind: CursorKind) -> tuple[datetime, UUID]:
    try:
        if not value or len(value) > MAX_CURSOR_LENGTH or not value.isascii():
            raise ValueError
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(document, dict) or set(document) != {
            "id",
            "kind",
            "timestamp",
            "version",
        }:
            raise ValueError
        version = document["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version != _CURSOR_VERSION:
            raise ValueError
        if document["kind"] != expected_kind:
            raise ValueError
        raw_timestamp = document["timestamp"]
        raw_identifier = document["id"]
        if not isinstance(raw_timestamp, str) or not isinstance(raw_identifier, str):
            raise ValueError
        timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        identifier = UUID(raw_identifier)
        if timestamp.utcoffset() != UTC.utcoffset(timestamp) or identifier.int == 0:
            raise ValueError
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
    ):
        raise InvalidConversationCursorError from None
    return timestamp, identifier


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError
