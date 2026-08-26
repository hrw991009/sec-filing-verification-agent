"""Typed durable Research L4 checkpoint, approval, and resume contracts."""

import base64
import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from industry_platform.modules.agent_runtime.checkpoints import CheckpointEnvelope
from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.research.domain import (
    RESEARCH_QUEUE_NAME,
    RESEARCH_TASK_NAME,
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchNode,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

APPROVAL_TTL_SECONDS = 900
_APPROVAL_NAMESPACE = UUID("16494ddf-f43c-49af-abdd-cd36d4c4bb65")
_TOKEN_DOMAIN = b"industry-platform:research-resume:v1\x00"


class ResearchDurabilityError(RuntimeError):
    code = "research_durability_error"


class ResearchApprovalNotFoundError(ResearchDurabilityError):
    code = "approval_not_found"


class ResearchApprovalConflictError(ResearchDurabilityError):
    code = "approval_conflict"


class ResearchResumeTokenError(ResearchDurabilityError):
    code = "resume_token_invalid"


class ResearchResumeStateError(ResearchDurabilityError):
    code = "resume_state_invalid"


@dataclass(frozen=True, slots=True)
class ResearchCheckpointSummary:
    checkpoint_id: UUID
    revision: int
    run_state_revision: int
    node: ResearchNode
    next_node: ResearchNode | None
    saved_at: datetime
    state_diff: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ResearchApprovalRequest:
    approval_request_id: UUID
    run_id: UUID
    checkpoint_id: UUID
    checkpoint_revision: int
    reason: ResearchApprovalReason
    status: ResearchApprovalStatus
    requested_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    decided_by_user_id: UUID | None = None
    decided_at: datetime | None = None
    resume_claimed: bool = False
    resume_job_id: UUID | None = None
    resumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResearchDurabilityTimeline:
    checkpoints: tuple[ResearchCheckpointSummary, ...]
    approvals: tuple[ResearchApprovalRequest, ...]
    duplicate_side_effect_count: int


@dataclass(frozen=True, slots=True)
class DecideResearchApproval:
    research_run_id: UUID
    approval_request_id: UUID
    checkpoint_revision: int
    outcome: ResearchApprovalOutcome


@dataclass(frozen=True, slots=True)
class ResumeResearch:
    research_run_id: UUID
    approval_request_id: UUID
    checkpoint_revision: int
    resume_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResearchResumeReceipt:
    run_id: UUID
    job_id: UUID
    created: bool


class ResearchDurabilityRepository(Protocol):
    async def record_completed_effects(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        effects: tuple[tuple[str, str, str], ...],
        completed_at: datetime,
    ) -> None: ...

    async def create_approval(
        self,
        scope: WorkspaceScope,
        *,
        checkpoint: CheckpointEnvelope,
        reason: ResearchApprovalReason,
        approval_request_id: UUID,
        resume_token_hash: bytes,
        requested_at: datetime,
        expires_at: datetime,
    ) -> ResearchApprovalRequest: ...

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideResearchApproval,
        *,
        decided_at: datetime,
        decision_id: UUID,
    ) -> ResearchApprovalRequest: ...

    async def resume(
        self,
        scope: WorkspaceScope,
        command: ResumeResearch,
        *,
        resume_token_hash: bytes,
        resumed_at: datetime,
        job_id: UUID,
        outbox_event_id: UUID,
    ) -> ResearchResumeReceipt: ...

    async def timeline(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ResearchDurabilityTimeline: ...


def approval_request_id(run_id: UUID, checkpoint_revision: int) -> UUID:
    require_non_nil_uuid(run_id, field_name="Approval Run ID")
    if isinstance(checkpoint_revision, bool) or checkpoint_revision < 0:
        raise ValueError("Approval Checkpoint revision is invalid")
    return uuid5(_APPROVAL_NAMESPACE, f"{run_id}:{checkpoint_revision}")


@dataclass(frozen=True, slots=True)
class ResumeTokenCodec:
    key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("Resume token key is invalid")

    def issue(self, *, request_id: UUID, run_id: UUID, checkpoint_revision: int) -> str:
        payload = self._payload(request_id, run_id, checkpoint_revision)
        digest = hmac.digest(self.key, payload, "sha256")
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def digest(self, token: str) -> bytes:
        if not token or len(token) > 100 or any(character.isspace() for character in token):
            raise ResearchResumeTokenError
        return hashlib.sha256(_TOKEN_DOMAIN + token.encode("ascii")).digest()

    @staticmethod
    def _payload(request_id: UUID, run_id: UUID, checkpoint_revision: int) -> bytes:
        return _TOKEN_DOMAIN + f"{request_id}:{run_id}:{checkpoint_revision}".encode("ascii")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ResearchDurabilityService:
    repository: ResearchDurabilityRepository
    token_codec: ResumeTokenCodec
    clock: Callable[[], datetime] = utc_now
    id_source: Callable[[], UUID] = uuid4

    async def interrupt(
        self,
        scope: WorkspaceScope,
        *,
        checkpoint: CheckpointEnvelope,
        reason: ResearchApprovalReason,
    ) -> tuple[ResearchApprovalRequest, str]:
        self._authorize(scope)
        if checkpoint.workspace_id != scope.workspace_id:
            raise WorkspaceAccessDeniedError
        now = self._now()
        request_id = approval_request_id(checkpoint.run_id, checkpoint.revision)
        token = self.token_codec.issue(
            request_id=request_id,
            run_id=checkpoint.run_id,
            checkpoint_revision=checkpoint.revision,
        )
        request = await self.repository.create_approval(
            scope,
            checkpoint=checkpoint,
            reason=reason,
            approval_request_id=request_id,
            resume_token_hash=self.token_codec.digest(token),
            requested_at=now,
            expires_at=now + timedelta(seconds=APPROVAL_TTL_SECONDS),
        )
        return request, token

    async def record_completed_effects(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        effects: tuple[tuple[str, str, str], ...],
    ) -> None:
        self._authorize(scope)
        await self.repository.record_completed_effects(
            scope,
            run_id=run_id,
            effects=effects,
            completed_at=self._now(),
        )

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideResearchApproval,
    ) -> ResearchApprovalRequest:
        self._authorize(scope)
        return await self.repository.decide(
            scope,
            command,
            decided_at=self._now(),
            decision_id=self._id(),
        )

    async def resume(
        self,
        scope: WorkspaceScope,
        command: ResumeResearch,
    ) -> ResearchResumeReceipt:
        self._authorize(scope)
        return await self.repository.resume(
            scope,
            command,
            resume_token_hash=self.token_codec.digest(command.resume_token),
            resumed_at=self._now(),
            job_id=self._id(),
            outbox_event_id=self._id(),
        )

    async def timeline(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ResearchDurabilityTimeline:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        return await self.repository.timeline(scope, research_run_id)

    def token_for(self, approval: ResearchApprovalRequest) -> str:
        return self.token_codec.issue(
            request_id=approval.approval_request_id,
            run_id=approval.run_id,
            checkpoint_revision=approval.checkpoint_revision,
        )

    @staticmethod
    def resume_job_definition(run_id: UUID, *, available_at: datetime) -> Mapping[str, object]:
        require_utc(available_at, field_name="Research resume availability")
        return {
            "task_name": RESEARCH_TASK_NAME,
            "queue_name": RESEARCH_QUEUE_NAME,
            "payload": {"schema_version": 1, "agent_run_id": str(run_id)},
        }

    @staticmethod
    def trace_id(value: str) -> TraceId:
        return TraceId(value)

    @staticmethod
    def _authorize(scope: WorkspaceScope) -> None:
        if not scope_allows(scope, WorkspaceAction.RUN_RESEARCH):
            raise WorkspaceAccessDeniedError

    def _now(self) -> datetime:
        value = self.clock()
        require_utc(value, field_name="Research durability clock")
        return value

    def _id(self) -> UUID:
        value = self.id_source()
        require_non_nil_uuid(value, field_name="Research durability ID")
        return value
