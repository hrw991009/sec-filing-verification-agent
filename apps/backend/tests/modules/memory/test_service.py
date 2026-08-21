"""Application tests for trusted Memory policy and Workspace scope."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.memory.domain import (
    CandidateCreationResult,
    ChangeMemoryStatus,
    CreateMemoryCandidate,
    DeleteMemory,
    Memory,
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryDetail,
    MemoryFeedback,
    MemoryKind,
    MemoryPolicyAssessment,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryRequestRejectedError,
    MemoryResolutionResult,
    MemoryScope,
    MemorySourceMessage,
    MemoryWriteAction,
    RecordMemoryFeedback,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
    UpdateMemory,
)
from industry_platform.modules.memory.service import MemoryApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")
MESSAGE_ID = UUID("44444444-4444-4444-8444-444444444444")
CANDIDATE_ID = UUID("55555555-5555-4555-8555-555555555555")


@dataclass(slots=True)
class RecordingRepository:
    sources: tuple[MemorySourceMessage, ...]
    assessment: MemoryPolicyAssessment | None = None
    request_fingerprint: str | None = None
    calls: list[str] = field(default_factory=list)

    async def load_source_messages(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        message_ids: tuple[UUID, ...],
    ) -> tuple[MemorySourceMessage, ...]:
        del scope, conversation_id, message_ids
        self.calls.append("load")
        return self.sources

    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
        *,
        sources: tuple[MemorySourceMessage, ...],
        suggested_content: str | None,
        assessment: MemoryPolicyAssessment,
        request_fingerprint: str,
    ) -> CandidateCreationResult:
        del scope, command, sources
        self.calls.append("create")
        self.assessment = assessment
        self.request_fingerprint = request_fingerprint
        return CandidateCreationResult(
            candidate=MemoryCandidate(
                candidate_id=CANDIDATE_ID,
                conversation_id=CONVERSATION_ID,
                source_message_ids=(MESSAGE_ID,),
                suggested_content=suggested_content,
                suggested_scope=MemoryScope.USER,
                suggested_expires_at=None,
                confidence=assessment.confidence,
                write_reason="user_selected_conversation_messages",
                policy_decision=assessment.decision,
                policy_reason=assessment.reason,
                status=MemoryCandidateStatus.CANDIDATE,
                revision=1,
                resolved_memory_id=None,
                created_at=NOW,
                updated_at=NOW,
            ),
            created=True,
        )

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None,
        limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        del scope, conversation_id, limit
        self.calls.append("list_candidates")
        return ()

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate:
        del scope, candidate_id
        raise NotImplementedError

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
        *,
        resolution_fingerprint: str,
    ) -> MemoryResolutionResult:
        del scope, command, resolution_fingerprint
        self.calls.append("resolve")
        raise NotImplementedError

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate:
        del scope, command
        raise NotImplementedError

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        query: str | None,
        status: object,
        memory_scope: object,
        kind: object,
        limit: int,
    ) -> tuple[Memory, ...]:
        del scope, query, status, memory_scope, kind, limit
        self.calls.append("list_memories")
        return ()

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail:
        del scope, memory_id
        raise NotImplementedError

    async def update_memory(self, scope: WorkspaceScope, command: UpdateMemory) -> MemoryDetail:
        del scope, command
        raise NotImplementedError

    async def change_status(
        self, scope: WorkspaceScope, command: ChangeMemoryStatus
    ) -> MemoryDetail:
        del scope, command
        raise NotImplementedError

    async def delete_memory(self, scope: WorkspaceScope, command: DeleteMemory) -> bool:
        del scope, command
        raise NotImplementedError

    async def record_feedback(
        self, scope: WorkspaceScope, command: RecordMemoryFeedback
    ) -> MemoryFeedback:
        del scope, command
        raise NotImplementedError


def scope(role: str = "member") -> WorkspaceScope:
    return WorkspaceScope(workspace_id=WORKSPACE_ID, user_id=USER_ID, role=role)  # type: ignore[arg-type]


def create_command() -> CreateMemoryCandidate:
    return CreateMemoryCandidate(
        conversation_id=CONVERSATION_ID,
        message_ids=(MESSAGE_ID,),
        scope=MemoryScope.USER,
        idempotency_key="memory-service-1",
        trace_id=TraceId("memory-service-test"),
    )


@pytest.mark.asyncio
async def test_service_derives_policy_and_fingerprint_from_trusted_scope_and_sources() -> None:
    repository = RecordingRepository(
        sources=(
            MemorySourceMessage(
                message_id=MESSAGE_ID,
                conversation_id=CONVERSATION_ID,
                role="user",
                content_markdown="以后使用中文回答。",
            ),
        )
    )
    service = MemoryApplicationService(repository, clock=lambda: NOW)

    result = await service.create_candidate(scope(), create_command())

    assert result.candidate.suggested_content == "以后使用中文回答。"
    assert repository.assessment == MemoryPolicyAssessment(
        decision=MemoryPolicyDecision.ALLOWED,
        reason=MemoryPolicyReason.USER_AUTHORED,
        confidence=0.95,
    )
    assert repository.request_fingerprint is not None
    assert len(repository.request_fingerprint) == 64
    assert str(USER_ID) not in repository.request_fingerprint
    assert repository.calls == ["load", "create"]


@pytest.mark.asyncio
async def test_viewer_cannot_create_candidate_before_sources_are_read() -> None:
    repository = RecordingRepository(sources=())
    service = MemoryApplicationService(repository, clock=lambda: NOW)

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.create_candidate(scope("viewer"), create_command())

    assert repository.calls == []


@pytest.mark.asyncio
async def test_expired_resolution_is_rejected_before_repository_mutation() -> None:
    repository = RecordingRepository(sources=())
    service = MemoryApplicationService(repository, clock=lambda: NOW)
    command = ResolveMemoryCandidate(
        candidate_id=CANDIDATE_ID,
        expected_candidate_revision=1,
        action=MemoryWriteAction.CREATE,
        content="长期偏好",
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        expires_at=NOW - timedelta(seconds=1),
        target_memory_id=None,
        expected_target_revision=None,
        trace_id=TraceId("memory-service-test"),
    )

    with pytest.raises(MemoryRequestRejectedError):
        await service.resolve_candidate(scope(), command)

    assert repository.calls == []


@pytest.mark.asyncio
async def test_list_bounds_are_checked_before_repository_access() -> None:
    repository = RecordingRepository(sources=())
    service = MemoryApplicationService(repository, clock=lambda: NOW)

    with pytest.raises(ValueError, match="page size"):
        await service.list_candidates(scope(), limit=101)
    with pytest.raises(ValueError, match="page size"):
        await service.list_memories(scope(), limit=0)

    assert repository.calls == []
