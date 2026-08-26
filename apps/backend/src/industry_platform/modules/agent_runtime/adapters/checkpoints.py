"""PostgreSQL CheckpointStore with optimistic revision checks."""

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.checkpoints import (
    CheckpointConflictError,
    CheckpointEnvelope,
    CheckpointNotFoundError,
    IncompatibleCheckpointVersionError,
    LoadCheckpointRequest,
    SaveCheckpointCommand,
    create_checkpoint_envelope,
    validate_checkpoint_cas,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.models import AgentCheckpointRecord
from industry_platform.modules.agent_runtime.state import RunState


class CheckpointPersistenceError(RuntimeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Checkpoint persistence is unavailable")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SqlAlchemyCheckpointStore:
    session_factory: AsyncSessionFactory

    async def save(self, command: SaveCheckpointCommand) -> CheckpointEnvelope:
        try:
            async with self.session_factory.begin() as session:
                current_record = await session.scalar(
                    select(AgentCheckpointRecord)
                    .where(
                        AgentCheckpointRecord.run_id == command.run_id,
                        AgentCheckpointRecord.workspace_id == command.workspace_id,
                    )
                    .order_by(AgentCheckpointRecord.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                current = None if current_record is None else _envelope(current_record)
                validate_checkpoint_cas(command, current)
                saved_at = await _database_now(session)
                envelope = create_checkpoint_envelope(
                    command,
                    checkpoint_id=uuid4(),
                    saved_at=max(saved_at, command.state.updated_at),
                )
                session.add(
                    AgentCheckpointRecord(
                        id=envelope.checkpoint_id,
                        workspace_id=envelope.workspace_id,
                        run_id=envelope.run_id,
                        revision=envelope.revision,
                        envelope_schema_version=envelope.envelope_schema_version,
                        state_schema_version=envelope.state_schema_version,
                        state={
                            "run_state": _state_document(envelope.state),
                            "payload": dict(envelope.payload),
                        },
                        saved_at=envelope.saved_at,
                    )
                )
            return envelope
        except (CheckpointConflictError, IncompatibleCheckpointVersionError):
            raise
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise CheckpointConflictError(
                    expected_revision=command.expected_revision,
                    current_revision=None,
                ) from None
            raise CheckpointPersistenceError(sqlstate=safe_sqlstate(error)) from None
        except (TypeError, ValueError):
            raise CheckpointPersistenceError() from None
        except SQLAlchemyError as error:
            raise CheckpointPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def load(self, request: LoadCheckpointRequest) -> CheckpointEnvelope:
        try:
            async with self.session_factory() as session:
                statement = select(AgentCheckpointRecord).where(
                    AgentCheckpointRecord.run_id == request.run_id,
                    AgentCheckpointRecord.workspace_id == request.workspace_id,
                )
                if request.revision is None:
                    statement = statement.order_by(AgentCheckpointRecord.revision.desc()).limit(1)
                else:
                    statement = statement.where(AgentCheckpointRecord.revision == request.revision)
                record = await session.scalar(statement)
            if record is None:
                raise CheckpointNotFoundError
            return _envelope(record)
        except (CheckpointNotFoundError, IncompatibleCheckpointVersionError):
            raise
        except (TypeError, ValueError):
            raise CheckpointPersistenceError() from None
        except SQLAlchemyError as error:
            raise CheckpointPersistenceError(sqlstate=safe_sqlstate(error)) from None


async def _database_now(session: object) -> datetime:
    from sqlalchemy import func
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise CheckpointPersistenceError()
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise CheckpointPersistenceError()
    return value


def _state_document(state: RunState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "run_id": str(state.run_id),
        "workspace_id": str(state.workspace_id),
        "revision": state.revision,
        "status": state.status.value,
        "step_count": state.step_count,
        "event_count": state.event_count,
        "input_tokens_used": state.input_tokens_used,
        "output_tokens_used": state.output_tokens_used,
        "cost_micro_usd": state.cost_micro_usd,
        "updated_at": state.updated_at.isoformat(),
        "artifact_ids": [str(value) for value in state.artifact_ids],
        "stop_reason": None if state.stop_reason is None else state.stop_reason.value,
        "max_steps_preflight_rejected": state.max_steps_preflight_rejected,
        "token_budget_preflight_rejected": state.token_budget_preflight_rejected,
        "cost_budget_preflight_rejected": state.cost_budget_preflight_rejected,
    }


def _envelope(record: AgentCheckpointRecord) -> CheckpointEnvelope:
    document = record.state
    raw_state = document.get("run_state")
    raw_payload = document.get("payload", {})
    if not isinstance(raw_state, dict) or not isinstance(raw_payload, dict):
        raise CheckpointPersistenceError()
    try:
        stop_reason_value = raw_state.get("stop_reason")
        state = RunState(
            schema_version=cast(int, raw_state["schema_version"]),
            run_id=UUID(cast(str, raw_state["run_id"])),
            workspace_id=UUID(cast(str, raw_state["workspace_id"])),
            revision=cast(int, raw_state["revision"]),
            status=AgentRunStatus(cast(str, raw_state["status"])),
            step_count=cast(int, raw_state["step_count"]),
            event_count=cast(int, raw_state["event_count"]),
            input_tokens_used=cast(int, raw_state["input_tokens_used"]),
            output_tokens_used=cast(int, raw_state["output_tokens_used"]),
            cost_micro_usd=cast(int, raw_state["cost_micro_usd"]),
            updated_at=datetime.fromisoformat(cast(str, raw_state["updated_at"])),
            artifact_ids=tuple(UUID(value) for value in cast(list[str], raw_state["artifact_ids"])),
            stop_reason=(
                None if stop_reason_value is None else RunStopReason(cast(str, stop_reason_value))
            ),
            max_steps_preflight_rejected=cast(bool, raw_state["max_steps_preflight_rejected"]),
            token_budget_preflight_rejected=cast(
                bool, raw_state["token_budget_preflight_rejected"]
            ),
            cost_budget_preflight_rejected=cast(bool, raw_state["cost_budget_preflight_rejected"]),
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointPersistenceError() from None
    return CheckpointEnvelope(
        envelope_schema_version=record.envelope_schema_version,
        state_schema_version=record.state_schema_version,
        checkpoint_id=record.id,
        run_id=record.run_id,
        workspace_id=record.workspace_id,
        revision=record.revision,
        saved_at=record.saved_at,
        state=state,
        payload=raw_payload,
    )
