"""Real SEC fixture ingestion, Dense retrieval, and calculation proof."""

import asyncio
import hashlib
import os
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.financial_verification.tool import (
    FinanceCalculateInput,
    FinanceOperandPayload,
)
from industry_platform.modules.identity.domain import AuthenticatedWorkspace, TraceId
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
from industry_platform.modules.jobs.domain import ClaimOutboxCommand
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_DELETION_TASK_NAME,
    KNOWLEDGE_INGESTION_TASK_NAME,
    CompleteKnowledgeUpload,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteDocument,
    DocumentStatus,
)
from industry_platform.modules.knowledge.models import DocumentRecord
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.domain import KnowledgeSearchStatus
from industry_platform.modules.retrieval.resources import create_retrieval_resources
from industry_platform.modules.retrieval.tool import KnowledgeSearchInput
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = (
    REPOSITORY_ROOT / "evals" / "fixtures" / "sec" / "sec-fixture-v1" / "apple-2023-10-k.md"
)
MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
VECTOR_TESTS_REQUIRED = "VECTOR_TESTS_REQUIRED"
ELASTICSEARCH_TESTS_REQUIRED = "ELASTICSEARCH_TESTS_REQUIRED"


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_sec_fixture_runs_through_ingestion_dense_reload_and_calculator(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    required = (MINIO_TESTS_REQUIRED, VECTOR_TESTS_REQUIRED, ELASTICSEARCH_TESTS_REQUIRED)
    if any(os.getenv(name) != "1" for name in required):
        pytest.skip(f"Set {', '.join(required)}=1 to run the SEC fixture integration test")

    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        store = create_private_file_object_store(settings)
        bucket = settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("MinIO test configuration is incomplete")

        workspace_id = uuid4()
        user_id = uuid4()
        now = datetime.now(UTC)
        source = FIXTURE_PATH.read_bytes()
        scope = WorkspaceScope(workspace_id, user_id, "owner")
        knowledge = KnowledgeApplicationService(
            repository=SqlAlchemyKnowledgeRepository(session_factory),
            transaction_factory=SqlAlchemyKnowledgeAcceptanceTransactionFactory(session_factory),
            object_store=store,
            bucket=bucket,
        )
        jobs = create_job_resources(settings, session_factory).application_service
        outbox = SqlAlchemyOutboxTransactionFactory(session_factory)

        async def execute_next(expected_job_id: UUID) -> None:
            async with outbox() as writer:
                claimed = await writer.claim_job_dispatches(
                    ClaimOutboxCommand(
                        dispatcher_id="sec-fixture-retrieval-integration",
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
                    settings,
                    session_factory,
                    jobs,
                    store,
                    internal_client,
                )
                handlers: dict[str, JobHandler] = {
                    KNOWLEDGE_INGESTION_TASK_NAME: KnowledgeIngestionJobHandler(ingestion.service),
                    KNOWLEDGE_DELETION_TASK_NAME: KnowledgeDeletionJobHandler(
                        ingestion.deletion_service
                    ),
                }
                runtime = JobExecutionRuntime(
                    jobs=jobs,
                    handlers=FixedJobHandlerRegistry(handlers),
                    worker_id="sec-fixture-retrieval-integration",
                    heartbeat_seconds=0.25,
                )
                assert (
                    await runtime.execute(claimed[0].message) is JobExecutionDisposition.SUCCEEDED
                )
                assert await runtime.execute(claimed[0].message) is JobExecutionDisposition.NO_OP

        try:
            async with session_factory.begin() as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"sec-fixture-{user_id}@example.test",
                        password_hash=str(user_id),
                        status=UserStatus.ACTIVE,
                        password_changed_at=now,
                    )
                )
                await session.flush()
                session.add(
                    Workspace(
                        id=workspace_id,
                        name="SEC Fixture Retrieval",
                        created_by_user_id=user_id,
                        status=WorkspaceStatus.ACTIVE,
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceMembership(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role=WorkspaceRole.OWNER,
                    )
                )

            knowledge_base = await knowledge.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name="Apple SEC Fixture",
                    description="Bounded filing fixture",
                    trace_id=TraceId("sec-fixture-retrieval-integration"),
                ),
            )
            upload = await knowledge.create_upload(
                scope,
                CreateKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    original_name="apple-2023-10-k.md",
                    declared_media_type=AttachmentMediaType.TEXT_MARKDOWN,
                    expected_size=len(source),
                    expected_sha256=hashlib.sha256(source).hexdigest(),
                    trace_id=TraceId("sec-fixture-retrieval-integration"),
                ),
            )
            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                response = await client.post(
                    upload.url,
                    data=upload.fields,
                    files={"file": ("apple-2023-10-k.md", source, "text/markdown")},
                )
            assert response.status_code == 204
            accepted = await knowledge.complete_upload(
                scope,
                CompleteKnowledgeUpload(
                    knowledge_base_id=knowledge_base.id,
                    file_id=upload.file_id,
                    title="Apple 2023 Form 10-K fixture",
                    idempotency_key="repeat-request",
                    trace_id=TraceId("sec-fixture-retrieval-integration"),
                ),
            )
            await execute_next(accepted.version.ingestion_job_id)

            financial_scope = FinancialScope(
                cik="0000320193",
                accession="0000320193-23-000106",
                form=FinancialForm.TEN_K,
                report_period=date(2023, 9, 30),
                as_of=datetime(2023, 11, 3, 12, tzinfo=UTC),
                unit="USD",
                scale=6,
            )
            runtime_context = TrustedRuntimeContext(
                principal=BackgroundRunPrincipal(
                    user_id=user_id,
                    workspaces=(
                        AuthenticatedWorkspace(workspace_id, "SEC Fixture Retrieval", "owner"),
                    ),
                ),
                workspace_scope=scope,
                capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
                budget=RunBudget(
                    schema_version=1,
                    max_steps=12,
                    max_total_tokens=4_096,
                    max_cost_micro_usd=100_000,
                    deadline=now + timedelta(minutes=10),
                ),
                knowledge_base_ids=(knowledge_base.id,),
                financial_scope=financial_scope,
            )

            async with httpx2.AsyncClient(trust_env=False) as internal_client:
                retrieval = create_retrieval_resources(
                    settings,
                    session_factory,
                    internal_client,
                )
                search_output, search_cost = await retrieval.knowledge_search_tool.invoke(
                    KnowledgeSearchInput(query="Total net sales in 2023 and 2022"),
                    runtime_context,
                    idempotency_key=None,
                )
                assert search_cost == 0
                assert search_output.status is KnowledgeSearchStatus.OK
                assert 1 <= len(search_output.items) <= 5
                hit = next(
                    item
                    for item in search_output.items
                    if "383285" in item.excerpt.replace(",", "")
                    and "394328" in item.excerpt.replace(",", "")
                )
                assert hit.accession == financial_scope.accession
                assert hit.document_version_id == accepted.version.id
                assert hit.section == "Item 8. Consolidated Statements of Operations"
                assert hit.page_number == 29

                (
                    calculation_output,
                    calculation_cost,
                ) = await retrieval.finance_calculate_tool.invoke(
                    FinanceCalculateInput(
                        operator="percent_change",
                        operands=[
                            FinanceOperandPayload(
                                value="383285",
                                evidence_ref=str(hit.evidence_ref),
                            ),
                            FinanceOperandPayload(
                                value="394328",
                                evidence_ref=str(hit.evidence_ref),
                            ),
                        ],
                        decimal_places=2,
                        rounding_mode="half_even",
                    ),
                    runtime_context,
                    idempotency_key=None,
                )
                assert calculation_cost == 0
                assert calculation_output.status is KnowledgeSearchStatus.OK
                assert calculation_output.result == "-2.80"
                assert calculation_output.evidence_refs == [hit.evidence_ref, hit.evidence_ref]

                cutoff_output, _cost = await retrieval.knowledge_search_tool.invoke(
                    KnowledgeSearchInput(query="net sales"),
                    replace(
                        runtime_context,
                        financial_scope=replace(
                            financial_scope,
                            as_of=datetime(2023, 11, 2, 18, tzinfo=UTC),
                        ),
                    ),
                    idempotency_key=None,
                )
                assert cutoff_output.status is KnowledgeSearchStatus.NO_RESULT
                assert cutoff_output.items == []

                unauthorized_output, _cost = await retrieval.knowledge_search_tool.invoke(
                    KnowledgeSearchInput(query="net sales"),
                    replace(runtime_context, knowledge_base_ids=(uuid4(),)),
                    idempotency_key=None,
                )
                assert unauthorized_output.status is KnowledgeSearchStatus.PERMISSION_DENIED
                assert unauthorized_output.items == []

            detail = await knowledge.get_document(
                scope,
                knowledge_base_id=knowledge_base.id,
                document_id=accepted.document.id,
            )
            deletion = await knowledge.delete_document(
                scope,
                DeleteDocument(
                    knowledge_base_id=knowledge_base.id,
                    document_id=accepted.document.id,
                    expected_revision=detail.document.revision,
                    trace_id=TraceId("sec-fixture-retrieval-integration"),
                ),
            )
            await execute_next(deletion.job_id)
            async with session_factory() as session:
                deleted = await session.get(DocumentRecord, accepted.document.id)
            assert deleted is not None
            assert deleted.status is DocumentStatus.DELETED
        finally:
            for prefix in (
                f"staging/{workspace_id}/knowledge/",
                f"ready/{workspace_id}/knowledge/",
                f"derived/{workspace_id}/knowledge/",
            ):
                with suppress(FileObjectStoreError):
                    await store.remove_prefix(bucket=bucket, object_prefix=prefix)
            async with session_factory() as session:
                source_files = tuple(
                    (
                        await session.scalars(
                            select(FileObject).where(FileObject.workspace_id == workspace_id)
                        )
                    ).all()
                )
            for source_file in source_files:
                for key in (source_file.staging_object_key, source_file.object_key):
                    if key is not None:
                        with suppress(FileObjectStoreError):
                            await store.remove(bucket=bucket, object_key=key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
