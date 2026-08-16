"""Application tests for bounded, Workspace-scoped conversation management."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.conversations.management import (
    ConversationDetail,
    ConversationManagementService,
    ConversationPage,
    ConversationSummary,
    MessagePage,
    RenameConversation,
)
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")


class RecordingRepository:
    def __init__(self) -> None:
        self.page_sizes: list[int] = []
        self.mutations: list[tuple[str, datetime]] = []

    async def list_conversations(self, *, scope, page_size, cursor):  # type: ignore[no-untyped-def]
        del scope, cursor
        self.page_sizes.append(page_size)
        return ConversationPage(items=(), next_cursor=None)

    async def get_conversation(self, *, scope, conversation_id):  # type: ignore[no-untyped-def]
        del scope
        return ConversationDetail(summary=summary(conversation_id), turn_count=0)

    async def list_messages(  # type: ignore[no-untyped-def]
        self, *, scope, conversation_id, page_size, cursor
    ):
        del scope, conversation_id, cursor
        self.page_sizes.append(page_size)
        return MessagePage(items=(), next_cursor=None)

    async def rename(self, *, scope, command, updated_at):  # type: ignore[no-untyped-def]
        del scope
        self.mutations.append((command.title, updated_at))
        return summary(command.conversation_id, title=command.title)

    async def delete(self, *, scope, conversation_id, deleted_at):  # type: ignore[no-untyped-def]
        del scope, conversation_id
        self.mutations.append(("deleted", deleted_at))
        return True


def summary(
    conversation_id: UUID = CONVERSATION_ID, *, title: str = "Quarterly risks"
) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=conversation_id,
        title=title,
        created_at=NOW,
        updated_at=NOW,
    )


def scope() -> WorkspaceScope:
    return WorkspaceScope(workspace_id=WORKSPACE_ID, user_id=USER_ID, role="member")


@pytest.mark.asyncio
async def test_service_rejects_unbounded_pages_before_calling_repository() -> None:
    repository = RecordingRepository()
    service = ConversationManagementService(repository=repository, clock=lambda: NOW)

    with pytest.raises(ValueError, match="page size"):
        await service.list_conversations(scope(), page_size=101)
    with pytest.raises(ValueError, match="page size"):
        await service.list_messages(scope(), CONVERSATION_ID, page_size=0)

    assert repository.page_sizes == []


@pytest.mark.asyncio
async def test_rename_and_delete_use_one_validated_utc_time() -> None:
    repository = RecordingRepository()
    service = ConversationManagementService(repository=repository, clock=lambda: NOW)

    renamed = await service.rename(
        scope(), RenameConversation(conversation_id=CONVERSATION_ID, title="New title")
    )
    deleted = await service.delete(scope(), CONVERSATION_ID)

    assert renamed.title == "New title"
    assert deleted is True
    assert repository.mutations == [("New title", NOW), ("deleted", NOW)]


@pytest.mark.asyncio
async def test_viewers_can_read_but_cannot_mutate_conversations() -> None:
    repository = RecordingRepository()
    service = ConversationManagementService(repository=repository, clock=lambda: NOW)
    viewer = WorkspaceScope(workspace_id=WORKSPACE_ID, user_id=USER_ID, role="viewer")

    page = await service.list_conversations(viewer)
    assert page.items == ()
    with pytest.raises(WorkspaceAccessDeniedError):
        await service.rename(
            viewer,
            RenameConversation(conversation_id=CONVERSATION_ID, title="Forbidden rename"),
        )
    with pytest.raises(WorkspaceAccessDeniedError):
        await service.delete(viewer, CONVERSATION_ID)

    assert repository.mutations == []


def test_titles_are_one_line_and_summary_repr_does_not_contain_message_content() -> None:
    with pytest.raises(ValueError, match="title"):
        RenameConversation(conversation_id=CONVERSATION_ID, title="line one\nline two")

    assert "message content" not in repr(summary())
