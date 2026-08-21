"""HTTP contract tests for Memory candidates and decisions."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.memory.domain import (
    CandidateCreationResult,
    CreateMemoryCandidate,
    Memory,
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryConflictError,
    MemoryDetail,
    MemoryKind,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryResolutionResult,
    MemoryRevision,
    MemoryRevisionValidity,
    MemoryScope,
    MemoryStatus,
    MemoryWriteAction,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
)
from industry_platform.modules.memory.router import get_memory_service
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
SESSION_ID = UUID("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID = UUID("55555555-5555-4555-8555-555555555555")
MESSAGE_ID = UUID("66666666-6666-4666-8666-666666666666")
CANDIDATE_ID = UUID("77777777-7777-4777-8777-777777777777")
MEMORY_ID = UUID("88888888-8888-4888-8888-888888888888")
REVISION_ID = UUID("99999999-9999-4999-8999-999999999999")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubMemoryService:
    failure: Exception | None = None
    create_calls: list[tuple[WorkspaceScope, CreateMemoryCandidate]] = field(default_factory=list)
    resolve_calls: list[tuple[WorkspaceScope, ResolveMemoryCandidate]] = field(default_factory=list)
    reject_calls: list[tuple[WorkspaceScope, RejectMemoryCandidate]] = field(default_factory=list)

    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
    ) -> CandidateCreationResult:
        self.create_calls.append((scope, command))
        self._fail()
        return CandidateCreationResult(candidate=candidate(), created=True)

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[MemoryCandidate, ...]:
        del scope, conversation_id, limit
        self._fail()
        return (candidate(),)

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate:
        del scope, candidate_id
        self._fail()
        return candidate()

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
    ) -> MemoryResolutionResult:
        self.resolve_calls.append((scope, command))
        self._fail()
        return MemoryResolutionResult(detail=memory_detail(), action=command.action, created=True)

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate:
        self.reject_calls.append((scope, command))
        self._fail()
        return candidate(status=MemoryCandidateStatus.REJECTED, revision=2)

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 20,
    ) -> tuple[Memory, ...]:
        del scope, limit
        self._fail()
        return (memory_detail().memory,)

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail:
        del scope, memory_id
        self._fail()
        return memory_detail()

    def _fail(self) -> None:
        if self.failure is not None:
            raise self.failure


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def candidate(
    *,
    status: MemoryCandidateStatus = MemoryCandidateStatus.CANDIDATE,
    revision: int = 1,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=CANDIDATE_ID,
        conversation_id=CONVERSATION_ID,
        source_message_ids=(MESSAGE_ID,),
        suggested_content="以后使用中文回答。",
        suggested_scope=MemoryScope.USER,
        suggested_expires_at=None,
        confidence=0.95,
        write_reason="user_selected_conversation_messages",
        policy_decision=MemoryPolicyDecision.ALLOWED,
        policy_reason=MemoryPolicyReason.USER_AUTHORED,
        status=status,
        revision=revision,
        resolved_memory_id=MEMORY_ID if status is MemoryCandidateStatus.CONFIRMED else None,
        created_at=NOW,
        updated_at=NOW,
    )


def memory_detail() -> MemoryDetail:
    revision = MemoryRevision(
        revision_id=REVISION_ID,
        version=1,
        content="以后使用中文回答。",
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        write_action=MemoryWriteAction.CREATE,
        write_reason="user_selected_conversation_messages",
        policy_decision=MemoryPolicyDecision.ALLOWED,
        editor_user_id=USER_ID,
        source_message_ids=(MESSAGE_ID,),
        validity=MemoryRevisionValidity.VALID,
        expires_at=None,
        created_at=NOW,
    )
    memory = Memory(
        memory_id=MEMORY_ID,
        owner_user_id=USER_ID,
        source_conversation_id=CONVERSATION_ID,
        scope=MemoryScope.USER,
        kind=MemoryKind.PREFERENCE,
        confidence=0.95,
        status=MemoryStatus.CONFIRMED,
        current_revision_id=REVISION_ID,
        current_version=1,
        revision=1,
        expires_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    return MemoryDetail(memory=memory, current_revision=revision, revisions=(revision,))


@contextmanager
def memory_client(
    settings: Settings,
    service: StubMemoryService,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_memory_service] = lambda: service
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def test_candidate_create_confirm_and_reject_use_trusted_scope_and_revision_headers(
    test_settings: Settings,
) -> None:
    service = StubMemoryService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/memories"
    with memory_client(test_settings, service) as client:
        created = client.post(
            f"{root}/candidates",
            headers={**bearer_header(), "Idempotency-Key": "memory-browser-1"},
            json={
                "conversation_id": str(CONVERSATION_ID),
                "message_ids": [str(MESSAGE_ID)],
                "scope": "user",
            },
        )
        confirmed = client.post(
            f"{root}/candidates/{CANDIDATE_ID}/confirm",
            headers={**bearer_header(), "If-Match": '"1"'},
            json={
                "action": "create",
                "content": "以后使用中文回答。",
                "kind": "preference",
                "scope": "user",
            },
        )
        rejected = client.post(
            f"{root}/candidates/{CANDIDATE_ID}/reject",
            headers={**bearer_header(), "If-Match": "1"},
        )

    assert created.status_code == 201
    assert created.headers["etag"] == '"1"'
    assert created.json()["created"] is True
    assert created.json()["suggested_content"] == "以后使用中文回答。"
    assert confirmed.status_code == 200
    assert confirmed.headers["etag"] == '"1"'
    assert confirmed.json()["memory"]["current_revision"]["content"] == "以后使用中文回答。"
    assert rejected.status_code == 200
    assert rejected.headers["etag"] == '"2"'
    expected_scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    create_scope, create_command = service.create_calls[0]
    resolve_scope, resolve_command = service.resolve_calls[0]
    reject_scope, reject_command = service.reject_calls[0]
    assert create_scope == resolve_scope == reject_scope == expected_scope
    assert create_command.message_ids == (MESSAGE_ID,)
    assert create_command.idempotency_key == "memory-browser-1"
    assert resolve_command.expected_candidate_revision == 1
    assert resolve_command.target_memory_id is None
    assert reject_command.expected_candidate_revision == 1


def test_routes_reject_untrusted_workspace_invalid_shape_and_stale_revision(
    test_settings: Settings,
) -> None:
    service = StubMemoryService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/memories"
    with memory_client(test_settings, service) as client:
        unauthenticated = client.get(root)
        outside_scope = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/memories",
            headers=bearer_header(),
        )
        duplicate_sources = client.post(
            f"{root}/candidates",
            headers={**bearer_header(), "Idempotency-Key": "memory-browser-2"},
            json={
                "conversation_id": str(CONVERSATION_ID),
                "message_ids": [str(MESSAGE_ID), str(MESSAGE_ID)],
            },
        )
        oversized_content = client.post(
            f"{root}/candidates/{CANDIDATE_ID}/confirm",
            headers={**bearer_header(), "If-Match": "1"},
            json={"action": "create", "content": "x" * 4001},
        )
        invalid_target = client.post(
            f"{root}/candidates/{CANDIDATE_ID}/confirm",
            headers={**bearer_header(), "If-Match": "1"},
            json={
                "action": "update",
                "content": "new content",
                "target_memory_id": str(MEMORY_ID),
            },
        )
        invalid_if_match = client.post(
            f"{root}/candidates/{CANDIDATE_ID}/reject",
            headers={**bearer_header(), "If-Match": "stale"},
        )

    assert unauthenticated.status_code == 401
    assert outside_scope.status_code == 403
    assert outside_scope.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert duplicate_sources.status_code == 422
    assert oversized_content.status_code == 422
    assert invalid_target.status_code == 422
    assert invalid_if_match.status_code == 422
    assert invalid_if_match.json()["code"] == "MEMORY_REQUEST_REJECTED"
    assert service.create_calls == []
    assert service.resolve_calls == []
    assert service.reject_calls == []


def test_conflicts_use_stable_problem_contract(test_settings: Settings) -> None:
    service = StubMemoryService(failure=MemoryConflictError())
    with memory_client(test_settings, service) as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/memories/candidates/{CANDIDATE_ID}/reject",
            headers={**bearer_header(), "If-Match": "1"},
        )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "MEMORY_CONFLICT"
