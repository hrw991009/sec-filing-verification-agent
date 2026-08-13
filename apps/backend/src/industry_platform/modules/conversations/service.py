"""Application service for atomic Conversation/Run/Job acceptance."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from industry_platform.modules.conversations.domain import (
    DIRECT_ANSWER_QUEUE_NAME,
    DIRECT_ANSWER_TASK_NAME,
    DirectAnswerTurnReceipt,
    PreparedDirectAnswerTurn,
    StartDirectAnswerTurn,
    build_queued_run,
    deterministic_run_id,
    fingerprint_direct_answer_turn,
)
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    JobDefinition,
    PreparedJobSubmission,
    hash_job_idempotency_key,
)


class ConversationNotFoundError(LookupError):
    """The requested Conversation is absent from the trusted Workspace."""


class ConversationPersistenceError(RuntimeError):
    """A sanitized transactional persistence failure."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Conversation persistence failed")
        self.sqlstate = sqlstate


class DirectAnswerTurnWriter(Protocol):
    """Write every acceptance fact through one database transaction."""

    async def submit(self, prepared: PreparedDirectAnswerTurn) -> DirectAnswerTurnReceipt: ...


class DirectAnswerTurnTransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[DirectAnswerTurnWriter]: ...


type UtcClock = Callable[[], datetime]
type IdSource = Callable[[], UUID]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ConversationApplicationService:
    """Accept one turn only after Conversation, Run, Job, and Outbox all commit."""

    transaction_factory: DirectAnswerTurnTransactionFactory
    clock: UtcClock = utc_now
    id_source: IdSource = uuid4

    async def start_direct_answer(self, command: StartDirectAnswerTurn) -> DirectAnswerTurnReceipt:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("Conversation clock must return UTC")

        run_id = deterministic_run_id(
            workspace_id=command.workspace_id,
            idempotency_key=command.idempotency_key,
        )
        conversation_id = command.conversation_id or self._new_id()
        turn_id = self._new_id()
        user_message_id = self._new_id()
        job_id = self._new_id()
        outbox_event_id = self._new_id()
        stream_id = self._new_id()

        job_definition = JobDefinition(
            scope=ExecutionScope(workspace_id=command.workspace_id),
            task_name=DIRECT_ANSWER_TASK_NAME,
            queue_name=DIRECT_ANSWER_QUEUE_NAME,
            payload={"agent_run_id": str(run_id), "schema_version": 1},
            available_at=now,
            max_attempts=3,
            idempotency_key=command.idempotency_key,
            soft_time_limit_seconds=300,
            hard_time_limit_seconds=330,
        )
        idempotency_hash = hash_job_idempotency_key(command.idempotency_key)
        job = PreparedJobSubmission(
            job_id=job_id,
            outbox_event_id=outbox_event_id,
            scope=job_definition.scope,
            task_name=job_definition.task_name,
            queue_name=job_definition.queue_name,
            payload=job_definition.payload,
            available_at=job_definition.available_at,
            max_attempts=job_definition.max_attempts,
            priority=job_definition.priority,
            soft_time_limit_seconds=job_definition.soft_time_limit_seconds,
            hard_time_limit_seconds=job_definition.hard_time_limit_seconds,
            trace_id=command.trace_id,
            idempotency_key_hash=idempotency_hash,
            request_fingerprint=fingerprint_direct_answer_turn(command, run_id=run_id),
            submitted_at=now,
        )
        run = build_queued_run(
            command,
            run_id=run_id,
            stream_id=stream_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            job_id=job_id,
            created_at=now,
        )
        prepared = PreparedDirectAnswerTurn(
            conversation_id=conversation_id,
            create_conversation=command.conversation_id is None,
            conversation_title=command.new_conversation_title,
            turn_id=turn_id,
            user_message_id=user_message_id,
            run=run,
            job=job,
            question=command.question,
            search_mode=command.search_mode,
            industry_id=command.industry_id,
            knowledge_base_ids=command.knowledge_base_ids,
        )
        async with self.transaction_factory() as writer:
            return await writer.submit(prepared)

    def _new_id(self) -> UUID:
        value = self.id_source()
        if value.int == 0:
            raise ValueError("Conversation ID source returned a nil UUID")
        return value
