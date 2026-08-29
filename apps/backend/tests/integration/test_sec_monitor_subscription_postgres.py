"""Prove approved SEC Monitor creation and Research resume are one transaction."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.adapters.execution import (
    _approved_monitor_tool_result,
)
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.disclosures.adapters.subscription_sqlalchemy import (
    SqlAlchemySecMonitorSubscriptionRepository,
)
from industry_platform.modules.disclosures.models import (
    SecDisclosureMonitorRecord,
    SecDisclosureMonitorRuleRecord,
    SecDisclosureMonitorWatermarkRecord,
    SecFilerRecord,
)
from industry_platform.modules.disclosures.monitor import SecMonitorStatus
from industry_platform.modules.disclosures.subscription import (
    ChangeSecMonitorStatus,
    DecideSecMonitorSubscription,
    SecMonitorRevisionConflictError,
    SecMonitorSubscriptionService,
)
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.models import Job, OutboxEvent, Schedule
from industry_platform.modules.knowledge.domain import KnowledgeBaseStatus
from industry_platform.modules.knowledge.models import KnowledgeBaseRecord
from industry_platform.modules.research.adapters.durability import (
    SqlAlchemyResearchDurabilityRepository,
)
from industry_platform.modules.research.domain import (
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
)
from industry_platform.modules.research.durability import (
    ApprovalToolRequest,
    ResearchApprovalConflictError,
    ResearchDurabilityService,
    ResearchResumeStateError,
    ResumeTokenCodec,
)
from industry_platform.modules.research.models import (
    ResearchApprovalRequestRecord,
    ResearchSideEffectRecord,
)
from industry_platform.modules.tools.domain import ToolReference, canonical_mapping_sha256
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_research_durability_postgres import NOW, MutableClock, _prepare_paused_research


def _arguments(knowledge_base_id: object) -> dict[str, object]:
    return {
        "cik": "0000320193",
        "knowledge_base_id": str(knowledge_base_id),
        "allowed_forms": ["10-K", "10-K/A"],
        "cron_expression": "0 3 * * *",
        "timezone_name": "Asia/Shanghai",
        "rules": [
            {
                "kind": "new_filing",
                "section_query": "management discussion and analysis",
                "taxonomy": None,
                "concept": None,
                "unit": None,
                "threshold": None,
                "comparator": None,
            }
        ],
    }


def test_monitor_allow_is_atomic_idempotent_and_deny_writes_no_business_rows(
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
                            email=f"monitor-hitl-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=NOW,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Monitor HITL",
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
                            name="SEC filings",
                            description=None,
                            status=KnowledgeBaseStatus.ACTIVE,
                            revision=1,
                        ),
                        SecFilerRecord(
                            id=uuid4(),
                            cik="0000320193",
                            canonical_name="Apple Inc.",
                            normalized_name="apple inc",
                            source_kind="company_tickers",
                            source_version="sec-company-tickers-v1",
                            source_url="https://www.sec.gov/files/company_tickers.json",
                            source_content_sha256=b"a" * 32,
                            source_observed_at=NOW,
                        ),
                    )
                )
            scope = WorkspaceScope(workspace_id, user_id, "owner")
            clock = MutableClock(NOW + timedelta(seconds=2))
            durability = ResearchDurabilityService(
                repository=SqlAlchemyResearchDurabilityRepository(session_factory),
                token_codec=ResumeTokenCodec(b"m" * 32),
                clock=clock,
            )
            receipt, checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="monitor-allow",
            )
            arguments = _arguments(knowledge_base_id)
            approval_id = uuid4()
            approval, _token = await durability.interrupt(
                scope,
                checkpoint=checkpoint,
                reason=ResearchApprovalReason.MONITOR_SUBSCRIPTION,
                request_id=approval_id,
                tool_request=ApprovalToolRequest(
                    call_id=uuid4(),
                    tool=ToolReference("sec.monitor.subscribe", "v1"),
                    arguments=arguments,
                    arguments_sha256=canonical_mapping_sha256(arguments),
                ),
            )
            service = SecMonitorSubscriptionService(
                repository=SqlAlchemySecMonitorSubscriptionRepository(session_factory),
                clock=lambda: NOW + timedelta(seconds=3),
            )
            command = DecideSecMonitorSubscription(
                research_run_id=receipt.research_run_id,
                approval_request_id=approval.approval_request_id,
                checkpoint_revision=checkpoint.revision,
                outcome=ResearchApprovalOutcome.ALLOW,
            )
            with pytest.raises(ResearchApprovalConflictError):
                await service.decide(
                    scope,
                    DecideSecMonitorSubscription(
                        research_run_id=receipt.research_run_id,
                        approval_request_id=approval.approval_request_id,
                        checkpoint_revision=checkpoint.revision + 1,
                        outcome=ResearchApprovalOutcome.ALLOW,
                    ),
                )
            async with session_factory.begin() as session:
                guarded_run = await session.get(AgentRunRecord, receipt.agent_run_id)
                assert guarded_run is not None
                guarded_run.cancel_requested_at = NOW + timedelta(seconds=3)
            with pytest.raises(ResearchResumeStateError):
                await service.decide(scope, command)
            async with session_factory.begin() as session:
                guarded_run = await session.get(AgentRunRecord, receipt.agent_run_id)
                assert guarded_run is not None
                guarded_run.cancel_requested_at = None
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecDisclosureMonitorRecord)
                    )
                    == 0
                )
                assert await session.scalar(select(func.count()).select_from(Schedule)) == 0

            allowed = await service.decide(scope, command)
            repeated = await service.decide(scope, command)

            assert allowed.created is True
            assert allowed.approval.status is ResearchApprovalStatus.ALLOWED
            assert allowed.monitor is not None
            assert repeated.created is False
            assert repeated.monitor == allowed.monitor
            assert repeated.resume_job_id == allowed.resume_job_id
            with pytest.raises(ResearchApprovalConflictError):
                await service.decide(
                    scope,
                    DecideSecMonitorSubscription(
                        research_run_id=receipt.research_run_id,
                        approval_request_id=approval.approval_request_id,
                        checkpoint_revision=checkpoint.revision,
                        outcome=ResearchApprovalOutcome.DENY,
                    ),
                )

            async with session_factory() as session:
                monitor_count = await session.scalar(
                    select(func.count()).select_from(SecDisclosureMonitorRecord)
                )
                schedule_count = await session.scalar(select(func.count()).select_from(Schedule))
                rule_count = await session.scalar(
                    select(func.count()).select_from(SecDisclosureMonitorRuleRecord)
                )
                watermark_count = await session.scalar(
                    select(func.count()).select_from(SecDisclosureMonitorWatermarkRecord)
                )
                effect_count = await session.scalar(
                    select(func.count())
                    .select_from(ResearchSideEffectRecord)
                    .where(ResearchSideEffectRecord.effect_kind == "monitor_subscription")
                )
                approval_record = await session.get(
                    ResearchApprovalRequestRecord, approval.approval_request_id
                )
                effect_record = await session.scalar(
                    select(ResearchSideEffectRecord).where(
                        ResearchSideEffectRecord.run_id == receipt.agent_run_id,
                        ResearchSideEffectRecord.effect_kind == "monitor_subscription",
                    )
                )
                resume_job = await session.get(Job, allowed.resume_job_id)
                outbox = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.payload["job_id"].as_string() == str(allowed.resume_job_id)
                    )
                )
            assert (monitor_count, schedule_count, rule_count, watermark_count, effect_count) == (
                1,
                1,
                1,
                1,
                1,
            )
            assert resume_job is not None
            assert outbox is not None
            assert approval_record is not None
            assert effect_record is not None
            approved_action, approved_observation = _approved_monitor_tool_result(
                approval_record,
                effect_record,
                workspace_id=workspace_id,
            )
            assert approved_action is not None
            assert approved_action.name == "sec.monitor.subscribe"
            assert approved_observation is not None
            assert approved_observation.model_text == f"sec-monitor:{allowed.monitor.monitor_id}"

            monitors = await service.list_monitors(scope)
            assert monitors == (allowed.monitor,)
            assert await service.get_monitor(scope, allowed.monitor.monitor_id) == allowed.monitor
            paused = await service.change_status(
                scope,
                ChangeSecMonitorStatus(
                    monitor_id=allowed.monitor.monitor_id,
                    expected_revision=1,
                    status=SecMonitorStatus.PAUSED,
                ),
            )
            assert paused.status is SecMonitorStatus.PAUSED
            assert paused.revision == 2
            resumed = await service.change_status(
                scope,
                ChangeSecMonitorStatus(
                    monitor_id=allowed.monitor.monitor_id,
                    expected_revision=2,
                    status=SecMonitorStatus.ACTIVE,
                ),
            )
            assert resumed.status is SecMonitorStatus.ACTIVE
            deleted = await service.change_status(
                scope,
                ChangeSecMonitorStatus(
                    monitor_id=allowed.monitor.monitor_id,
                    expected_revision=3,
                    status=SecMonitorStatus.DELETED,
                ),
            )
            assert deleted.status is SecMonitorStatus.DELETED
            with pytest.raises(SecMonitorRevisionConflictError):
                await service.change_status(
                    scope,
                    ChangeSecMonitorStatus(
                        monitor_id=allowed.monitor.monitor_id,
                        expected_revision=4,
                        status=SecMonitorStatus.ACTIVE,
                    ),
                )
            async with session_factory() as session:
                schedule = await session.get(Schedule, allowed.monitor.schedule_id)
                assert schedule is not None
                assert schedule.enabled is False

            denied_receipt, denied_checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="monitor-deny",
            )
            denied_id = uuid4()
            denied_approval, _ = await durability.interrupt(
                scope,
                checkpoint=denied_checkpoint,
                reason=ResearchApprovalReason.MONITOR_SUBSCRIPTION,
                request_id=denied_id,
                tool_request=ApprovalToolRequest(
                    call_id=uuid4(),
                    tool=ToolReference("sec.monitor.subscribe", "v1"),
                    arguments=arguments,
                    arguments_sha256=canonical_mapping_sha256(arguments),
                ),
            )
            denied = await service.decide(
                scope,
                DecideSecMonitorSubscription(
                    research_run_id=denied_receipt.research_run_id,
                    approval_request_id=denied_approval.approval_request_id,
                    checkpoint_revision=denied_checkpoint.revision,
                    outcome=ResearchApprovalOutcome.DENY,
                ),
            )
            assert denied.approval.status is ResearchApprovalStatus.DENIED
            assert denied.monitor is None
            assert denied.resume_job_id is None
            async with session_factory() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecDisclosureMonitorRecord)
                    )
                    == 1
                )
                assert await session.scalar(select(func.count()).select_from(Schedule)) == 1

            timed_out_receipt, timed_out_checkpoint = await _prepare_paused_research(
                session_factory,
                scope,
                knowledge_base_id=knowledge_base_id,
                suffix="monitor-timeout",
            )
            timed_out_approval, _ = await durability.interrupt(
                scope,
                checkpoint=timed_out_checkpoint,
                reason=ResearchApprovalReason.MONITOR_SUBSCRIPTION,
                request_id=uuid4(),
                tool_request=ApprovalToolRequest(
                    call_id=uuid4(),
                    tool=ToolReference("sec.monitor.subscribe", "v1"),
                    arguments=arguments,
                    arguments_sha256=canonical_mapping_sha256(arguments),
                ),
            )
            async with session_factory() as session:
                outbox_count_before_timeout = await session.scalar(
                    select(func.count()).select_from(OutboxEvent)
                )
            timed_out_service = SecMonitorSubscriptionService(
                repository=SqlAlchemySecMonitorSubscriptionRepository(session_factory),
                clock=lambda: NOW + timedelta(minutes=20),
            )
            timed_out_command = DecideSecMonitorSubscription(
                research_run_id=timed_out_receipt.research_run_id,
                approval_request_id=timed_out_approval.approval_request_id,
                checkpoint_revision=timed_out_checkpoint.revision,
                outcome=ResearchApprovalOutcome.ALLOW,
            )
            timed_out = await timed_out_service.decide(scope, timed_out_command)
            repeated_timeout = await timed_out_service.decide(scope, timed_out_command)
            assert timed_out.approval.status is ResearchApprovalStatus.TIMED_OUT
            assert timed_out.monitor is None
            assert timed_out.resume_job_id is None
            assert repeated_timeout == timed_out
            async with session_factory() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SecDisclosureMonitorRecord)
                    )
                    == 1
                )
                assert await session.scalar(select(func.count()).select_from(Schedule)) == 1
                assert (
                    await session.scalar(select(func.count()).select_from(OutboxEvent))
                    == outbox_count_before_timeout
                )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
