"""Prove source-typed SEC XBRL facts against PostgreSQL and private MinIO bytes."""

import asyncio
import os
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

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
    MinioSecXbrlSnapshotStore,
)
from industry_platform.modules.disclosures.adapters.xbrl import FrozenSecCompanyFactsAdapter
from industry_platform.modules.disclosures.adapters.xbrl_sqlalchemy import (
    SqlAlchemySecXbrlRepository,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingForm,
    SecXbrlFactQuery,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    sec_companyfacts_url,
    sec_xbrl_source_version,
    sha256_hex,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingImportService
from industry_platform.modules.disclosures.models import (
    SecSourceSnapshotRecord,
    SecXbrlContextRecord,
    SecXbrlFactRecord,
    SecXbrlSourceRecord,
)
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
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
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.domain import CreateKnowledgeBase, DocumentVersionStatus
from industry_platform.modules.knowledge.models import DocumentVersionRecord
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_sec_filing_import_postgres_minio import (
    ACCEPTED,
    ACCESSION,
    CIK,
    archive,
    submission_set,
)

MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
USER_ID = UUID("31313131-3131-4131-8131-313131313131")
WORKSPACE_ID = UUID("41414141-4141-4141-8141-414141414141")


def _raw_xbrl_body() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2023"
      xmlns:aapl="http://www.apple.com/20230930"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:dei="http://xbrl.sec.gov/dei/2023"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <context id="D2023">
    <entity>
      <identifier scheme="https://www.sec.gov/CIK">0000320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="dei:LegalEntityAxis">
          aapl:AppleIncMember
        </xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2022-09-25</startDate><endDate>2023-09-30</endDate></period>
  </context>
  <unit id="USD"><measure>iso4217:USD</measure></unit>
  <us-gaap:Revenue contextRef="D2023" unitRef="USD" decimals="-6">100</us-gaap:Revenue>
  <aapl:CustomerContractAsset contextRef="D2023" unitRef="USD" decimals="-6">
    25
  </aapl:CustomerContractAsset>
</xbrl>"""


def _companyfacts_source(now: datetime) -> SecXbrlSourceSnapshot:
    body = (
        b'{"cik":320193,"facts":{"us-gaap":{"Revenue":{"units":{"USD":['
        b'{"accn":"0000320193-23-000106","form":"10-K","filed":"2023-11-03",'
        b'"start":"2022-09-25","end":"2023-09-30","val":100}]}}}}}'
    )
    content_hash = sha256_hex(body)
    return SecXbrlSourceSnapshot(
        source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
        cik=CIK,
        source_url=sec_companyfacts_url(CIK),
        source_version=sec_xbrl_source_version(
            SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
            content_hash,
        ),
        content_type="application/json",
        content_sha256=content_hash,
        byte_size=len(body),
        retrieved_at=now,
        source_available_at=ACCEPTED,
        body=body,
    )


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_sec_xbrl_sync_is_idempotent_authorized_and_source_typed(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run SEC XBRL integration tests")

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
                        email="sec-xbrl@example.test",
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
                        name="SEC XBRL Workspace",
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
            knowledge_base = await knowledge_service.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name="SEC XBRL facts",
                    description=None,
                    trace_id=TraceId("sec-xbrl-integration"),
                ),
            )
            filing_repository = SqlAlchemySecFilingRepository(
                session_factory,
                object_bucket=bucket,
            )
            submissions = submission_set(now)
            await filing_repository.replace_submission_set(
                submissions,
                object_keys={submissions.current.source_version: "integration/submissions.json"},
                scope=FilingSelectionScope(
                    cik=CIK,
                    allowed_forms=(SecFilingForm.TEN_K,),
                    report_period_start=date(2023, 1, 1),
                    report_period_end=date(2023, 12, 31),
                    as_of=now,
                    amendment_policy=SecAmendmentPolicy.AS_FILED,
                ),
            )
            content_repository = SqlAlchemySecFilingContentRepository(
                session_factory,
                object_bucket=bucket,
            )
            canonical = await content_repository.get_canonical_filing(ACCESSION)
            frozen_archive = archive(canonical, now)
            original_instance = frozen_archive.document(SecFilingDocumentKind.XBRL_INSTANCE)
            raw_body = _raw_xbrl_body()
            raw_archive = replace(
                frozen_archive,
                documents=tuple(
                    replace(
                        source,
                        source_version=f"sec-filing-instance-{sha256_hex(raw_body)[:24]}",
                        content_sha256=sha256_hex(raw_body),
                        byte_size=len(raw_body),
                        body=raw_body,
                    )
                    if source is original_instance
                    else source
                    for source in frozen_archive.documents
                ),
            )
            import_service = SecFilingImportService(
                repository=content_repository,
                archive_source=FrozenSecFilingArchiveAdapter(raw_archive),
                snapshot_store=MinioSecFilingDocumentSnapshotStore(store, bucket=bucket),
                knowledge_service=knowledge_service,
                clock=lambda: now,
            )
            imported = await import_service.import_filing(
                scope,
                accession=ACCESSION,
                knowledge_base_id=knowledge_base.id,
                as_of=now,
                trace_id=TraceId("sec-xbrl-integration"),
            )
            async with session_factory.begin() as session:
                version = await session.get(
                    DocumentVersionRecord,
                    imported.document_version_id,
                    with_for_update=True,
                )
                if version is None:
                    raise AssertionError("Accepted SEC import version disappeared")
                version.status = DocumentVersionStatus.READY
                version.ready_at = now

            service = SecXbrlService(
                repository=SqlAlchemySecXbrlRepository(
                    session_factory,
                    object_bucket=bucket,
                ),
                filing_repository=content_repository,
                companyfacts_source=FrozenSecCompanyFactsAdapter(_companyfacts_source(now)),
                snapshot_store=MinioSecXbrlSnapshotStore(store, bucket=bucket),
                clock=lambda: now,
            )
            first = await service.sync(
                scope,
                accession=ACCESSION,
                knowledge_base_id=knowledge_base.id,
            )
            repeated = await service.sync(
                scope,
                accession=ACCESSION,
                knowledge_base_id=knowledge_base.id,
            )
            assert first == repeated
            assert (first.source_count, first.context_count, first.fact_count) == (3, 1, 3)

            aggregate = await service.get_imported_facts(
                scope,
                knowledge_base_ids=(knowledge_base.id,),
                accession=ACCESSION,
                as_of=now,
                query=SecXbrlFactQuery(
                    taxonomy="us-gaap",
                    concept="Revenue",
                    source_kinds=(SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,),
                ),
            )
            assert aggregate.status is SecFilingContentStatus.OK
            assert aggregate.facts[0].context_id is None
            assert aggregate.facts[0].unavailable_fields == (
                "context_id",
                "decimals",
                "dimensions",
                "scale",
            )

            raw = await service.get_imported_facts(
                scope,
                knowledge_base_ids=(knowledge_base.id,),
                accession=ACCESSION,
                as_of=now,
                query=SecXbrlFactQuery(
                    taxonomy="aapl",
                    concept="CustomerContractAsset",
                    period_kind=SecXbrlPeriodKind.DURATION,
                    source_kinds=(SecXbrlSourceKind.RAW_INSTANCE,),
                ),
            )
            assert raw.status is SecFilingContentStatus.OK
            assert raw.facts[0].context_id == "D2023"
            assert raw.facts[0].dimensions == (("dei:LegalEntityAxis", "aapl:AppleIncMember"),)
            assert raw.facts[0].decimals == "-6"
            assert raw.facts[0].is_custom is True

            unauthorized = await service.get_imported_facts(
                WorkspaceScope(uuid4(), USER_ID, "owner"),
                knowledge_base_ids=(knowledge_base.id,),
                accession=ACCESSION,
                as_of=now,
                query=SecXbrlFactQuery(),
            )
            assert unauthorized.status is SecFilingContentStatus.PERMISSION_DENIED
            cutoff = await service.get_imported_facts(
                scope,
                knowledge_base_ids=(knowledge_base.id,),
                accession=ACCESSION,
                as_of=ACCEPTED.replace(day=2),
                query=SecXbrlFactQuery(),
            )
            assert cutoff.status is SecFilingContentStatus.NO_RESULT

            async with session_factory() as session:
                counts = [
                    int(await session.scalar(select(func.count(model.id))) or 0)
                    for model in (
                        SecXbrlSourceRecord,
                        SecXbrlContextRecord,
                        SecXbrlFactRecord,
                    )
                ]
                snapshots = (await session.scalars(select(SecSourceSnapshotRecord))).all()
                files = (await session.scalars(select(FileObject))).all()
                aggregate_sources = (
                    await session.scalars(
                        select(SecXbrlSourceRecord).where(
                            SecXbrlSourceRecord.source_kind
                            == SecXbrlSourceKind.COMPANYFACTS_AGGREGATE.value
                        )
                    )
                ).all()
            assert counts == [3, 1, 3]
            assert len(aggregate_sources) == 1
            assert aggregate_sources[0].object_key is not None
            owned_keys.update(snapshot.object_key for snapshot in snapshots)
            owned_keys.update(file.staging_object_key for file in files)
            owned_keys.add(aggregate_sources[0].object_key)
        finally:
            for key in owned_keys:
                if not key.startswith(
                    (
                        f"sec/filings/{CIK}/{ACCESSION.replace('-', '')}/",
                        f"sec/xbrl/companyfacts/{CIK}/",
                        f"staging/{WORKSPACE_ID}/knowledge/",
                    )
                ):
                    raise RuntimeError("Refusing to remove an object outside the SEC XBRL test")
                with suppress(FileObjectStoreError):
                    await store.remove(bucket=bucket, object_key=key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
