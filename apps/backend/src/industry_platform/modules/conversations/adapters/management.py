"""SQLAlchemy conversation management backed by formal Workspace-owned rows."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.conversations.management import (
    ConversationAttachment,
    ConversationCursor,
    ConversationDetail,
    ConversationMessage,
    ConversationMessageRole,
    ConversationMessageStatus,
    ConversationPage,
    ConversationSummary,
    MessageCursor,
    MessagePage,
    RenameConversation,
)
from industry_platform.modules.conversations.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageAttachment,
    MessageRole,
    MessageStatus,
    Turn,
)
from industry_platform.modules.conversations.service import (
    ConversationNotFoundError,
    ConversationPersistenceError,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class SqlAlchemyConversationManagementRepository:
    session_factory: AsyncSessionFactory

    async def list_conversations(
        self,
        *,
        scope: WorkspaceScope,
        page_size: int,
        cursor: ConversationCursor | None,
    ) -> ConversationPage:
        statement = select(Conversation).where(
            Conversation.workspace_id == scope.workspace_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    Conversation.updated_at < cursor.updated_at,
                    and_(
                        Conversation.updated_at == cursor.updated_at,
                        Conversation.id < cursor.conversation_id,
                    ),
                )
            )
        statement = statement.order_by(
            Conversation.updated_at.desc(), Conversation.id.desc()
        ).limit(page_size + 1)
        try:
            async with self.session_factory() as session:
                records = tuple(await session.scalars(statement))
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None
        visible = records[:page_size]
        next_cursor = None
        if len(records) > page_size:
            last = visible[-1]
            next_cursor = ConversationCursor(
                updated_at=last.updated_at,
                conversation_id=last.id,
            )
        return ConversationPage(
            items=tuple(_summary(record) for record in visible),
            next_cursor=next_cursor,
        )

    async def get_conversation(
        self, *, scope: WorkspaceScope, conversation_id: UUID
    ) -> ConversationDetail:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.workspace_id == scope.workspace_id,
                        Conversation.status == ConversationStatus.ACTIVE,
                    )
                )
                if record is None:
                    raise ConversationNotFoundError
                turn_count = await session.scalar(
                    select(func.count())
                    .select_from(Turn)
                    .where(
                        Turn.conversation_id == conversation_id,
                        Turn.workspace_id == scope.workspace_id,
                    )
                )
        except ConversationNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return ConversationDetail(summary=_summary(record), turn_count=turn_count or 0)

    async def list_messages(
        self,
        *,
        scope: WorkspaceScope,
        conversation_id: UUID,
        page_size: int,
        cursor: MessageCursor | None,
    ) -> MessagePage:
        try:
            async with self.session_factory() as session:
                exists = await session.scalar(
                    select(Conversation.id).where(
                        Conversation.id == conversation_id,
                        Conversation.workspace_id == scope.workspace_id,
                        Conversation.status == ConversationStatus.ACTIVE,
                    )
                )
                if exists is None:
                    raise ConversationNotFoundError
                statement = (
                    select(Message, Turn)
                    .join(
                        Turn,
                        and_(
                            Turn.id == Message.turn_id,
                            Turn.workspace_id == Message.workspace_id,
                        ),
                    )
                    .where(
                        Turn.conversation_id == conversation_id,
                        Message.workspace_id == scope.workspace_id,
                    )
                )
                if cursor is not None:
                    statement = statement.where(
                        or_(
                            Message.created_at > cursor.created_at,
                            and_(
                                Message.created_at == cursor.created_at,
                                Message.id > cursor.message_id,
                            ),
                        )
                    )
                statement = statement.order_by(Message.created_at, Message.id).limit(page_size + 1)
                records = tuple((await session.execute(statement)).tuples())
                visible = records[:page_size]
                attachments_by_message: dict[UUID, list[ConversationAttachment]] = defaultdict(list)
                if visible:
                    attachment_statement = (
                        select(
                            MessageAttachment.message_id,
                            FileObject.id,
                            FileObject.original_name,
                            FileObject.kind,
                            FileObject.detected_media_type,
                            FileObject.actual_size,
                            FileObject.status,
                            FileObject.width,
                            FileObject.height,
                        )
                        .select_from(MessageAttachment)
                        .join(
                            FileObject,
                            and_(
                                FileObject.id == MessageAttachment.file_id,
                                FileObject.workspace_id == MessageAttachment.workspace_id,
                            ),
                        )
                        .where(
                            MessageAttachment.workspace_id == scope.workspace_id,
                            MessageAttachment.message_id.in_(
                                tuple(message.id for message, _turn in visible)
                            ),
                        )
                        .order_by(MessageAttachment.message_id, MessageAttachment.ordinal)
                    )
                    for row in (await session.execute(attachment_statement)).all():
                        attachments_by_message[row.message_id].append(
                            ConversationAttachment(
                                file_id=row.id,
                                original_name=row.original_name,
                                kind=AttachmentKind(_enum_value(row.kind)),
                                detected_media_type=AttachmentMediaType(
                                    _enum_value(row.detected_media_type)
                                ),
                                actual_size=_required_positive_integer(row.actual_size),
                                status=FileObjectStatus(_enum_value(row.status)),
                                width=row.width,
                                height=row.height,
                            )
                        )
        except ConversationNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None
        next_cursor = None
        if len(records) > page_size:
            last, _turn = visible[-1]
            next_cursor = MessageCursor(created_at=last.created_at, message_id=last.id)
        return MessagePage(
            items=tuple(
                _message(
                    message,
                    turn=turn,
                    attachments=tuple(attachments_by_message[message.id]),
                )
                for message, turn in visible
            ),
            next_cursor=next_cursor,
        )

    async def rename(
        self,
        *,
        scope: WorkspaceScope,
        command: RenameConversation,
        updated_at: datetime,
    ) -> ConversationSummary:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == command.conversation_id,
                        Conversation.workspace_id == scope.workspace_id,
                        Conversation.status == ConversationStatus.ACTIVE,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise ConversationNotFoundError
                record.title = command.title
                record.updated_at = updated_at
                await session.flush()
                result = _summary(record)
        except ConversationNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return result

    async def delete(
        self,
        *,
        scope: WorkspaceScope,
        conversation_id: UUID,
        deleted_at: datetime,
    ) -> bool:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.workspace_id == scope.workspace_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise ConversationNotFoundError
                if record.status is ConversationStatus.DELETED:
                    return False
                record.status = ConversationStatus.DELETED
                record.deleted_at = deleted_at
                record.updated_at = deleted_at
                return True
        except ConversationNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None


def _summary(record: Conversation) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message(
    record: Message,
    *,
    turn: Turn,
    attachments: tuple[ConversationAttachment, ...] = (),
) -> ConversationMessage:
    role: ConversationMessageRole = "user" if record.role is MessageRole.USER else "assistant"
    if record.status is MessageStatus.COMMITTED:
        status: ConversationMessageStatus = "committed"
    elif record.status is MessageStatus.PARTIAL:
        status = "partial"
    else:
        status = "final"
    return ConversationMessage(
        message_id=record.id,
        turn_id=record.turn_id,
        agent_run_id=record.agent_run_id,
        role=role,
        status=status,
        content_markdown=record.content_markdown,
        created_at=record.created_at,
        search_mode=turn.search_mode,
        industry_id=turn.industry_id,
        knowledge_base_ids=tuple(turn.knowledge_base_ids),
        attachments=attachments,
    )


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ConversationPersistenceError()
    return raw


def _required_positive_integer(value: int | None) -> int:
    if value is None or isinstance(value, bool) or value <= 0:
        raise ConversationPersistenceError()
    return value
