"""Prove the locked SEC filing import path against PostgreSQL and MinIO."""

import asyncio
import os
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.disclosures.adapters.filing_content_sqlalchemy import (
    SqlAlchemySecFilingContentRepository,
)
from industry_platform.modules.disclosures.adapters.filings_sqlalchemy import (
    SqlAlchemySecFilingRepository,
)
from industry_platform.modules.disclosures.adapters.sec_archives import (
    FrozenSecFilingArchiveAdapter,
)
from industry_platform.modules.disclosures.adapters.snapshots import (
    MinioSecFilingDocumentSnapshotStore,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecCanonicalFiling,
    SecFilingArchive,
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingObservation,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceSnapshot,
    sec_complete_submission_url,
    sec_filing_document_url,
    sec_primary_document_url,
    sec_submissions_current_url,
    sec_submissions_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.filing_content_service import (
    SecFilingContentService,
    SecFilingImportService,
)
from industry_platform.modules.disclosures.models import (
    SecFilingDocumentRecord,
    SecSourceSnapshotRecord,
    WorkspaceSecImportRecord,
)
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
from industry_platform.modules.ingestion.index_contract import MILVUS_COLLECTION
from industry_platform.modules.ingestion.resources import create_ingestion_resources
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyOutboxTransactionFactory
from industry_platform.modules.jobs.domain import ClaimOutboxCommand
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_DELETION_TASK_NAME,
    KNOWLEDGE_INGESTION_TASK_NAME,
    CreateKnowledgeBase,
    DeleteDocument,
    DocumentStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentRecord,
    DocumentVersionRecord,
    IngestionCheckpointRecord,
)
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex
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
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
CIK = "0000320193"
ACCESSION = "0000320193-23-000106"
ACCEPTED = datetime(2023, 11, 3, 18, 1, tzinfo=UTC)


def submission_set(now: datetime) -> SecSubmissionSet:
    body = b'{"fixture":"sec-filing-import"}'
    content_hash = sha256_hex(body)
    source = SecSubmissionSourceSnapshot(
        cik=CIK,
        source_kind=SecSubmissionSourceKind.CURRENT,
        source_name=f"CIK{CIK}.json",
        source_url=sec_submissions_current_url(CIK),
        source_version=sec_submissions_source_version(
            SecSubmissionSourceKind.CURRENT,
            content_hash,
        ),
        content_sha256=content_hash,
        retrieved_at=now,
        source_available_at=ACCEPTED,
        body=body,
        filings=(
            SecFilingObservation(
                cik=CIK,
                accession=ACCESSION,
                form=SecFilingForm.TEN_K,
                report_date=date(2023, 9, 30),
                filed_date=date(2023, 11, 3),
                accepted_at=ACCEPTED,
                primary_document="aapl-20230930.htm",
            ),
        ),
    )
    return SecSubmissionSet(current=source, supplementals=(), required_supplemental_names=())


