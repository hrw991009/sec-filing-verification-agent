"""Owner-authorized, bounded replay of a dead-letter logical Job."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import JobSubmissionRecord, hash_job_idempotency_key
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope


class JobRecoveryConflictError(RuntimeError):
    """The request cannot safely reopen this job or conflicts with a prior replay."""


class JobRecoveryNotFoundError(RuntimeError):
    """No job is visible in the current workspace."""


@dataclass(frozen=True, slots=True)
class ReplayDeadLetter:
    job_id: UUID
    expected_dispatch_generation: int
    additional_attempts: int
    idempotency_key: str = field(repr=False)
    trace_id: TraceId

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.expected_dispatch_generation < 1:
            raise ValueError("Replay requires a non-nil Job and positive dispatch generation")
        if not 1 <= self.additional_attempts <= 3:
            raise ValueError("Replay grants one to three additional attempts")
        hash_job_idempotency_key(self.idempotency_key)


class JobRecoveryRepository(Protocol):
    async def replay(
        self, scope: WorkspaceScope, command: ReplayDeadLetter
    ) -> JobSubmissionRecord: ...


@dataclass(frozen=True, slots=True)
class JobRecoveryService:
    repository: JobRecoveryRepository

    async def replay(self, scope: WorkspaceScope, command: ReplayDeadLetter) -> JobSubmissionRecord:
        if scope.role != "owner":
            raise WorkspaceAccessDeniedError
        return await self.repository.replay(scope, command)
