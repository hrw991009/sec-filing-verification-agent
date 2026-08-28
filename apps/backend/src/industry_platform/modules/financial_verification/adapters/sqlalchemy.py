"""PostgreSQL authorization and identity reload for SEC XBRL calculation operands."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.models import (
    SecFilingRecord,
    SecXbrlFactRecord,
    SecXbrlSourceRecord,
    WorkspaceSecImportRecord,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialPeriodKind,
    FinancialScope,
    sec_xbrl_evidence_ref,
)
from industry_platform.modules.financial_verification.ports import (
    FinancialOperandReference,
    FinancialOperandRepository,
    FinancialOperandResolution,
    FinancialOperandResolutionStatus,
)
from industry_platform.modules.knowledge.domain import (
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeBaseStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentRecord,
    DocumentVersionRecord,
    KnowledgeBaseRecord,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SqlAlchemyFinancialOperandRepository(FinancialOperandRepository):
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        references: tuple[FinancialOperandReference, ...],
    ) -> FinancialOperandResolution:
        if not references or not knowledge_base_ids:
            return FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
        fact_ids = tuple(dict.fromkeys(item.source_fact_id for item in references))
        try:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(SecXbrlFactRecord, SecXbrlSourceRecord, SecFilingRecord)
                            .join(
                                SecXbrlSourceRecord,
                                SecXbrlSourceRecord.id == SecXbrlFactRecord.source_id,
                            )
                            .join(
                                SecFilingRecord, SecFilingRecord.id == SecXbrlFactRecord.filing_id
                            )
                            .join(
                                WorkspaceSecImportRecord,
                                and_(
                                    WorkspaceSecImportRecord.workspace_id == scope.workspace_id,
                                    WorkspaceSecImportRecord.filing_id == SecFilingRecord.id,
                                    WorkspaceSecImportRecord.knowledge_base_id.in_(
                                        knowledge_base_ids
                                    ),
                                ),
                            )
                            .join(
                                KnowledgeBaseRecord,
                                and_(
                                    KnowledgeBaseRecord.id
                                    == WorkspaceSecImportRecord.knowledge_base_id,
                                    KnowledgeBaseRecord.workspace_id
                                    == WorkspaceSecImportRecord.workspace_id,
                                ),
                            )
                            .join(
                                DocumentRecord,
                                and_(
                                    DocumentRecord.id == WorkspaceSecImportRecord.document_id,
                                    DocumentRecord.workspace_id
                                    == WorkspaceSecImportRecord.workspace_id,
                                ),
                            )
                            .join(
                                DocumentVersionRecord,
                                and_(
                                    DocumentVersionRecord.id
                                    == WorkspaceSecImportRecord.document_version_id,
                                    DocumentVersionRecord.workspace_id
                                    == WorkspaceSecImportRecord.workspace_id,
                                ),
                            )
                            .where(
                                SecXbrlFactRecord.id.in_(fact_ids),
                                SecXbrlFactRecord.accession == financial_scope.accession,
                                SecXbrlFactRecord.form == financial_scope.form.value,
                                SecXbrlSourceRecord.cik == financial_scope.cik,
                                SecXbrlSourceRecord.source_available_at <= financial_scope.as_of,
                                SecFilingRecord.report_date == financial_scope.report_period,
                                KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                                DocumentRecord.status == DocumentStatus.ACTIVE,
                                DocumentRecord.active_version_id == DocumentVersionRecord.id,
                                DocumentVersionRecord.status == DocumentVersionStatus.READY,
                            )
                        )
                    )
                    .unique()
                    .all()
                )
        except SQLAlchemyError:
            return FinancialOperandResolution(FinancialOperandResolutionStatus.DEPENDENCY_FAILED)

        by_id = {fact.id: (fact, source, filing) for fact, source, filing in rows}
        resolved: list[FinancialEvidenceOperand] = []
        for reference in references:
            row = by_id.get(reference.source_fact_id)
            if row is None:
                return FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
            fact, source, filing = row
            expected_ref = sec_xbrl_evidence_ref(
                workspace_id=scope.workspace_id,
                fact_id=fact.id,
                as_of=financial_scope.as_of,
                authorization_role=scope.role,
            )
            if reference.evidence_ref != expected_ref or reference.value != fact.value:
                return FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
            try:
                resolved.append(
                    financial_evidence_operand_from_records(
                        workspace_id=scope.workspace_id,
                        as_of=financial_scope.as_of,
                        authorization_role=scope.role,
                        fact=fact,
                        source=source,
                        filing=filing,
                    )
                )
            except ValueError:
                return FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
        if Counter(item.source_fact_id for item in resolved) != Counter(
            item.source_fact_id for item in references
        ):
            return FinancialOperandResolution(FinancialOperandResolutionStatus.NO_RESULT)
        return FinancialOperandResolution(
            FinancialOperandResolutionStatus.OK,
            operands=tuple(resolved),
        )


def financial_evidence_operand_from_records(
    *,
    workspace_id: UUID,
    as_of: datetime,
    authorization_role: str,
    fact: SecXbrlFactRecord,
    source: SecXbrlSourceRecord,
    filing: SecFilingRecord,
) -> FinancialEvidenceOperand:
    return FinancialEvidenceOperand(
        evidence_ref=sec_xbrl_evidence_ref(
            workspace_id=workspace_id,
            fact_id=fact.id,
            as_of=as_of,
            authorization_role=authorization_role,
        ),
        source_fact_id=fact.id,
        value=fact.value,
        cik=source.cik,
        accession=fact.accession,
        form=FinancialForm(fact.form),
        report_period=filing.report_date,
        unit=fact.unit,
        scale=0 if fact.scale is None else fact.scale,
        period_kind=FinancialPeriodKind(fact.period_kind),
        instant=fact.instant,
        start_date=fact.start_date,
        end_date=fact.end_date,
        context_id=fact.raw_context_id,
        dimensions=tuple(fact.dimensions.items()),
        taxonomy=fact.taxonomy,
        concept=fact.concept,
        is_custom=fact.is_custom,
        source_kind=source.source_kind,
        source_version=source.source_version,
        source_available_at=source.source_available_at,
        amendment_relation_status=filing.amendment_relation_status,
        base_accession=filing.base_accession,
    )
