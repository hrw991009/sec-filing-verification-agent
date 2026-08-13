"""Tests for generic Checkpoint envelopes and optimistic CAS rules."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.checkpoints import (
    CHECKPOINT_ENVELOPE_SCHEMA_VERSION,
    CheckpointConflictError,
    CheckpointEnvelope,
    IncompatibleCheckpointVersionError,
    LoadCheckpointRequest,
    SaveCheckpointCommand,
    create_checkpoint_envelope,
    validate_checkpoint_cas,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRunStatus,
)
from industry_platform.modules.agent_runtime.state import RunState

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
CHECKPOINT_ZERO_ID = UUID("33333333-3333-4333-8333-333333333333")
CHECKPOINT_ONE_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 13, 5, 0, tzinfo=UTC)


def state(*, revision: int, event_count: int) -> RunState:
    return RunState(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        revision=revision,
        status=AgentRunStatus.QUEUED,
        step_count=0,
        event_count=event_count,
        input_tokens_used=0,
        output_tokens_used=0,
        cost_micro_usd=0,
        updated_at=NOW + timedelta(seconds=revision),
    )


def initial_command() -> SaveCheckpointCommand:
    return SaveCheckpointCommand(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        expected_revision=None,
        state=state(revision=0, event_count=1),
    )


def envelope_zero() -> CheckpointEnvelope:
    return create_checkpoint_envelope(
        initial_command(),
        checkpoint_id=CHECKPOINT_ZERO_ID,
        saved_at=NOW,
    )


def test_initial_checkpoint_uses_revision_zero_and_no_magic_negative_value() -> None:
    command = initial_command()
    envelope = create_checkpoint_envelope(
        command,
        checkpoint_id=CHECKPOINT_ZERO_ID,
        saved_at=NOW,
    )

    validate_checkpoint_cas(command, None)
    assert command.expected_revision is None
    assert envelope.revision == 0
    assert envelope.state is command.state
    assert "event_count" not in repr(envelope)

    with pytest.raises(ValueError, match="revision zero"):
        replace(command, state=state(revision=1, event_count=2))


def test_successor_requires_exact_expected_revision_plus_one() -> None:
    current = envelope_zero()
    command = SaveCheckpointCommand(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        expected_revision=0,
        state=state(revision=1, event_count=2),
    )

    validate_checkpoint_cas(command, current)
    successor = create_checkpoint_envelope(
        command,
        checkpoint_id=CHECKPOINT_ONE_ID,
        saved_at=NOW + timedelta(seconds=1),
    )
    assert successor.revision == 1

    with pytest.raises(ValueError, match="exactly one"):
        replace(command, state=state(revision=2, event_count=3))


def test_cas_rejects_duplicate_create_stale_update_and_missing_predecessor() -> None:
    current = envelope_zero()
    with pytest.raises(CheckpointConflictError) as duplicate_create:
        validate_checkpoint_cas(initial_command(), current)
    assert duplicate_create.value.current_revision == 0

    stale = SaveCheckpointCommand(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        expected_revision=1,
        state=state(revision=2, event_count=3),
    )
    with pytest.raises(CheckpointConflictError) as stale_update:
        validate_checkpoint_cas(stale, current)
    assert stale_update.value.expected_revision == 1
    assert str(stale_update.value) == "Checkpoint revision conflict"

    with pytest.raises(CheckpointConflictError):
        validate_checkpoint_cas(stale, None)


def test_incompatible_envelope_or_state_version_is_rejected_explicitly() -> None:
    envelope = envelope_zero()
    assert envelope.envelope_schema_version == CHECKPOINT_ENVELOPE_SCHEMA_VERSION

    with pytest.raises(IncompatibleCheckpointVersionError):
        replace(envelope, envelope_schema_version=2)
    with pytest.raises(IncompatibleCheckpointVersionError):
        replace(envelope, state_schema_version=2)


def test_envelope_and_load_request_enforce_scope_revision_and_time() -> None:
    envelope = envelope_zero()
    latest = LoadCheckpointRequest(run_id=RUN_ID, workspace_id=WORKSPACE_ID)
    exact = LoadCheckpointRequest(run_id=RUN_ID, workspace_id=WORKSPACE_ID, revision=0)
    assert latest.revision is None
    assert exact.revision == 0

    with pytest.raises(ValueError, match="revisions must match"):
        replace(envelope, revision=1)
    with pytest.raises(ValueError, match="before its State"):
        replace(envelope, saved_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="non-negative"):
        replace(exact, revision=-1)
