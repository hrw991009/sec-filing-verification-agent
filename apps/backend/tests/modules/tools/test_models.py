"""Static database contracts for Tool execution and safe audit facts."""

from typing import cast

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord


def constraint_columns(
    constraint: ForeignKeyConstraint | UniqueConstraint,
) -> tuple[str, ...]:
    return tuple(column.name for column in constraint.columns)


def foreign_keys(table: Table) -> dict[tuple[str, ...], ForeignKeyConstraint]:
    return {
        constraint_columns(constraint): constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def unique_columns(table: Table) -> set[tuple[str, ...]]:
    return {
        constraint_columns(constraint)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def check_names(table: Table) -> set[str]:
    return {
        str(constraint.name)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def checks(table: Table) -> dict[str, CheckConstraint]:
    return {
        str(constraint.name): constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def indexes(table: Table) -> dict[str, Index]:
    return {str(index.name): index for index in table.indexes}


def test_tool_call_links_the_request_and_optional_execution_to_one_tenant_run() -> None:
    table = cast(Table, ToolCallRecord.__table__)
    keys = foreign_keys(table)

    for columns in (
        ("requested_by_step_id", "run_id", "workspace_id"),
        ("execution_step_id", "run_id", "workspace_id"),
    ):
        assert tuple(element.target_fullname for element in keys[columns].elements) == (
            "agent_steps.id",
            "agent_steps.run_id",
            "agent_steps.workspace_id",
        )
    assert {
        ("id", "workspace_id"),
        ("id", "run_id", "workspace_id"),
        ("requested_by_step_id",),
        ("execution_step_id",),
    } <= unique_columns(table)
    assert table.c.requested_by_step_id.nullable is False
    assert table.c.execution_step_id.nullable is True
    assert table.c.requested_tool_name.nullable is False
    assert table.c.requested_tool_version.nullable is False


def test_tool_run_uses_the_tool_call_id_as_its_shared_one_to_one_identity() -> None:
    table = cast(Table, ToolRunRecord.__table__)
    keys = foreign_keys(table)
    call_key = keys[("id", "run_id", "workspace_id")]

    assert tuple(element.target_fullname for element in call_key.elements) == (
        "tool_calls.id",
        "tool_calls.run_id",
        "tool_calls.workspace_id",
    )
    assert tuple(column.name for column in table.primary_key.columns) == ("id",)
    assert "tool_call_id" not in table.c
    assert ("id", "workspace_id") in unique_columns(table)
    actor_key = keys[("run_id", "workspace_id", "actor_user_id")]
    assert tuple(element.target_fullname for element in actor_key.elements) == (
        "agent_runs.id",
        "agent_runs.workspace_id",
        "agent_runs.user_id",
    )


def test_unresolved_tool_requests_keep_requested_identity_without_fake_registry_data() -> None:
    call_table = cast(Table, ToolCallRecord.__table__)
    run_table = cast(Table, ToolRunRecord.__table__)
    registry_columns = (
        "resolved_tool_name",
        "tool_version",
        "input_schema_version",
        "output_schema_version",
        "required_capability",
        "cost_class",
        "side_effect_class",
        "approval_policy",
        "retry_classification",
        "timeout_ms",
        "max_result_bytes",
        "max_cost_micro_usd",
    )

    for table in (call_table, run_table):
        assert table.c.requested_tool_name.nullable is False
        assert table.c.requested_tool_version.nullable is False
        assert all(table.c[column_name].nullable for column_name in registry_columns)


def test_tool_tables_store_hashes_and_bounded_summaries_instead_of_raw_secrets() -> None:
    call_table = cast(Table, ToolCallRecord.__table__)
    run_table = cast(Table, ToolRunRecord.__table__)

    for column_name in ("sanitized_arguments_hash", "idempotency_key_hash"):
        column_type = call_table.c[column_name].type
        assert isinstance(column_type, LargeBinary)
        assert column_type.length == 32

    assert isinstance(call_table.c.observation.type, JSONB)
    for column_name in (
        "sanitized_input_summary",
        "sanitized_output_summary",
        "source_summary",
    ):
        assert isinstance(run_table.c[column_name].type, JSONB)

    forbidden_columns = {
        "arguments",
        "raw_arguments",
        "raw_result",
        "provider_payload",
        "provider_secret",
        "runtime_context",
        "secret",
    }
    assert forbidden_columns.isdisjoint(call_table.c.keys())
    assert forbidden_columns.isdisjoint(run_table.c.keys())


def test_tool_lifecycle_and_safe_payload_constraints_are_database_backed() -> None:
    call_table = cast(Table, ToolCallRecord.__table__)
    run_table = cast(Table, ToolRunRecord.__table__)

    assert {
        "ck_tool_calls_requested_tool_not_blank",
        "ck_tool_calls_registry_metadata_paired",
        "ck_tool_calls_cost_class",
        "ck_tool_calls_side_effect_class",
        "ck_tool_calls_approval_policy",
        "ck_tool_calls_retry_classification",
        "ck_tool_calls_policy_reason_paired",
        "ck_tool_calls_sanitized_arguments_hash_length",
        "ck_tool_calls_idempotency_key_hash_length",
        "ck_tool_calls_idempotency_requires_side_effect",
        "ck_tool_calls_allowed_write_requires_idempotency",
        "ck_tool_calls_observation_fields_paired_and_bounded",
        "ck_tool_calls_lifecycle_consistent",
        "ck_tool_calls_timeout_bounds",
        "ck_tool_calls_result_size_bounds",
        "ck_tool_calls_cost_bounds",
    } <= check_names(call_table)
    assert {
        "ck_tool_runs_required_names_not_blank",
        "ck_tool_runs_registry_metadata_paired",
        "ck_tool_runs_cost_class",
        "ck_tool_runs_side_effect_class",
        "ck_tool_runs_approval_policy",
        "ck_tool_runs_retry_classification",
        "ck_tool_runs_policy_reason_paired",
        "ck_tool_runs_input_summary_bounded",
        "ck_tool_runs_output_summary_bounded",
        "ck_tool_runs_source_summary_bounded",
        "ck_tool_runs_lifecycle_consistent",
        "ck_tool_runs_actor_role",
    } <= check_names(run_table)

    call_checks = checks(call_table)
    run_checks = checks(run_table)
    observation_sql = str(
        call_checks["ck_tool_calls_observation_fields_paired_and_bounded"].sqltext
    )
    call_lifecycle_sql = str(call_checks["ck_tool_calls_lifecycle_consistent"].sqltext)
    run_lifecycle_sql = str(run_checks["ck_tool_runs_lifecycle_consistent"].sqltext)
    for table_checks, constraint_name in (
        (call_checks, "ck_tool_calls_retry_classification"),
        (run_checks, "ck_tool_runs_retry_classification"),
    ):
        retry_sql = str(table_checks[constraint_name].sqltext)
        assert "'never', 'safe_read_only', 'idempotent_write'" in retry_sql

    assert "observation_content_sha256 ~ '^[0-9a-f]{64}$'" in observation_sql
    assert "observation_envelope_sha256 ~ '^[0-9a-f]{64}$'" in observation_sql
    assert "observation_schema_version IS NOT NULL" in observation_sql
    assert "observation_content_sha256 IS NOT NULL" in observation_sql
    assert "observation_envelope_sha256 IS NOT NULL" in observation_sql
    idempotency_sql = str(call_checks["ck_tool_calls_allowed_write_requires_idempotency"].sqltext)
    assert "side_effect_class IS NOT NULL" in idempotency_sql
    assert "policy_decision IS NULL AND execution_step_id IS NULL" in call_lifecycle_sql
    assert "policy_decision IS NOT NULL AND policy_decision = 'allow'" in call_lifecycle_sql
    assert "policy_decision IS NULL AND duration_ms IS NULL" in run_lifecycle_sql
    assert "policy_decision IS NOT NULL AND policy_decision = 'allow'" in run_lifecycle_sql


def test_tool_indexes_support_workspace_timeline_and_idempotency_queries() -> None:
    call_indexes = indexes(cast(Table, ToolCallRecord.__table__))
    run_indexes = indexes(cast(Table, ToolRunRecord.__table__))

    idempotency = call_indexes["uq_tool_calls_workspace_idempotency"]
    assert idempotency.unique is True
    assert idempotency.dialect_options["postgresql"]["where"] is not None
    assert tuple(column.name for column in idempotency.columns) == (
        "workspace_id",
        "requested_tool_name",
        "idempotency_key_hash",
    )
    assert {
        "ix_tool_calls_workspace_id_run_id_created_at_id",
        "ix_tool_calls_workspace_id_status_updated_at",
    } <= call_indexes.keys()
    assert {
        "ix_tool_runs_workspace_id_run_id_created_at_id",
        "ix_tool_runs_workspace_id_status_created_at_id",
        "ix_tool_runs_workspace_id_trace_id",
    } <= run_indexes.keys()
