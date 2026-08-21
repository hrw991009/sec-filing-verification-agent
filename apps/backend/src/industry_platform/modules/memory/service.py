"""Application service for candidate-first, user-controlled Memory writes."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from industry_platform.modules.memory.domain import (
    MAX_MEMORY_LIST_SIZE,
    CandidateCreationResult,
    CreateMemoryCandidate,
    Memory,
    MemoryCandidate,
    MemoryDetail,
    MemoryRequestRejectedError,
    MemoryResolutionResult,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
    assess_memory_candidate,
    build_candidate_content,
    canonical_fingerprint,
    require_memory_content,
    utc_now,
)
from industry_platform.modules.memory.ports import MemoryRepository
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


class MemoryApplicationService:
    """Enforce trusted scope, policy, and CAS before persistence."""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
    ) -> CandidateCreationResult:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        sources = await self._repository.load_source_messages(
            scope,
            conversation_id=command.conversation_id,
            message_ids=command.message_ids,
        )
        try:
            suggested_content = build_candidate_content(sources)
        except ValueError as error:
            raise MemoryRequestRejectedError from error
        assessment = assess_memory_candidate(sources, suggested_content)
        request_fingerprint = canonical_fingerprint(
            {
                "conversation_id": str(command.conversation_id),
                "message_ids": [str(message_id) for message_id in command.message_ids],
                "scope": command.scope.value,
                "user_id": str(scope.user_id),
                "workspace_id": str(scope.workspace_id),
            }
        )
        return await self._repository.create_candidate(
            scope,
            command,
            sources=sources,
            suggested_content=suggested_content,
            assessment=assessment,
            request_fingerprint=request_fingerprint,
        )

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[MemoryCandidate, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        self._require_limit(limit)
        return await self._repository.list_candidates(
            scope,
            conversation_id=conversation_id,
            limit=limit,
        )

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.get_candidate(scope, candidate_id)

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
    ) -> MemoryResolutionResult:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        if command.expires_at is not None and command.expires_at <= self._clock():
            raise MemoryRequestRejectedError
        content = require_memory_content(command.content)
        resolution_fingerprint = canonical_fingerprint(
            {
                "action": command.action.value,
                "candidate_id": str(command.candidate_id),
                "content": content,
                "expected_candidate_revision": command.expected_candidate_revision,
                "expected_target_revision": command.expected_target_revision,
                "expires_at": (
                    command.expires_at.isoformat() if command.expires_at is not None else None
                ),
                "kind": command.kind.value,
                "scope": command.scope.value,
                "target_memory_id": (
                    str(command.target_memory_id) if command.target_memory_id is not None else None
                ),
                "user_id": str(scope.user_id),
                "workspace_id": str(scope.workspace_id),
            }
        )
        return await self._repository.resolve_candidate(
            scope,
            command,
            resolution_fingerprint=resolution_fingerprint,
        )

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        return await self._repository.reject_candidate(scope, command)

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 20,
    ) -> tuple[Memory, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        self._require_limit(limit)
        return await self._repository.list_memories(scope, limit=limit)

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail:
        self._require(scope, WorkspaceAction.VIEW)
        return await self._repository.get_memory(scope, memory_id)

    @staticmethod
    def _require(scope: WorkspaceScope, action: WorkspaceAction) -> None:
        if not scope_allows(scope, action):
            raise WorkspaceAccessDeniedError

    @staticmethod
    def _require_limit(limit: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_MEMORY_LIST_SIZE:
            raise ValueError("Memory page size is invalid")
