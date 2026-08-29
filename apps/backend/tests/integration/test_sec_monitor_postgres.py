"""Prove SEC Monitor scheduling, watermark fencing, and retry idempotency in PostgreSQL."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.disclosures.adapters.monitor_sqlalchemy import (
    SqlAlchemySecMonitorRepository,
    sec_monitor_occurrence_observer,
)
from industry_platform.modules.disclosures.models import (
    SecDisclosureCaseEvidenceRecord,
    SecDisclosureCaseRecord,
    SecDisclosureMonitorRecord,
    SecDisclosureMonitorRuleRecord,
    SecDisclosureMonitorRunRecord,
    SecDisclosureMonitorWatermarkRecord,
    SecFilerRecord,
    SecFilingRecord,
    SecSubmissionSourceRecord,
)
from industry_platform.modules.disclosures.monitor import (
    SEC_MONITOR_DIFF_VERSION,
    SEC_MONITOR_RULE_SET_VERSION,
    SEC_MONITOR_TASK_NAME,
    SecMonitorAnalysis,
    SecMonitorApplicationService,
    SecMonitorExecutionRequest,
    SecMonitorRunStatus,
    SecMonitorStatus,
)
from industry_platform.modules.evidence.domain import (
    EVIDENCE_NORMALIZER_VERSION,
    AuthorizationSnapshot,
    EvidenceKind,
    EvidenceStatus,
    SecXbrlFactLocatorV1,
    parse_evidence_locator,
)
from industry_platform.modules.evidence.models import EvidenceRecord
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    ManualScheduleTriggerCommand,
    ScheduleDefinition,
    ScheduleMisfirePolicy,
)
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.domain import KnowledgeBaseStatus
from industry_platform.modules.knowledge.models import KnowledgeBaseRecord
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


@dataclass(slots=True)
class CompleteNoFindingAnalyzer:
    calls: int = 0

    async def analyze(self, request: SecMonitorExecutionRequest) -> SecMonitorAnalysis:
        self.calls += 1
        return SecMonitorAnalysis(
            coverage_version="sec-filings-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            accepted_at=request.watermark.accepted_at,
            accession=request.watermark.accession,
            findings=(),
        )


def test_monitor_occurrence_and_completed_job_retry_advance_watermark_once(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        owner_id = uuid4()
        workspace_id = uuid4()
        knowledge_base_id = uuid4()
        filer_id = uuid4()
        monitor_id = uuid4()
        rule_id = uuid4()
        initial_watermark_id = uuid4()
        analyzer = CompleteNoFindingAnalyzer()
        jobs = create_job_resources(
            migrated_postgres_probe.settings,
            session_factory,
            occurrence_observer=sec_monitor_occurrence_observer,
        )

        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=owner_id,
                            email=f"monitor-owner-{owner_id}@example.test",
                            password_hash=str(owner_id),
                            status=UserStatus.ACTIVE,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="SEC Monitor",
                            created_by_user_id=owner_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceMembership(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        user_id=owner_id,
                        role=WorkspaceRole.OWNER,
                    )
                )
                await session.flush()
                session.add_all(
                    (
                        KnowledgeBaseRecord(
                            id=knowledge_base_id,
                            workspace_id=workspace_id,
                            created_by_user_id=owner_id,
                            name="SEC filings",
                            description=None,
                            status=KnowledgeBaseStatus.ACTIVE,
                            revision=1,
                            deleted_at=None,
                        ),
                        SecFilerRecord(
                            id=filer_id,
                            cik="0000320193",
                            canonical_name="Apple Inc.",
                            normalized_name="apple inc",
                            source_kind="company_tickers",
                            source_version="sec-company-tickers-v1",
                            source_url="https://www.sec.gov/files/company_tickers.json",
                            source_content_sha256=b"a" * 32,
                            source_observed_at=datetime.now(UTC),
                        ),
                    )
                )

            ensured = await jobs.schedule_service.ensure_schedule(
                ScheduleDefinition(
                    scope=ExecutionScope(workspace_id=workspace_id),
                    name=f"sec-monitor-{monitor_id}",
                    task_name=SEC_MONITOR_TASK_NAME,
                    cron_expression="0 3 * * *",
                    timezone_name="Asia/Shanghai",
                    payload={"schema_version": 1, "monitor_id": str(monitor_id)},
                    queue_name="default",
                    max_attempts=3,
                    soft_time_limit_seconds=300,
                    hard_time_limit_seconds=360,
                    misfire_policy=ScheduleMisfirePolicy.COALESCE_LATEST,
                )
            )

            async with session_factory.begin() as session:
                monitor = SecDisclosureMonitorRecord(
                    id=monitor_id,
                    workspace_id=workspace_id,
                    owner_user_id=owner_id,
                    filer_id=filer_id,
                    knowledge_base_id=knowledge_base_id,
                    schedule_id=ensured.schedule_id,
                    allowed_forms=["10-K", "10-K/A"],
                    rule_set_version=SEC_MONITOR_RULE_SET_VERSION,
                    diff_version=SEC_MONITOR_DIFF_VERSION,
                    timezone_name="Asia/Shanghai",
                    status=SecMonitorStatus.ACTIVE.value,
                    current_watermark_id=None,
                    created_from_approval_id=None,
                    revision=1,
                )
                session.add(monitor)
                await session.flush()
                session.add_all(
                    (
                        SecDisclosureMonitorRuleRecord(
                            id=rule_id,
                            monitor_id=monitor_id,
                            workspace_id=workspace_id,
                            ordinal=1,
                            kind="new_filing",
                            rule_version=SEC_MONITOR_RULE_SET_VERSION,
                            section_query="management discussion and analysis",
                            taxonomy=None,
                            concept=None,
                            unit=None,
                            threshold=None,
                            comparator=None,
                        ),
                        SecDisclosureMonitorWatermarkRecord(
                            id=initial_watermark_id,
                            monitor_id=monitor_id,
                            workspace_id=workspace_id,
                            revision=1,
                            coverage_version=("sec-filings-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                            accepted_at=None,
                            accession=None,
                            monitor_run_id=None,
                        ),
                    )
                )
                await session.flush()
                monitor.current_watermark_id = initial_watermark_id

            triggered = await jobs.schedule_service.trigger_manual(
                ManualScheduleTriggerCommand(
                    schedule_id=ensured.schedule_id,
                    trigger_id=uuid4(),
                )
            )
            service = SecMonitorApplicationService(
                repository=SqlAlchemySecMonitorRepository(session_factory),
                analyzer=analyzer,
            )
            first = await service.execute_job(
                job_id=triggered.job_id,
                workspace_id=workspace_id,
            )
            replay = await service.execute_job(
                job_id=triggered.job_id,
                workspace_id=workspace_id,
            )

            assert replay == first
            assert analyzer.calls == 1
            async with session_factory() as session:
                persisted_monitor = await session.get(SecDisclosureMonitorRecord, monitor_id)
                run = await session.get(
                    SecDisclosureMonitorRunRecord,
                    triggered.occurrence_id,
                )
                watermark_count = await session.scalar(
                    select(func.count())
                    .select_from(SecDisclosureMonitorWatermarkRecord)
                    .where(SecDisclosureMonitorWatermarkRecord.monitor_id == monitor_id)
                )
                case_count = await session.scalar(
                    select(func.count())
                    .select_from(SecDisclosureCaseRecord)
                    .where(SecDisclosureCaseRecord.monitor_id == monitor_id)
                )
            assert persisted_monitor is not None
            assert run is not None
            assert persisted_monitor.current_watermark_id == first.watermark_id
            assert run.status == SecMonitorRunStatus.SUCCEEDED.value
            assert run.result_watermark_id == first.watermark_id
            assert watermark_count == 2
            assert case_count == 0

            source_id = uuid4()
            baseline_filing_id = uuid4()
            target_filing_id = uuid4()
            case_id = uuid4()
            baseline_accession = "0000320193-23-000106"
            target_accession = "0000320193-23-000120"
            captured_at = datetime.now(UTC)
            async with session_factory.begin() as session:
                session.add(
                    SecSubmissionSourceRecord(
                        id=source_id,
                        cik="0000320193",
                        source_kind="submissions_current",
                        source_name="CIK0000320193.json",
                        source_url="https://data.sec.gov/submissions/CIK0000320193.json",
                        source_version="sec-submissions-current-v1",
                        content_sha256=b"s" * 32,
                        object_bucket="test-private",
                        object_key="sec/submissions/apple.json",
                        retrieved_at=captured_at,
                        source_available_at=captured_at - timedelta(minutes=1),
                        filing_from=None,
                        filing_to=None,
                    )
                )
                await session.flush()
                session.add_all(
                    (
                        SecFilingRecord(
                            id=baseline_filing_id,
                            source_id=source_id,
                            cik="0000320193",
                            accession=baseline_accession,
                            form="10-K",
                            report_date=date(2023, 9, 30),
                            filed_date=date(2023, 11, 3),
                            accepted_at=datetime(2023, 11, 3, 18, tzinfo=UTC),
                            public_available_at=datetime(2023, 11, 3, 18, tzinfo=UTC),
                            visibility_policy_version="sec-acceptance-source-v1",
                            primary_document="aapl-20230930.htm",
                            amendment_relation_status="not_amendment",
                            base_accession=None,
                        ),
                        SecFilingRecord(
                            id=target_filing_id,
                            source_id=source_id,
                            cik="0000320193",
                            accession=target_accession,
                            form="10-K/A",
                            report_date=date(2023, 9, 30),
                            filed_date=date(2023, 11, 10),
                            accepted_at=datetime(2023, 11, 10, 18, tzinfo=UTC),
                            public_available_at=datetime(2023, 11, 10, 18, tzinfo=UTC),
                            visibility_policy_version="sec-acceptance-source-v1",
                            primary_document="aapl-20230930x10ka.htm",
                            amendment_relation_status="resolved",
                            base_accession=baseline_accession,
                        ),
                    )
                )
                await session.flush()
                session.add(
                    SecDisclosureCaseRecord(
                        id=case_id,
                        workspace_id=workspace_id,
                        monitor_id=monitor_id,
                        monitor_run_id=triggered.occurrence_id,
                        rule_id=rule_id,
                        trigger_kind="amendment",
                        rule_version=SEC_MONITOR_RULE_SET_VERSION,
                        source_coverage_version=("sec-filings-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                        baseline_filing_id=baseline_filing_id,
                        target_filing_id=target_filing_id,
                        baseline_accession=baseline_accession,
                        target_accession=target_accession,
                        diff_version=SEC_MONITOR_DIFF_VERSION,
                        diff_payload={"version": SEC_MONITOR_DIFF_VERSION},
                        diff_sha256=hashlib.sha256(b"monitor-diff").digest(),
                        verification_status="verified",
                        notification_status="pending",
                        idempotency_key="c" * 64,
                        created_at=captured_at,
                        updated_at=captured_at,
                    )
                )
                await session.flush()
                evidence_ids = (uuid4(), uuid4())
                accessions = (baseline_accession, target_accession)
                for ordinal, (evidence_id, accession) in enumerate(
                    zip(evidence_ids, accessions, strict=True),
                    start=1,
                ):
                    locator = SecXbrlFactLocatorV1(
                        cik="0000320193",
                        accession=accession,
                        form="10-K" if ordinal == 1 else "10-K/A",
                        report_period="2023-09-30",
                        as_of=captured_at.isoformat(),
                        fact_id=uuid4(),
                        filing_id=(baseline_filing_id if ordinal == 1 else target_filing_id),
                        source_id=uuid4(),
                        source_snapshot_id=None,
                        source_kind="companyfacts_aggregate",
                        taxonomy="us-gaap",
                        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
                        unit="USD",
                        period_kind="duration",
                        instant=None,
                        start_date="2022-09-25",
                        end_date="2023-09-30",
                        context_id=None,
                        dimensions={},
                        decimals=None,
                        scale=None,
                        source_url=(
                            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
                        ),
                        source_version=f"sec-companyfacts-{ordinal}",
                        source_content_sha256=("d" if ordinal == 1 else "e") * 64,
                        content_sha256=("f" if ordinal == 1 else "a") * 64,
                        source_available_at=captured_at.isoformat(),
                        retrieved_at=captured_at.isoformat(),
                    )
                    authorization = AuthorizationSnapshot(
                        workspace_id=workspace_id,
                        actor_user_id=owner_id,
                        role="owner",
                        action="evidence.normalize",
                        captured_at=captured_at,
                    )
                    session.add(
                        EvidenceRecord(
                            id=evidence_id,
                            workspace_id=workspace_id,
                            schema_version=1,
                            kind=EvidenceKind.FILING,
                            title=f"SEC fact {ordinal}",
                            canonical_url=(
                                "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
                            ),
                            locator_type=locator.locator_type,
                            locator=dict(locator.to_mapping()),
                            excerpt=f'{{"accession":"{accession}"}}',
                            content_sha256=("f" if ordinal == 1 else "a") * 64,
                            source_published_at=captured_at,
                            retrieved_at=captured_at,
                            license_or_terms="Official SEC public filing data.",
                            status=EvidenceStatus.ACTIVE,
                            revision=1,
                            invalidated_at=None,
                            invalidation_reason=None,
                            origin_run_id=None,
                            origin_step_id=None,
                            origin_tool_call_id=None,
                            origin_case_id=case_id,
                            origin_observation_id=uuid4(),
                            origin_source_ordinal=ordinal,
                            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
                            authorization_snapshot=dict(authorization.to_mapping()),
                            source_resource_version=f"sec-companyfacts-{ordinal}:fact",
                            source_item_id=None,
                            query_run_id=None,
                            document_version_id=None,
                            chunk_id=None,
                            deduplication_key=("1" if ordinal == 1 else "2") * 64,
                            created_at=captured_at,
                            updated_at=captured_at,
                        )
                    )
                await session.flush()
                session.add_all(
                    tuple(
                        SecDisclosureCaseEvidenceRecord(
                            workspace_id=workspace_id,
                            case_id=case_id,
                            evidence_id=evidence_id,
                            side=side,
                            created_at=captured_at,
                        )
                        for side, evidence_id in zip(
                            ("baseline", "target"), evidence_ids, strict=True
                        )
                    )
                )

            async with session_factory() as session:
                linked = tuple(
                    (
                        await session.execute(
                            select(SecDisclosureCaseEvidenceRecord, EvidenceRecord)
                            .join(
                                EvidenceRecord,
                                EvidenceRecord.id == SecDisclosureCaseEvidenceRecord.evidence_id,
                            )
                            .where(SecDisclosureCaseEvidenceRecord.case_id == case_id)
                            .order_by(SecDisclosureCaseEvidenceRecord.side.asc())
                        )
                    ).all()
                )
            assert tuple(link.side for link, _evidence in linked) == ("baseline", "target")
            locators = tuple(parse_evidence_locator(evidence.locator) for _link, evidence in linked)
            assert all(isinstance(locator, SecXbrlFactLocatorV1) for locator in locators)
            assert {
                locator.accession
                for locator in locators
                if isinstance(locator, SecXbrlFactLocatorV1)
            } == {baseline_accession, target_accession}
            assert all(evidence.origin_case_id == case_id for _link, evidence in linked)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
