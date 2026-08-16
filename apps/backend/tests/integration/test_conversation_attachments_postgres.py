"""PostgreSQL proof for ordered, one-time Conversation attachment association."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.conversations.adapters.management import (
    SqlAlchemyConversationManagementRepository,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.management import (
    ConversationManagementService,
)
from industry_platform.modules.conversations.models import (
    Conversation,
    Message,
    MessageAttachment,
    Turn,
)
from industry_platform.modules.conversations.service import (
    ConversationApplicationService,
    ConversationAttachmentNotReadyError,
    ConversationAttachmentNotSupportedError,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.domain import JobIdempotencyConflictError
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
TEXT_FILE_IDS = (
    UUID("44444444-4444-4444-8444-444444444444"),
    UUID("55555555-5555-4555-8555-555555555555"),
)
IMAGE_FILE_ID = UUID("66666666-6666-4666-8666-666666666666")
STAGING_FILE_ID = UUID("77777777-7777-4777-8777-777777777777")
OTHER_WORKSPACE_FILE_ID = UUID("88888888-8888-4888-8888-888888888888")


def test_conversation_attachments_are_atomic_ordered_idempotent_and_scoped(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await _seed_file_facts(session_factory)
            service = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(
                    session_factory,
                    supports_image_input=False,
                ),
                clock=lambda: NOW,
            )
            original = _command(
                key="attachment-success",
                attachment_ids=TEXT_FILE_IDS,
            )

            created = await service.start_direct_answer(original)
            reused = await service.start_direct_answer(original)

            assert created.created is True
            assert reused.created is False
            assert reused == replace(created, created=False)
            assert await _fact_counts(session_factory) == (1, 1, 1, 1, 1, 2, 1)

            async with session_factory() as session:
                stored_links = tuple(
                    (file_id, ordinal)
                    for file_id, ordinal in (
                        await session.execute(
                            select(
                                MessageAttachment.file_id,
                                MessageAttachment.ordinal,
                            ).order_by(MessageAttachment.ordinal)
                        )
                    ).all()
                )
                attached_at_by_file = {
                    file_id: attached_at
                    for file_id, attached_at in (
                        await session.execute(
                            select(FileObject.id, FileObject.attached_at).where(
                                FileObject.id.in_(TEXT_FILE_IDS)
                            )
                        )
                    ).all()
                }
            assert stored_links == ((TEXT_FILE_IDS[0], 0), (TEXT_FILE_IDS[1], 1))
            assert attached_at_by_file == {
                TEXT_FILE_IDS[0]: NOW,
                TEXT_FILE_IDS[1]: NOW,
            }

            management = ConversationManagementService(
                repository=SqlAlchemyConversationManagementRepository(session_factory)
            )
            refreshed = await management.list_messages(
                WorkspaceScope(WORKSPACE_ID, USER_ID, "owner"),
                created.conversation_id,
            )
            assert len(refreshed.items) == 1
            assert (
                tuple(attachment.file_id for attachment in refreshed.items[0].attachments)
                == TEXT_FILE_IDS
            )
            assert tuple(
                attachment.original_name for attachment in refreshed.items[0].attachments
            ) == ("first-note.txt", "second-note.txt")

            with pytest.raises(JobIdempotencyConflictError):
                await service.start_direct_answer(
                    replace(original, attachment_ids=(TEXT_FILE_IDS[0],))
                )
            with pytest.raises(JobIdempotencyConflictError):
                await service.start_direct_answer(
                    replace(original, attachment_ids=tuple(reversed(TEXT_FILE_IDS)))
                )

            with pytest.raises(ConversationAttachmentNotReadyError):
                await service.start_direct_answer(
                    _command(
                        key="attachment-not-ready",
                        attachment_ids=(STAGING_FILE_ID,),
                    )
                )
            with pytest.raises(ConversationAttachmentNotReadyError):
                await service.start_direct_answer(
                    _command(
                        key="attachment-cross-workspace",
                        attachment_ids=(OTHER_WORKSPACE_FILE_ID,),
                    )
                )
            with pytest.raises(ConversationAttachmentNotSupportedError):
                await service.start_direct_answer(
                    _command(
                        key="attachment-image-not-supported",
                        attachment_ids=(IMAGE_FILE_ID,),
                    )
                )

            assert await _fact_counts(session_factory) == (1, 1, 1, 1, 1, 2, 1)
            async with session_factory() as session:
                untouched = {
                    file_id: attached_at
                    for file_id, attached_at in (
                        await session.execute(
                            select(FileObject.id, FileObject.attached_at).where(
                                FileObject.id.in_(
                                    (
                                        IMAGE_FILE_ID,
                                        STAGING_FILE_ID,
                                        OTHER_WORKSPACE_FILE_ID,
                                    )
                                )
                            )
                        )
                    ).all()
                }
            assert untouched == {
                IMAGE_FILE_ID: None,
                STAGING_FILE_ID: None,
                OTHER_WORKSPACE_FILE_ID: None,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def _command(*, key: str, attachment_ids: tuple[UUID, ...]) -> StartDirectAnswerTurn:
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
        question="Explain the attached evidence.",
        new_conversation_title="Attachment integration",
        attachment_ids=attachment_ids,
    )


async def _fact_counts(session_factory: AsyncSessionFactory) -> tuple[int, ...]:
    async with session_factory() as session:
        values: list[int] = []
        for model in (
            Conversation,
            Turn,
            AgentRunRecord,
            Job,
            Message,
            MessageAttachment,
            OutboxEvent,
        ):
            values.append((await session.scalar(select(func.count()).select_from(model))) or 0)
    return tuple(values)


async def _seed_file_facts(session_factory: AsyncSessionFactory) -> None:
    async with session_factory.begin() as session:
        session.add(
            User(
                id=USER_ID,
                email="conversation-attachments@example.test",
                password_hash=str(USER_ID),
                status=UserStatus.ACTIVE,
                password_changed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                Workspace(
                    id=WORKSPACE_ID,
                    name="Attachment Workspace",
                    created_by_user_id=USER_ID,
                    status=WorkspaceStatus.ACTIVE,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                Workspace(
                    id=OTHER_WORKSPACE_ID,
                    name="Other Attachment Workspace",
                    created_by_user_id=USER_ID,
                    status=WorkspaceStatus.ACTIVE,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
        await session.flush()
        session.add_all(
            (
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=WORKSPACE_ID,
                    user_id=USER_ID,
                    role=WorkspaceRole.OWNER,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=OTHER_WORKSPACE_ID,
                    user_id=USER_ID,
                    role=WorkspaceRole.OWNER,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                _ready_file(
                    TEXT_FILE_IDS[0],
                    workspace_id=WORKSPACE_ID,
                    original_name="first-note.txt",
                    kind=AttachmentKind.TEXT,
                ),
                _ready_file(
                    TEXT_FILE_IDS[1],
                    workspace_id=WORKSPACE_ID,
                    original_name="second-note.txt",
                    kind=AttachmentKind.TEXT,
                ),
                _ready_file(
                    IMAGE_FILE_ID,
                    workspace_id=WORKSPACE_ID,
                    original_name="chart.png",
                    kind=AttachmentKind.IMAGE,
                ),
                _staging_file(STAGING_FILE_ID),
                _ready_file(
                    OTHER_WORKSPACE_FILE_ID,
                    workspace_id=OTHER_WORKSPACE_ID,
                    original_name="other-note.txt",
                    kind=AttachmentKind.TEXT,
                ),
            )
        )


def _ready_file(
    file_id: UUID,
    *,
    workspace_id: UUID,
    original_name: str,
    kind: AttachmentKind,
) -> FileObject:
    is_image = kind is AttachmentKind.IMAGE
    media_type = AttachmentMediaType.IMAGE_PNG if is_image else AttachmentMediaType.TEXT_PLAIN
    size = 128 if is_image else 32
    digest = file_id.hex * 2
    return FileObject(
        id=file_id,
        workspace_id=workspace_id,
        created_by_user_id=USER_ID,
        original_name=original_name,
        declared_media_type=media_type.value,
        detected_media_type=media_type,
        kind=kind,
        bucket="private-files",
        staging_object_key=f"staging/{workspace_id}/{file_id}",
        object_key=f"ready/{workspace_id}/{file_id}",
        expected_size=size,
        actual_size=size,
        safe_size=size,
        expected_sha256=digest,
        source_sha256=digest,
        safe_sha256=digest,
        source_etag=f"etag-{file_id}",
        status=FileObjectStatus.READY,
        extracted_text=None if is_image else f"safe text for {file_id}",
        parser_version="chat-attachment-parser-v1",
        sanitizer_version="chat-attachment-sanitizer-v1",
        width=4 if is_image else None,
        height=3 if is_image else None,
        revision=2,
        upload_expires_at=NOW + timedelta(minutes=10),
        processing_started_at=NOW - timedelta(seconds=2),
        ready_at=NOW - timedelta(seconds=1),
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(seconds=1),
    )


def _staging_file(file_id: UUID) -> FileObject:
    return FileObject(
        id=file_id,
        workspace_id=WORKSPACE_ID,
        created_by_user_id=USER_ID,
        original_name="pending-note.txt",
        declared_media_type=AttachmentMediaType.TEXT_PLAIN.value,
        bucket="private-files",
        staging_object_key=f"staging/{WORKSPACE_ID}/{file_id}",
        expected_size=16,
        expected_sha256=file_id.hex * 2,
        status=FileObjectStatus.STAGING,
        revision=0,
        upload_expires_at=NOW + timedelta(minutes=10),
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )
