"""Exercise the complete Alembic history against disposable PostgreSQL."""

from typing import cast

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Inspector

from industry_platform.model_registry import metadata

from .postgres import PostgresProbe

ALEMBIC_VERSION_TABLE = "alembic_version"

EXPECTED_BUSINESS_TABLES = {
    "users",
    "workspaces",
    "workspace_members",
    "refresh_session_families",
    "refresh_sessions",
    "audit_logs",
}

pytestmark = pytest.mark.migration_smoke


def assert_foreign_key(
    inspector: Inspector,
    *,
    source_table: str,
    constraint_name: str,
    constrained_columns: tuple[str, ...],
    referred_table: str,
    referred_columns: tuple[str, ...],
    on_delete: str,
) -> None:
    """Assert the exact structure of one reflected foreign key."""

    matches = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(
            source_table,
            schema="public",
        )
        if foreign_key["name"] == constraint_name
    ]

    assert len(matches) == 1, f"Expected one {constraint_name} constraint, found {len(matches)}"

    foreign_key = matches[0]

    assert tuple(foreign_key["constrained_columns"]) == constrained_columns
    assert foreign_key["referred_table"] == referred_table
    assert tuple(foreign_key["referred_columns"]) == referred_columns

    actual_on_delete = str(foreign_key.get("options", {}).get("ondelete", "")).upper()
    assert actual_on_delete == on_delete


def assert_head_schema(probe: PostgresProbe, expected_head: str) -> None:
    """Assert tables, revision, and security-critical foreign keys at head."""

    with probe.engine.connect() as connection:
        inspector = inspect(connection)
        actual_tables = set(inspector.get_table_names(schema="public"))
        expected_tables = set(metadata.tables) | {ALEMBIC_VERSION_TABLE}

        assert set(metadata.tables) >= EXPECTED_BUSINESS_TABLES
        assert actual_tables == expected_tables

        actual_head = cast(
            str,
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
        )
        assert actual_head == expected_head

        assert_foreign_key(
            inspector,
            source_table="refresh_session_families",
            constraint_name=("fk_refresh_session_families_current_session_id_refresh_sessions"),
            constrained_columns=("id", "current_session_id"),
            referred_table="refresh_sessions",
            referred_columns=("rotation_family_id", "id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="refresh_sessions",
            constraint_name=("fk_refresh_sessions_family_user_refresh_session_families"),
            constrained_columns=("rotation_family_id", "user_id"),
            referred_table="refresh_session_families",
            referred_columns=("id", "user_id"),
            on_delete="CASCADE",
        )


def assert_base_schema(probe: PostgresProbe) -> None:
    """Assert downgrade removed all business tables and revision rows."""

    with probe.engine.connect() as connection:
        inspector = inspect(connection)
        actual_tables = set(inspector.get_table_names(schema="public"))

        assert actual_tables == {ALEMBIC_VERSION_TABLE}

        version_count = cast(
            int,
            connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one(),
        )
        assert version_count == 0


def test_complete_migration_history_round_trip(
    postgres_probe: PostgresProbe,
) -> None:
    """Prove the complete history upgrades, downgrades, and upgrades again."""

    script_directory = ScriptDirectory.from_config(postgres_probe.config)
    heads = script_directory.get_heads()

    assert len(heads) == 1
    expected_head = heads[0]

    command.upgrade(postgres_probe.config, "head")
    assert_head_schema(postgres_probe, expected_head)
    command.check(postgres_probe.config)

    command.downgrade(postgres_probe.config, "base")
    assert_base_schema(postgres_probe)

    command.upgrade(postgres_probe.config, "head")
    assert_head_schema(postgres_probe, expected_head)
    command.check(postgres_probe.config)
