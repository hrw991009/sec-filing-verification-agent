"""Prove append-only SEC verification reports on real PostgreSQL."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.evidence.domain import ClaimVerificationStatus, ResearchClaim
from industry_platform.modules.evidence.models import ResearchClaimRecord
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.knowledge.domain import KnowledgeBaseStatus
from industry_platform.modules.knowledge.models import KnowledgeBaseRecord
from industry_platform.modules.research.adapters.verification import (
    SqlAlchemyVerificationReportRepository,
)
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    ResearchBriefInput,
    ResearchDraftStatus,
)
from industry_platform.modules.research.models import ResearchDraftRecord, ResearchPlanRecord
from industry_platform.modules.research.service import ResearchSubmissionService, StartResearch
from industry_platform.modules.research.verification import (
    VERIFICATION_CHECKER_VERSION,
    VerificationConflictError,
    VerificationIssueCode,
    VerificationSnapshot,
    VerificationStatus,
    evaluate_verification_snapshot,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)


def test_verification_report_is_append_only_and_rejects_stale_claim_revision(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        knowledge_base_id = uuid4()
        claim_id = uuid4()
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"verification-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=NOW,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="SEC Verification",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                        KnowledgeBaseRecord(
                            id=knowledge_base_id,
                            workspace_id=workspace_id,
                            created_by_user_id=user_id,
                            name="SEC filing evidence",
                            description=None,
                            status=KnowledgeBaseStatus.ACTIVE,
                            revision=1,
                        ),
                    )
                )
            scope = WorkspaceScope(workspace_id, user_id, "owner")
            financial_scope = FinancialScope(
                cik="0000320193",
                accession="0000320193-23-000106",
                form=FinancialForm.TEN_K,
                report_period=date(2023, 9, 30),
                as_of=datetime(2023, 11, 3, 12, tzinfo=UTC),
                unit="USD",
                scale=6,
            )
            receipt = await ResearchSubmissionService(
                ConversationApplicationService(
                    SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            ).start(
                scope,
                StartResearch(
                    trace_id=TraceId(f"verification-postgres-{user_id}"),
                    industry_id=None,
                    brief=ResearchBriefInput(
                        original_question="Verify the filing Claim.",
                        confirmed_scope=("Apple 2023 Form 10-K",),
                        exclusions=("Investment advice",),
                        completion_criteria=("Every Claim requires SEC Evidence",),
                        financial_scope=financial_scope,
                    ),
                    idempotency_key=f"verification-{user_id}",
                    search_mode=TurnSearchMode.LOCAL,
                    knowledge_base_ids=(knowledge_base_id,),
                ),
            )
            plan_id = uuid4()
            draft_id = uuid4()
            async with session_factory.begin() as session:
                session.add(
                    ResearchPlanRecord(
                        id=plan_id,
                        workspace_id=workspace_id,
                        research_run_id=receipt.research_run_id,
                        brief_revision=1,
                        revision=1,
                        actions=[
                            {
                                "ordinal": 1,
                                "objective": "Read the locked filing",
                                "allowed_tool_names": ["sec.read_filing@v1"],
                            }
                        ],
                        planner_summary="Use only the locked SEC scope.",
                        created_at=NOW,
                    )
                )
                await session.flush()
                session.add_all(
                    (
                        ResearchDraftRecord(
                            id=draft_id,
                            workspace_id=workspace_id,
                            research_run_id=receipt.research_run_id,
                            plan_id=plan_id,
                            status=ResearchDraftStatus.UNCERTAIN_DRAFT,
                            content_markdown="# Draft\n\nEvidence is still missing.",
                            outline=["Claim", "Limitations"],
                            evidence_refs=[],
                            claim_refs=[str(claim_id)],
                            uncertainty_summary="No active Evidence supports the Claim.",
                            content_bytes=39,
                            created_at=NOW,
                            updated_at=NOW,
                        ),
                        ResearchClaimRecord(
                            id=claim_id,
                            workspace_id=workspace_id,
                            research_run_id=receipt.research_run_id,
                            statement="Apple reported a filing fact.",
                            confidence=0.5,
                            verification_status=ClaimVerificationStatus.UNCERTAIN,
                            coverage=0,
                            conflict=False,
                            revision=1,
                            created_at=NOW,
                            updated_at=NOW,
                        ),
                    )
                )
            claim = ResearchClaim(
                claim_id=claim_id,
                workspace_id=workspace_id,
                research_run_id=receipt.research_run_id,
                statement="Apple reported a filing fact.",
                confidence=0.5,
                verification_status=ClaimVerificationStatus.UNCERTAIN,
                coverage=0,
                conflict=False,
                revision=1,
                relations=(),
                created_at=NOW,
                updated_at=NOW,
            )
            repository = SqlAlchemyVerificationReportRepository(session_factory)
            first = evaluate_verification_snapshot(
                verification_snapshot(
                    workspace_id=workspace_id,
                    research_run_id=receipt.research_run_id,
                    agent_run_id=receipt.agent_run_id,
                    draft_id=draft_id,
                    claim=claim,
                    revision=await repository.next_revision(scope, receipt.research_run_id),
                )
            )
            saved = await repository.save(scope, first)
            loaded = await repository.latest(scope, receipt.research_run_id)

            assert saved.status is VerificationStatus.INSUFFICIENT_EVIDENCE
            assert loaded == saved
            assert loaded is not None
            assert loaded.runtime_stop_reason is None
            assert {issue.code for issue in loaded.issues} == {
                VerificationIssueCode.MISSING_EVIDENCE,
                VerificationIssueCode.COVERAGE_INCOMPLETE,
            }

            stale = evaluate_verification_snapshot(
                verification_snapshot(
                    workspace_id=workspace_id,
                    research_run_id=receipt.research_run_id,
                    agent_run_id=receipt.agent_run_id,
                    draft_id=draft_id,
                    claim=claim,
                    revision=await repository.next_revision(scope, receipt.research_run_id),
                )
            )
            async with session_factory.begin() as session:
                record = await session.scalar(
                    select(ResearchClaimRecord).where(ResearchClaimRecord.id == claim_id)
                )
                assert record is not None
                record.revision = 2
            with pytest.raises(VerificationConflictError):
                await repository.save(scope, stale)
            assert await repository.next_revision(scope, receipt.research_run_id) == 2
        finally:
            await engine.dispose()

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()


def verification_snapshot(
    *,
    workspace_id: UUID,
    research_run_id: UUID,
    agent_run_id: UUID,
    draft_id: UUID,
    claim: ResearchClaim,
    revision: int,
) -> VerificationSnapshot:
    return VerificationSnapshot(
        report_id=uuid5(
            research_run_id,
            f"verification:{VERIFICATION_CHECKER_VERSION}:{revision}:{draft_id}",
        ),
        research_run_id=research_run_id,
        agent_run_id=agent_run_id,
        workspace_id=workspace_id,
        draft_id=draft_id,
        revision=revision,
        graph_version=RESEARCH_GRAPH_VERSION,
        financial_scope=FinancialScope(
            cik="0000320193",
            accession="0000320193-23-000106",
            form=FinancialForm.TEN_K,
            report_period=date(2023, 9, 30),
            as_of=datetime(2023, 11, 3, 12, tzinfo=UTC),
            unit="USD",
            scale=6,
        ),
        required_claim_ids=(claim.claim_id,),
        claims=(claim,),
        evidence_states=(),
        runtime_stop_reason=None,
        created_at=NOW,
    )
