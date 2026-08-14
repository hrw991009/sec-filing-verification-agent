"""HTTP contract tests for Workspace-owned conversation management."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.conversations.management import (
    ConversationCursor,
    ConversationDetail,
    ConversationManagementUseCase,
    ConversationMessage,
    ConversationPage,
    ConversationSummary,
    MessageCursor,
    MessagePage,
    RenameConversation,
)
from industry_platform.modules.conversations.router import (
    get_conversation_management_service,
)
from industry_platform.modules.conversations.schemas import encode_message_cursor
from industry_platform.modules.conversations.service import (
    ConversationNotFoundError,
    ConversationPersistenceError,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID = UUID("55555555-5555-4555-8555-555555555555")
MESSAGE_ID = UUID("66666666-6666-4666-8666-666666666666")
TURN_ID = UUID("77777777-7777-4777-8777-777777777777")
RUN_ID = UUID("88888888-8888-4888-8888-888888888888")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubConversationService:
    failure: Exception | None = None
    calls: list[tuple[str, WorkspaceScope, object | None]] = field(default_factory=list)

    async def list_conversations(
        self,
        scope: WorkspaceScope,
        *,
        page_size: int = 20,
        cursor: ConversationCursor | None = None,
    ) -> ConversationPage:
        self._record("list", scope, (page_size, cursor))
        return ConversationPage(
            items=(summary(),),
            next_cursor=ConversationCursor(NOW, CONVERSATION_ID),
        )

    async def get_conversation(
        self, scope: WorkspaceScope, conversation_id: UUID
    ) -> ConversationDetail:
        self._record("get", scope, conversation_id)
        return ConversationDetail(summary=summary(), turn_count=2)

    async def list_messages(
        self,
        scope: WorkspaceScope,
        conversation_id: UUID,
        *,
        page_size: int = 20,
        cursor: MessageCursor | None = None,
    ) -> MessagePage:
        self._record("messages", scope, (conversation_id, page_size, cursor))
        return MessagePage(
            items=(message(),),
            next_cursor=MessageCursor(NOW, MESSAGE_ID),
        )

    async def rename(
        self, scope: WorkspaceScope, command: RenameConversation
    ) -> ConversationSummary:
        self._record("rename", scope, command)
        return summary(title=command.title)

    async def delete(self, scope: WorkspaceScope, conversation_id: UUID) -> bool:
        self._record("delete", scope, conversation_id)
        return True

    def _record(self, name: str, scope: WorkspaceScope, value: object | None) -> None:
        self.calls.append((name, scope, value))
        if self.failure is not None:
            raise self.failure


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def summary(*, title: str = "Quarterly risks") -> ConversationSummary:
    return ConversationSummary(
        conversation_id=CONVERSATION_ID,
        title=title,
        created_at=NOW - timedelta(minutes=5),
        updated_at=NOW,
    )


def message() -> ConversationMessage:
    return ConversationMessage(
        message_id=MESSAGE_ID,
        turn_id=TURN_ID,
        agent_run_id=RUN_ID,
        role="assistant",
        status="final",
        content_markdown="A durable **answer**.",
        created_at=NOW,
    )


@contextmanager
def conversation_client(
    settings: Settings,
    *,
    service: ConversationManagementUseCase | None = None,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_conversation_management_service] = lambda: (
        service if service is not None else StubConversationService()
    )
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def assert_problem(response: HttpxResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == code


def test_routes_round_trip_opaque_cursors_and_trusted_workspace_scope(
    test_settings: Settings,
) -> None:
    service = StubConversationService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/conversations"
    with conversation_client(test_settings, service=service) as client:
        first_page = client.get(root, headers=bearer_header(), params={"limit": 1})
        cursor = first_page.json()["next_cursor"]
        second_page = client.get(root, headers=bearer_header(), params={"cursor": cursor})
        detail = client.get(f"{root}/{CONVERSATION_ID}", headers=bearer_header())
        messages = client.get(
            f"{root}/{CONVERSATION_ID}/messages",
            headers=bearer_header(),
            params={"limit": 1},
        )
        message_cursor = messages.json()["next_cursor"]
        client.get(
            f"{root}/{CONVERSATION_ID}/messages",
            headers=bearer_header(),
            params={"cursor": message_cursor},
        )
        renamed = client.patch(
            f"{root}/{CONVERSATION_ID}",
            headers=bearer_header(),
            json={"title": "Renamed conversation"},
        )
        deleted = client.delete(f"{root}/{CONVERSATION_ID}", headers=bearer_header())

    assert first_page.status_code == 200
    assert first_page.json()["conversations"][0]["title"] == "Quarterly risks"
    assert detail.json()["turn_count"] == 2
    assert messages.json()["messages"][0]["content_markdown"] == "A durable **answer**."
    assert renamed.json()["title"] == "Renamed conversation"
    assert deleted.status_code == 204
    assert all(call[1] == WorkspaceScope(WORKSPACE_ID, USER_ID, "member") for call in service.calls)
    assert service.calls[1][2] == (20, ConversationCursor(NOW, CONVERSATION_ID))
    assert service.calls[4][2] == (
        CONVERSATION_ID,
        20,
        MessageCursor(NOW, MESSAGE_ID),
    )
    for response in (first_page, second_page, detail, messages, renamed, deleted):
        assert response.headers["cache-control"] == "no-store"


def test_routes_reject_untrusted_scope_cursor_and_payload(
    test_settings: Settings,
) -> None:
    service = StubConversationService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/conversations"
    with conversation_client(test_settings, service=service) as client:
        unauthenticated = client.get(root)
        outside_scope = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/conversations",
            headers=bearer_header(),
        )
        invalid_cursor = client.get(root, headers=bearer_header(), params={"cursor": "%%%"})
        wrong_cursor_kind = client.get(
            root,
            headers=bearer_header(),
            params={"cursor": encode_message_cursor(MessageCursor(NOW, MESSAGE_ID))},
        )
        invalid_title = client.patch(
            f"{root}/{CONVERSATION_ID}",
            headers=bearer_header(),
            json={"title": " line one\nline two "},
        )

    assert_problem(unauthenticated, 401, "INVALID_AUTHENTICATED_SESSION")
    assert_problem(outside_scope, 403, "WORKSPACE_ACCESS_DENIED")
    assert_problem(invalid_cursor, 400, "INVALID_CONVERSATION_CURSOR")
    assert_problem(wrong_cursor_kind, 400, "INVALID_CONVERSATION_CURSOR")
    assert_problem(invalid_title, 422, "REQUEST_VALIDATION_FAILED")
    assert service.calls == []


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (ConversationNotFoundError(), 404, "CONVERSATION_NOT_FOUND"),
        (
            ConversationPersistenceError(sqlstate="40001"),
            503,
            "CONVERSATION_UNAVAILABLE",
        ),
    ],
)
def test_service_failures_use_safe_problem_contracts(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubConversationService(failure=failure)
    with conversation_client(test_settings, service=service) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/conversations/{CONVERSATION_ID}",
            headers=bearer_header(),
        )

    assert_problem(response, status_code, code)
    assert "40001" not in response.text
