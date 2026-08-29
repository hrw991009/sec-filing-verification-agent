"""Application boundaries for the Evidence and Claim ledger."""

from datetime import datetime
from typing import Protocol
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
from industry_platform.modules.research.domain import CreateResearchRun, ResearchRun
from industry_platform.modules.workspaces.domain import WorkspaceScope


class EvidenceRepository(Protocol):
    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
        *,
        authorization: AuthorizationSnapshot,
    ) -> EvidenceNormalizationResult: ...

    async def list_evidence(
        self,
        scope: WorkspaceScope,
        *,
        status: EvidenceStatus | None,
        kind: EvidenceKind | None,
        origin_run_id: UUID | None,
        limit: int,
    ) -> tuple[Evidence, ...]: ...

    async def get_evidence(self, scope: WorkspaceScope, evidence_id: UUID) -> Evidence: ...

    async def is_evidence_available(self, scope: WorkspaceScope, evidence_id: UUID) -> bool: ...

    async def invalidate_evidence(
        self,
        scope: WorkspaceScope,
        command: InvalidateEvidence,
        *,
        invalidated_at: datetime,
    ) -> Evidence: ...

    async def create_research_run(
        self,
        scope: WorkspaceScope,
        command: CreateResearchRun,
        *,
        created_at: datetime,
    ) -> ResearchRun: ...

    async def list_research_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[ResearchRun, ...]: ...

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime,
    ) -> ResearchClaim: ...

    async def list_claims(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        limit: int,
    ) -> tuple[ResearchClaim, ...]: ...

    async def get_claim(self, scope: WorkspaceScope, claim_id: UUID) -> ResearchClaim: ...

    async def get_claim_graph(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ClaimGraph: ...


class EvidenceUseCase(Protocol):
    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
    ) -> EvidenceNormalizationResult: ...

    async def list_evidence(
        self,
        scope: WorkspaceScope,
        *,
        status: EvidenceStatus | None = None,
        kind: EvidenceKind | None = None,
        origin_run_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[Evidence, ...]: ...

    async def get_evidence(self, scope: WorkspaceScope, evidence_id: UUID) -> Evidence: ...

    async def is_evidence_available(self, scope: WorkspaceScope, evidence_id: UUID) -> bool: ...

    async def invalidate_evidence(
        self,
        scope: WorkspaceScope,
        command: InvalidateEvidence,
        *,
        invalidated_at: datetime | None = None,
    ) -> Evidence: ...

    async def create_research_run(
        self,
        scope: WorkspaceScope,
        command: CreateResearchRun,
        *,
        created_at: datetime | None = None,
    ) -> ResearchRun: ...

    async def list_research_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 20,
    ) -> tuple[ResearchRun, ...]: ...

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime | None = None,
    ) -> ResearchClaim: ...

    async def list_claims(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        limit: int = 20,
    ) -> tuple[ResearchClaim, ...]: ...

    async def get_claim(self, scope: WorkspaceScope, claim_id: UUID) -> ResearchClaim: ...

    async def get_claim_graph(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ClaimGraph: ...
