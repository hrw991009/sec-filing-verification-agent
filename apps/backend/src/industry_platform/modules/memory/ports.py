"""Dependency boundaries for user-controlled Memory persistence."""

from typing import Protocol
from uuid import UUID

from industry_platform.modules.memory.domain import (
    CandidateCreationResult,
    ChangeMemoryStatus,
    CreateMemoryCandidate,
    DeleteMemory,
    Memory,
    MemoryCandidate,
    MemoryDetail,
    MemoryFeedback,
    MemoryKind,
    MemoryPolicyAssessment,
    MemoryResolutionResult,
    MemoryScope,
    MemorySourceMessage,
    MemoryStatus,
    RecordMemoryFeedback,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
    UpdateMemory,
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
        query: str | None,
        status: MemoryStatus | None,
        memory_scope: MemoryScope | None,
        kind: MemoryKind | None,
        limit: int,
    ) -> tuple[Memory, ...]: ...

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail: ...

    async def update_memory(self, scope: WorkspaceScope, command: UpdateMemory) -> MemoryDetail: ...

    async def change_status(
        self, scope: WorkspaceScope, command: ChangeMemoryStatus
    ) -> MemoryDetail: ...

    async def delete_memory(self, scope: WorkspaceScope, command: DeleteMemory) -> bool: ...

    async def record_feedback(
        self, scope: WorkspaceScope, command: RecordMemoryFeedback
    ) -> MemoryFeedback: ...


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
        query: str | None = None,
        status: MemoryStatus | None = None,
        memory_scope: MemoryScope | None = None,
        kind: MemoryKind | None = None,
        limit: int = 20,
    ) -> tuple[Memory, ...]: ...

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail: ...

    async def update_memory(self, scope: WorkspaceScope, command: UpdateMemory) -> MemoryDetail: ...

    async def change_status(
        self, scope: WorkspaceScope, command: ChangeMemoryStatus
    ) -> MemoryDetail: ...

    async def delete_memory(self, scope: WorkspaceScope, command: DeleteMemory) -> bool: ...

    async def record_feedback(
        self, scope: WorkspaceScope, command: RecordMemoryFeedback
    ) -> MemoryFeedback: ...
