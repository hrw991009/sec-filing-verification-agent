"""Workspace-scoped conversation listing, detail, pagination, rename, and deletion."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.conversations.domain import MAX_CONVERSATION_TITLE_LENGTH
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

DEFAULT_CONVERSATION_PAGE_SIZE: Final = 20
MAX_CONVERSATION_PAGE_SIZE: Final = 100

type ConversationMessageRole = Literal["user", "assistant"]
type ConversationMessageStatus = Literal["committed", "partial", "final"]


def _require_page_size(value: int) -> None:
    if isinstance(value, bool) or not 1 <= value <= MAX_CONVERSATION_PAGE_SIZE:
        raise ValueError("Conversation page size is invalid")


def _require_title(value: str) -> str:
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


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    """Last visible conversation key for stable descending pagination."""

    updated_at: datetime
    conversation_id: UUID

    def __post_init__(self) -> None:
        require_utc(self.updated_at, field_name="Conversation cursor time")
        require_non_nil_uuid(self.conversation_id, field_name="Conversation cursor ID")


@dataclass(frozen=True, slots=True)
class MessageCursor:
    """Last visible message key for stable chronological pagination."""

    created_at: datetime
    message_id: UUID

    def __post_init__(self) -> None:
        require_utc(self.created_at, field_name="Message cursor time")
        require_non_nil_uuid(self.message_id, field_name="Message cursor ID")


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.conversation_id, field_name="Conversation summary ID")
        _require_title(self.title)
        require_utc(self.created_at, field_name="Conversation creation time")
        require_utc(self.updated_at, field_name="Conversation update time")
        if self.updated_at < self.created_at:
            raise ValueError("Conversation update time precedes creation")


@dataclass(frozen=True, slots=True)
class ConversationDetail:
    summary: ConversationSummary
    turn_count: int

    def __post_init__(self) -> None:
        if isinstance(self.turn_count, bool) or self.turn_count < 0:
            raise ValueError("Conversation Turn count is invalid")


@dataclass(frozen=True, slots=True)
class ConversationAttachment:
    """Safe metadata returned with a durable message; storage coordinates stay private."""

    file_id: UUID
    original_name: str
    kind: AttachmentKind
    detected_media_type: AttachmentMediaType
    actual_size: int
    status: FileObjectStatus
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.file_id, field_name="Conversation attachment file ID")
        if not self.original_name or "\x00" in self.original_name:
            raise ValueError("Conversation attachment filename is invalid")
        if not isinstance(self.kind, AttachmentKind):
            raise ValueError("Conversation attachment kind is invalid")
        if not isinstance(self.detected_media_type, AttachmentMediaType):
            raise ValueError("Conversation attachment media type is invalid")
        if isinstance(self.actual_size, bool) or self.actual_size < 0:
            raise ValueError("Conversation attachment size is invalid")
        if not isinstance(self.status, FileObjectStatus):
            raise ValueError("Conversation attachment status is invalid")
        dimensions = (self.width, self.height)
        if any(
            value is not None and (isinstance(value, bool) or value <= 0) for value in dimensions
        ):
            raise ValueError("Conversation attachment dimensions are invalid")
        if self.kind is AttachmentKind.IMAGE and None in dimensions:
            raise ValueError("Image attachment dimensions are required")
        if self.kind is AttachmentKind.TEXT and any(value is not None for value in dimensions):
            raise ValueError("Text attachments cannot declare image dimensions")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: UUID
    turn_id: UUID
    agent_run_id: UUID
    role: ConversationMessageRole
    status: ConversationMessageStatus
    content_markdown: str = field(repr=False)
    created_at: datetime
    attachments: tuple[ConversationAttachment, ...] = ()

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.message_id, "Conversation Message ID"),
            (self.turn_id, "Conversation Message Turn ID"),
            (self.agent_run_id, "Conversation Message Run ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.role not in {"user", "assistant"} or self.status not in {
            "committed",
            "partial",
            "final",
        }:
            raise ValueError("Conversation Message role or status is invalid")
        if not self.content_markdown.strip() or "\x00" in self.content_markdown:
            raise ValueError("Conversation Message content is invalid")
        require_utc(self.created_at, field_name="Conversation Message creation time")
        attachments = tuple(self.attachments)
        if len({item.file_id for item in attachments}) != len(attachments):
            raise ValueError("Conversation Message attachments must be unique")
        object.__setattr__(self, "attachments", attachments)


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: tuple[ConversationSummary, ...]
    next_cursor: ConversationCursor | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: tuple[ConversationMessage, ...]
    next_cursor: MessageCursor | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class RenameConversation:
    conversation_id: UUID
    title: str

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.conversation_id, field_name="Conversation ID")
        object.__setattr__(self, "title", _require_title(self.title))


class ConversationManagementRepository(Protocol):
    async def list_conversations(
        self,
        *,
        scope: WorkspaceScope,
        page_size: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage: ...

    async def get_conversation(
        self, *, scope: WorkspaceScope, conversation_id: UUID
    ) -> ConversationDetail: ...

    async def list_messages(
        self,
        *,
        scope: WorkspaceScope,
        conversation_id: UUID,
        page_size: int,
        cursor: MessageCursor | None,
    ) -> MessagePage: ...

    async def rename(
        self, *, scope: WorkspaceScope, command: RenameConversation, updated_at: datetime
    ) -> ConversationSummary: ...

    async def delete(
        self, *, scope: WorkspaceScope, conversation_id: UUID, deleted_at: datetime
    ) -> bool: ...


class ConversationManagementUseCase(Protocol):
    """Workspace-authorized conversation operations exposed to delivery adapters."""

    async def list_conversations(
        self,
        scope: WorkspaceScope,
        *,
        page_size: int = DEFAULT_CONVERSATION_PAGE_SIZE,
        cursor: ConversationCursor | None = None,
    ) -> ConversationPage: ...

    async def get_conversation(
        self, scope: WorkspaceScope, conversation_id: UUID
    ) -> ConversationDetail: ...

    async def list_messages(
        self,
        scope: WorkspaceScope,
        conversation_id: UUID,
        *,
        page_size: int = DEFAULT_CONVERSATION_PAGE_SIZE,
        cursor: MessageCursor | None = None,
    ) -> MessagePage: ...

    async def rename(
        self, scope: WorkspaceScope, command: RenameConversation
    ) -> ConversationSummary: ...

    async def delete(self, scope: WorkspaceScope, conversation_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True)
class ConversationManagementService:
    """Validate caller-controlled limits and delegate only with a trusted Workspace scope."""

    repository: ConversationManagementRepository
    clock: Callable[[], datetime] = utc_now

    async def list_conversations(
        self,
        scope: WorkspaceScope,
        *,
        page_size: int = DEFAULT_CONVERSATION_PAGE_SIZE,
        cursor: ConversationCursor | None = None,
    ) -> ConversationPage:
        _require_action(scope, WorkspaceAction.VIEW)
        _require_page_size(page_size)
        return await self.repository.list_conversations(
            scope=scope, page_size=page_size, cursor=cursor
        )

    async def get_conversation(
        self, scope: WorkspaceScope, conversation_id: UUID
    ) -> ConversationDetail:
        _require_action(scope, WorkspaceAction.VIEW)
        require_non_nil_uuid(conversation_id, field_name="Conversation ID")
        return await self.repository.get_conversation(scope=scope, conversation_id=conversation_id)

    async def list_messages(
        self,
        scope: WorkspaceScope,
        conversation_id: UUID,
        *,
        page_size: int = DEFAULT_CONVERSATION_PAGE_SIZE,
        cursor: MessageCursor | None = None,
    ) -> MessagePage:
        _require_action(scope, WorkspaceAction.VIEW)
        require_non_nil_uuid(conversation_id, field_name="Conversation ID")
        _require_page_size(page_size)
        return await self.repository.list_messages(
            scope=scope,
            conversation_id=conversation_id,
            page_size=page_size,
            cursor=cursor,
        )

    async def rename(
        self, scope: WorkspaceScope, command: RenameConversation
    ) -> ConversationSummary:
        _require_action(scope, WorkspaceAction.UPDATE_RESOURCE)
        now = self._now()
        return await self.repository.rename(scope=scope, command=command, updated_at=now)

    async def delete(self, scope: WorkspaceScope, conversation_id: UUID) -> bool:
        _require_action(scope, WorkspaceAction.DELETE_RESOURCE)
        require_non_nil_uuid(conversation_id, field_name="Conversation ID")
        return await self.repository.delete(
            scope=scope,
            conversation_id=conversation_id,
            deleted_at=self._now(),
        )

    def _now(self) -> datetime:
        value = self.clock()
        require_utc(value, field_name="Conversation management time")
        return value


def _require_action(scope: WorkspaceScope, action: WorkspaceAction) -> None:
    if not scope_allows(scope, action):
        raise WorkspaceAccessDeniedError
