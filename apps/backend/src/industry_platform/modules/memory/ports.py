"""Dependency boundaries for user-controlled Memory persistence."""

from typing import Protocol
from uuid import UUID

from industry_platform.modules.memory.domain import (
    CandidateCreationResult,
    CreateMemoryCandidate,
    Memory,
    MemoryCandidate,
    MemoryDetail,
    MemoryPolicyAssessment,
    MemoryResolutionResult,
    MemorySourceMessage,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class MemoryRepository(Protocol):
    async def load_source_messages(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        message_ids: tuple[UUID, ...],
    ) -> tuple[MemorySourceMessage, ...]: ...

    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
        *,
        sources: tuple[MemorySourceMessage, ...],
        suggested_content: str | None,
        assessment: MemoryPolicyAssessment,
        request_fingerprint: str,
    ) -> CandidateCreationResult: ...

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None,
        limit: int,
    ) -> tuple[MemoryCandidate, ...]: ...

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate: ...

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
        *,
        resolution_fingerprint: str,
    ) -> MemoryResolutionResult: ...

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate: ...

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[Memory, ...]: ...

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail: ...


class MemoryUseCase(Protocol):
    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
    ) -> CandidateCreationResult: ...

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[MemoryCandidate, ...]: ...

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate: ...

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
    ) -> MemoryResolutionResult: ...

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate: ...

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 20,
    ) -> tuple[Memory, ...]: ...

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail: ...
