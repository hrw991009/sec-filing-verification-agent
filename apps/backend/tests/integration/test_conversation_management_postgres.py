"""PostgreSQL proof for conversation pagination, messages, rename, and soft deletion."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.conversations.adapters.management import (
    SqlAlchemyConversationManagementRepository,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.management import (
    ConversationManagementService,
    RenameConversation,
)
from industry_platform.modules.conversations.models import Message, Turn
from industry_platform.modules.conversations.service import (
    ConversationApplicationService,
    ConversationNotFoundError,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("99999999-9999-4999-8999-999999999999")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_management_is_paginated_workspace_scoped_and_soft_deletes(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await _seed_workspace(session_factory)
            times = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))
            writer = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: next(times),
            )
            first = await writer.start_direct_answer(
                _command(
                    key="management-1",
                    question="Explain the first risk.",
                    title=None,
                )
            )
            await writer.start_direct_answer(
                _command(
                    key="management-2",
                    question="Explain the second risk.",
                    conversation_id=first.conversation_id,
                )
            )
            second = await writer.start_direct_answer(
                _command(
                    key="management-3",
                    question="Another conversation.",
                    title="Explicit title",
                )
            )

            service = ConversationManagementService(
                repository=SqlAlchemyConversationManagementRepository(session_factory),
                clock=lambda: NOW + timedelta(minutes=1),
            )
            trusted_scope = _scope(WORKSPACE_ID)
            first_page = await service.list_conversations(trusted_scope, page_size=1)
            assert first_page.next_cursor is not None
            second_page = await service.list_conversations(
                trusted_scope,
                page_size=1,
                cursor=first_page.next_cursor,
            )
            assert {first_page.items[0].conversation_id, second_page.items[0].conversation_id} == {
                first.conversation_id,
                second.conversation_id,
            }

            detail = await service.get_conversation(trusted_scope, first.conversation_id)
            assert detail.summary.title == "Explain the first risk."
            assert detail.turn_count == 2

            messages_one = await service.list_messages(
                trusted_scope, first.conversation_id, page_size=1
            )
            assert messages_one.next_cursor is not None
            messages_two = await service.list_messages(
                trusted_scope,
                first.conversation_id,
                page_size=1,
                cursor=messages_one.next_cursor,
            )
            assert tuple(
                message.content_markdown for message in (*messages_one.items, *messages_two.items)
            ) == ("Explain the first risk.", "Explain the second risk.")
            assert "Explain the first risk." not in repr(messages_one)

            renamed = await service.rename(
                trusted_scope,
                RenameConversation(
                    conversation_id=first.conversation_id,
                    title="Renamed risk review",
                ),
            )
            assert renamed.title == "Renamed risk review"
            assert await service.delete(trusted_scope, first.conversation_id) is True
            assert await service.delete(trusted_scope, first.conversation_id) is False
            with pytest.raises(ConversationNotFoundError):
                await service.get_conversation(trusted_scope, first.conversation_id)
            with pytest.raises(ConversationNotFoundError):
                await service.get_conversation(_scope(OTHER_WORKSPACE_ID), second.conversation_id)

            remaining = await service.list_conversations(trusted_scope)
            assert tuple(item.conversation_id for item in remaining.items) == (
                second.conversation_id,
            )
            async with session_factory() as session:
                retained_messages = await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .join(
                        Turn,
                        (Turn.id == Message.turn_id) & (Turn.workspace_id == Message.workspace_id),
                    )
                    .where(
                        Turn.conversation_id == first.conversation_id,
                        Turn.workspace_id == WORKSPACE_ID,
                    )
                )
            assert retained_messages == 2
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


async def _seed_workspace(session_factory: object) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        raise TypeError("Expected an async SQLAlchemy session factory")
    async with session_factory.begin() as session:
        assert isinstance(session, AsyncSession)
        session.add(
            User(
                id=USER_ID,
                email="conversation-management@example.test",
                password_hash=str(USER_ID),
                status=UserStatus.ACTIVE,
                password_changed_at=NOW,
            )
        )
        session.add(
            Workspace(
                id=WORKSPACE_ID,
                name="Conversation Management",
                created_by_user_id=USER_ID,
                status=WorkspaceStatus.ACTIVE,
            )
        )
        session.add(
            WorkspaceMembership(
                id=uuid4(),
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                role=WorkspaceRole.OWNER,
            )
        )


def _scope(workspace_id: UUID) -> WorkspaceScope:
    return WorkspaceScope(workspace_id=workspace_id, user_id=USER_ID, role="owner")


def _command(
    *,
    key: str,
    question: str,
    conversation_id: UUID | None = None,
    title: str | None = None,
) -> StartDirectAnswerTurn:
    return StartDirectAnswerTurn(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        trace_id=TraceId(f"trace-{key}"),
        budget=RunBudget(
            schema_version=1,
            max_steps=2,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=10),
        ),
        runtime_version="direct-answer-runtime-v0",
        harness_version="harness-v0",
        idempotency_key=key,
        question=question,
        conversation_id=conversation_id,
        new_conversation_title=title,
    )
