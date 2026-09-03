"""Workspace-authorized application service for Evidence and Claims."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimGraph,
    CreateClaim,
    Evidence,
    EvidenceKind,
    EvidenceNormalizationResult,
    EvidenceStatus,
    InvalidateEvidence,
    NormalizeObservation,
    ResearchClaim,
)
from industry_platform.modules.evidence.ports import EvidenceRepository
from industry_platform.modules.research.domain import CreateResearchRun, ResearchRun
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

MAX_LEDGER_LIST_SIZE = 100


class EvidenceApplicationService:
    def __init__(
        self,
        repository: EvidenceRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
    ) -> EvidenceNormalizationResult:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        snapshot = AuthorizationSnapshot(
            workspace_id=scope.workspace_id,
            actor_user_id=scope.user_id,
            role=scope.role,
            action="evidence.normalize",
            captured_at=self._clock(),
        )
        return await self._repository.normalize_observation(
            scope,
            command,
            authorization=snapshot,
        )

    async def list_evidence(
        self,
        scope: WorkspaceScope,
        *,
        status: EvidenceStatus | None = None,
        kind: EvidenceKind | None = None,
        origin_run_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[Evidence, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        self._require_limit(limit)
        return await self._repository.list_evidence(
            scope,
            status=status,
            kind=kind,
            origin_run_id=origin_run_id,
            limit=limit,
        )

    async def get_evidence(self, scope: WorkspaceScope, evidence_id: UUID) -> Evidence:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.get_evidence(scope, evidence_id)

    async def is_evidence_available(self, scope: WorkspaceScope, evidence_id: UUID) -> bool:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.is_evidence_available(scope, evidence_id)

    async def resolve_evidence(
        self, scope: WorkspaceScope, evidence_id: UUID
    ) -> tuple[Evidence, bool]:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.resolve_evidence(scope, evidence_id)

    async def invalidate_evidence(
        self,
        scope: WorkspaceScope,
        command: InvalidateEvidence,
        *,
        invalidated_at: datetime | None = None,
    ) -> Evidence:
        self._require(scope, WorkspaceAction.UPDATE_RESOURCE)
        return await self._repository.invalidate_evidence(
            scope,
            command,
            invalidated_at=invalidated_at or self._clock(),
        )

    async def create_research_run(
        self,
        scope: WorkspaceScope,
        command: CreateResearchRun,
        *,
        created_at: datetime | None = None,
    ) -> ResearchRun:
        self._require(scope, WorkspaceAction.RUN_RESEARCH)
        return await self._repository.create_research_run(
            scope,
            command,
            created_at=created_at or self._clock(),
        )

    async def list_research_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 20,
    ) -> tuple[ResearchRun, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        self._require_limit(limit)
        return await self._repository.list_research_runs(scope, limit=limit)

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime | None = None,
    ) -> ResearchClaim:
        self._require(scope, WorkspaceAction.RUN_RESEARCH)
        return await self._repository.create_claim(
            scope,
            command,
            created_at=created_at or self._clock(),
        )

    async def list_claims(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        limit: int = 20,
    ) -> tuple[ResearchClaim, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        self._require_limit(limit)
        return await self._repository.list_claims(scope, research_run_id, limit=limit)

    async def get_claim(self, scope: WorkspaceScope, claim_id: UUID) -> ResearchClaim:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.get_claim(scope, claim_id)

    async def get_claim_graph(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ClaimGraph:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.get_claim_graph(scope, research_run_id)

    @staticmethod
    def _require(scope: WorkspaceScope, action: WorkspaceAction) -> None:
        if not scope_allows(scope, action):
            raise WorkspaceAccessDeniedError

    @staticmethod
    def _require_limit(limit: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_LEDGER_LIST_SIZE:
            raise ValueError("Evidence ledger page size is invalid")
