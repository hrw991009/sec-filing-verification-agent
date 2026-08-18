"""Security and terminal-state tests for Tool Event projections."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.adapters import persistence
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.tools.domain import (
    ToolObservation,
    ToolReference,
    ToolSource,
)
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
REQUEST_STEP_ID = UUID("44444444-4444-4444-8444-444444444444")
EXECUTION_STEP_ID = UUID("55555555-5555-4555-8555-555555555555")
CALL_ID = UUID("66666666-6666-4666-8666-666666666666")
OBSERVATION_ID = UUID("77777777-7777-4777-8777-777777777777")
STREAM_ID = UUID("88888888-8888-4888-8888-888888888888")
OTHER_ID = UUID("99999999-9999-4999-8999-999999999999")
TRACE_ID = TraceId("tool-persistence-trace")
NOW = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def call_record(*, status: str = "running") -> ToolCallRecord:
    execution_started = status == "running"
    return ToolCallRecord(
        id=CALL_ID,
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        requested_by_step_id=REQUEST_STEP_ID,
        execution_step_id=EXECUTION_STEP_ID if execution_started else None,
        schema_version=1,
        requested_tool_name="fake.industry_lookup",
        requested_tool_version="v1",
        resolved_tool_name="fake.industry_lookup" if execution_started else None,
        tool_version="v1" if execution_started else None,
        toolset_version="toolset-v1",
        input_schema_version="input-v1" if execution_started else None,
        output_schema_version="output-v1" if execution_started else None,
        required_capability="workspace.tools.run" if execution_started else None,
        cost_class="low" if execution_started else None,
        side_effect_class="read_only" if execution_started else None,
        approval_policy="auto_allow" if execution_started else None,
        retry_classification="safe_read_only" if execution_started else None,
        policy_version="policy-v1",
        policy_decision="allow" if execution_started else None,
        policy_reason_code="static_policy_allowed" if execution_started else None,
        status=status,
        timeout_ms=1_000 if execution_started else None,
        max_result_bytes=20_000 if execution_started else None,
        max_cost_micro_usd=1_000 if execution_started else None,
        cost_micro_usd=0,
        sanitized_arguments_hash=b"a" * 32,
        idempotency_key_hash=None,
        observation_schema_version=None,
        observation=None,
        observation_content_sha256=None,
        observation_envelope_sha256=None,
        started_at=NOW if execution_started else None,
        terminal_at=None,
        error_code=None,
        created_at=NOW - timedelta(seconds=1),
        updated_at=NOW,
    )


def audit_record(*, status: str = "running") -> ToolRunRecord:
    execution_started = status == "running"
    return ToolRunRecord(
        id=CALL_ID,
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        actor_user_id=USER_ID,
        actor_role="owner",
        trace_id=str(TRACE_ID),
        schema_version=1,
        requested_tool_name="fake.industry_lookup",
        requested_tool_version="v1",
        resolved_tool_name="fake.industry_lookup" if execution_started else None,
        tool_version="v1" if execution_started else None,
        toolset_version="toolset-v1",
        input_schema_version="input-v1" if execution_started else None,
        output_schema_version="output-v1" if execution_started else None,
        required_capability="workspace.tools.run" if execution_started else None,
        cost_class="low" if execution_started else None,
        side_effect_class="read_only" if execution_started else None,
        approval_policy="auto_allow" if execution_started else None,
        retry_classification="safe_read_only" if execution_started else None,
        policy_version="policy-v1",
        policy_decision="allow" if execution_started else None,
        policy_reason_code="static_policy_allowed" if execution_started else None,
        status=status,
        sanitizer_version="arguments-v1",
        sanitized_input_summary={"argument_count": 1, "canonical_bytes": 17},
        sanitized_output_summary=None,
        source_summary=[],
        timeout_ms=1_000 if execution_started else None,
        max_result_bytes=20_000 if execution_started else None,
        max_cost_micro_usd=1_000 if execution_started else None,
        cost_micro_usd=0,
        duration_ms=None,
        terminal_at=None,
        error_code=None,
        created_at=NOW - timedelta(seconds=1),
        updated_at=NOW,
    )


def event(event_type: AgentEventType, payload: dict[str, object]) -> AgentEvent:
    return AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=5,
        occurred_at=NOW + timedelta(seconds=2),
        trace_id=TRACE_ID,
        event_type=event_type,
        payload=payload,
    )


def completed_payload() -> dict[str, object]:
    text = "bounded normalized result"
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    observed_at = NOW + timedelta(seconds=1)
    observation = ToolObservation(
        schema_version=1,
        observation_id=OBSERVATION_ID,
        call_id=CALL_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        tool=ToolReference("fake.industry_lookup", "v1"),
        normalizer_version="normalizer-v1",
        model_text=text,
        sources=(
            ToolSource(
                source_type="fixture",
                source_version="fixture-v1",
                locator="fixture://safe/result",
                observed_at=observed_at,
                content_sha256=digest,
            ),
        ),
        observed_at=observed_at,
        content_sha256=digest,
    )
    envelope = dict(observation.to_persistence_payload())
    return {
        "call_id": str(CALL_ID),
        "execution_step_id": str(EXECUTION_STEP_ID),
        "duration_ms": 25,
        "cost_micro_usd": 0,
        "sanitized_output_summary": dict(observation.sanitized_output_summary),
        "source_summary": deepcopy(cast(list[dict[str, object]], envelope["sources"])),
        "observation_schema_version": observation.schema_version,
        "observation_id": str(observation.observation_id),
        "observation_content_sha256": observation.content_sha256,
        "observation_envelope_sha256": observation.model_visible_envelope_sha256,
        "observation": envelope,
    }


@pytest.mark.parametrize(
    ("event_type", "expected_status", "expected_error"),
    [
        (AgentEventType.RUN_FAILED, "failed", "runtime_interrupted"),
        (AgentEventType.RUN_CANCELLED, "cancelled", None),
    ],
)
def test_terminal_run_settles_pre_execution_request_without_faking_execution(
    event_type: AgentEventType,
    expected_status: str,
    expected_error: str | None,
) -> None:
    call = call_record(status="requested")
    audit = audit_record(status="requested")

    persistence._settle_interrupted_tool_fact(call, audit, event(event_type, {}))

    assert call.status == audit.status == expected_status
    assert call.execution_step_id is None
    assert call.started_at is None
    assert call.policy_decision is None
    assert audit.policy_decision is None
    assert audit.duration_ms is None
    assert call.error_code == audit.error_code == expected_error
    assert call.terminal_at == audit.terminal_at == NOW + timedelta(seconds=2)


@pytest.mark.parametrize(
    "event_type",
    [AgentEventType.RUN_FAILED, AgentEventType.RUN_CANCELLED],
)
@pytest.mark.parametrize("side_effect_class", ["idempotent_write", "non_idempotent_write"])
def test_terminal_run_marks_started_write_outcome_unknown(
    event_type: AgentEventType,
    side_effect_class: str,
) -> None:
    call = call_record()
    audit = audit_record()
    call.side_effect_class = side_effect_class
    audit.side_effect_class = side_effect_class

    persistence._settle_interrupted_tool_fact(call, audit, event(event_type, {}))

    assert call.status == audit.status == "failed"
    assert call.error_code == audit.error_code == "tool_outcome_unknown"
    assert call.execution_step_id == EXECUTION_STEP_ID
    assert call.started_at == NOW
    assert audit.duration_ms == 2_000


def test_completed_projection_rebuilds_one_canonical_observation() -> None:
    projection = persistence._validated_tool_observation_projection(
        call_record(),
        event(AgentEventType.TOOL_COMPLETED, completed_payload()),
    )

    assert projection.envelope["call_id"] == str(CALL_ID)
    assert projection.envelope["tool_name"] == "fake.industry_lookup"
    assert projection.content_sha256 == projection.envelope["content_sha256"]
    assert projection.envelope_sha256 == completed_payload()["observation_envelope_sha256"]
    assert projection.source_summary == projection.envelope["sources"]


def test_definition_snapshot_persists_retry_classification_on_both_facts() -> None:
    call = call_record(status="requested")
    audit = audit_record(status="requested")
    payload: dict[str, object] = {
        "resolved_tool_name": "fake.industry_lookup",
        "tool_version": "v1",
        "input_schema_version": "input-v1",
        "output_schema_version": "output-v1",
        "required_capability": "workspace.tools.run",
        "cost_class": "low",
        "side_effect_class": "read_only",
        "approval_policy": "auto_allow",
        "retry_classification": "safe_read_only",
        "policy_version": "policy-v1",
        "timeout_ms": 1_000,
        "max_result_bytes": 20_000,
        "max_cost_micro_usd": 1_000,
    }

    persistence._apply_tool_definition_snapshot(call, audit, payload)

    assert call.retry_classification == "safe_read_only"
    assert audit.retry_classification == "safe_read_only"


@pytest.mark.parametrize(
    "tampering",
    [
        "call",
        "tool",
        "model_text",
        "uppercase_hash",
        "envelope_hash",
        "source_summary",
        "output_summary",
    ],
)
def test_completed_projection_rejects_inconsistent_or_noncanonical_observation(
    tampering: str,
) -> None:
    payload = completed_payload()
    envelope = cast(dict[str, object], payload["observation"])
    if tampering == "call":
        envelope["call_id"] = str(OTHER_ID)
    elif tampering == "tool":
        envelope["tool_name"] = "fake.other_lookup"
    elif tampering == "model_text":
        envelope["model_text"] = "tampered result"
    elif tampering == "uppercase_hash":
        uppercase = cast(str, envelope["content_sha256"]).upper()
        envelope["content_sha256"] = uppercase
        payload["observation_content_sha256"] = uppercase
    elif tampering == "envelope_hash":
        payload["observation_envelope_sha256"] = "0" * 64
    elif tampering == "source_summary":
        sources = cast(list[dict[str, object]], payload["source_summary"])
        sources[0]["locator"] = "fixture://different"
    else:
        summary = cast(dict[str, object], payload["sanitized_output_summary"])
        summary["source_count"] = 2

    with pytest.raises(persistence.AgentEventPersistenceError):
        persistence._validated_tool_observation_projection(
            call_record(),
            event(AgentEventType.TOOL_COMPLETED, payload),
        )
