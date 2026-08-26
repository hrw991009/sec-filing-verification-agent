"""Prove Research L4 approval and resume facts on real PostgreSQL."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.adapters.checkpoints import SqlAlchemyCheckpointStore
from industry_platform.modules.agent_runtime.checkpoints import (
    CheckpointEnvelope,
    LoadCheckpointRequest,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.models import AgentCheckpointRecord, AgentRunRecord
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.knowledge.domain import KnowledgeBaseStatus
from industry_platform.modules.knowledge.models import KnowledgeBaseRecord
from industry_platform.modules.research.adapters.durability import (
    SqlAlchemyResearchDurabilityRepository,
)
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchBriefInput,
    ResearchNode,
    ResearchRunStatus,
    ResearchStartReceipt,
)
from industry_platform.modules.research.durability import (
    DecideResearchApproval,
    ResearchApprovalConflictError,
    ResearchDurabilityService,
    ResearchResumeStateError,
    ResearchResumeTokenError,
    ResumeResearch,
    ResumeTokenCodec,
)
from industry_platform.modules.research.models import (
    ResearchApprovalDecisionRecord,
    ResearchApprovalRequestRecord,
    ResearchRunRecord,
    ResearchSideEffectRecord,
)
from industry_platform.modules.research.service import ResearchSubmissionService, StartResearch
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


async def _prepare_paused_research(
    session_factory: AsyncSessionFactory,
    scope: WorkspaceScope,
    *,
    knowledge_base_id: UUID,
    suffix: str,
) -> tuple[ResearchStartReceipt, CheckpointEnvelope]:
    financial_scope = FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=datetime(2023, 11, 3, tzinfo=UTC),
        unit="USD",
        scale=6,
    )
    receipt = await ResearchSubmissionService(
        ConversationApplicationService(
            transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    ).start(
        scope,
        StartResearch(
            trace_id=TraceId(f"research-l4-durability-postgres-{suffix}"),
            industry_id=None,
            search_mode=TurnSearchMode.LOCAL,
            knowledge_base_ids=(knowledge_base_id,),
            brief=ResearchBriefInput(
                original_question="Confirm the selected company and period.",
                confirmed_scope=("Public company filing",),
                exclusions=("Investment advice",),
                completion_criteria=("Continue only after approval",),
                financial_scope=financial_scope,
                approval_reason=ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY,
            ),
            idempotency_key=f"research-l4-durability-{scope.user_id}-{suffix}",
            max_steps=20,
            max_total_tokens=12_000,
            max_cost_micro_usd=300_000,
            timeout_seconds=600,
        ),
    )
    checkpoint_at = NOW + timedelta(seconds=1)
    graph_state = {
        "schema_version": RESEARCH_STATE_SCHEMA_VERSION,
        "graph_version": RESEARCH_GRAPH_VERSION,
        "research_run_id": str(receipt.research_run_id),
        "run_id": str(receipt.agent_run_id),
        "workspace_id": str(scope.workspace_id),
        "brief_revision": 1,
        "plan_id": str(uuid4()),
        "current_node": ResearchNode.PLAN.value,
        "pending_actions": [1],
        "evidence_refs": [],
        "claim_refs": [],
        "artifact_refs": [],
        "status": AgentRunStatus.RUNNING.value,
        "step_count": 0,
        "input_tokens_used": 0,
        "output_tokens_used": 0,
        "cost_micro_usd": 0,
        "revise_count": 0,
        "approval_status": "not_required",
        "approval_reason": ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY.value,
        "cancel_requested": False,
        "stop_reason": None,
        "error_summary": None,
    }
    checkpoint_payload = {
        "kind": "research_l4_v1",
        "graph_version": RESEARCH_GRAPH_VERSION,
        "research_state_schema_version": RESEARCH_STATE_SCHEMA_VERSION,
        "research_run_id": str(receipt.research_run_id),
        "financial_scope": dict(financial_scope.to_mapping()),
        "node": ResearchNode.PLAN.value,
        "next_node": ResearchNode.RESEARCH_LOOP.value,
        "graph_state": graph_state,
        "execution": {
            "observations": [],
            "final_decision": None,
            "final_response": None,
            "final_markdown": None,
            "outline": [],
            "steps": [],
        },
    }
    async with session_factory.begin() as session:
        run = await session.get(AgentRunRecord, receipt.agent_run_id)
        research = await session.get(ResearchRunRecord, receipt.research_run_id)
        assert run is not None
        assert research is not None
        run.status = AgentRunStatus.PAUSED
        run.started_at = checkpoint_at
        run.state_revision = 1
        run.updated_at = checkpoint_at
        research.status = ResearchRunStatus.PAUSED
        research.current_node = ResearchNode.PLAN
        research.updated_at = checkpoint_at
        session.add(
            AgentCheckpointRecord(
                id=uuid4(),
                workspace_id=scope.workspace_id,
                run_id=receipt.agent_run_id,
                revision=0,
                envelope_schema_version=1,
                state_schema_version=1,
                state={
                    "run_state": {
                        "schema_version": 1,
                        "run_id": str(receipt.agent_run_id),
                        "workspace_id": str(scope.workspace_id),
                        "revision": 1,
                        "status": AgentRunStatus.RUNNING.value,
                        "step_count": 0,
                        "event_count": 1,
                        "input_tokens_used": 0,
                        "output_tokens_used": 0,
                        "cost_micro_usd": 0,
                        "updated_at": checkpoint_at.isoformat(),
                        "artifact_ids": [],
                        "stop_reason": None,
                        "max_steps_preflight_rejected": False,
                        "token_budget_preflight_rejected": False,
                        "cost_budget_preflight_rejected": False,
                    },
                    "payload": checkpoint_payload,
                },
                saved_at=checkpoint_at,
            )
        )

    checkpoint = await SqlAlchemyCheckpointStore(session_factory).load(
        LoadCheckpointRequest(
            run_id=receipt.agent_run_id,
            workspace_id=scope.workspace_id,
            revision=0,
        )
    )
    return receipt, checkpoint


def test_approval_resume_is_atomic_idempotent_and_does_not_store_raw_token(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        knowledge_base_id = uuid4()
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"research-durability-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=NOW,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Research L4 durability",
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
                            name="SEC fixture",
                            description=None,
                            status=KnowledgeBaseStatus.ACTIVE,
                            revision=1,
                        ),
                    )
                )
            scope = WorkspaceScope(workspace_id, user_id, "owner")
            receipt, checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="allow",
            )
            clock = MutableClock(NOW + timedelta(seconds=2))
            codec = ResumeTokenCodec(b"d" * 32)
            service = ResearchDurabilityService(
                repository=SqlAlchemyResearchDurabilityRepository(session_factory),
                token_codec=codec,
                clock=clock,
            )
            approval, token = await service.interrupt(
                scope,
                checkpoint=checkpoint,
                reason=ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY,
            )
            clock.value = NOW + timedelta(seconds=3)
            decide = DecideResearchApproval(
                research_run_id=receipt.research_run_id,
                approval_request_id=approval.approval_request_id,
                checkpoint_revision=0,
                outcome=ResearchApprovalOutcome.ALLOW,
            )
            allowed = await service.decide(scope, decide)
            repeated_allowed = await service.decide(scope, decide)
            assert allowed.status is ResearchApprovalStatus.ALLOWED
            assert repeated_allowed == allowed
            with pytest.raises(ResearchApprovalConflictError):
                await service.decide(
                    scope,
                    DecideResearchApproval(
                        research_run_id=receipt.research_run_id,
                        approval_request_id=approval.approval_request_id,
                        checkpoint_revision=0,
                        outcome=ResearchApprovalOutcome.DENY,
                    ),
                )

            resume = ResumeResearch(
                research_run_id=receipt.research_run_id,
                approval_request_id=approval.approval_request_id,
                checkpoint_revision=0,
                resume_token=token,
            )
            with pytest.raises(ResearchResumeTokenError):
                await service.resume(
                    scope,
                    ResumeResearch(
                        research_run_id=receipt.research_run_id,
                        approval_request_id=approval.approval_request_id,
                        checkpoint_revision=0,
                        resume_token="x" * 43,
                    ),
                )
            async with session_factory.begin() as session:
                guarded_run = await session.get(AgentRunRecord, receipt.agent_run_id)
                assert guarded_run is not None
                guarded_run.cancel_requested_at = clock.value
            with pytest.raises(ResearchResumeStateError):
                await service.resume(scope, resume)
            async with session_factory.begin() as session:
                guarded_run = await session.get(AgentRunRecord, receipt.agent_run_id)
                assert guarded_run is not None
                guarded_run.cancel_requested_at = None
                guarded_run.step_count = guarded_run.max_steps
            with pytest.raises(ResearchResumeStateError):
                await service.resume(scope, resume)
            async with session_factory.begin() as session:
                guarded_run = await session.get(AgentRunRecord, receipt.agent_run_id)
                assert guarded_run is not None
                guarded_run.step_count = 0
            clock.value = NOW + timedelta(seconds=4)
            resumed = await service.resume(scope, resume)
            repeated_resumed = await service.resume(scope, resume)
            assert resumed.created is True
            assert repeated_resumed.created is False
            assert repeated_resumed.job_id == resumed.job_id

            effect = ("tool_call", str(uuid4()), "a" * 64)
            await service.record_completed_effects(
                scope,
                run_id=receipt.agent_run_id,
                effects=(effect,),
            )
            await service.record_completed_effects(
                scope,
                run_id=receipt.agent_run_id,
                effects=(effect,),
            )
            timeline = await service.timeline(scope, receipt.research_run_id)

            async with session_factory() as session:
                stored_approval = await session.get(
                    ResearchApprovalRequestRecord,
                    approval.approval_request_id,
                )
                resume_job = await session.get(Job, resumed.job_id)
                outbox = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.payload["job_id"].as_string() == str(resumed.job_id)
                    )
                )
                decision_count = await session.scalar(
                    select(func.count()).select_from(ResearchApprovalDecisionRecord)
                )
                effect_count = await session.scalar(
                    select(func.count()).select_from(ResearchSideEffectRecord)
                )
                stored_run = await session.get(AgentRunRecord, receipt.agent_run_id)

            assert stored_approval is not None
            assert resume_job is not None
            assert outbox is not None
            assert stored_run is not None
            assert stored_approval.resume_token_hash == codec.digest(token)
            assert token.encode("ascii") != stored_approval.resume_token_hash
            assert stored_run.job_id == resumed.job_id
            assert decision_count == 1
            assert effect_count == 1
            assert timeline.duplicate_side_effect_count == 0
            assert timeline.checkpoints[0].node is ResearchNode.PLAN
            assert timeline.approvals[0].resume_claimed is True

            denied_receipt, denied_checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="deny",
            )
            clock.value = NOW + timedelta(seconds=5)
            denied_approval, _ = await service.interrupt(
                scope,
                checkpoint=denied_checkpoint,
                reason=ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY,
            )
            deny_command = DecideResearchApproval(
                research_run_id=denied_receipt.research_run_id,
                approval_request_id=denied_approval.approval_request_id,
                checkpoint_revision=0,
                outcome=ResearchApprovalOutcome.DENY,
            )
            denied = await service.decide(scope, deny_command)
            assert denied.status is ResearchApprovalStatus.DENIED
            assert await service.decide(scope, deny_command) == denied
            assert denied.resume_job_id is None

            timeout_receipt, timeout_checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="timeout",
            )
            clock.value = NOW + timedelta(seconds=6)
            timeout_approval, _ = await service.interrupt(
                scope,
                checkpoint=timeout_checkpoint,
                reason=ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY,
            )
            clock.value = timeout_approval.expires_at
            timed_out = await service.decide(
                scope,
                DecideResearchApproval(
                    research_run_id=timeout_receipt.research_run_id,
                    approval_request_id=timeout_approval.approval_request_id,
                    checkpoint_revision=0,
                    outcome=ResearchApprovalOutcome.ALLOW,
                ),
            )
            assert timed_out.status is ResearchApprovalStatus.TIMED_OUT
            assert timed_out.decided_by_user_id is None
            assert timed_out.resume_job_id is None

            async with session_factory() as session:
                denied_run = await session.get(
                    AgentRunRecord,
                    denied_receipt.agent_run_id,
                )
                timeout_run = await session.get(
                    AgentRunRecord,
                    timeout_receipt.agent_run_id,
                )
            assert denied_run is not None
            assert timeout_run is not None
            assert denied_run.stop_reason is RunStopReason.APPROVAL_DENIED
            assert timeout_run.stop_reason is RunStopReason.APPROVAL_TIMED_OUT
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
