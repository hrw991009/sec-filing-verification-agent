"""Transactional PostgreSQL persistence for SEC disclosure Monitor execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.domain import SecFilingForm
from industry_platform.modules.disclosures.models import (
    SecDisclosureCaseEvidenceRecord,
    SecDisclosureCaseRecord,
    SecDisclosureMonitorRecord,
    SecDisclosureMonitorRuleRecord,
    SecDisclosureMonitorRunRecord,
    SecDisclosureMonitorWatermarkRecord,
    SecFilerRecord,
    SecFilingRecord,
    SecSourceSnapshotRecord,
    SecXbrlFactRecord,
    SecXbrlSourceRecord,
    WorkspaceSecImportRecord,
)
from industry_platform.modules.disclosures.monitor import (
    SEC_MONITOR_TASK_NAME,
    SecCaseNotificationStatus,
    SecCaseVerificationStatus,
    SecMonitorAnalysis,
    SecMonitorDependencyError,
    SecMonitorEvidencePair,
    SecMonitorExecutionRequest,
    SecMonitorExecutionResult,
    SecMonitorFinding,
    SecMonitorRule,
    SecMonitorRuleKind,
    SecMonitorRunStatus,
    SecMonitorStateError,
    SecMonitorStatus,
    SecMonitorWatermark,
)
from industry_platform.modules.disclosures.schemas import (
    SecFilingDiffResponse,
    SecXbrlFactResponse,
)
from industry_platform.modules.evidence.domain import (
    EVIDENCE_NORMALIZER_VERSION,
    AuthorizationSnapshot,
    EvidenceKind,
    EvidenceStatus,
    SecFilingTableCellCoordinateV1,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
    canonical_fingerprint,
)
from industry_platform.modules.evidence.models import EvidenceRecord
from industry_platform.modules.financial_verification.domain import sec_xbrl_evidence_ref
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import WorkspaceMembership
from industry_platform.modules.jobs.domain import ScheduleOccurrenceMaterialization
from industry_platform.modules.knowledge.domain import (
    DocumentIndexKind,
    DocumentIndexStatus,
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeBaseStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentRecord,
    DocumentVersionRecord,
    KnowledgeBaseRecord,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


async def sec_monitor_occurrence_observer(
    session: AsyncSession,
    materialization: ScheduleOccurrenceMaterialization,
) -> None:
    """Project a Monitor Run inside the Schedule/Occurrence/Job/Outbox transaction."""

    if materialization.task_name != SEC_MONITOR_TASK_NAME:
        return
    workspace_id = materialization.scope.workspace_id
    if workspace_id is None or materialization.scope.system_scope_key is not None:
        raise ValueError("SEC Monitor schedule must be Workspace-scoped")
    payload = materialization.payload
    if set(payload) != {"schema_version", "monitor_id"} or payload.get("schema_version") != 1:
        raise ValueError("SEC Monitor schedule payload is invalid")
    try:
        monitor_id = UUID(str(payload["monitor_id"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("SEC Monitor schedule payload is invalid") from None
    monitor = await session.scalar(
        select(SecDisclosureMonitorRecord).where(
            SecDisclosureMonitorRecord.id == monitor_id,
            SecDisclosureMonitorRecord.workspace_id == workspace_id,
        )
    )
    if (
        monitor is None
        or monitor.status != SecMonitorStatus.ACTIVE.value
        or monitor.schedule_id != materialization.schedule_id
        or monitor.current_watermark_id is None
    ):
        raise ValueError("SEC Monitor schedule state is invalid")
    await session.flush()
    session.add(
        SecDisclosureMonitorRunRecord(
            id=materialization.occurrence_id,
            workspace_id=workspace_id,
            monitor_id=monitor.id,
            schedule_occurrence_id=materialization.occurrence_id,
            job_id=materialization.job_id,
            source_watermark_id=monitor.current_watermark_id,
            result_watermark_id=None,
            status=SecMonitorRunStatus.QUEUED.value,
            scheduled_for=materialization.scheduled_for,
            window_start=materialization.window_start,
            window_end=materialization.window_end,
            coalesced_count=materialization.coalesced_count,
            trace_id=str(materialization.trace_id),
            completed_at=None,
            created_at=materialization.materialized_at,
            updated_at=materialization.materialized_at,
        )
    )


@dataclass(frozen=True, slots=True)
class SqlAlchemySecMonitorRepository:
    session_factory: AsyncSessionFactory

    async def completed_result(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
    ) -> SecMonitorExecutionResult | None:
        try:
            async with self.session_factory() as session:
                run = await session.scalar(
                    select(SecDisclosureMonitorRunRecord).where(
                        SecDisclosureMonitorRunRecord.job_id == job_id,
                        SecDisclosureMonitorRunRecord.workspace_id == workspace_id,
                    )
                )
                if run is None:
                    raise SecMonitorStateError("monitor_run_not_found")
                if run.status != SecMonitorRunStatus.SUCCEEDED.value:
                    return None
                return await self._completed_result(session, run)
        except SecMonitorStateError:
            raise
        except SQLAlchemyError:
            raise SecMonitorDependencyError("monitor_persistence_unavailable") from None

    async def prepare(self, *, job_id: UUID, workspace_id: UUID) -> SecMonitorExecutionRequest:
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(SecDisclosureMonitorRunRecord)
                    .where(
                        SecDisclosureMonitorRunRecord.job_id == job_id,
                        SecDisclosureMonitorRunRecord.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    raise SecMonitorStateError("monitor_run_not_found")
                monitor = await session.scalar(
                    select(SecDisclosureMonitorRecord)
                    .where(
                        SecDisclosureMonitorRecord.id == run.monitor_id,
                        SecDisclosureMonitorRecord.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
                if (
                    monitor is None
                    or monitor.status != SecMonitorStatus.ACTIVE.value
                    or monitor.current_watermark_id is None
                ):
                    raise SecMonitorStateError("monitor_state_invalid")
                watermark = await session.scalar(
                    select(SecDisclosureMonitorWatermarkRecord).where(
                        SecDisclosureMonitorWatermarkRecord.id == monitor.current_watermark_id,
                        SecDisclosureMonitorWatermarkRecord.monitor_id == monitor.id,
                        SecDisclosureMonitorWatermarkRecord.workspace_id == workspace_id,
                    )
                )
                filer = await session.get(SecFilerRecord, monitor.filer_id)
                membership = await session.scalar(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id == monitor.owner_user_id,
                    )
                )
                rules = tuple(
                    await session.scalars(
                        select(SecDisclosureMonitorRuleRecord)
                        .where(
                            SecDisclosureMonitorRuleRecord.monitor_id == monitor.id,
                            SecDisclosureMonitorRuleRecord.workspace_id == workspace_id,
                        )
                        .order_by(SecDisclosureMonitorRuleRecord.ordinal.asc())
                    )
                )
                if watermark is None or filer is None or membership is None or not rules:
                    raise SecMonitorStateError("monitor_configuration_incomplete")
                run.source_watermark_id = watermark.id
                run.status = SecMonitorRunStatus.RUNNING.value
                run.updated_at = await _database_now(session)
                return SecMonitorExecutionRequest(
                    run_id=run.id,
                    job_id=run.job_id,
                    monitor_id=monitor.id,
                    scope=WorkspaceScope(
                        workspace_id=workspace_id,
                        user_id=monitor.owner_user_id,
                        role=membership.role.value,
                    ),
                    owner_user_id=monitor.owner_user_id,
                    cik=filer.cik,
                    allowed_forms=tuple(
                        sorted(
                            (SecFilingForm(value) for value in monitor.allowed_forms),
                            key=lambda value: value.value,
                        )
                    ),
                    knowledge_base_id=monitor.knowledge_base_id,
                    rules=tuple(_rule_snapshot(rule) for rule in rules),
                    watermark=_watermark_snapshot(watermark),
                    window_start=run.window_start,
                    window_end=run.window_end,
                    trace_id=TraceId(run.trace_id),
                )
        except SecMonitorStateError:
            raise
        except SQLAlchemyError:
            raise SecMonitorDependencyError("monitor_persistence_unavailable") from None

    async def commit(
        self,
        request: SecMonitorExecutionRequest,
        analysis: SecMonitorAnalysis,
    ) -> SecMonitorExecutionResult:
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(SecDisclosureMonitorRunRecord)
                    .where(
                        SecDisclosureMonitorRunRecord.id == request.run_id,
                        SecDisclosureMonitorRunRecord.workspace_id == request.scope.workspace_id,
                    )
                    .with_for_update()
                )
                monitor = await session.scalar(
                    select(SecDisclosureMonitorRecord)
                    .where(
                        SecDisclosureMonitorRecord.id == request.monitor_id,
                        SecDisclosureMonitorRecord.workspace_id == request.scope.workspace_id,
                    )
                    .with_for_update()
                )
                if run is None or monitor is None:
                    raise SecMonitorStateError("monitor_commit_state_missing")
                if run.status == SecMonitorRunStatus.SUCCEEDED.value:
                    return await self._completed_result(session, run)
                if (
                    monitor.current_watermark_id != request.watermark.watermark_id
                    or run.source_watermark_id != request.watermark.watermark_id
                ):
                    raise SecMonitorDependencyError("monitor_watermark_conflict")
                _validate_cursor_progress(request.watermark, analysis)
                now = await _database_now(session)
                case_ids: list[UUID] = []
                for finding in analysis.findings:
                    case_id = await self._persist_case(
                        session,
                        request,
                        analysis,
                        finding,
                        now=now,
                    )
                    case_ids.append(case_id)
                watermark = SecDisclosureMonitorWatermarkRecord(
                    id=uuid4(),
                    monitor_id=monitor.id,
                    workspace_id=request.scope.workspace_id,
                    revision=request.watermark.revision + 1,
                    coverage_version=analysis.coverage_version,
                    accepted_at=analysis.accepted_at,
                    accession=analysis.accession,
                    monitor_run_id=run.id,
                    created_at=now,
                )
                session.add(watermark)
                await session.flush()
                monitor.current_watermark_id = watermark.id
                monitor.revision += 1
                monitor.updated_at = now
                run.result_watermark_id = watermark.id
                run.status = SecMonitorRunStatus.SUCCEEDED.value
                run.completed_at = now
                run.updated_at = now
                return SecMonitorExecutionResult(
                    run_id=run.id,
                    monitor_id=monitor.id,
                    watermark_id=watermark.id,
                    watermark_revision=watermark.revision,
                    case_ids=tuple(case_ids),
                )
        except (SecMonitorDependencyError, SecMonitorStateError):
            raise
        except SQLAlchemyError:
            raise SecMonitorDependencyError("monitor_persistence_unavailable") from None

    async def _persist_case(
        self,
        session: AsyncSession,
        request: SecMonitorExecutionRequest,
        analysis: SecMonitorAnalysis,
        finding: SecMonitorFinding,
        *,
        now: datetime,
    ) -> UUID:
        baseline = finding.diff.baseline
        target = finding.diff.target
        if baseline is None or target is None:
            raise SecMonitorStateError("monitor_diff_identity_missing")
        filings = tuple(
            await session.scalars(
                select(SecFilingRecord).where(
                    SecFilingRecord.accession.in_((baseline.accession, target.accession))
                )
            )
        )
        by_accession = {filing.accession: filing for filing in filings}
        if set(by_accession) != {baseline.accession, target.accession}:
            raise SecMonitorStateError("monitor_filing_identity_missing")
        idempotency_key = canonical_fingerprint(
            {
                "baseline_accession": baseline.accession,
                "coverage_version": analysis.coverage_version,
                "monitor_id": str(request.monitor_id),
                "rule_id": str(finding.rule.rule_id),
                "rule_version": finding.rule.rule_version,
                "target_accession": target.accession,
                "workspace_id": str(request.scope.workspace_id),
            }
        )
        existing = await session.scalar(
            select(SecDisclosureCaseRecord).where(
                SecDisclosureCaseRecord.workspace_id == request.scope.workspace_id,
                SecDisclosureCaseRecord.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing.id
        case_id = uuid5(NAMESPACE_URL, f"sec-monitor-case:{idempotency_key}")
        payload = SecFilingDiffResponse.from_domain(finding.diff).model_dump(mode="json")
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        session.add(
            SecDisclosureCaseRecord(
                id=case_id,
                workspace_id=request.scope.workspace_id,
                monitor_id=request.monitor_id,
                monitor_run_id=request.run_id,
                rule_id=finding.rule.rule_id,
                trigger_kind=finding.rule.kind.value,
                rule_version=finding.rule.rule_version,
                source_coverage_version=analysis.coverage_version,
                baseline_filing_id=by_accession[baseline.accession].id,
                target_filing_id=by_accession[target.accession].id,
                baseline_accession=baseline.accession,
                target_accession=target.accession,
                diff_version=finding.diff.version,
                diff_payload=payload,
                diff_sha256=hashlib.sha256(encoded).digest(),
                verification_status=SecCaseVerificationStatus.VERIFIED.value,
                notification_status=SecCaseNotificationStatus.PENDING.value,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        evidence_ids = await self._persist_evidence_pair(
            session,
            request,
            case_id=case_id,
            pair=finding.evidence,
            now=now,
        )
        for side, evidence_id in zip(("baseline", "target"), evidence_ids, strict=True):
            session.add(
                SecDisclosureCaseEvidenceRecord(
                    workspace_id=request.scope.workspace_id,
                    case_id=case_id,
                    evidence_id=evidence_id,
                    side=side,
                    created_at=now,
                )
            )
        return case_id

    async def _persist_evidence_pair(
        self,
        session: AsyncSession,
        request: SecMonitorExecutionRequest,
        *,
        case_id: UUID,
        pair: SecMonitorEvidencePair,
        now: datetime,
    ) -> tuple[UUID, UUID]:
        if pair.baseline_text is not None and pair.target_text is not None:
            return (
                await self._persist_text_evidence(
                    session, request, case_id, pair.baseline_text, ordinal=1, now=now
                ),
                await self._persist_text_evidence(
                    session, request, case_id, pair.target_text, ordinal=2, now=now
                ),
            )
        if pair.baseline_fact is None or pair.target_fact is None:
            raise SecMonitorStateError("monitor_evidence_pair_invalid")
        return (
            await self._persist_fact_evidence(
                session, request, case_id, pair.baseline_fact, ordinal=1, now=now
            ),
            await self._persist_fact_evidence(
                session, request, case_id, pair.target_fact, ordinal=2, now=now
            ),
        )

    async def _persist_text_evidence(
        self,
        session: AsyncSession,
        request: SecMonitorExecutionRequest,
        case_id: UUID,
        hit: object,
        *,
        ordinal: int,
        now: datetime,
    ) -> UUID:
        from industry_platform.modules.disclosures.domain import SecFilingSearchHit

        if not isinstance(hit, SecFilingSearchHit):
            raise SecMonitorStateError("monitor_text_evidence_invalid")
        row = (
            await session.execute(
                select(
                    DocumentChunkRecord,
                    DocumentVersionRecord,
                    DocumentRecord,
                    DocumentIndexRecord,
                    WorkspaceSecImportRecord,
                    SecFilingRecord,
                    SecSourceSnapshotRecord,
                )
                .join(
                    DocumentVersionRecord,
                    and_(
                        DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                        DocumentVersionRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentRecord,
                    and_(
                        DocumentRecord.id == DocumentChunkRecord.document_id,
                        DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    KnowledgeBaseRecord,
                    and_(
                        KnowledgeBaseRecord.id == DocumentVersionRecord.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == DocumentVersionRecord.workspace_id,
                    ),
                )
                .join(
                    WorkspaceSecImportRecord,
                    and_(
                        WorkspaceSecImportRecord.workspace_id == DocumentChunkRecord.workspace_id,
                        WorkspaceSecImportRecord.document_version_id
                        == DocumentChunkRecord.document_version_id,
                    ),
                )
                .join(SecFilingRecord, SecFilingRecord.id == WorkspaceSecImportRecord.filing_id)
                .join(
                    SecSourceSnapshotRecord,
                    SecSourceSnapshotRecord.id == WorkspaceSecImportRecord.primary_snapshot_id,
                )
                .join(
                    DocumentIndexRecord,
                    and_(
                        DocumentIndexRecord.chunk_id == DocumentChunkRecord.id,
                        DocumentIndexRecord.workspace_id == DocumentChunkRecord.workspace_id,
                        DocumentIndexRecord.kind == DocumentIndexKind.VECTOR,
                        DocumentIndexRecord.status == DocumentIndexStatus.SUCCEEDED,
                        DocumentIndexRecord.index_version == hit.index_version,
                    ),
                )
                .where(
                    DocumentChunkRecord.id == hit.chunk_id,
                    DocumentChunkRecord.workspace_id == request.scope.workspace_id,
                    DocumentVersionRecord.status == DocumentVersionStatus.READY,
                    DocumentRecord.status == DocumentStatus.ACTIVE,
                    DocumentRecord.active_version_id == DocumentVersionRecord.id,
                    KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    WorkspaceSecImportRecord.accession == hit.accession,
                    SecSourceSnapshotRecord.id == hit.snapshot_id,
                    SecSourceSnapshotRecord.source_version == hit.source_version,
                    SecSourceSnapshotRecord.source_available_at <= request.window_end,
                )
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            raise SecMonitorStateError("monitor_text_evidence_unauthorized")
        chunk, version, document, index, _imported, filing, snapshot = row
        if (
            chunk.content_hash.hex() != hit.content_sha256
            or snapshot.content_sha256.hex() != hit.source_content_sha256
        ):
            raise SecMonitorStateError("monitor_text_evidence_hash_mismatch")
        locator = SecFilingTextLocatorV1(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            report_period=filing.report_date.isoformat(),
            as_of=request.window_end.isoformat(),
            filed_at=datetime.combine(
                filing.filed_date, datetime.min.time(), tzinfo=UTC
            ).isoformat(),
            accepted_at=filing.accepted_at.isoformat(),
            canonical_url=snapshot.source_url,
            snapshot_id=snapshot.id,
            source_version=snapshot.source_version,
            source_content_sha256=snapshot.content_sha256.hex(),
            knowledge_base_id=version.knowledge_base_id,
            document_id=document.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            section=chunk.title_path[-1] if chunk.title_path else "Filing excerpt",
            page_number=chunk.page_number,
            content_sha256=chunk.content_hash.hex(),
            parser_version=version.parser_version,
            chunker_version=chunk.chunker_version,
            index_version=index.index_version,
            retrieval_profile_version="monitor-diff-v1",
            retrieval_channels=hit.retrieval_channels,
            table_cells=tuple(
                SecFilingTableCellCoordinateV1(
                    table_index=cell.table_index,
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    row_span=cell.row_span,
                    column_span=cell.column_span,
                    content_sha256=cell.content_sha256,
                )
                for cell in hit.table_cells
            ),
        )
        return await self._persist_evidence(
            session,
            request,
            case_id=case_id,
            ordinal=ordinal,
            locator=locator,
            title=f"{document.title}: {locator.section}",
            canonical_url=snapshot.source_url,
            excerpt=chunk.text_content,
            content_sha256=chunk.content_hash.hex(),
            source_published_at=filing.accepted_at,
            retrieved_at=snapshot.retrieved_at,
            source_resource_version=f"{snapshot.source_version}:{index.index_version}:monitor-diff-v1",
            document_version_id=version.id,
            chunk_id=chunk.id,
            now=now,
        )

    async def _persist_fact_evidence(
        self,
        session: AsyncSession,
        request: SecMonitorExecutionRequest,
        case_id: UUID,
        fact: object,
        *,
        ordinal: int,
        now: datetime,
    ) -> UUID:
        from industry_platform.modules.disclosures.domain import SecXbrlFact

        if not isinstance(fact, SecXbrlFact):
            raise SecMonitorStateError("monitor_fact_evidence_invalid")
        row = (
            await session.execute(
                select(SecXbrlFactRecord, SecXbrlSourceRecord, SecFilingRecord)
                .join(SecXbrlSourceRecord, SecXbrlSourceRecord.id == SecXbrlFactRecord.source_id)
                .join(SecFilingRecord, SecFilingRecord.id == SecXbrlFactRecord.filing_id)
                .join(
                    WorkspaceSecImportRecord,
                    and_(
                        WorkspaceSecImportRecord.workspace_id == request.scope.workspace_id,
                        WorkspaceSecImportRecord.filing_id == SecFilingRecord.id,
                        WorkspaceSecImportRecord.knowledge_base_id == request.knowledge_base_id,
                    ),
                )
                .where(
                    SecXbrlFactRecord.id == fact.id,
                    SecXbrlFactRecord.accession == fact.accession,
                    SecXbrlSourceRecord.source_version == fact.source_version,
                    SecXbrlSourceRecord.source_available_at <= request.window_end,
                )
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            raise SecMonitorStateError("monitor_fact_evidence_unauthorized")
        fact_record, source, filing = row
        locator = SecXbrlFactLocatorV1(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            report_period=filing.report_date.isoformat(),
            as_of=request.window_end.isoformat(),
            fact_id=fact_record.id,
            filing_id=fact_record.filing_id,
            source_id=fact_record.source_id,
            source_snapshot_id=source.filing_snapshot_id,
            source_kind=source.source_kind,
            taxonomy=fact_record.taxonomy,
            concept=fact_record.concept,
            unit=fact_record.unit,
            period_kind=fact_record.period_kind,
            instant=None if fact_record.instant is None else fact_record.instant.isoformat(),
            start_date=None
            if fact_record.start_date is None
            else fact_record.start_date.isoformat(),
            end_date=None if fact_record.end_date is None else fact_record.end_date.isoformat(),
            context_id=fact_record.raw_context_id,
            dimensions=fact_record.dimensions,
            decimals=fact_record.decimals,
            scale=fact_record.scale,
            source_url=source.source_url,
            source_version=source.source_version,
            source_content_sha256=source.content_sha256.hex(),
            content_sha256=fact.source_content_sha256,
            source_available_at=source.source_available_at.isoformat(),
            retrieved_at=source.retrieved_at.isoformat(),
        )
        evidence_id = sec_xbrl_evidence_ref(
            workspace_id=request.scope.workspace_id,
            fact_id=fact.id,
            as_of=request.window_end,
            authorization_role=request.scope.role,
        )
        return await self._persist_evidence(
            session,
            request,
            case_id=case_id,
            ordinal=ordinal,
            locator=locator,
            title=f"{filing.form} {filing.accession}: {fact.taxonomy}:{fact.concept}",
            canonical_url=source.source_url,
            excerpt=json.dumps(
                SecXbrlFactResponse.from_domain(fact).model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            content_sha256=fact.source_content_sha256,
            source_published_at=filing.accepted_at,
            retrieved_at=source.retrieved_at,
            source_resource_version=f"{source.source_version}:{fact_record.locator_key}",
            document_version_id=None,
            chunk_id=None,
            now=now,
            evidence_id=evidence_id,
        )

    async def _persist_evidence(
        self,
        session: AsyncSession,
        request: SecMonitorExecutionRequest,
        *,
        case_id: UUID,
        ordinal: int,
        locator: SecFilingTextLocatorV1 | SecXbrlFactLocatorV1,
        title: str,
        canonical_url: str,
        excerpt: str,
        content_sha256: str,
        source_published_at: datetime,
        retrieved_at: datetime,
        source_resource_version: str,
        document_version_id: UUID | None,
        chunk_id: UUID | None,
        now: datetime,
        evidence_id: UUID | None = None,
    ) -> UUID:
        authorization = AuthorizationSnapshot(
            workspace_id=request.scope.workspace_id,
            actor_user_id=request.owner_user_id,
            role=request.scope.role,
            action="evidence.normalize",
            captured_at=now,
        )
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": content_sha256,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(request.scope.workspace_id),
            }
        )
        existing = await session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.workspace_id == request.scope.workspace_id,
                EvidenceRecord.deduplication_key == dedupe,
            )
        )
        if existing is not None:
            return existing.id
        resolved_id = evidence_id or uuid5(
            NAMESPACE_URL,
            f"{request.scope.workspace_id}:{locator.locator_type.value}:{dedupe}",
        )
        session.add(
            EvidenceRecord(
                id=resolved_id,
                workspace_id=request.scope.workspace_id,
                schema_version=1,
                kind=EvidenceKind.FILING,
                title=title,
                canonical_url=canonical_url,
                locator_type=locator.locator_type,
                locator=dict(locator.to_mapping()),
                excerpt=excerpt,
                content_sha256=content_sha256,
                source_published_at=source_published_at,
                retrieved_at=retrieved_at,
                license_or_terms="Official SEC public filing data subject to SEC.gov terms.",
                status=EvidenceStatus.ACTIVE,
                revision=1,
                invalidated_at=None,
                invalidation_reason=None,
                origin_run_id=None,
                origin_step_id=None,
                origin_tool_call_id=None,
                origin_case_id=case_id,
                origin_observation_id=uuid5(
                    NAMESPACE_URL,
                    f"sec-monitor-observation:{case_id}:{ordinal}",
                ),
                origin_source_ordinal=ordinal,
                normalizer_version=EVIDENCE_NORMALIZER_VERSION,
                authorization_snapshot=dict(authorization.to_mapping()),
                source_resource_version=source_resource_version,
                source_item_id=None,
                query_run_id=None,
                document_version_id=document_version_id,
                chunk_id=chunk_id,
                deduplication_key=dedupe,
                created_at=now,
                updated_at=now,
            )
        )
        return resolved_id

    @staticmethod
    async def _completed_result(
        session: AsyncSession,
        run: SecDisclosureMonitorRunRecord,
    ) -> SecMonitorExecutionResult:
        if run.result_watermark_id is None:
            raise SecMonitorStateError("monitor_terminal_state_invalid")
        watermark = await session.get(
            SecDisclosureMonitorWatermarkRecord,
            run.result_watermark_id,
        )
        if watermark is None:
            raise SecMonitorStateError("monitor_terminal_watermark_missing")
        case_ids = tuple(
            await session.scalars(
                select(SecDisclosureCaseRecord.id)
                .where(SecDisclosureCaseRecord.monitor_run_id == run.id)
                .order_by(SecDisclosureCaseRecord.id.asc())
            )
        )
        return SecMonitorExecutionResult(
            run_id=run.id,
            monitor_id=run.monitor_id,
            watermark_id=watermark.id,
            watermark_revision=watermark.revision,
            case_ids=case_ids,
        )


def _rule_snapshot(record: SecDisclosureMonitorRuleRecord) -> SecMonitorRule:
    return SecMonitorRule(
        rule_id=record.id,
        kind=SecMonitorRuleKind(record.kind),
        rule_version=record.rule_version,
        section_query=record.section_query,
        taxonomy=record.taxonomy,
        concept=record.concept,
        unit=record.unit,
        threshold=record.threshold,
        comparator=record.comparator,
    )


def _watermark_snapshot(record: SecDisclosureMonitorWatermarkRecord) -> SecMonitorWatermark:
    return SecMonitorWatermark(
        watermark_id=record.id,
        revision=record.revision,
        coverage_version=record.coverage_version,
        accepted_at=record.accepted_at,
        accession=record.accession,
    )


def _validate_cursor_progress(
    watermark: SecMonitorWatermark,
    analysis: SecMonitorAnalysis,
) -> None:
    if watermark.accepted_at is None or watermark.accession is None:
        return
    if analysis.accepted_at is None or analysis.accession is None:
        raise SecMonitorStateError("monitor_watermark_regressed")
    if (analysis.accepted_at, analysis.accession) < (watermark.accepted_at, watermark.accession):
        raise SecMonitorStateError("monitor_watermark_regressed")


async def _database_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.now()))
    if not isinstance(now, datetime):
        raise SecMonitorDependencyError("monitor_database_clock_unavailable")
    return now
