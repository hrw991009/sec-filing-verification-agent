"""PostgreSQL adapter for append-only SEC verification reports."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.evidence.adapters.sqlalchemy import SqlAlchemyEvidenceRepository
from industry_platform.modules.evidence.domain import EvidenceStatus
from industry_platform.modules.evidence.models import EvidenceRecord, ResearchClaimRecord
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.research.models import (
    ResearchDraftRecord,
    ResearchRunRecord,
    ResearchVerificationClaimRecord,
    ResearchVerificationIssueRecord,
    ResearchVerificationReportRecord,
)
from industry_platform.modules.research.service import ResearchNotFoundError
from industry_platform.modules.research.verification import (
    VerificationClaimResult,
    VerificationConflictError,
    VerificationEvidenceSnapshot,
    VerificationIssue,
    VerificationPersistenceError,
    VerificationReport,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class SqlAlchemyVerificationReportRepository:
    session_factory: AsyncSessionFactory

    async def next_revision(self, scope: WorkspaceScope, research_run_id: UUID) -> int:
        try:
            async with self.session_factory() as session:
                await _require_run(session, scope, research_run_id)
                latest = await session.scalar(
                    select(func.max(ResearchVerificationReportRecord.revision)).where(
                        ResearchVerificationReportRecord.research_run_id == research_run_id,
                        ResearchVerificationReportRecord.workspace_id == scope.workspace_id,
                    )
                )
                return (latest or 0) + 1
        except ResearchNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise VerificationPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def save(self, scope: WorkspaceScope, report: VerificationReport) -> VerificationReport:
        if report.workspace_id != scope.workspace_id:
            raise ResearchNotFoundError
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(ResearchRunRecord)
                    .where(
                        ResearchRunRecord.id == report.research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    raise ResearchNotFoundError
                draft = await session.scalar(
                    select(ResearchDraftRecord).where(
                        ResearchDraftRecord.id == report.draft_id,
                        ResearchDraftRecord.research_run_id == report.research_run_id,
                        ResearchDraftRecord.workspace_id == scope.workspace_id,
                    )
                )
                if (
                    draft is None
                    or run.agent_run_id != report.agent_run_id
                    or run.graph_version != report.graph_version
                    or tuple(UUID(item) for item in draft.claim_refs) != report.required_claim_ids
                ):
                    raise VerificationConflictError
                latest_revision = await session.scalar(
                    select(func.max(ResearchVerificationReportRecord.revision)).where(
                        ResearchVerificationReportRecord.research_run_id == report.research_run_id,
                        ResearchVerificationReportRecord.workspace_id == scope.workspace_id,
                    )
                )
                if (latest_revision or 0) + 1 != report.revision:
                    raise VerificationConflictError
                await _verify_claim_revisions(session, scope, report)
                await _verify_evidence_revisions(
                    session,
                    scope,
                    report,
                    evidence_repository=SqlAlchemyEvidenceRepository(self.session_factory),
                )
                session.add(
                    ResearchVerificationReportRecord(
                        id=report.report_id,
                        workspace_id=report.workspace_id,
                        research_run_id=report.research_run_id,
                        agent_run_id=report.agent_run_id,
                        draft_id=report.draft_id,
                        revision=report.revision,
                        schema_version=report.schema_version,
                        checker_version=report.checker_version,
                        graph_version=report.graph_version,
                        financial_scope=dict(report.financial_scope.to_mapping()),
                        status=report.status,
                        coverage=report.coverage,
                        required_claim_ids=[str(item) for item in report.required_claim_ids],
                        evidence_snapshots=[
                            {
                                "evidence_id": str(item.evidence_id),
                                "revision": item.revision,
                                "status": item.status.value,
                                "content_sha256": item.content_sha256,
                                "available": item.available,
                            }
                            for item in report.evidence_snapshots
                        ],
                        runtime_stop_reason=report.runtime_stop_reason,
                        created_at=report.created_at,
                    )
                )
                await session.flush()
                for ordinal, claim in enumerate(report.claims, start=1):
                    session.add(
                        ResearchVerificationClaimRecord(
                            report_id=report.report_id,
                            claim_id=claim.claim_id,
                            workspace_id=report.workspace_id,
                            ordinal=ordinal,
                            claim_revision=claim.claim_revision,
                            required=claim.required,
                            verdict=claim.verdict,
                            coverage=claim.coverage,
                            evidence_refs=[str(item) for item in claim.evidence_refs],
                            citation_refs=[str(item) for item in claim.citation_refs],
                            calculation_refs=[str(item) for item in claim.calculation_refs],
                            created_at=report.created_at,
                        )
                    )
                for ordinal, issue in enumerate(report.issues, start=1):
                    session.add(
                        ResearchVerificationIssueRecord(
                            id=issue.issue_id,
                            workspace_id=report.workspace_id,
                            report_id=report.report_id,
                            ordinal=ordinal,
                            code=issue.code,
                            severity=issue.severity,
                            claim_id=issue.claim_id,
                            expected_refs=list(issue.expected_refs),
                            observed_refs=list(issue.observed_refs),
                            repairability=issue.repairability,
                            allowed_action=issue.allowed_action,
                            details_digest=issue.details_digest,
                            created_at=report.created_at,
                        )
                    )
            return report
        except (ResearchNotFoundError, VerificationConflictError):
            raise
        except (TypeError, ValueError):
            raise VerificationConflictError from None
        except IntegrityError:
            raise VerificationConflictError from None
        except SQLAlchemyError as error:
            raise VerificationPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def latest(
        self, scope: WorkspaceScope, research_run_id: UUID
    ) -> VerificationReport | None:
        try:
            async with self.session_factory() as session:
                await _require_run(session, scope, research_run_id)
                record = await session.scalar(
                    select(ResearchVerificationReportRecord)
                    .where(
                        ResearchVerificationReportRecord.research_run_id == research_run_id,
                        ResearchVerificationReportRecord.workspace_id == scope.workspace_id,
                    )
                    .order_by(ResearchVerificationReportRecord.revision.desc())
                    .limit(1)
                )
                if record is None:
                    return None
                claim_records = tuple(
                    await session.scalars(
                        select(ResearchVerificationClaimRecord)
                        .where(ResearchVerificationClaimRecord.report_id == record.id)
                        .order_by(ResearchVerificationClaimRecord.ordinal)
                    )
                )
                issue_records = tuple(
                    await session.scalars(
                        select(ResearchVerificationIssueRecord)
                        .where(ResearchVerificationIssueRecord.report_id == record.id)
                        .order_by(ResearchVerificationIssueRecord.ordinal)
                    )
                )
                issues = tuple(
                    VerificationIssue(
                        issue_id=item.id,
                        code=item.code,
                        severity=item.severity,
                        claim_id=item.claim_id,
                        expected_refs=tuple(_strings(item.expected_refs)),
                        observed_refs=tuple(_strings(item.observed_refs)),
                        repairability=item.repairability,
                        allowed_action=item.allowed_action,
                        details_digest=item.details_digest,
                    )
                    for item in issue_records
                )
                claims = tuple(
                    VerificationClaimResult(
                        claim_id=item.claim_id,
                        claim_revision=item.claim_revision,
                        required=item.required,
                        verdict=item.verdict,
                        coverage=item.coverage,
                        evidence_refs=tuple(UUID(value) for value in _strings(item.evidence_refs)),
                        citation_refs=tuple(UUID(value) for value in _strings(item.citation_refs)),
                        calculation_refs=tuple(
                            UUID(value) for value in _strings(item.calculation_refs)
                        ),
                        issues=tuple(issue for issue in issues if issue.claim_id == item.claim_id),
                    )
                    for item in claim_records
                )
                return VerificationReport(
                    report_id=record.id,
                    research_run_id=record.research_run_id,
                    agent_run_id=record.agent_run_id,
                    workspace_id=record.workspace_id,
                    draft_id=record.draft_id,
                    revision=record.revision,
                    graph_version=record.graph_version,
                    financial_scope=FinancialScope.from_mapping(record.financial_scope),
                    status=record.status,
                    coverage=record.coverage,
                    required_claim_ids=tuple(
                        UUID(value) for value in _strings(record.required_claim_ids)
                    ),
                    claims=claims,
                    evidence_snapshots=tuple(
                        _evidence_snapshot(item) for item in _mappings(record.evidence_snapshots)
                    ),
                    issues=issues,
                    runtime_stop_reason=record.runtime_stop_reason,
                    created_at=record.created_at,
                    checker_version=record.checker_version,
                    schema_version=record.schema_version,
                )
        except ResearchNotFoundError:
            raise
        except (TypeError, ValueError):
            raise VerificationPersistenceError from None
        except SQLAlchemyError as error:
            raise VerificationPersistenceError(sqlstate=safe_sqlstate(error)) from None


async def _require_run(session: object, scope: WorkspaceScope, research_run_id: UUID) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise VerificationPersistenceError
    found = await session.scalar(
        select(ResearchRunRecord.id).where(
            ResearchRunRecord.id == research_run_id,
            ResearchRunRecord.workspace_id == scope.workspace_id,
            ResearchRunRecord.owner_user_id == scope.user_id,
        )
    )
    if found is None:
        raise ResearchNotFoundError


async def _verify_claim_revisions(
    session: object, scope: WorkspaceScope, report: VerificationReport
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise VerificationPersistenceError
    records = tuple(
        await session.scalars(
            select(ResearchClaimRecord).where(
                ResearchClaimRecord.workspace_id == scope.workspace_id,
                ResearchClaimRecord.research_run_id == report.research_run_id,
                ResearchClaimRecord.id.in_(report.required_claim_ids),
            )
        )
    )
    revisions = {item.id: item.revision for item in records}
    if any(revisions.get(item.claim_id) != item.claim_revision for item in report.claims):
        raise VerificationConflictError


async def _verify_evidence_revisions(
    session: object,
    scope: WorkspaceScope,
    report: VerificationReport,
    *,
    evidence_repository: SqlAlchemyEvidenceRepository,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise VerificationPersistenceError
    if not report.evidence_snapshots:
        return
    records = tuple(
        await session.scalars(
            select(EvidenceRecord).where(
                EvidenceRecord.workspace_id == scope.workspace_id,
                EvidenceRecord.id.in_(item.evidence_id for item in report.evidence_snapshots),
            )
        )
    )
    current = {item.id: item for item in records}
    for snapshot in report.evidence_snapshots:
        record = current.get(snapshot.evidence_id)
        if (
            record is None
            or record.revision != snapshot.revision
            or record.status is not snapshot.status
            or record.content_sha256 != snapshot.content_sha256
        ):
            raise VerificationConflictError
        available = await evidence_repository.is_evidence_record_available(session, record)
        if available != snapshot.available:
            raise VerificationConflictError


def _evidence_snapshot(value: dict[str, object]) -> VerificationEvidenceSnapshot:
    revision = value.get("revision")
    available = value.get("available")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not isinstance(available, bool)
    ):
        raise ValueError("Verification Evidence snapshot is invalid")
    return VerificationEvidenceSnapshot(
        evidence_id=UUID(str(value.get("evidence_id"))),
        revision=revision,
        status=EvidenceStatus(str(value.get("status"))),
        content_sha256=str(value.get("content_sha256")),
        available=available,
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Verification string list is invalid")
    return value


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Verification mapping list is invalid")
    return value
