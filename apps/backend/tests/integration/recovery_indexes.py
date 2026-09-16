"""Exercise scoped index loss, partial reconstruction and idempotent resumption."""

import socket
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select, update

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.ingestion.adapters.rebuild import SqlAlchemyIndexRebuildRepository
from industry_platform.modules.ingestion.index_contract import (
    ELASTICSEARCH_INDEX,
    MILVUS_COLLECTION,
)
from industry_platform.modules.ingestion.rebuild import (
    INDEX_REBUILD_TASK_NAME,
    IndexRebuildSubmissionService,
)
from industry_platform.modules.ingestion.resources import create_ingestion_resources
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyOutboxTransactionFactory
from industry_platform.modules.jobs.domain import ClaimOutboxCommand
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.domain import DocumentVersionStatus
from industry_platform.modules.knowledge.models import DocumentIndexRecord, DocumentVersionRecord
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope
from industry_platform.workers.runtime import (
    FixedJobHandlerRegistry,
    IndexRebuildJobHandler,
    JobExecutionDisposition,
    JobExecutionRuntime,
)


async def verify_index_reconstruction(
    *,
    settings: Settings,
    session_factory: AsyncSessionFactory,
    scope: WorkspaceScope,
    knowledge_base_id: UUID,
    version_id: UUID,
) -> None:
    jobs = create_job_resources(settings, session_factory).application_service
    store = create_private_file_object_store(settings)
    acceptance = IndexRebuildSubmissionService(SqlAlchemyIndexRebuildRepository(session_factory))
    async with session_factory() as session:
        original_rows = tuple(
            await session.scalars(
                select(DocumentIndexRecord).where(
                    DocumentIndexRecord.document_version_id == version_id
                )
            )
        )
    external_ids = tuple(sorted({row.external_id for row in original_rows}))
    original_identity = {(row.id, row.chunk_id, row.kind, row.external_id) for row in original_rows}
    assert external_ids
    async with httpx2.AsyncClient(trust_env=False) as client:
        resources = create_ingestion_resources(settings, session_factory, jobs, store, client)
        # Only entries with IDs loaded from this disposable database are removed.
        # Shared collections, indexes, other documents and source objects stay intact.
        await resources.service.vector_index.delete(external_ids)
        await resources.service.lexical_index.delete(external_ids)
        for external_id in external_ids:
            missing = await client.get(
                f"{settings.elasticsearch_endpoint}/{ELASTICSEARCH_INDEX}/_doc/{external_id}"
            )
            assert missing.status_code == 404
            missing_vector = await client.post(
                f"{settings.milvus_endpoint}/v2/vectordb/entities/get",
                json={
                    "collectionName": MILVUS_COLLECTION,
                    "id": external_id,
                    "consistencyLevel": "Strong",
                },
            )
            missing_vector.raise_for_status()
            assert missing_vector.json()["code"] == 0
            assert missing_vector.json()["data"] == []
        with pytest.raises(WorkspaceAccessDeniedError):
            await acceptance.submit(
                WorkspaceScope(scope.workspace_id, scope.user_id, "viewer"),
                knowledge_base_id=knowledge_base_id,
                document_version_id=version_id,
                idempotency_key="rebuild-denied",
                trace_id=TraceId("rebuild-test"),
            )
        first = await acceptance.submit(
            scope,
            knowledge_base_id=knowledge_base_id,
            document_version_id=version_id,
            idempotency_key="rebuild-once",
            trace_id=TraceId("rebuild-test"),
        )
        repeated = await acceptance.submit(
            scope,
            knowledge_base_id=knowledge_base_id,
            document_version_id=version_id,
            idempotency_key="rebuild-once",
            trace_id=TraceId("rebuild-test"),
        )
        assert first.created
        assert not repeated.created
        assert first.job_id == repeated.job_id
        outbox = SqlAlchemyOutboxTransactionFactory(session_factory)

        async def deliver(execution_settings: Settings) -> JobExecutionDisposition:
            async with outbox() as writer:
                deliveries = await writer.claim_job_dispatches(
                    ClaimOutboxCommand(
                        dispatcher_id="rebuild-test",
                        batch_size=1,
                        claim_seconds=60,
                    )
                )
            assert len(deliveries) == 1
            assert deliveries[0].message.job_id == first.job_id
            async with outbox() as writer:
                assert await writer.mark_published(deliveries[0].proof)
            current = create_ingestion_resources(
                execution_settings, session_factory, jobs, store, client
            )
            runtime = JobExecutionRuntime(
                jobs=jobs,
                handlers=FixedJobHandlerRegistry(
                    {INDEX_REBUILD_TASK_NAME: IndexRebuildJobHandler(current.rebuild_service)}
                ),
                worker_id="rebuild-worker",
                heartbeat_seconds=0.25,
            )
            disposition = await runtime.execute(deliveries[0].message)
            if disposition is JobExecutionDisposition.SUCCEEDED:
                assert await runtime.execute(deliveries[0].message) is JobExecutionDisposition.NO_OP
            return disposition

        with socket.socket() as refused:
            refused.bind(("127.0.0.1", 0))
            assert (
                await deliver(
                    settings.model_copy(
                        update={
                            "elasticsearch_endpoint": f"http://127.0.0.1:{refused.getsockname()[1]}"
                        }
                    )
                )
                is JobExecutionDisposition.RETRY_SCHEDULED
            )
        async with session_factory.begin() as session:
            partial = await session.get(DocumentVersionRecord, version_id)
            assert partial is not None
            assert partial.status is not DocumentVersionStatus.READY
            assert partial.ready_at is None
            due = datetime.now(UTC) - timedelta(seconds=1)
            await session.execute(
                update(Job).where(Job.id == first.job_id).values(available_at=due)
            )
            await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.source_job_id == first.job_id,
                    OutboxEvent.job_dispatch_generation == 2,
                )
                .values(next_attempt_at=due)
            )
        assert await deliver(settings) is JobExecutionDisposition.SUCCEEDED
        for external_id in external_ids:
            restored = await client.get(
                f"{settings.elasticsearch_endpoint}/{ELASTICSEARCH_INDEX}/_doc/{external_id}"
            )
            assert restored.status_code == 200
            assert restored.json()["_source"]["document_version_id"] == str(version_id)
            vector = await client.post(
                f"{settings.milvus_endpoint}/v2/vectordb/entities/get",
                json={
                    "collectionName": MILVUS_COLLECTION,
                    "id": external_id,
                    "consistencyLevel": "Strong",
                },
            )
            vector.raise_for_status()
            assert vector.json()["code"] == 0
            assert len(vector.json()["data"]) == 1
            assert vector.json()["data"][0]["document_version_id"] == str(version_id)
    async with session_factory() as session:
        final = await session.get(DocumentVersionRecord, version_id)
        assert final is not None
        assert final.status is DocumentVersionStatus.READY
        rows = tuple(
            await session.scalars(
                select(DocumentIndexRecord).where(
                    DocumentIndexRecord.document_version_id == version_id
                )
            )
        )
        assert {
            (row.id, row.chunk_id, row.kind, row.external_id) for row in rows
        } == original_identity
