"""Identity, workspace, session, and audit persistence models."""

from datetime import datetime
from enum import Enum as PythonEnum
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


def enum_values(enum_type: type[PythonEnum]) -> list[str]:
    """Persist enum values such as 'active' rather than names such as 'ACTIVE'."""

    return [str(member.value) for member in enum_type]


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETING = "deleting"
    DELETED = "deleted"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    DELETING = "deleting"
    DELETED = "deleted"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class AuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registered platform account."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email = lower(btrim(email))",
            name="email_normalized",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'deleting', 'deleted')",
            name="user_status",
        ),
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
    )
    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        SqlEnum(
            UserStatus,
            name="user_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Top-level tenant boundary."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(name)) > 0",
            name="name_not_blank",
        ),
        CheckConstraint(
            "status IN ('active', 'deleting', 'deleted')",
            name="workspace_status",
        ),
    )

    name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        SqlEnum(
            WorkspaceStatus,
            name="workspace_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=WorkspaceStatus.ACTIVE,
        server_default=WorkspaceStatus.ACTIVE.value,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class WorkspaceMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's current role inside one workspace."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name="workspace_role",
        ),
        Index(None, "user_id", "workspace_id"),
        Index(None, "workspace_id", "role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        SqlEnum(
            WorkspaceRole,
            name="workspace_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=WorkspaceRole.MEMBER,
        server_default=WorkspaceRole.MEMBER.value,
    )


class RefreshSessionFamily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A lockable refresh-token rotation family."""

    __tablename__ = "refresh_session_families"
    __table_args__ = (
        CheckConstraint(
            "absolute_expires_at > created_at",
            name="absolute_expiration_after_creation",
        ),
        UniqueConstraint("id", "user_id"),
        ForeignKeyConstraint(
            ["id", "current_session_id"],
            ["refresh_sessions.rotation_family_id", "refresh_sessions.id"],
            name="fk_refresh_session_families_current_session_id_refresh_sessions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index(None, "user_id", "revoked_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )


class RefreshSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One refresh-token generation inside a rotation family."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        UniqueConstraint("csrf_token_hash"),
        UniqueConstraint("previous_session_id"),
        UniqueConstraint("replaced_by_session_id"),
        UniqueConstraint("rotation_family_id", "id"),
        ForeignKeyConstraint(
            ["rotation_family_id", "user_id"],
            [
                "refresh_session_families.id",
                "refresh_session_families.user_id",
            ],
            name="fk_refresh_sessions_family_user_refresh_session_families",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "octet_length(token_hash) = 32",
            name="token_hash_length",
        ),
        CheckConstraint(
            "octet_length(csrf_token_hash) = 32",
            name="csrf_token_hash_length",
        ),
        CheckConstraint(
            "octet_length(device_hash) = 32",
            name="device_hash_length",
        ),
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="expiration_order",
        ),
        CheckConstraint(
            "(recovery_envelope IS NULL) = (recovery_expires_at IS NULL)",
            name="recovery_fields_paired",
        ),
        CheckConstraint(
            "previous_session_id IS NULL OR previous_session_id <> id",
            name="previous_session_not_self",
        ),
        CheckConstraint(
            "replaced_by_session_id IS NULL OR replaced_by_session_id <> id",
            name="replacement_session_not_self",
        ),
        CheckConstraint(
            "(used_at IS NULL AND replaced_by_session_id IS NULL) OR "
            "(used_at IS NOT NULL AND replaced_by_session_id IS NOT NULL)",
            name="rotation_state_consistent",
        ),
        Index(None, "user_id", "revoked_at"),
        Index(None, "rotation_family_id", "created_at"),
        Index(
            "ix_refresh_sessions_pending_recovery_expiry",
            "recovery_expires_at",
            postgresql_where=text("recovery_expires_at IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rotation_family_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32),
        nullable=False,
    )
    csrf_token_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32),
        nullable=False,
    )
    device_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32),
        nullable=False,
    )
    previous_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    recovery_envelope: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    recovery_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable security-relevant event with sanitized metadata."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "metadata_schema_version > 0",
            name="metadata_schema_version_positive",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name="audit_outcome",
        ),
        Index(None, "workspace_id", "created_at"),
        Index(None, "actor_user_id", "created_at"),
        Index(None, "trace_id"),
    )

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    resource_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    outcome: Mapped[AuditOutcome] = mapped_column(
        SqlEnum(
            AuditOutcome,
            name="audit_outcome",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    metadata_schema_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    sanitized_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
