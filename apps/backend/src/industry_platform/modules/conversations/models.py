"""PostgreSQL models for conversations, turns, and durable messages."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.identity.models import enum_values


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    COMMITTED = "committed"
    PARTIAL = "partial"
    FINAL = "final"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-owned chat container with recoverable deletion metadata."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("status IN ('active', 'deleted')", name="status"),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status = 'active' AND deleted_at IS NULL)",
            name="deletion_state_consistent",
        ),
        Index(None, "workspace_id", "updated_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        SqlEnum(
            ConversationStatus,
            name="conversation_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=ConversationStatus.ACTIVE,
        server_default=ConversationStatus.ACTIVE.value,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Turn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable user-request snapshot inside one Conversation."""

    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("conversation_id", "sequence"),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("search_mode IN ('none', 'web', 'local', 'both')", name="search_mode"),
        CheckConstraint(
            "search_mode <> 'none' OR cardinality(knowledge_base_ids) = 0",
            name="none_mode_has_no_knowledge_bases",
        ),
        Index(None, "workspace_id", "conversation_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    search_mode: Mapped[TurnSearchMode] = mapped_column(
        SqlEnum(
            TurnSearchMode,
            name="turn_search_mode",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    industry_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    knowledge_base_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), nullable=False, default=list, server_default=text("'{}'::uuid[]")
    )


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable user input, partial response, or successful final response."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["conversation_turns.id", "conversation_turns.workspace_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("status IN ('committed', 'partial', 'final')", name="status"),
        CheckConstraint("length(btrim(content_markdown)) > 0", name="content_not_blank"),
        CheckConstraint(
            "(role = 'user' AND status = 'committed' AND created_by_user_id IS NOT NULL) OR "
            "(role = 'assistant' AND status IN ('partial', 'final') "
            "AND created_by_user_id IS NULL)",
            name="role_state_consistent",
        ),
        Index(
            "uq_conversation_messages_one_user_input_per_turn",
            "turn_id",
            unique=True,
            postgresql_where=text("role = 'user'"),
        ),
        Index(
            "uq_conversation_messages_one_final_per_run",
            "agent_run_id",
            unique=True,
            postgresql_where=text("role = 'assistant' AND status = 'final'"),
        ),
        Index(None, "workspace_id", "turn_id", "created_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    turn_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    role: Mapped[MessageRole] = mapped_column(
        SqlEnum(
            MessageRole,
            name="message_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    status: Mapped[MessageStatus] = mapped_column(
        SqlEnum(
            MessageStatus,
            name="message_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