def archive(filing: SecCanonicalFiling, now: datetime) -> SecFilingArchive:
    complete_body = b"<SEC-DOCUMENT>complete filing</SEC-DOCUMENT>"
    primary_body = b"<html><h1>Net sales</h1><p>Net sales increased.</p></html>"
    instance_body = b"<xbrl><fact>net sales</fact></xbrl>"
    schema_body = b"<schema><element name='NetSales'/></schema>"
    return SecFilingArchive(
        filing=filing,
        documents=(
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.COMPLETE_SUBMISSION,
                cik=CIK,
                accession=ACCESSION,
                filename=f"{ACCESSION}.txt",
                source_url=sec_complete_submission_url(CIK, ACCESSION),
                source_version=f"sec-filing-complete-{sha256_hex(complete_body)[:24]}",
                content_type="text/plain",
                content_sha256=sha256_hex(complete_body),
                byte_size=len(complete_body),
                retrieved_at=now,
                source_available_at=ACCEPTED,
                body=complete_body,
            ),
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.PRIMARY_DOCUMENT,
                cik=CIK,
                accession=ACCESSION,
                filename="aapl-20230930.htm",
                source_url=sec_primary_document_url(CIK, ACCESSION, "aapl-20230930.htm"),
                source_version=f"sec-filing-primary-{sha256_hex(primary_body)[:24]}",
                content_type="text/html",
                content_sha256=sha256_hex(primary_body),
                byte_size=len(primary_body),
                retrieved_at=now,
                source_available_at=ACCEPTED,
                body=primary_body,
            ),
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.XBRL_INSTANCE,
                cik=CIK,
                accession=ACCESSION,
                filename="aapl-20230930_htm.xml",
                source_url=sec_filing_document_url(
                    CIK,
                    ACCESSION,
                    "aapl-20230930_htm.xml",
                ),
                source_version=f"sec-filing-instance-{sha256_hex(instance_body)[:24]}",
                content_type="application/xml",
                content_sha256=sha256_hex(instance_body),
                byte_size=len(instance_body),
                retrieved_at=now,
                source_available_at=ACCEPTED,
                body=instance_body,
            ),
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.XBRL_ATTACHMENT,
                cik=CIK,
                accession=ACCESSION,
                filename="aapl-20230930.xsd",
                source_url=sec_filing_document_url(
                    CIK,
                    ACCESSION,
                    "aapl-20230930.xsd",
                ),
                source_version=f"sec-filing-schema-{sha256_hex(schema_body)[:24]}",
                content_type="text/xml",
                content_sha256=sha256_hex(schema_body),
                byte_size=len(schema_body),
                retrieved_at=now,
                source_available_at=ACCEPTED,
                body=schema_body,
            ),
        ),
    )


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_sec_filing_import_is_idempotent_and_uses_knowledge_acceptance(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    required = (MINIO_TESTS_REQUIRED, VECTOR_TESTS_REQUIRED, ELASTICSEARCH_TESTS_REQUIRED)
    if any(os.getenv(name) != "1" for name in required):
        pytest.skip(f"Set {', '.join(required)}=1 to run SEC filing import tests")

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
                        email="sec-import@example.test",
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
                        name="SEC Import Workspace",
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

            scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
            knowledge_service = KnowledgeApplicationService(
                repository=SqlAlchemyKnowledgeRepository(session_factory),
                transaction_factory=SqlAlchemyKnowledgeAcceptanceTransactionFactory(
                    session_factory
                ),
                object_store=store,
                bucket=bucket,
            )
            jobs = create_job_resources(
                migrated_postgres_probe.settings,
                session_factory,
            ).application_service
            outbox = SqlAlchemyOutboxTransactionFactory(session_factory)

            async def execute_next(expected_job_id: UUID) -> None:
                async with outbox() as writer:
                    claimed = await writer.claim_job_dispatches(
                        ClaimOutboxCommand(
                            dispatcher_id="sec-filing-import-integration",
                            batch_size=1,
                            claim_seconds=60,
                        )
                    )
                assert len(claimed) == 1
                assert claimed[0].message.job_id == expected_job_id
                async with outbox() as writer:
                    assert await writer.mark_published(claimed[0].proof) is True
                async with httpx2.AsyncClient(trust_env=False) as internal_client:
                    ingestion = create_ingestion_resources(
                        migrated_postgres_probe.settings,
                        session_factory,
                        jobs,
                        store,
                        internal_client,
                    )
                    handlers: dict[str, JobHandler] = {
                        KNOWLEDGE_INGESTION_TASK_NAME: KnowledgeIngestionJobHandler(
                            ingestion.service
                        ),
                        KNOWLEDGE_DELETION_TASK_NAME: KnowledgeDeletionJobHandler(
                            ingestion.deletion_service
                        ),
                    }
                    runtime = JobExecutionRuntime(
                        jobs=jobs,
                        handlers=FixedJobHandlerRegistry(handlers),
                        worker_id="sec-filing-import-integration",
                        heartbeat_seconds=0.25,
                    )
                    disposition = await runtime.execute(claimed[0].message)
                    if disposition is not JobExecutionDisposition.SUCCEEDED:
                        async with session_factory() as session:
                            failed_job = await session.get(Job, expected_job_id)
                            completed_stages = tuple(
                                (
                                    await session.scalars(
                                        select(IngestionCheckpointRecord.stage)
                                        .where(
                                            IngestionCheckpointRecord.ingestion_job_id
                                            == expected_job_id
                                        )
                                        .order_by(IngestionCheckpointRecord.stage_sequence)
                                    )
                                ).all()
                            )
                        failure_code = (
                            failed_job.last_error_code if failed_job is not None else "missing"
                        )
                        raise AssertionError(
                            "SEC filing worker returned "
                            f"{disposition}; error={failure_code}; stages={completed_stages}"
                        )
                    assert (
                        await runtime.execute(claimed[0].message) is JobExecutionDisposition.NO_OP
                    )

            knowledge_base = await knowledge_service.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name="SEC filings",
                    description=None,
                    trace_id=TraceId("sec-import-integration"),
                ),
            )
            filing_repository = SqlAlchemySecFilingRepository(
                session_factory,
                object_bucket=bucket,
            )
            submissions = submission_set(now)
            selection = FilingSelectionScope(
                cik=CIK,
                allowed_forms=(SecFilingForm.TEN_K,),
                report_period_start=date(2023, 1, 1),
                report_period_end=date(2023, 12, 31),
                as_of=now,
                amendment_policy=SecAmendmentPolicy.AS_FILED,
            )
            await filing_repository.replace_submission_set(
                submissions,
                object_keys={submissions.current.source_version: "integration/submissions.json"},
                scope=selection,
            )
            content_repository = SqlAlchemySecFilingContentRepository(
                session_factory,
                object_bucket=bucket,
            )
            canonical = await content_repository.get_canonical_filing(ACCESSION)
            import_service = SecFilingImportService(
                repository=content_repository,
                archive_source=FrozenSecFilingArchiveAdapter(archive(canonical, now)),
                snapshot_store=MinioSecFilingDocumentSnapshotStore(store, bucket=bucket),
                knowledge_service=knowledge_service,
                clock=lambda: now,
            )

            first = await import_service.import_filing(
                scope,
                accession=ACCESSION,
                knowledge_base_id=knowledge_base.id,
                as_of=now,
                trace_id=TraceId("sec-import-integration"),
            )
            repeated = await import_service.import_filing(
                scope,
                accession=ACCESSION,
                knowledge_base_id=knowledge_base.id,
                as_of=now,
                trace_id=TraceId("sec-import-integration"),
            )

            assert first == repeated
            assert first.status is SecFilingImportStatus.QUEUED
            async with session_factory() as session:
                models = (
                    SecFilingDocumentRecord,
                    SecSourceSnapshotRecord,
                    WorkspaceSecImportRecord,
                    FileObject,
                    DocumentRecord,
                    DocumentVersionRecord,
                    Job,
                    OutboxEvent,
                )
                counts = [
                    int(await session.scalar(select(func.count(model.id))) or 0) for model in models
                ]
                snapshots = (await session.scalars(select(SecSourceSnapshotRecord))).all()
                files = (await session.scalars(select(FileObject))).all()
            assert counts == [4, 4, 1, 1, 1, 1, 1, 1]
            assert {snapshot.content_type for snapshot in snapshots} == {
                "text/plain",
                "text/html",
                "application/xml",
                "text/xml",
            }
            owned_keys.update(snapshot.object_key for snapshot in snapshots)
            owned_keys.update(file.staging_object_key for file in files)
            owned_keys.update(file.object_key for file in files if file.object_key is not None)

            await execute_next(first.ingestion_job_id)
            async with httpx2.AsyncClient(trust_env=False) as internal_client:
                content_service = SecFilingContentService(
                    repository=content_repository,
                    dense_index=MilvusDenseIndex(
                        client=internal_client,
                        endpoint=migrated_postgres_probe.settings.milvus_endpoint,
                        token=(
                            None
                            if migrated_postgres_probe.settings.milvus_token is None
                            else (migrated_postgres_probe.settings.milvus_token.get_secret_value())
                        ),
                        collection=MILVUS_COLLECTION,
                        timeout_seconds=(
                            migrated_postgres_probe.settings.knowledge_index_timeout_seconds
                        ),
                    ),
                )
                result = await content_service.search_imported(
                    scope,
                    knowledge_base_ids=(knowledge_base.id,),
                    accession=ACCESSION,
                    as_of=now,
                    query="net sales increased",
                )
                assert result.status is SecFilingContentStatus.OK
                assert result.retrieval_profile_version == "dense-v1"
                assert result.hits
                hit = result.hits[0]
                assert hit.snapshot_id == first.primary_snapshot_id
                assert hit.source_url == sec_primary_document_url(
                    CIK,
                    ACCESSION,
                    "aapl-20230930.htm",
                )
                section = await content_service.read_imported_section(
                    scope,
                    accession=ACCESSION,
                    as_of=now,
                    knowledge_base_ids=(knowledge_base.id,),
                    document_version_id=hit.document_version_id,
                    chunk_id=hit.chunk_id,
                )
                assert section.snapshot_id == hit.snapshot_id
                assert section.content_sha256 == hit.content_sha256
                assert "Net sales increased" in section.text

                unauthorized = await content_service.search_imported(
                    scope,
                    knowledge_base_ids=(uuid4(),),
                    accession=ACCESSION,
                    as_of=now,
                    query="net sales",
                )
                assert unauthorized.status is SecFilingContentStatus.PERMISSION_DENIED
                cutoff = await content_service.search_imported(
                    scope,
                    knowledge_base_ids=(knowledge_base.id,),
                    accession=ACCESSION,
                    as_of=ACCEPTED.replace(day=2),
                    query="net sales",
                )
                assert cutoff.status is SecFilingContentStatus.NO_RESULT

            original_archive = archive(canonical, now)
            original_primary = original_archive.document(SecFilingDocumentKind.PRIMARY_DOCUMENT)
            changed_body = b"<html><h1>Changed source identity</h1></html>"
            changed_primary = replace(
                original_primary,
                source_version=f"sec-filing-primary-{sha256_hex(changed_body)[:24]}",
                content_sha256=sha256_hex(changed_body),
                byte_size=len(changed_body),
                body=changed_body,
            )
            changed_archive = replace(
                original_archive,
                documents=tuple(
                    changed_primary
                    if source.kind is SecFilingDocumentKind.PRIMARY_DOCUMENT
                    else source
                    for source in original_archive.documents
                ),
            )
            changed_service = replace(
                import_service,
                archive_source=FrozenSecFilingArchiveAdapter(changed_archive),
            )
            with pytest.raises(SecFilingContentError) as caught:
                await changed_service.import_filing(
                    scope,
                    accession=ACCESSION,
                    knowledge_base_id=knowledge_base.id,
                    as_of=now,
                    trace_id=TraceId("sec-import-anomaly"),
                )
            assert caught.value.code is SecSourceErrorCode.SNAPSHOT_ANOMALY

            async with session_factory() as session:
                final_snapshots = (await session.scalars(select(SecSourceSnapshotRecord))).all()
                primary_document = await session.scalar(
                    select(SecFilingDocumentRecord).where(
                        SecFilingDocumentRecord.document_kind
                        == SecFilingDocumentKind.PRIMARY_DOCUMENT.value
                    )
                )
                import_count = await session.scalar(select(func.count(WorkspaceSecImportRecord.id)))
            assert len(final_snapshots) == 5
            assert (
                sum(
                    snapshot.status == SecFilingSnapshotStatus.QUARANTINED.value
                    for snapshot in final_snapshots
                )
                == 1
            )
            assert primary_document is not None
            active_snapshot = next(
                snapshot
                for snapshot in final_snapshots
                if snapshot.id == primary_document.current_snapshot_id
            )
            assert active_snapshot.status == SecFilingSnapshotStatus.ACTIVE.value
            assert import_count == 1
            owned_keys.update(snapshot.object_key for snapshot in final_snapshots)

            detail = await knowledge_service.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=first.document_id,
            )
            deletion = await knowledge_service.delete_document(
                scope,
                DeleteDocument(
                    knowledge_base_id=knowledge_base.id,
                    document_id=first.document_id,
                    expected_revision=detail.document.revision,
                    trace_id=TraceId("sec-import-cleanup"),
                ),
            )
            await execute_next(deletion.job_id)
            async with session_factory() as session:
                deleted = await session.get(DocumentRecord, first.document_id)
            assert deleted is not None
            assert deleted.status is DocumentStatus.DELETED
        finally:
            for key in owned_keys:
                if not key.startswith(
                    (
                        f"sec/filings/{CIK}/{ACCESSION.replace('-', '')}/",
                        f"staging/{WORKSPACE_ID}/knowledge/",
                        f"ready/{WORKSPACE_ID}/knowledge/",
                    )
                ):
                    raise RuntimeError("Refusing to remove an object outside the SEC import test")
                with suppress(FileObjectStoreError):
                    await store.remove(bucket=bucket, object_key=key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
