"""PostgreSQL execution facts and safe audit projections for Agent Tools."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

_TOOL_CALL_STATUSES = (
    "'requested', 'approval_required', 'denied', 'running', 'completed', 'failed', 'cancelled'"
)
_TOOL_POLICY_DECISIONS = "'allow', 'deny', 'approval_required'"
_TOOL_COST_CLASSES = "'low', 'medium', 'high'"
_TOOL_SIDE_EFFECT_CLASSES = "'read_only', 'idempotent_write', 'non_idempotent_write'"
_TOOL_APPROVAL_POLICIES = "'auto_allow', 'auto_deny', 'require_approval'"
_TOOL_RETRY_CLASSIFICATIONS = "'never', 'safe_read_only', 'idempotent_write'"


class ToolCallRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authoritative execution projection for one server-identified Tool request."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("id", "run_id", "workspace_id"),
        UniqueConstraint("requested_by_step_id"),
        UniqueConstraint("execution_step_id"),
        ForeignKeyConstraint(
            ["requested_by_step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_tool_calls_request_step_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["execution_step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_tool_calls_execution_step_run_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_TOOL_CALL_STATUSES})", name="status"),
        CheckConstraint(
            f"policy_decision IS NULL OR policy_decision IN ({_TOOL_POLICY_DECISIONS})",
            name="policy_decision",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "length(btrim(requested_tool_name)) > 0 AND length(btrim(requested_tool_version)) > 0",
            name="requested_tool_not_blank",
        ),
        CheckConstraint(
            "length(btrim(toolset_version)) > 0 AND length(btrim(policy_version)) > 0",
            name="required_versions_not_blank",
        ),
        CheckConstraint(
            "(resolved_tool_name IS NULL AND tool_version IS NULL "
            "AND input_schema_version IS NULL AND output_schema_version IS NULL "
            "AND required_capability IS NULL AND cost_class IS NULL "
            "AND side_effect_class IS NULL AND approval_policy IS NULL "
            "AND retry_classification IS NULL "
            "AND timeout_ms IS NULL AND max_result_bytes IS NULL "
            "AND max_cost_micro_usd IS NULL) OR "
            "(resolved_tool_name IS NOT NULL AND length(btrim(resolved_tool_name)) > 0 "
            "AND tool_version IS NOT NULL AND length(btrim(tool_version)) > 0 "
            "AND input_schema_version IS NOT NULL "
            "AND length(btrim(input_schema_version)) > 0 "
            "AND output_schema_version IS NOT NULL "
            "AND length(btrim(output_schema_version)) > 0 "
            "AND required_capability IS NOT NULL "
            "AND length(btrim(required_capability)) > 0 "
            "AND cost_class IS NOT NULL AND length(btrim(cost_class)) > 0 "
            "AND side_effect_class IS NOT NULL "
            "AND length(btrim(side_effect_class)) > 0 "
            "AND approval_policy IS NOT NULL AND length(btrim(approval_policy)) > 0 "
            "AND retry_classification IS NOT NULL "
            "AND length(btrim(retry_classification)) > 0 "
            "AND timeout_ms IS NOT NULL AND max_result_bytes IS NOT NULL "
            "AND max_cost_micro_usd IS NOT NULL)",
            name="registry_metadata_paired",
        ),
        CheckConstraint(
            f"cost_class IS NULL OR cost_class IN ({_TOOL_COST_CLASSES})",
            name="cost_class",
        ),
        CheckConstraint(
            f"side_effect_class IS NULL OR side_effect_class IN ({_TOOL_SIDE_EFFECT_CLASSES})",
            name="side_effect_class",
        ),
        CheckConstraint(
            f"approval_policy IS NULL OR approval_policy IN ({_TOOL_APPROVAL_POLICIES})",
            name="approval_policy",
        ),
        CheckConstraint(
            "retry_classification IS NULL OR "
            f"retry_classification IN ({_TOOL_RETRY_CLASSIFICATIONS})",
            name="retry_classification",
        ),
        CheckConstraint(
            "(policy_decision IS NULL AND policy_reason_code IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_reason_code IS NOT NULL "
            "AND length(btrim(policy_reason_code)) > 0)",
            name="policy_reason_paired",
        ),
        CheckConstraint(
            "octet_length(sanitized_arguments_hash) = 32",
            name="sanitized_arguments_hash_length",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR octet_length(idempotency_key_hash) = 32",
            name="idempotency_key_hash_length",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR "
            "(side_effect_class IS NOT NULL AND side_effect_class <> 'read_only')",
            name="idempotency_requires_side_effect",
        ),
        CheckConstraint(
            "policy_decision IS NULL OR policy_decision <> 'allow' "
            "OR (side_effect_class IS NOT NULL AND "
            "(side_effect_class = 'read_only' OR idempotency_key_hash IS NOT NULL))",
            name="allowed_write_requires_idempotency",
        ),
        CheckConstraint(
            "timeout_ms IS NULL OR timeout_ms BETWEEN 1 AND 300000",
            name="timeout_bounds",
        ),
        CheckConstraint(
            "max_result_bytes IS NULL OR max_result_bytes BETWEEN 1 AND 10000000",
            name="result_size_bounds",
        ),
        CheckConstraint(
            "(max_cost_micro_usd IS NULL OR "
            "max_cost_micro_usd BETWEEN 1 AND 1000000000) AND cost_micro_usd >= 0",
            name="cost_bounds",
        ),
        CheckConstraint(
            "(observation_schema_version IS NULL AND observation IS NULL "
            "AND observation_content_sha256 IS NULL AND observation_envelope_sha256 IS NULL) OR "
            "(observation_schema_version IS NOT NULL AND observation_schema_version >= 1 "
            "AND observation IS NOT NULL "
            "AND jsonb_typeof(observation) = 'object' "
            "AND octet_length(observation::text) <= 524288 "
            "AND observation_content_sha256 IS NOT NULL "
            "AND observation_content_sha256 ~ '^[0-9a-f]{64}$' "
            "AND observation_envelope_sha256 IS NOT NULL "
            "AND observation_envelope_sha256 ~ '^[0-9a-f]{64}$')",
            name="observation_fields_paired_and_bounded",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="start_after_request",
        ),
        CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= COALESCE(started_at, created_at)",
            name="terminal_after_start",
        ),
        CheckConstraint(
            "(status = 'requested' AND policy_decision IS NULL "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'approval_required' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'approval_required' "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'denied' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'deny' "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND observation IS NULL) OR "
            "(status = 'running' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL AND started_at IS NOT NULL "
            "AND terminal_at IS NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'completed' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL AND started_at IS NOT NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NULL AND observation IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL "
            "AND error_code IS NOT NULL AND observation IS NULL AND "
            "((policy_decision IS NULL AND execution_step_id IS NULL AND started_at IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL "
            "AND started_at IS NOT NULL))) OR "
            "(status = 'cancelled' AND terminal_at IS NOT NULL "
            "AND error_code IS NULL AND observation IS NULL AND "
            "((policy_decision IS NULL AND execution_step_id IS NULL AND started_at IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL "
            "AND started_at IS NOT NULL)))",
            name="lifecycle_consistent",
        ),
        Index(None, "workspace_id", "run_id", "created_at", "id"),
        Index(None, "workspace_id", "status", "updated_at"),
        Index(
            "uq_tool_calls_workspace_idempotency",
            "workspace_id",
            "requested_tool_name",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("idempotency_key_hash IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_step_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    execution_step_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    requested_tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    toolset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_schema_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    required_capability: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side_effect_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="requested", server_default="requested"
    )
    timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_result_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    sanitized_arguments_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    idempotency_key_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    observation_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    observation: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    observation_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observation_envelope_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ToolRunRecord(TimestampMixin, Base):
    """Workspace-queryable, sanitized audit projection of one ToolCallRecord."""

    __tablename__ = "tool_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["id", "run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_tool_runs_call_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "workspace_id", "actor_user_id"],
            ["agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"],
            name="fk_tool_runs_actor_run_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_TOOL_CALL_STATUSES})", name="status"),
        CheckConstraint(
            f"policy_decision IS NULL OR policy_decision IN ({_TOOL_POLICY_DECISIONS})",
            name="policy_decision",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "actor_role IN ('owner', 'admin', 'member', 'viewer')",
            name="actor_role",
        ),
        CheckConstraint(
            "length(btrim(trace_id)) > 0 "
            "AND length(btrim(requested_tool_name)) > 0 "
            "AND length(btrim(requested_tool_version)) > 0",
            name="required_names_not_blank",
        ),
        CheckConstraint(
            "length(btrim(toolset_version)) > 0 "
            "AND length(btrim(policy_version)) > 0 "
            "AND length(btrim(sanitizer_version)) > 0",
            name="required_versions_not_blank",
        ),
        CheckConstraint(
            "(resolved_tool_name IS NULL AND tool_version IS NULL "
            "AND input_schema_version IS NULL AND output_schema_version IS NULL "
            "AND required_capability IS NULL AND cost_class IS NULL "
            "AND side_effect_class IS NULL AND approval_policy IS NULL "
            "AND retry_classification IS NULL "
            "AND timeout_ms IS NULL AND max_result_bytes IS NULL "
            "AND max_cost_micro_usd IS NULL) OR "
            "(resolved_tool_name IS NOT NULL AND length(btrim(resolved_tool_name)) > 0 "
            "AND tool_version IS NOT NULL AND length(btrim(tool_version)) > 0 "
            "AND input_schema_version IS NOT NULL "
            "AND length(btrim(input_schema_version)) > 0 "
            "AND output_schema_version IS NOT NULL "
            "AND length(btrim(output_schema_version)) > 0 "
            "AND required_capability IS NOT NULL "
            "AND length(btrim(required_capability)) > 0 "
            "AND cost_class IS NOT NULL AND length(btrim(cost_class)) > 0 "
            "AND side_effect_class IS NOT NULL "
            "AND length(btrim(side_effect_class)) > 0 "
            "AND approval_policy IS NOT NULL AND length(btrim(approval_policy)) > 0 "
            "AND retry_classification IS NOT NULL "
            "AND length(btrim(retry_classification)) > 0 "
            "AND timeout_ms IS NOT NULL AND max_result_bytes IS NOT NULL "
            "AND max_cost_micro_usd IS NOT NULL)",
            name="registry_metadata_paired",
        ),
        CheckConstraint(
            f"cost_class IS NULL OR cost_class IN ({_TOOL_COST_CLASSES})",
            name="cost_class",
        ),
        CheckConstraint(
            f"side_effect_class IS NULL OR side_effect_class IN ({_TOOL_SIDE_EFFECT_CLASSES})",
            name="side_effect_class",
        ),
        CheckConstraint(
            f"approval_policy IS NULL OR approval_policy IN ({_TOOL_APPROVAL_POLICIES})",
            name="approval_policy",
        ),
        CheckConstraint(
            "retry_classification IS NULL OR "
            f"retry_classification IN ({_TOOL_RETRY_CLASSIFICATIONS})",
            name="retry_classification",
        ),
        CheckConstraint(
            "(policy_decision IS NULL AND policy_reason_code IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_reason_code IS NOT NULL "
            "AND length(btrim(policy_reason_code)) > 0)",
            name="policy_reason_paired",
        ),
        CheckConstraint(
            "jsonb_typeof(sanitized_input_summary) = 'object' "
            "AND octet_length(sanitized_input_summary::text) <= 16384",
            name="input_summary_bounded",
        ),
        CheckConstraint(
            "sanitized_output_summary IS NULL OR "
            "(jsonb_typeof(sanitized_output_summary) = 'object' "
            "AND octet_length(sanitized_output_summary::text) <= 32768)",
            name="output_summary_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(source_summary) = 'array' "
            "AND octet_length(source_summary::text) <= 262144",
            name="source_summary_bounded",
        ),
        CheckConstraint(
            "timeout_ms IS NULL OR timeout_ms BETWEEN 1 AND 300000",
            name="timeout_bounds",
        ),
        CheckConstraint(
            "max_result_bytes IS NULL OR max_result_bytes BETWEEN 1 AND 10000000",
            name="result_size_bounds",
        ),
        CheckConstraint(
            "(max_cost_micro_usd IS NULL OR "
            "max_cost_micro_usd BETWEEN 1 AND 1000000000) "
            "AND cost_micro_usd >= 0 "
            "AND (duration_ms IS NULL OR duration_ms >= 0)",
            name="usage_nonnegative",
        ),
        CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= created_at",
            name="terminal_after_request",
        ),
        CheckConstraint(
            "(status = 'requested' AND policy_decision IS NULL "
            "AND terminal_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'approval_required' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'approval_required' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'denied' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'deny' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NULL "
            "AND error_code IS NOT NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'running' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND terminal_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'completed' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NOT NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL "
            "AND error_code IS NOT NULL AND sanitized_output_summary IS NULL AND "
            "((policy_decision IS NULL AND duration_ms IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND duration_ms IS NOT NULL))) OR "
            "(status = 'cancelled' AND terminal_at IS NOT NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL AND "
            "((policy_decision IS NULL AND duration_ms IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND duration_ms IS NOT NULL)))",
            name="lifecycle_consistent",
        ),
        Index(None, "workspace_id", "run_id", "created_at", "id"),
        Index(None, "workspace_id", "status", "created_at", "id"),
        Index(None, "workspace_id", "trace_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    requested_tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    toolset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_schema_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    required_capability: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side_effect_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="requested", server_default="requested"
    )
    sanitizer_version: Mapped[str] = mapped_column(String(128), nullable=False)
    sanitized_input_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sanitized_output_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    source_summary: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_result_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
