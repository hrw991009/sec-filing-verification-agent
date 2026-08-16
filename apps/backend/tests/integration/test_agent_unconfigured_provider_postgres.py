"""Prove production composition fails closed when no model Provider is configured."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
from sqlalchemy import select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunBudget, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.models import Message, MessageRole
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyOutboxTransactionFactory,
)
from industry_platform.modules.jobs.domain import ClaimedJobDispatch, JobStatus
from industry_platform.modules.jobs.models import Job
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.dispatcher import OutboxDispatcher
from industry_platform.workers.runtime import (
    JobExecutionDisposition,
    create_job_delivery_runtime,
)

from .postgres import PostgresProbe


@dataclass(slots=True)
class CapturingPublisher:
    """Capture only the bounded broker coordinates produced by the real dispatcher."""

    dispatches: list[ClaimedJobDispatch] = field(default_factory=list)

    async def publish(self, dispatch: ClaimedJobDispatch) -> None:
        self.dispatches.append(dispatch)


def test_unconfigured_provider_reaches_one_explainable_terminal_failure(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings.model_copy(
            update={
                "agent_model_provider_base_url": None,
                "agent_model_provider_api_key": None,
                "agent_model_route": None,
            }
        )
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        accepted_at = datetime.now(UTC)
        question = "Keep this user message even when the production Provider is absent."
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"unconfigured-provider-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=accepted_at,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Unconfigured Provider Workspace",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )

            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: accepted_at,
            ).start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    trace_id=TraceId("unconfigured-provider-full-chain"),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=2,
                        max_total_tokens=4_096,
                        max_cost_micro_usd=250_000,
                        deadline=accepted_at + timedelta(minutes=5),
                    ),
                    runtime_version="direct-answer-runtime-v0",
                    harness_version="harness-v0",
                    idempotency_key=f"unconfigured-provider-{user_id}",
                    question=question,
                )
            )

            publisher = CapturingPublisher()
            dispatch_result = await OutboxDispatcher(
                transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
                publisher=publisher,
                dispatcher_id="unconfigured-provider-dispatcher",
                batch_size=1,
                claim_seconds=60,
            ).dispatch_once()
            assert dispatch_result.published == 1
            assert len(publisher.dispatches) == 1

            def reject_network(request: httpx2.Request) -> httpx2.Response:
                raise AssertionError(f"No Provider request was expected for {request.url.host}")

            async with httpx2.AsyncClient(transport=httpx2.MockTransport(reject_network)) as client:
                worker = create_job_delivery_runtime(settings, session_factory, client)
                disposition = await worker.execute(publisher.dispatches[0].message)

            assert disposition is JobExecutionDisposition.SUCCEEDED
            async with session_factory() as session:
                run = await session.get(AgentRunRecord, receipt.run_id)
                job = await session.get(Job, receipt.job_id)
                events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == receipt.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                steps = tuple(
                    await session.scalars(
                        select(AgentStepRecord)
                        .where(AgentStepRecord.run_id == receipt.run_id)
                        .order_by(AgentStepRecord.sequence)
                    )
                )
                manifests = tuple(
                    await session.scalars(
                        select(ContextManifestRecord).where(
                            ContextManifestRecord.run_id == receipt.run_id
                        )
                    )
                )
                messages = tuple(
                    await session.scalars(
                        select(Message)
                        .where(Message.agent_run_id == receipt.run_id)
                        .order_by(Message.created_at, Message.id)
                    )
                )

            assert run is not None
            assert job is not None
            assert run.status is AgentRunStatus.FAILED
            assert run.stop_reason is RunStopReason.PROVIDER_ERROR
            assert job.status is JobStatus.SUCCEEDED
            assert tuple(event.event_type for event in events) == (
                AgentEventType.RUN_QUEUED,
                AgentEventType.RUN_STARTED,
                AgentEventType.STEP_STARTED,
                AgentEventType.MODEL_STARTED,
                AgentEventType.STEP_FAILED,
                AgentEventType.RUN_FAILED,
            )
            assert events[-2].payload["error_code"] == "provider_not_configured"
            assert events[-1].payload["stop_reason"] == RunStopReason.PROVIDER_ERROR.value
            assert len(steps) == 1
            assert len(manifests) == 1
            assert len(messages) == 1
            assert messages[0].role is MessageRole.USER
            assert messages[0].content_markdown == question
            assert question not in repr(events)
            assert question not in repr(manifests)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
