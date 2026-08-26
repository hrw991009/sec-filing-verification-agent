"""Versioned Checkpoint envelope and optimistic compare-and-swap rules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)
from industry_platform.modules.agent_runtime.state import RunState

CHECKPOINT_ENVELOPE_SCHEMA_VERSION = 1


class CheckpointError(RuntimeError):
    """Base class for stable Checkpoint Store failures."""


class CheckpointConflictError(CheckpointError):
    """Raised when a create or update loses its optimistic revision race."""

    def __init__(
        self,
        *,
        expected_revision: int | None,
        current_revision: int | None,
    ) -> None:
        super().__init__("Checkpoint revision conflict")
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class CheckpointNotFoundError(CheckpointError):
    """Raised when the requested Run revision has no persisted Checkpoint."""

    def __init__(self) -> None:
        super().__init__("Checkpoint not found")


class IncompatibleCheckpointVersionError(CheckpointError):
    """Raised before an unsupported envelope or State can be restored."""

    def __init__(self) -> None:
        super().__init__("Checkpoint version is incompatible")


def _require_non_negative_revision(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SaveCheckpointCommand:
    """Validated intent to create revision zero or CAS-write its successor."""

    run_id: UUID
    workspace_id: UUID
    expected_revision: int | None
    state: RunState = field(repr=False)
    checkpoint_revision: int | None = None
    payload: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.run_id, field_name="Checkpoint save run ID")
        require_non_nil_uuid(self.workspace_id, field_name="Checkpoint save workspace ID")
        if self.state.run_id != self.run_id or self.state.workspace_id != self.workspace_id:
            raise ValueError("Checkpoint State belongs to another run or workspace")
        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Checkpoint payload must be canonical JSON data",
            ),
        )
        if self.checkpoint_revision is not None:
            _require_non_negative_revision(
                self.checkpoint_revision,
                field_name="Checkpoint revision",
            )
            expected_checkpoint_revision = (
                0 if self.expected_revision is None else self.expected_revision + 1
            )
            if self.checkpoint_revision != expected_checkpoint_revision:
                raise ValueError("Checkpoint revision must increase by exactly one")
            return
        if self.expected_revision is None:
            if self.state.revision != 0:
                raise ValueError("Initial Checkpoint State must use revision zero")
            return
        _require_non_negative_revision(
            self.expected_revision,
            field_name="Expected Checkpoint revision",
        )
        if self.state.revision != self.expected_revision + 1:
            raise ValueError("Checkpoint State revision must increase by exactly one")


@dataclass(frozen=True, slots=True)
class LoadCheckpointRequest:
    """Workspace-scoped lookup for one revision or the latest Checkpoint."""

    run_id: UUID
    workspace_id: UUID
    revision: int | None = None

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.run_id, field_name="Checkpoint load run ID")
        require_non_nil_uuid(self.workspace_id, field_name="Checkpoint load workspace ID")
        if self.revision is not None:
            _require_non_negative_revision(
                self.revision,
                field_name="Requested Checkpoint revision",
            )


@dataclass(frozen=True, slots=True)
class CheckpointEnvelope:
    """Self-describing State snapshot safe to reject before restoration."""

    envelope_schema_version: int
    state_schema_version: int
    checkpoint_id: UUID
    run_id: UUID
    workspace_id: UUID
    revision: int
    saved_at: datetime
    state: RunState = field(repr=False)
    payload: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.envelope_schema_version != CHECKPOINT_ENVELOPE_SCHEMA_VERSION:
            raise IncompatibleCheckpointVersionError
        if self.state_schema_version != AGENT_RUNTIME_SCHEMA_VERSION:
            raise IncompatibleCheckpointVersionError
        if self.state.schema_version != self.state_schema_version:
            raise IncompatibleCheckpointVersionError
        for identifier, field_name in (
            (self.checkpoint_id, "Checkpoint ID"),
            (self.run_id, "Checkpoint run ID"),
            (self.workspace_id, "Checkpoint workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        _require_non_negative_revision(self.revision, field_name="Checkpoint revision")
        require_utc(self.saved_at, field_name="Checkpoint save time")
        if self.state.run_id != self.run_id or self.state.workspace_id != self.workspace_id:
            raise ValueError("Checkpoint State belongs to another run or workspace")
        if self.state.revision != self.revision and not self.payload:
            raise ValueError("Checkpoint and State revisions must match")
        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Checkpoint payload must be canonical JSON data",
            ),
        )
        if self.saved_at < self.state.updated_at:
            raise ValueError("Checkpoint cannot be saved before its State update")


def validate_checkpoint_cas(
    command: SaveCheckpointCommand,
    current: CheckpointEnvelope | None,
) -> None:
    """Apply the exact create-or-update comparison every Store must use."""

    current_revision = current.revision if current is not None else None
    if current is not None and (
        current.run_id != command.run_id or current.workspace_id != command.workspace_id
    ):
        raise ValueError("Current Checkpoint belongs to another run or workspace")
    if command.expected_revision != current_revision:
        raise CheckpointConflictError(
            expected_revision=command.expected_revision,
            current_revision=current_revision,
        )


def create_checkpoint_envelope(
    command: SaveCheckpointCommand,
    *,
    checkpoint_id: UUID,
    saved_at: datetime,
) -> CheckpointEnvelope:
    """Build the canonical envelope after a Store wins its CAS operation."""

    return CheckpointEnvelope(
        envelope_schema_version=CHECKPOINT_ENVELOPE_SCHEMA_VERSION,
        state_schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        checkpoint_id=checkpoint_id,
        run_id=command.run_id,
        workspace_id=command.workspace_id,
        revision=(
            command.state.revision
            if command.checkpoint_revision is None
            else command.checkpoint_revision
        ),
        saved_at=saved_at,
        state=command.state,
        payload=command.payload,
    )
