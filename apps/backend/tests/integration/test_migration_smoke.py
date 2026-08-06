"""Exercise the complete Alembic history against disposable PostgreSQL."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector

from industry_platform.core.config import Settings
from industry_platform.core.database import build_database_url
from industry_platform.model_registry import metadata

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic.ini"

MIGRATION_SMOKE_REQUIRED = "MIGRATION_SMOKE_REQUIRED"
PROBE_DATABASE_PREFIX = "iip_migration_smoke_"
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


@dataclass(frozen=True, slots=True)
class MigrationProbe:
    """Resources belonging to one disposable migration test."""

    config: Config
    engine: Engine


@pytest.fixture
def migration_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MigrationProbe]:
    """Create and always remove one randomly named PostgreSQL database."""

    if os.getenv(MIGRATION_SMOKE_REQUIRED) != "1":
        pytest.skip("Set MIGRATION_SMOKE_REQUIRED=1 to run PostgreSQL migrations")

    admin_settings = Settings()
    probe_database = f"{PROBE_DATABASE_PREFIX}{uuid4().hex}"

    with psycopg.connect(
        host=admin_settings.postgres_host,
        port=admin_settings.postgres_port,
        dbname="postgres",
        user=admin_settings.postgres_user,
        password=admin_settings.postgres_password.get_secret_value(),
        autocommit=True,
    ) as admin_connection:
        admin_connection.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(probe_database),
            )
        )

    engine: Engine | None = None

    try:
        monkeypatch.setenv("APP_ENVIRONMENT", "test")
        monkeypatch.setenv("POSTGRES_DB", probe_database)

        probe_settings = Settings()
        engine = create_engine(
            build_database_url(probe_settings),
            pool_pre_ping=True,
        )

        yield MigrationProbe(
            config=Config(str(ALEMBIC_CONFIG_PATH)),
            engine=engine,
        )
    finally:
        if engine is not None:
            engine.dispose()

        if not probe_database.startswith(PROBE_DATABASE_PREFIX):
            raise RuntimeError(f"Refusing to remove unexpected database: {probe_database}")

        with psycopg.connect(
            host=admin_settings.postgres_host,
            port=admin_settings.postgres_port,
            dbname="postgres",
            user=admin_settings.postgres_user,
            password=admin_settings.postgres_password.get_secret_value(),
            autocommit=True,
        ) as admin_connection:
            admin_connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(probe_database),
                )
            )


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


def assert_head_schema(probe: MigrationProbe, expected_head: str) -> None:
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


def assert_base_schema(probe: MigrationProbe) -> None:
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
    migration_probe: MigrationProbe,
) -> None:
    """Prove the complete history upgrades, downgrades, and upgrades again."""

    script_directory = ScriptDirectory.from_config(migration_probe.config)
    heads = script_directory.get_heads()

    assert len(heads) == 1
    expected_head = heads[0]

    command.upgrade(migration_probe.config, "head")
    assert_head_schema(migration_probe, expected_head)
    command.check(migration_probe.config)

    command.downgrade(migration_probe.config, "base")
    assert_base_schema(migration_probe)

    command.upgrade(migration_probe.config, "head")
    assert_head_schema(migration_probe, expected_head)
    command.check(migration_probe.config)
