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
    "tool_calls",
    "tool_runs",
    "sec_filers",
    "sec_filer_aliases",
    "sec_catalog_syncs",
    "sec_submission_sources",
    "sec_filing_observations",
    "sec_filings",
    "sec_filing_coverage_manifests",
    "sec_filing_coverage_sources",
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


def assert_check_contains(
    inspector: Inspector,
    *,
    table_name: str,
    constraint_name: str,
    fragments: tuple[str, ...],
) -> None:
    matches = [
        constraint
        for constraint in inspector.get_check_constraints(table_name, schema="public")
        if constraint["name"] == constraint_name
    ]

    assert len(matches) == 1, f"Expected one {constraint_name} constraint, found {len(matches)}"
    sqltext = str(matches[0]["sqltext"])
    assert all(fragment in sqltext for fragment in fragments)


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
        assert_check_contains(
            inspector,
            table_name="query_runs",
            constraint_name="ck_query_runs_lifecycle_consistent",
            fragments=("completed", "schema_snapshot_id IS NOT NULL", "plan_rows IS NOT NULL"),
        )
        assert_foreign_key(
            inspector,
            source_table="query_runs",
            constraint_name="fk_query_runs_agent_run_workspace_actor",
            constrained_columns=("agent_run_id", "workspace_id", "actor_user_id"),
            referred_table="agent_runs",
            referred_columns=("id", "workspace_id", "user_id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="memory_candidate_sources",
            constraint_name="fk_memory_candidate_sources_message_workspace",
            constrained_columns=("message_id", "workspace_id"),
            referred_table="conversation_messages",
            referred_columns=("id", "workspace_id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="memory_revisions",
            constraint_name="fk_memory_revisions_memory_workspace_owner",
            constrained_columns=("memory_id", "workspace_id", "owner_user_id"),
            referred_table="memories",
            referred_columns=("id", "workspace_id", "owner_user_id"),
            on_delete="RESTRICT",
        )
        assert_check_contains(
            inspector,
            table_name="memory_candidates",
            constraint_name="ck_memory_candidates_lifecycle_consistent",
            fragments=("candidate", "confirmed", "rejected", "resolution_fingerprint"),
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
        assert_check_contains(
            inspector,
            table_name="tool_calls",
            constraint_name="ck_tool_calls_observation_fields_paired_and_bounded",
            fragments=(
                "[0-9a-f]{64}",
                "observation_content_sha256",
                "observation_envelope_sha256",
            ),
        )
        assert_check_contains(
            inspector,
            table_name="tool_calls",
            constraint_name="ck_tool_calls_lifecycle_consistent",
            fragments=("policy_decision IS NULL", "execution_step_id IS NULL"),
        )
        assert_check_contains(
            inspector,
            table_name="tool_calls",
            constraint_name="ck_tool_calls_allowed_write_requires_idempotency",
            fragments=("side_effect_class IS NOT NULL", "idempotency_key_hash IS NOT NULL"),
        )
        assert_check_contains(
            inspector,
            table_name="tool_calls",
            constraint_name="ck_tool_calls_retry_classification",
            fragments=("never", "safe_read_only", "idempotent_write"),
        )
        assert_check_contains(
            inspector,
            table_name="tool_runs",
            constraint_name="ck_tool_runs_lifecycle_consistent",
            fragments=("policy_decision IS NULL", "duration_ms IS NULL"),
        )
        assert_check_contains(
            inspector,
            table_name="tool_runs",
            constraint_name="ck_tool_runs_retry_classification",
            fragments=("never", "safe_read_only", "idempotent_write"),
        )
        assert_foreign_key(
            inspector,
            source_table="tool_calls",
            constraint_name="fk_tool_calls_request_step_run_workspace",
            constrained_columns=("requested_by_step_id", "run_id", "workspace_id"),
            referred_table="agent_steps",
            referred_columns=("id", "run_id", "workspace_id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="tool_calls",
            constraint_name="fk_tool_calls_execution_step_run_workspace",
            constrained_columns=("execution_step_id", "run_id", "workspace_id"),
            referred_table="agent_steps",
            referred_columns=("id", "run_id", "workspace_id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="tool_runs",
            constraint_name="fk_tool_runs_call_run_workspace",
            constrained_columns=("id", "run_id", "workspace_id"),
            referred_table="tool_calls",
            referred_columns=("id", "run_id", "workspace_id"),
            on_delete="RESTRICT",
        )
        assert_foreign_key(
            inspector,
            source_table="tool_runs",
            constraint_name="fk_tool_runs_actor_run_workspace",
            constrained_columns=("run_id", "workspace_id", "actor_user_id"),
            referred_table="agent_runs",
            referred_columns=("id", "workspace_id", "user_id"),
            on_delete="RESTRICT",
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
