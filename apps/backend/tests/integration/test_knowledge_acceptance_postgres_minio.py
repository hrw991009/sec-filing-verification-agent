"""Real PostgreSQL and MinIO proof for Knowledge ingestion acceptance."""

import asyncio
import hashlib
import os
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    FileObjectPurpose,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.files.service import FileValidationRejectedError
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.domain import (
    CompleteKnowledgeUpload,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DocumentVersionStatus,
    KnowledgeConflictError,
)
from industry_platform.modules.knowledge.models import DocumentRecord, DocumentVersionRecord
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_knowledge_complete_is_atomic_idempotent_and_private(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        store = create_private_file_object_store(migrated_postgres_probe.settings)
        bucket = migrated_postgres_probe.settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("MinIO test configuration is incomplete")
        owned_keys: set[str] = set()
        try:
            now = datetime.now(UTC)
            async with session_factory.begin() as session:
                session.add(
                    User(
                        id=USER_ID,
                        email="knowledge@example.test",
                        password_hash=str(USER_ID),
                        status=UserStatus.ACTIVE,
                        password_changed_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                session.add(
                    Workspace(
                        id=WORKSPACE_ID,
                        name="Knowledge Workspace",
                        created_by_user_id=USER_ID,
                        status=WorkspaceStatus.ACTIVE,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceMembership(
                        id=uuid4(),
                        workspace_id=WORKSPACE_ID,
                        user_id=USER_ID,
                        role=WorkspaceRole.OWNER,
                        created_at=now,
                        updated_at=now,
                    )
                )

            service = KnowledgeApplicationService(
                repository=SqlAlchemyKnowledgeRepository(session_factory),
                transaction_factory=SqlAlchemyKnowledgeAcceptanceTransactionFactory(
                    session_factory
                ),
                object_store=store,
                bucket=bucket,
            )
            scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
            knowledge_base = await service.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name="Private sources",
                    description=None,
                    trace_id=TraceId("knowledge-integration"),
                ),
            )
            source = b"Private market outlook.\n"
            ticket = await service.create_upload(
                scope,
                CreateKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    original_name="outlook.txt",
                    declared_media_type=AttachmentMediaType.TEXT_PLAIN,
                    expected_size=len(source),
                    expected_sha256=hashlib.sha256(source).hexdigest(),
                    trace_id=TraceId("knowledge-integration"),
                ),
            )
            async with session_factory() as session:
                staged = await session.scalar(
                    select(FileObject).where(FileObject.id == ticket.file_id)
                )
                assert staged is not None
                owned_keys.add(staged.staging_object_key)

            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                uploaded = await client.post(
                    ticket.url,
                    data=ticket.fields,
                    files={"file": ("outlook.txt", source, "text/plain")},
                )
                assert uploaded.status_code == 204

            command = CompleteKnowledgeUpload(
                knowledge_base_id=knowledge_base.id,
                file_id=ticket.file_id,
                title="Market outlook",
                idempotency_key="first",
                trace_id=TraceId("knowledge-integration"),
            )
            accepted = await service.complete_upload(scope, command)
            repeated = await service.complete_upload(scope, command)

            assert accepted.created is True
            assert repeated.created is False
            assert repeated.document.id == accepted.document.id
            assert repeated.version.id == accepted.version.id
            assert accepted.version.status is DocumentVersionStatus.QUEUED
            assert accepted.document.active_version_id is None

            async with session_factory() as session:
                counts: list[int] = []
                for model in (DocumentRecord, DocumentVersionRecord, Job, OutboxEvent):
                    count = await session.scalar(select(func.count(model.id)))
                    counts.append(int(count or 0))
                file = await session.scalar(
                    select(FileObject).where(FileObject.id == ticket.file_id)
                )
                assert file is not None
                assert file.status is FileObjectStatus.READY
                assert file.purpose is FileObjectPurpose.KNOWLEDGE_SOURCE
                assert file.object_key is not None
                owned_keys.add(file.object_key)
            assert counts == [1, 1, 1, 1]

            second_source = b"Second private source.\n"
            second_ticket = await service.create_upload(
                scope,
                CreateKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    original_name="second.txt",
                    declared_media_type=AttachmentMediaType.TEXT_PLAIN,
                    expected_size=len(second_source),
                    expected_sha256=hashlib.sha256(second_source).hexdigest(),
                    trace_id=TraceId("knowledge-integration"),
                ),
            )
            async with session_factory() as session:
                staged = await session.scalar(
                    select(FileObject).where(FileObject.id == second_ticket.file_id)
                )
                assert staged is not None
                owned_keys.add(staged.staging_object_key)
            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                uploaded = await client.post(
                    second_ticket.url,
                    data=second_ticket.fields,
                    files={"file": ("second.txt", second_source, "text/plain")},
                )
                assert uploaded.status_code == 204
            second = await service.complete_upload(
                scope,
                CompleteKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    file_id=second_ticket.file_id,
                    title="Second source",
                    idempotency_key="second",
                    trace_id=TraceId("knowledge-integration"),
                ),
            )
            with pytest.raises(KnowledgeConflictError):
                await service.complete_upload(
                    scope,
                    CompleteKnowledgeUpload(
                        knowledge_base_id=knowledge_base.id,
                        file_id=ticket.file_id,
                        title="Market outlook",
                        idempotency_key="second",
                        trace_id=TraceId("knowledge-integration"),
                    ),
                )
            async with session_factory() as session:
                final_counts: list[int] = []
                for model in (DocumentRecord, DocumentVersionRecord, Job, OutboxEvent):
                    count = await session.scalar(select(func.count(model.id)))
                    final_counts.append(int(count or 0))
                second_file = await session.scalar(
                    select(FileObject).where(FileObject.id == second_ticket.file_id)
                )
                assert second_file is not None
                assert second_file.object_key is not None
                owned_keys.add(second_file.object_key)
            assert second.created is True
            assert final_counts == [2, 2, 2, 2]

            fake_pdf = b"not a pdf"
            rejected_ticket = await service.create_upload(
                scope,
                CreateKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    original_name="spoofed.pdf",
                    declared_media_type=AttachmentMediaType.APPLICATION_PDF,
                    expected_size=len(fake_pdf),
                    expected_sha256=hashlib.sha256(fake_pdf).hexdigest(),
                    trace_id=TraceId("knowledge-integration"),
                ),
            )
            async with session_factory() as session:
                staged = await session.scalar(
                    select(FileObject).where(FileObject.id == rejected_ticket.file_id)
                )
                assert staged is not None
                owned_keys.add(staged.staging_object_key)
            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                uploaded = await client.post(
                    rejected_ticket.url,
                    data=rejected_ticket.fields,
                    files={"file": ("spoofed.pdf", fake_pdf, "application/pdf")},
                )
                assert uploaded.status_code == 204
            with pytest.raises(FileValidationRejectedError):
                await service.complete_upload(
                    scope,
                    CompleteKnowledgeUpload(
                        knowledge_base_id=knowledge_base.id,
                        file_id=rejected_ticket.file_id,
                        title="Spoofed",
                        idempotency_key="spoof",
                        trace_id=TraceId("knowledge-integration"),
                    ),
                )
            async with session_factory() as session:
                rejected = await session.scalar(
                    select(FileObject).where(FileObject.id == rejected_ticket.file_id)
                )
                assert rejected is not None
                assert rejected.status is FileObjectStatus.REJECTED
        finally:
            for key in owned_keys:
                if not key.startswith(
                    (f"staging/{WORKSPACE_ID}/knowledge/", f"ready/{WORKSPACE_ID}/knowledge/")
                ):
                    raise RuntimeError(
                        "Refusing to remove an object outside the Knowledge test prefix"
                    )
                with suppress(FileObjectStoreError):
                    await store.remove(bucket=bucket, object_key=key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
