"""Authorized policy boundary for accepting one queued direct-answer turn."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import RunBudget, require_utc
from industry_platform.modules.conversations.domain import (
    DirectAnswerTurnReceipt,
    StartDirectAnswerTurn,
    TurnSearchMode,
)
from industry_platform.modules.conversations.service import ConversationPersistenceError
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import (
    JobIdempotencyConflictError,
    JobPersistenceError,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


class ConversationModeNotReadyError(RuntimeError):
    """Raised when a Turn requests a mode whose real Provider path is not ready."""

    def __init__(self, mode: TurnSearchMode) -> None:
        super().__init__(f"Conversation mode {mode.value!r} is not ready")
        self.mode = mode


class ConversationIdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is reused for a changed Turn request."""


@dataclass(frozen=True, slots=True)
class SubmitConversationTurn:
    trace_id: TraceId
    idempotency_key: str = field(repr=False)
    question: str = field(repr=False)
    conversation_id: UUID | None = None
    title: str | None = None
    search_mode: TurnSearchMode = TurnSearchMode.NONE
    industry_id: UUID | None = None
    knowledge_base_ids: tuple[UUID, ...] = ()
    attachment_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "knowledge_base_ids", tuple(self.knowledge_base_ids))
        object.__setattr__(self, "attachment_ids", tuple(self.attachment_ids))


@dataclass(frozen=True, slots=True)
class DirectAnswerSubmissionPolicy:
    runtime_version: str = "direct-answer-runtime-v0"
    harness_version: str = "harness-v0"
    max_steps: int = 2
    max_total_tokens: int = 4_096
    max_cost_micro_usd: int = 250_000
    timeout_seconds: int = 300

    def budget_at(self, accepted_at: datetime) -> RunBudget:
        require_utc(accepted_at, field_name="Turn acceptance time")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
            raise ValueError("Direct-answer timeout is invalid")
        return RunBudget(
            schema_version=1,
            max_steps=self.max_steps,
            max_total_tokens=self.max_total_tokens,
            max_cost_micro_usd=self.max_cost_micro_usd,
            deadline=accepted_at + timedelta(seconds=self.timeout_seconds),
        )


class DirectAnswerStarter(Protocol):
    async def start_direct_answer(
        self, command: StartDirectAnswerTurn
    ) -> DirectAnswerTurnReceipt: ...


class ConversationSubmissionUseCase(Protocol):
    async def submit(
        self,
        scope: WorkspaceScope,
        request: SubmitConversationTurn,
    ) -> DirectAnswerTurnReceipt: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ConversationSubmissionService:
    application: DirectAnswerStarter
    policy: DirectAnswerSubmissionPolicy = DirectAnswerSubmissionPolicy()
    clock: Callable[[], datetime] = utc_now

    async def submit(
        self,
        scope: WorkspaceScope,
        request: SubmitConversationTurn,
    ) -> DirectAnswerTurnReceipt:
        if not scope_allows(scope, WorkspaceAction.CREATE_RESOURCE):
            raise WorkspaceAccessDeniedError
        if request.search_mode is not TurnSearchMode.NONE:
            raise ConversationModeNotReadyError(request.search_mode)
        if request.knowledge_base_ids:
            raise ConversationModeNotReadyError(TurnSearchMode.LOCAL)

        accepted_at = self.clock()
        command = StartDirectAnswerTurn(
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            trace_id=request.trace_id,
            budget=self.policy.budget_at(accepted_at),
            runtime_version=self.policy.runtime_version,
            harness_version=self.policy.harness_version,
            idempotency_key=request.idempotency_key,
            question=request.question,
            conversation_id=request.conversation_id,
            new_conversation_title=request.title,
            search_mode=request.search_mode,
            industry_id=request.industry_id,
            knowledge_base_ids=request.knowledge_base_ids,
            attachment_ids=request.attachment_ids,
        )
        try:
            return await self.application.start_direct_answer(command)
        except JobIdempotencyConflictError:
            raise ConversationIdempotencyConflictError from None
        except JobPersistenceError as error:
            raise ConversationPersistenceError(sqlstate=error.sqlstate) from None
