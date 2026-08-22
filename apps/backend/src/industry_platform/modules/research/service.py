"""Authorized application services for the Research L3 user journey."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.conversations.domain import (
    DirectAnswerTurnReceipt,
    StartDirectAnswerTurn,
    TurnSearchMode,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.research.domain import (
    RESEARCH_HARNESS_VERSION,
    RESEARCH_RUNTIME_VERSION,
    ResearchBriefInput,
    ResearchRunView,
    ResearchStartReceipt,
    research_run_id_for_agent_run,
)
from industry_platform.modules.research.ports import ResearchQueryRepository
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


class ResearchStarter(Protocol):
    async def start_direct_answer(
        self, command: StartDirectAnswerTurn
    ) -> DirectAnswerTurnReceipt: ...


class ResearchNotFoundError(LookupError):
    """Absent and cross-workspace Research resources share the same public error."""


@dataclass(frozen=True, slots=True)
class StartResearch:
    trace_id: TraceId
    industry_id: UUID
    brief: ResearchBriefInput
    idempotency_key: str = field(repr=False)
    max_steps: int = 20
    max_total_tokens: int = 16_384
    max_cost_micro_usd: int = 500_000
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        if self.industry_id.int == 0:
            raise ValueError("Research industry ID is invalid")
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 200:
            raise ValueError("Research idempotency key is invalid")
        for value, lower, upper, name in (
            (self.max_steps, 12, 64, "Research max steps"),
            (self.max_total_tokens, 1_024, 100_000, "Research token budget"),
            (self.max_cost_micro_usd, 0, 10_000_000, "Research cost budget"),
            (self.timeout_seconds, 30, 1_500, "Research deadline"),
        ):
            if isinstance(value, bool) or not lower <= value <= upper:
                raise ValueError(f"{name} is invalid")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ResearchSubmissionService:
    application: ResearchStarter
    clock: Callable[[], datetime] = utc_now

    async def start(
        self,
        scope: WorkspaceScope,
        request: StartResearch,
    ) -> ResearchStartReceipt:
        if not scope_allows(scope, WorkspaceAction.RUN_RESEARCH):
            raise WorkspaceAccessDeniedError
        accepted_at = self.clock()
        budget = RunBudget(
            schema_version=1,
            max_steps=request.max_steps,
            max_total_tokens=request.max_total_tokens,
            max_cost_micro_usd=request.max_cost_micro_usd,
            deadline=accepted_at + timedelta(seconds=request.timeout_seconds),
        )
        receipt = await self.application.start_direct_answer(
            StartDirectAnswerTurn(
                workspace_id=scope.workspace_id,
                user_id=scope.user_id,
                trace_id=request.trace_id,
                budget=budget,
                runtime_version=RESEARCH_RUNTIME_VERSION,
                harness_version=RESEARCH_HARNESS_VERSION,
                idempotency_key=request.idempotency_key,
                question=request.brief.original_question,
                new_conversation_title=None,
                search_mode=TurnSearchMode.WEB,
                industry_id=request.industry_id,
                research_brief=request.brief,
            )
        )
        return ResearchStartReceipt(
            research_run_id=research_run_id_for_agent_run(receipt.run_id),
            agent_run_id=receipt.run_id,
            conversation_id=receipt.conversation_id,
            turn_id=receipt.turn_id,
            job_id=receipt.job_id,
            created=receipt.created,
        )


@dataclass(frozen=True, slots=True)
class ResearchQueryService:
    repository: ResearchQueryRepository

    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchRunView:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        if research_run_id.int == 0:
            raise ResearchNotFoundError
        return await self.repository.get(scope, research_run_id)

    async def list(self, scope: WorkspaceScope, *, limit: int) -> tuple[ResearchRunView, ...]:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Research list limit is invalid")
        return await self.repository.list(scope, limit=limit)
