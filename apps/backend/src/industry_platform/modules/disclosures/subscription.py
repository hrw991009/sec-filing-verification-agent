"""Durable SEC Monitor subscription approval and read-model contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.disclosures.monitor import SecMonitorRule, SecMonitorStatus
from industry_platform.modules.research.domain import ResearchApprovalOutcome
from industry_platform.modules.research.durability import ResearchApprovalRequest
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


class SecMonitorSubscriptionError(RuntimeError):
    code = "monitor_subscription_error"


class SecMonitorNotFoundError(SecMonitorSubscriptionError):
    code = "monitor_not_found"


class SecMonitorRevisionConflictError(SecMonitorSubscriptionError):
    code = "monitor_revision_conflict"


@dataclass(frozen=True, slots=True)
class DecideSecMonitorSubscription:
    research_run_id: UUID
    approval_request_id: UUID
    checkpoint_revision: int
    outcome: ResearchApprovalOutcome


@dataclass(frozen=True, slots=True)
class ChangeSecMonitorStatus:
    monitor_id: UUID
    expected_revision: int
    status: SecMonitorStatus


@dataclass(frozen=True, slots=True)
class SecMonitorView:
    monitor_id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    cik: str
    canonical_name: str
    knowledge_base_id: UUID
    schedule_id: UUID
    cron_expression: str
    timezone_name: str
    allowed_forms: tuple[str, ...]
    rules: tuple[SecMonitorRule, ...]
    status: SecMonitorStatus
    revision: int
    watermark_revision: int
    watermark_coverage_version: str
    watermark_accepted_at: datetime | None
    watermark_accession: str | None
    created_from_approval_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SecCaseEvidenceLink:
    side: str
    evidence_id: UUID


@dataclass(frozen=True, slots=True)
class SecDisclosureCaseView:
    case_id: UUID
    monitor_id: UUID
    monitor_run_id: UUID
    rule_id: UUID
    trigger_kind: str
    source_coverage_version: str
    baseline_accession: str
    target_accession: str
    diff_version: str
    diff_payload: dict[str, object]
    diff_sha256: str
    verification_status: str
    notification_status: str
    evidence: tuple[SecCaseEvidenceLink, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SecMonitorSubscriptionDecisionResult:
    approval: ResearchApprovalRequest
    monitor: SecMonitorView | None
    resume_job_id: UUID | None
    created: bool


class SecMonitorSubscriptionRepository(Protocol):
    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideSecMonitorSubscription,
        *,
        decided_at: datetime,
        decision_id: UUID,
        resume_job_id: UUID,
        resume_outbox_event_id: UUID,
    ) -> SecMonitorSubscriptionDecisionResult: ...

    async def list_monitors(self, scope: WorkspaceScope) -> tuple[SecMonitorView, ...]: ...

    async def get_monitor(self, scope: WorkspaceScope, monitor_id: UUID) -> SecMonitorView: ...

    async def change_status(
        self,
        scope: WorkspaceScope,
        command: ChangeSecMonitorStatus,
        *,
        changed_at: datetime,
    ) -> SecMonitorView: ...

    async def list_cases(
        self,
        scope: WorkspaceScope,
        *,
        monitor_id: UUID | None,
    ) -> tuple[SecDisclosureCaseView, ...]: ...

    async def get_case(self, scope: WorkspaceScope, case_id: UUID) -> SecDisclosureCaseView: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SecMonitorSubscriptionService:
    repository: SecMonitorSubscriptionRepository
    clock: Callable[[], datetime] = utc_now
    id_source: Callable[[], UUID] = uuid4

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideSecMonitorSubscription,
    ) -> SecMonitorSubscriptionDecisionResult:
        self._require_mutation(scope)
        now = self._now()
        return await self.repository.decide(
            scope,
            command,
            decided_at=now,
            decision_id=self._id(),
            resume_job_id=self._id(),
            resume_outbox_event_id=self._id(),
        )

    async def list_monitors(self, scope: WorkspaceScope) -> tuple[SecMonitorView, ...]:
        self._require_view(scope)
        return await self.repository.list_monitors(scope)

    async def get_monitor(self, scope: WorkspaceScope, monitor_id: UUID) -> SecMonitorView:
        self._require_view(scope)
        return await self.repository.get_monitor(scope, monitor_id)

    async def change_status(
        self,
        scope: WorkspaceScope,
        command: ChangeSecMonitorStatus,
    ) -> SecMonitorView:
        self._require_mutation(scope)
        return await self.repository.change_status(scope, command, changed_at=self._now())

    async def list_cases(
        self,
        scope: WorkspaceScope,
        *,
        monitor_id: UUID | None = None,
    ) -> tuple[SecDisclosureCaseView, ...]:
        self._require_view(scope)
        return await self.repository.list_cases(scope, monitor_id=monitor_id)

    async def get_case(self, scope: WorkspaceScope, case_id: UUID) -> SecDisclosureCaseView:
        self._require_view(scope)
        return await self.repository.get_case(scope, case_id)

    @staticmethod
    def _require_view(scope: WorkspaceScope) -> None:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError

    @staticmethod
    def _require_mutation(scope: WorkspaceScope) -> None:
        if not scope_allows(scope, WorkspaceAction.RUN_RESEARCH):
            raise WorkspaceAccessDeniedError

    def _now(self) -> datetime:
        value = self.clock()
        require_utc(value, field_name="SEC Monitor subscription clock")
        return value

    def _id(self) -> UUID:
        value = self.id_source()
        require_non_nil_uuid(value, field_name="SEC Monitor subscription ID")
        return value
