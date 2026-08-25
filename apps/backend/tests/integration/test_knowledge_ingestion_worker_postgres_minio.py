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
from industry_platform.modules.files.domain import AttachmentMediaType, FileObjectStatus
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    AuditLog,
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.ingestion.index_contract import (
    ELASTICSEARCH_INDEX,
    MILVUS_COLLECTION,
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
    KNOWLEDGE_DELETION_TASK_NAME,
    KNOWLEDGE_INGESTION_TASK_NAME,
    ActivateDocumentVersion,
    CancelDocumentVersion,
    CompleteKnowledgeUpload,
    CreateDocumentVersion,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteDocument,
    DocumentDeletionTargetKind,
    DocumentDeletionTargetStatus,
    DocumentStatus,
    DocumentVersionStatus,
)
from industry_platform.modules.knowledge.models import (
    ChunkAssetLinkRecord,
    ChunkEmbeddingRecord,
    DocumentAssetRecord,
    DocumentChunkRecord,
    DocumentDeletionTargetRecord,
    DocumentIndexRecord,
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
    JobHandler,
    KnowledgeDeletionJobHandler,
    KnowledgeIngestionJobHandler,
)

from .postgres import PostgresProbe

MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
VECTOR_TESTS_REQUIRED = "VECTOR_TESTS_REQUIRED"
ELASTICSEARCH_TESTS_REQUIRED = "ELASTICSEARCH_TESTS_REQUIRED"
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
def test_worker_persists_dual_indexes_and_deduplicates_delivery(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    required = (MINIO_TESTS_REQUIRED, VECTOR_TESTS_REQUIRED, ELASTICSEARCH_TESTS_REQUIRED)
    if any(os.getenv(name) != "1" for name in required):
        pytest.skip(f"Set {', '.join(required)}=1 to run Knowledge index integration tests")

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
            outbox = SqlAlchemyOutboxTransactionFactory(session_factory)

            async def execute_next(
                expected_job_id: UUID,
                expected_disposition: JobExecutionDisposition = (JobExecutionDisposition.SUCCEEDED),
            ) -> None:
                async with outbox() as writer:
                    claimed = await writer.claim_job_dispatches(
                        ClaimOutboxCommand(
                            dispatcher_id="knowledge-worker-integration",
                            batch_size=1,
                            claim_seconds=60,
                        )
                    )
                assert len(claimed) == 1
                assert claimed[0].message.job_id == expected_job_id
                async with outbox() as writer:
                    assert await writer.mark_published(claimed[0].proof) is True

                async with httpx2.AsyncClient(trust_env=False) as internal_client:
                    resources = create_ingestion_resources(
                        settings,
                        session_factory,
                        jobs,
                        store,
                        internal_client,
                    )
                    handlers: dict[str, JobHandler] = {
                        KNOWLEDGE_INGESTION_TASK_NAME: KnowledgeIngestionJobHandler(
                            resources.service
                        ),
                        KNOWLEDGE_DELETION_TASK_NAME: KnowledgeDeletionJobHandler(
                            resources.deletion_service
                        ),
                    }
                    runtime = JobExecutionRuntime(
                        jobs=jobs,
                        handlers=FixedJobHandlerRegistry(handlers),
                        worker_id="knowledge-worker-integration",
                        heartbeat_seconds=0.25,
                    )
                    disposition = await runtime.execute(claimed[0].message)
                    if disposition is not expected_disposition:
                        async with session_factory() as session:
                            failed_job = await session.get(Job, expected_job_id)
                        failure_code = (
                            failed_job.last_error_code if failed_job is not None else "missing"
                        )
                        raise AssertionError(
                            f"Knowledge worker returned {disposition}; error={failure_code}"
                        )
                    if expected_disposition is JobExecutionDisposition.SUCCEEDED:
                        assert (
                            await runtime.execute(claimed[0].message)
                            is JobExecutionDisposition.NO_OP
                        )

            await execute_next(accepted.version.ingestion_job_id)

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
                        ChunkEmbeddingRecord,
                        DocumentIndexRecord,
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
            assert document.active_version_id == accepted.version.id
            assert version is not None
            assert version.status is DocumentVersionStatus.READY
            assert version.ready_at is not None
            assert job is not None
            assert job.status is JobStatus.SUCCEEDED
            assert job.result == {
                "asset_count": 2,
                "chunk_count": 1,
                "document_version_id": str(accepted.version.id),
                "page_count": 1,
                "status": "ready",
            }
            assert [checkpoint.stage_sequence for checkpoint in checkpoints] == list(range(1, 8))
            assert all(checkpoint.fencing_token == 1 for checkpoint in checkpoints)
            assert all(checkpoint.attempt_count == 1 for checkpoint in checkpoints)
            assert counts == {
                "document_pages": 1,
                "document_chunks": 1,
                "document_assets": 2,
                "chunk_asset_links": 2,
                "chunk_embeddings": 1,
                "document_index_records": 2,
            }

            detail = await knowledge.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
            )
            assert len(detail.assets) == 2
            assert len(detail.indexes) == 2
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
            assert next_version.document.active_version_id == accepted.version.id

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
            assert stored_document.active_version_id == accepted.version.id
            assert version_count == 2
            assert retained_page_count == 1
            assert retained_asset_count == 2

            await execute_next(next_version.version.ingestion_job_id)
            ready_detail = await knowledge.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
            )
            assert ready_detail.document.active_version_id == next_version.version.id
            assert ready_detail.versions[-1].status is DocumentVersionStatus.READY

            rolled_back = await knowledge.activate_document_version(
                scope,
                ActivateDocumentVersion(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    version_id=accepted.version.id,
                    expected_revision=ready_detail.document.revision,
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            assert rolled_back.active_version_id == accepted.version.id
            restored = await knowledge.activate_document_version(
                scope,
                ActivateDocumentVersion(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    version_id=next_version.version.id,
                    expected_revision=rolled_back.revision,
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            assert restored.active_version_id == next_version.version.id
            async with session_factory() as session:
                activation_audits = tuple(
                    (
                        await session.scalars(
                            select(AuditLog)
                            .where(
                                AuditLog.action == "knowledge.document.activate_version",
                                AuditLog.resource_id == accepted.document.id,
                            )
                            .order_by(AuditLog.created_at, AuditLog.id)
                        )
                    ).all()
                )
            assert len(activation_audits) == 2
            assert activation_audits[-1].sanitized_metadata == {
                "from_version_id": str(accepted.version.id),
                "to_version_id": str(next_version.version.id),
            }

            third_version = await knowledge.create_document_version(
                scope,
                CreateDocumentVersion(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    expected_revision=restored.revision,
                    idempotency_key="cancelled-version",
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            cancelled_version = await knowledge.cancel_document_version(
                scope,
                CancelDocumentVersion(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    version_id=third_version.version.id,
                    expected_revision=third_version.version.revision,
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            assert cancelled_version.status is DocumentVersionStatus.CANCELLED
            async with session_factory() as session:
                cancelled_job = await session.get(Job, third_version.version.ingestion_job_id)
            assert cancelled_job is not None
            assert cancelled_job.status is JobStatus.CANCELLED
            await execute_next(
                third_version.version.ingestion_job_id,
                JobExecutionDisposition.NO_OP,
            )

            ready_detail = await knowledge.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
            )
            assert ready_detail.document.active_version_id == next_version.version.id
            assert ready_detail.document.latest_version_number == 3
            assert (
                next(
                    version
                    for version in ready_detail.versions
                    if version.id == third_version.version.id
                ).status
                is DocumentVersionStatus.CANCELLED
            )

            delete_cancelled_version = await knowledge.create_document_version(
                scope,
                CreateDocumentVersion(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    expected_revision=ready_detail.document.revision,
                    idempotency_key="delete-cancelled-version",
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            orphan_key = (
                f"derived/{WORKSPACE_ID}/knowledge/{delete_cancelled_version.version.id}/"
                "uncommitted-preview.bin"
            )
            await store.put_private(
                bucket=bucket,
                object_key=orphan_key,
                content_type="application/octet-stream",
                content=b"cancelled-stage-output",
            )
            owned_keys.add(orphan_key)

            deletion = await knowledge.delete_document(
                scope,
                DeleteDocument(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    expected_revision=delete_cancelled_version.document.revision,
                    trace_id=TraceId("knowledge-worker-integration"),
                ),
            )
            assert deletion.document.status is DocumentStatus.DELETING
            async with session_factory() as session:
                deletion_cancelled_job = await session.get(
                    Job,
                    delete_cancelled_version.version.ingestion_job_id,
                )
                pending_targets = tuple(
                    (
                        await session.scalars(
                            select(DocumentDeletionTargetRecord)
                            .where(DocumentDeletionTargetRecord.document_id == accepted.document.id)
                            .order_by(
                                DocumentDeletionTargetRecord.kind,
                                DocumentDeletionTargetRecord.target_key,
                            )
                        )
                    ).all()
                )
            assert deletion_cancelled_job is not None
            assert deletion_cancelled_job.status is JobStatus.CANCELLED
            assert pending_targets
            assert all(
                target.status is DocumentDeletionTargetStatus.PENDING for target in pending_targets
            )
            vector_ids = tuple(
                target.target_key
                for target in pending_targets
                if target.kind is DocumentDeletionTargetKind.VECTOR
            )
            lexical_ids = tuple(
                target.target_key
                for target in pending_targets
                if target.kind is DocumentDeletionTargetKind.LEXICAL
            )
            object_targets = tuple(
                target
                for target in pending_targets
                if target.kind is DocumentDeletionTargetKind.OBJECT
            )
            prefix_targets = tuple(
                target
                for target in pending_targets
                if target.kind is DocumentDeletionTargetKind.OBJECT_PREFIX
            )
            assert len(vector_ids) == 2
            assert len(lexical_ids) == 2
            assert object_targets
            assert len(prefix_targets) == 4

            await execute_next(
                delete_cancelled_version.version.ingestion_job_id,
                JobExecutionDisposition.NO_OP,
            )
            await execute_next(deletion.job_id)

            async with session_factory() as session:
                deleted_document = await session.get(DocumentRecord, accepted.document.id)
                deleted_versions = tuple(
                    (
                        await session.scalars(
                            select(DocumentVersionRecord).where(
                                DocumentVersionRecord.document_id == accepted.document.id
                            )
                        )
                    ).all()
                )
                deleted_source = await session.get(FileObject, upload.file_id)
                deletion_job = await session.get(Job, deletion.job_id)
                deleted_targets = tuple(
                    (
                        await session.scalars(
                            select(DocumentDeletionTargetRecord).where(
                                DocumentDeletionTargetRecord.document_id == accepted.document.id
                            )
                        )
                    ).all()
                )
            assert deleted_document is not None
            assert deleted_document.status is DocumentStatus.DELETED
            assert deleted_document.active_version_id is None
            assert all(
                version.status is DocumentVersionStatus.DELETED for version in deleted_versions
            )
            assert len(deleted_versions) == 4
            assert deleted_source is not None
            assert deleted_source.status is FileObjectStatus.DELETED
            assert deletion_job is not None
            assert deletion_job.status is JobStatus.SUCCEEDED
            assert all(
                target.status is DocumentDeletionTargetStatus.DELETED for target in deleted_targets
            )

            for target in object_targets:
                with pytest.raises(FileObjectStoreError):
                    await store.stat(bucket=target.bucket or "", object_key=target.target_key)
            with pytest.raises(FileObjectStoreError):
                await store.stat(bucket=bucket, object_key=orphan_key)

            async with httpx2.AsyncClient(trust_env=False, timeout=10.0) as client:
                for external_id in vector_ids:
                    response = await client.post(
                        f"{settings.milvus_endpoint}/v2/vectordb/entities/get",
                        json={
                            "collectionName": MILVUS_COLLECTION,
                            "id": external_id,
                            "outputFields": ["id"],
                        },
                    )
                    assert response.status_code == 200
                    assert response.json()["data"] == []
                for external_id in lexical_ids:
                    response = await client.get(
                        f"{settings.elasticsearch_endpoint}/{ELASTICSEARCH_INDEX}/_doc/{external_id}"
                    )
                    assert response.status_code == 404

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
