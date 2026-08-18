"""Application tests for one-transaction Conversation/Run/Job preparation."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunBudget
from industry_platform.modules.conversations.domain import (
    DirectAnswerTurnReceipt,
    PreparedDirectAnswerTurn,
    StartDirectAnswerTurn,
    TurnSearchMode,
)
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.conversations.submission import (
    ConversationModeNotReadyError,
    ConversationSubmissionService,
    SubmitConversationTurn,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
IDS = tuple(UUID(f"00000000-0000-4000-8000-{value:012d}") for value in range(1, 20))
ATTACHMENT_IDS = (
    UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
)
INDUSTRY_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class RecordingWriter:
    def __init__(self) -> None:
        self.prepared: list[PreparedDirectAnswerTurn] = []

    async def submit(self, prepared: PreparedDirectAnswerTurn) -> DirectAnswerTurnReceipt:
        self.prepared.append(prepared)
        return DirectAnswerTurnReceipt(
            conversation_id=prepared.conversation_id,
            turn_id=prepared.turn_id,
            user_message_id=prepared.user_message_id,
            run_id=prepared.run.run_id,
            job_id=prepared.job.job_id,
            outbox_event_id=prepared.job.outbox_event_id,
            created=True,
        )


def request() -> StartDirectAnswerTurn:
    return StartDirectAnswerTurn(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        trace_id=TraceId("atomic-turn-trace"),
        budget=RunBudget(
            schema_version=1,
            max_steps=2,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        runtime_version="direct-answer-runtime-v0",
        harness_version="harness-v0",
        idempotency_key="browser-request-1",
        question="private-user-question",
        new_conversation_title="New conversation",
        attachment_ids=ATTACHMENT_IDS,
    )


def service(writer: RecordingWriter) -> ConversationApplicationService:
    iterator = iter(IDS)

    @asynccontextmanager
    async def transaction() -> AsyncIterator[RecordingWriter]:
        yield writer

    return ConversationApplicationService(
        transaction_factory=transaction,
        clock=lambda: NOW,
        id_source=lambda: next(iterator),
    )


@pytest.mark.asyncio
async def test_service_prepares_one_linked_queued_run_and_durable_job() -> None:
    writer = RecordingWriter()

    receipt = await service(writer).start_direct_answer(request())

    assert receipt.created is True
    assert len(writer.prepared) == 1
    prepared = writer.prepared[0]
    assert prepared.run.status is AgentRunStatus.QUEUED
    assert prepared.run.thread_id == prepared.conversation_id
    assert prepared.run.turn_id == prepared.turn_id
    assert prepared.run.job_id == prepared.job.job_id == receipt.job_id
    assert prepared.job.scope.workspace_id == WORKSPACE_ID
    assert prepared.job.payload == {"agent_run_id": str(prepared.run.run_id), "schema_version": 1}
    assert prepared.attachment_ids == ATTACHMENT_IDS
    assert "private-user-question" not in repr(prepared)
    assert "private-user-question" not in str(dict(prepared.job.payload))


@pytest.mark.asyncio
async def test_same_idempotency_key_builds_the_same_run_id() -> None:
    first_writer = RecordingWriter()
    second_writer = RecordingWriter()

    await service(first_writer).start_direct_answer(request())
    await service(second_writer).start_direct_answer(request())

    assert first_writer.prepared[0].run.run_id == second_writer.prepared[0].run.run_id


@pytest.mark.asyncio
async def test_service_derives_title_when_a_new_conversation_omits_it() -> None:
    writer = RecordingWriter()
    automatic = replace(
        request(),
        new_conversation_title=None,
        question="  Explain   the quarterly risks.  ",
    )

    await service(writer).start_direct_answer(automatic)

    assert writer.prepared[0].conversation_title == "Explain the quarterly risks."


@pytest.mark.asyncio
async def test_transaction_failure_is_not_reported_as_an_accepted_turn() -> None:
    class FailingWriter(RecordingWriter):
        async def submit(self, prepared: PreparedDirectAnswerTurn) -> DirectAnswerTurnReceipt:
            self.prepared.append(prepared)
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await service(FailingWriter()).start_direct_answer(request())


@pytest.mark.asyncio
async def test_submission_policy_builds_the_trusted_command_and_budget() -> None:
    writer = RecordingWriter()
    submission = ConversationSubmissionService(
        application=service(writer),
        clock=lambda: NOW,
    )

    receipt = await submission.submit(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        SubmitConversationTurn(
            trace_id=TraceId("http-turn-trace"),
            idempotency_key="http-request-1",
            question="Explain this market.",
            attachment_ids=ATTACHMENT_IDS,
        ),
    )

    assert receipt.created is True
    prepared = writer.prepared[0]
    assert prepared.run.workspace_id == WORKSPACE_ID
    assert prepared.run.user_id == USER_ID
    assert prepared.run.runtime_version == "direct-answer-runtime-v0"
    assert prepared.run.harness_version == "harness-v0"
    assert prepared.run.budget.max_steps == 2
    assert prepared.run.budget.max_total_tokens == 4_096
    assert prepared.run.budget.max_cost_micro_usd == 250_000
    assert prepared.run.budget.deadline == NOW + timedelta(seconds=300)
    assert prepared.attachment_ids == ATTACHMENT_IDS


@pytest.mark.asyncio
async def test_submission_rejects_viewers_and_unready_modes_before_writing() -> None:
    writer = RecordingWriter()
    submission = ConversationSubmissionService(
        application=service(writer),
        clock=lambda: NOW,
    )
    request_value = SubmitConversationTurn(
        trace_id=TraceId("http-turn-trace"),
        idempotency_key="http-request-1",
        question="Explain this market.",
    )

    with pytest.raises(WorkspaceAccessDeniedError):
        await submission.submit(WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"), request_value)
    with pytest.raises(ConversationModeNotReadyError):
        await submission.submit(
            WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
            replace(request_value, search_mode=TurnSearchMode.LOCAL),
        )

    assert writer.prepared == []


@pytest.mark.asyncio
async def test_submission_materializes_web_mode_as_a_bounded_tool_run() -> None:
    writer = RecordingWriter()
    submission = ConversationSubmissionService(application=service(writer), clock=lambda: NOW)

    await submission.submit(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        SubmitConversationTurn(
            trace_id=TraceId("web-turn-trace"),
            idempotency_key="web-request-1",
            question="Find current policy updates.",
            search_mode=TurnSearchMode.WEB,
            industry_id=INDUSTRY_ID,
        ),
    )

    prepared = writer.prepared[0]
    assert prepared.run.run_type.value == "tool_loop"
    assert prepared.run.runtime_version == "agent-runtime-v1"
    assert prepared.run.harness_version == "harness-v1"
    assert prepared.run.budget.max_steps == 8
    assert prepared.run.budget.max_total_tokens == 8_192
    assert prepared.search_mode is TurnSearchMode.WEB
    assert prepared.industry_id == INDUSTRY_ID
