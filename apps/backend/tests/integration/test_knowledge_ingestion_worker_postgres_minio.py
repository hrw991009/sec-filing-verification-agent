"""Real PostgreSQL and MinIO proof for the versioned Knowledge worker."""

import asyncio
import hashlib
import io
import os
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
from PIL import Image, ImageDraw
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.ingestion.resources import create_ingestion_resources
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyOutboxTransactionFactory,
)
from industry_platform.modules.jobs.domain import ClaimOutboxCommand, JobStatus
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_INGESTION_TASK_NAME,
    CompleteKnowledgeUpload,
    CreateDocumentVersion,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DocumentVersionStatus,
)
from industry_platform.modules.knowledge.models import (
    ChunkAssetLinkRecord,
    DocumentAssetRecord,
    DocumentChunkRecord,
    DocumentPageRecord,
    DocumentRecord,
    DocumentVersionRecord,
    IngestionCheckpointRecord,
)
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import (
    FixedJobHandlerRegistry,
    JobExecutionDisposition,
    JobExecutionRuntime,
    KnowledgeIngestionJobHandler,
)

from .postgres import PostgresProbe

MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
USER_ID = UUID("61111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("62222222-2222-4222-8222-222222222222")


def _source_pdf() -> bytes:
    chart = Image.new("RGB", (360, 160), "white")
    drawing = ImageDraw.Draw(chart)
    drawing.rectangle((30, 35, 95, 140), fill="#2f6fed")
    drawing.rectangle((125, 65, 190, 140), fill="#d6544d")
    drawing.text((30, 10), "Capacity", fill="black")
    chart_bytes = io.BytesIO()
    chart.save(chart_bytes, format="PNG")

    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter, pageCompression=0)
    document.setFont("Helvetica-Bold", 16)
    document.drawString(60, 735, "Semiconductor capacity outlook")
    document.setFont("Helvetica", 11)
    document.drawString(
        60, 710, "Utilization reached 82 percent and expansion remains disciplined."
    )
    document.drawImage(
        ImageReader(io.BytesIO(chart_bytes.getvalue())),
        60,
        470,
        width=300,
        height=133,
        mask="auto",
    )
    left, bottom, width, row_height = 60, 220, 360, 40
    document.setStrokeColor(HexColor("#475569"))
    for offset in (0, row_height, row_height * 2, row_height * 3):
        document.line(left, bottom + offset, left + width, bottom + offset)
    for offset in (0, 120, 240, 360):
        document.line(left + offset, bottom, left + offset, bottom + row_height * 3)
    for row_index, row in enumerate(
        (("Region", "Q1", "Q2"), ("North", "12", "18"), ("South", "9", "14"))
    ):
        y = bottom + row_height * 3 - (row_index + 1) * row_height + 14
        for column_index, value in enumerate(row):
            document.drawString(left + column_index * 120 + 8, y, value)
    document.save()
    return output.getvalue()


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_worker_persists_four_stages_and_deduplicates_delivery(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        store = create_private_file_object_store(settings)
        bucket = settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("MinIO test configuration is incomplete")
        owned_keys: set[str] = set()

        try:
            now = datetime.now(UTC)
            async with session_factory.begin() as session:
                session.add(
                    User(
                        id=USER_ID,
                        email="knowledge-worker@example.test",
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
                        name="Knowledge Worker Workspace",
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

            knowledge = KnowledgeApplicationService(
                repository=SqlAlchemyKnowledgeRepository(session_factory),
                transaction_factory=SqlAlchemyKnowledgeAcceptanceTransactionFactory(
                    session_factory
                ),
                object_store=store,
                bucket=bucket,
            )
            scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
            knowledge_base = await knowledge.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name="Versioned ingestion",
                    description=None,
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            source = _source_pdf()
            upload = await knowledge.create_upload(
                scope,
                CreateKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    original_name="capacity.pdf",
                    declared_media_type=AttachmentMediaType.APPLICATION_PDF,
                    expected_size=len(source),
                    expected_sha256=hashlib.sha256(source).hexdigest(),
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            async with session_factory() as session:
                staged = await session.get(FileObject, upload.file_id)
                assert staged is not None
                owned_keys.add(staged.staging_object_key)

            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.post(
                    upload.url,
                    data=upload.fields,
                    files={"file": ("capacity.pdf", source, "application/pdf")},
                )
            assert response.status_code == 204

            accepted = await knowledge.complete_upload(
                scope,
                CompleteKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    file_id=upload.file_id,
                    title="Capacity outlook",
                    idempotency_key="worker-chain",
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            async with session_factory() as session:
                source_file = await session.get(FileObject, upload.file_id)
                assert source_file is not None
                assert source_file.object_key is not None
                owned_keys.add(source_file.object_key)

            jobs = create_job_resources(settings, session_factory).application_service
            ingestion = create_ingestion_resources(session_factory, jobs, store).service
            outbox = SqlAlchemyOutboxTransactionFactory(session_factory)
            async with outbox() as writer:
                claimed = await writer.claim_job_dispatches(
                    ClaimOutboxCommand(
                        dispatcher_id="knowledge-worker-integration",
                        batch_size=1,
                        claim_seconds=60,
                    )
                )
            assert len(claimed) == 1
            assert claimed[0].message.job_id == accepted.version.ingestion_job_id
            async with outbox() as writer:
                assert await writer.mark_published(claimed[0].proof) is True

            runtime = JobExecutionRuntime(
                jobs=jobs,
                handlers=FixedJobHandlerRegistry(
                    {KNOWLEDGE_INGESTION_TASK_NAME: KnowledgeIngestionJobHandler(ingestion)}
                ),
                worker_id="knowledge-worker-integration",
                heartbeat_seconds=0.25,
            )
            disposition = await runtime.execute(claimed[0].message)
            if disposition is not JobExecutionDisposition.SUCCEEDED:
                async with session_factory() as session:
                    failed_job = await session.get(Job, accepted.version.ingestion_job_id)
                raise AssertionError(
                    f"Knowledge worker returned {disposition}; "
                    f"error={failed_job.last_error_code if failed_job is not None else 'missing'}"
                )
            assert await runtime.execute(claimed[0].message) is JobExecutionDisposition.NO_OP

            async with session_factory() as session:
                document = await session.get(DocumentRecord, accepted.document.id)
                version = await session.get(DocumentVersionRecord, accepted.version.id)
                job = await session.get(Job, accepted.version.ingestion_job_id)
                checkpoints = tuple(
                    (
                        await session.scalars(
                            select(IngestionCheckpointRecord)
                            .where(
                                IngestionCheckpointRecord.document_version_id == accepted.version.id
                            )
                            .order_by(IngestionCheckpointRecord.stage_sequence)
                        )
                    ).all()
                )
                counts = {
                    model.__tablename__: int(
                        await session.scalar(select(func.count(model.id))) or 0
                    )
                    for model in (
                        DocumentPageRecord,
                        DocumentChunkRecord,
                        DocumentAssetRecord,
                        ChunkAssetLinkRecord,
                    )
                }
                assets = tuple(
                    (
                        await session.scalars(
                            select(DocumentAssetRecord).where(
                                DocumentAssetRecord.document_version_id == accepted.version.id
                            )
                        )
                    ).all()
                )

            assert document is not None
            assert document.active_version_id is None
            assert version is not None
            assert version.status is DocumentVersionStatus.PARSED
            assert version.ready_at is None
            assert job is not None
            assert job.status is JobStatus.SUCCEEDED
            assert job.result == {
                "asset_count": 2,
                "chunk_count": 1,
                "document_version_id": str(accepted.version.id),
                "page_count": 1,
                "status": "parsed",
            }
            assert [checkpoint.stage_sequence for checkpoint in checkpoints] == [1, 2, 3, 4]
            assert all(checkpoint.fencing_token == 1 for checkpoint in checkpoints)
            assert all(checkpoint.attempt_count == 1 for checkpoint in checkpoints)
            assert counts == {
                "document_pages": 1,
                "document_chunks": 1,
                "document_assets": 2,
                "chunk_asset_links": 2,
            }

            detail = await knowledge.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
            )
            assert len(detail.assets) == 2
            assert all(
                asset.preview_url is not None and "X-Amz-Signature=" in asset.preview_url
                for asset in detail.assets
            )

            version_command = CreateDocumentVersion(
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
                expected_revision=detail.document.revision,
                idempotency_key="parser-upgrade-version",
                trace_id=TraceId("knowledge-worker-integration"),
            )
            next_version = await knowledge.create_document_version(scope, version_command)
            repeated_version = await knowledge.create_document_version(scope, version_command)
            assert next_version.created is True
            assert repeated_version.created is False
            assert repeated_version.version.id == next_version.version.id
            assert next_version.version.version == 2
            assert next_version.version.file_id == accepted.version.file_id
            assert next_version.document.active_version_id is None

            async with session_factory() as session:
                stored_document = await session.get(DocumentRecord, accepted.document.id)
                version_count = int(
                    await session.scalar(
                        select(func.count(DocumentVersionRecord.id)).where(
                            DocumentVersionRecord.document_id == accepted.document.id
                        )
                    )
                    or 0
                )
                retained_page_count = int(
                    await session.scalar(
                        select(func.count(DocumentPageRecord.id)).where(
                            DocumentPageRecord.document_version_id == accepted.version.id
                        )
                    )
                    or 0
                )
                retained_asset_count = int(
                    await session.scalar(
                        select(func.count(DocumentAssetRecord.id)).where(
                            DocumentAssetRecord.document_version_id == accepted.version.id
                        )
                    )
                    or 0
                )
            assert stored_document is not None
            assert stored_document.latest_version_number == 2
            assert stored_document.active_version_id is None
            assert version_count == 2
            assert retained_page_count == 1
            assert retained_asset_count == 2

            for checkpoint in checkpoints:
                if checkpoint.output_object_key is not None:
                    owned_keys.add(checkpoint.output_object_key)
            for asset in assets:
                owned_keys.add(asset.preview_object_key)
        finally:
            for key in owned_keys:
                if not key.startswith(
                    (
                        f"staging/{WORKSPACE_ID}/knowledge/",
                        f"ready/{WORKSPACE_ID}/knowledge/",
                        f"derived/{WORKSPACE_ID}/knowledge/",
                    )
                ):
                    raise RuntimeError(
                        "Refusing to remove an object outside the Knowledge test prefix"
                    )
                with suppress(FileObjectStoreError):
                    await store.remove(bucket=bucket, object_key=key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
