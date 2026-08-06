"""Tests for the identity persistence schema."""

from typing import cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Table,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SqlEnum,
)

from industry_platform.model_registry import metadata
from industry_platform.modules.identity.models import (
    RefreshSession,
    UserStatus,
    WorkspaceMembership,
)

EXPECTED_IDENTITY_TABLES = {
    "users",
    "workspaces",
    "workspace_members",
    "refresh_session_families",
    "refresh_sessions",
    "audit_logs",
}

ENUM_CHECK_SPECS: dict[str, tuple[str, str, frozenset[str]]] = {
    "users": (
        "status",
        "ck_users_user_status",
        frozenset({"active", "disabled", "deleting", "deleted"}),
    ),
    "workspaces": (
        "status",
        "ck_workspaces_workspace_status",
        frozenset({"active", "deleting", "deleted"}),
    ),
    "workspace_members": (
        "role",
        "ck_workspace_members_workspace_role",
        frozenset({"owner", "admin", "member", "viewer"}),
    ),
    "audit_logs": (
        "outcome",
        "ck_audit_logs_audit_outcome",
        frozenset({"succeeded", "denied", "failed"}),
    ),
}


def test_identity_models_are_registered_for_alembic() -> None:
    assert set(metadata.tables) >= EXPECTED_IDENTITY_TABLES


def test_membership_is_unique_per_user_and_workspace() -> None:
    membership_table = cast(Table, WorkspaceMembership.__table__)

    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in membership_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("workspace_id", "user_id") in unique_column_sets


def test_enum_checks_are_explicit_and_visible_to_alembic() -> None:
    for table_name, (
        column_name,
        expected_constraint_name,
        expected_values,
    ) in ENUM_CHECK_SPECS.items():
        table = metadata.tables[table_name]
        enum_type = table.c[column_name].type

        assert isinstance(enum_type, SqlEnum)
        assert enum_type.native_enum is False
        assert enum_type.create_constraint is False
        assert set(enum_type.enums) == expected_values

        matching_checks = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
            and str(constraint.name) == expected_constraint_name
        ]

        assert len(matching_checks) == 1

        expected_sqltext = (
            f"{column_name} IN ({', '.join(repr(value) for value in enum_type.enums)})"
        )

        assert str(matching_checks[0].sqltext) == expected_sqltext


def test_refresh_session_has_security_consistency_checks() -> None:
    refresh_session_table = cast(Table, RefreshSession.__table__)

    check_names = {
        str(constraint.name)
        for constraint in refresh_session_table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_refresh_sessions_token_hash_length",
        "ck_refresh_sessions_csrf_token_hash_length",
        "ck_refresh_sessions_device_hash_length",
        "ck_refresh_sessions_expiration_order",
        "ck_refresh_sessions_recovery_fields_paired",
        "ck_refresh_sessions_rotation_state_consistent",
    } <= check_names


def test_identity_timestamps_are_timezone_aware() -> None:
    for table_name in EXPECTED_IDENTITY_TABLES:
        table = metadata.tables[table_name]

        for column_name in ("created_at", "updated_at"):
            column_type = table.c[column_name].type

            assert isinstance(column_type, DateTime)
            assert column_type.timezone is True


def test_user_status_uses_stable_database_values() -> None:
    assert {status.value for status in UserStatus} == {
        "active",
        "disabled",
        "deleting",
        "deleted",
    }
