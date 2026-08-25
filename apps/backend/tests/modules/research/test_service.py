"""Authorization and trusted-command tests for Research L3 submission."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from industry_platform.modules.conversations.domain import (
    DirectAnswerTurnReceipt,
    StartDirectAnswerTurn,
    TurnSearchMode,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.research.domain import (
    ResearchBriefInput,
    research_run_id_for_agent_run,
)
from industry_platform.modules.research.service import (
    ResearchSubmissionService,
    StartResearch,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000001")
USER_ID = UUID("20000000-0000-4000-8000-000000000002")
INDUSTRY_ID = UUID("20000000-0000-4000-8000-000000000003")
RUN_ID = UUID("20000000-0000-4000-8000-000000000004")
CONVERSATION_ID = UUID("20000000-0000-4000-8000-000000000005")
TURN_ID = UUID("20000000-0000-4000-8000-000000000006")
MESSAGE_ID = UUID("20000000-0000-4000-8000-000000000007")
JOB_ID = UUID("20000000-0000-4000-8000-000000000008")
OUTBOX_ID = UUID("20000000-0000-4000-8000-000000000009")
KNOWLEDGE_BASE_ID = UUID("20000000-0000-4000-8000-000000000010")


@dataclass
class RecordingStarter:
    commands: list[StartDirectAnswerTurn] = field(default_factory=list)

    async def start_direct_answer(self, command: StartDirectAnswerTurn) -> DirectAnswerTurnReceipt:
        self.commands.append(command)
        return DirectAnswerTurnReceipt(
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            user_message_id=MESSAGE_ID,
            run_id=RUN_ID,
            job_id=JOB_ID,
            outbox_event_id=OUTBOX_ID,
            created=True,
        )


def request() -> StartResearch:
    return StartResearch(
        trace_id=TraceId("research-http-trace"),
        industry_id=INDUSTRY_ID,
        brief=ResearchBriefInput(
            original_question="Compare steel and copper changes.",
            confirmed_scope=("Public market sources",),
            exclusions=("Investment advice",),
            completion_criteria=("Produce an attributable L3 draft",),
        ),
        idempotency_key="research-request-1",
        max_steps=20,
        max_total_tokens=12_000,
        max_cost_micro_usd=300_000,
        timeout_seconds=600,
    )


def local_request() -> StartResearch:
    return StartResearch(
        trace_id=TraceId("local-research-http-trace"),
        industry_id=None,
        search_mode=TurnSearchMode.LOCAL,
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        brief=ResearchBriefInput(
            original_question="Calculate Apple's fiscal 2023 net sales change.",
            confirmed_scope=("Apple 2023 Form 10-K",),
            exclusions=("Live SEC data",),
            completion_criteria=("Produce filing and calculation Evidence",),
            financial_scope=FinancialScope(
                cik="0000320193",
                accession="0000320193-23-000106",
                form=FinancialForm.TEN_K,
                report_period=date(2023, 9, 30),
                as_of=datetime(2023, 11, 3, 12, tzinfo=UTC),
                unit="USD",
                scale=6,
            ),
        ),
        idempotency_key="local-research-request-1",
    )


@pytest.mark.asyncio
async def test_member_submission_builds_the_trusted_research_command() -> None:
    starter = RecordingStarter()
    service = ResearchSubmissionService(starter, clock=lambda: NOW)

    receipt = await service.start(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        request(),
    )

    assert receipt.research_run_id == research_run_id_for_agent_run(RUN_ID)
    assert receipt.agent_run_id == RUN_ID
    assert receipt.created is True
    assert len(starter.commands) == 1
    command = starter.commands[0]
    assert command.workspace_id == WORKSPACE_ID
    assert command.user_id == USER_ID
    assert command.search_mode is TurnSearchMode.WEB
    assert command.industry_id == INDUSTRY_ID
    assert command.runtime_version == "agent-runtime-v1"
    assert command.harness_version == "harness-research-v1"
    assert command.research_brief == request().brief
    assert command.budget.max_steps == 20
    assert command.budget.max_total_tokens == 12_000
    assert command.budget.max_cost_micro_usd == 300_000
    assert command.budget.deadline.timestamp() - NOW.timestamp() == 600


@pytest.mark.asyncio
async def test_local_submission_pins_the_kb_and_financial_scope() -> None:
    starter = RecordingStarter()
    service = ResearchSubmissionService(starter, clock=lambda: NOW)

    await service.start(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        local_request(),
    )

    command = starter.commands[0]
    assert command.search_mode is TurnSearchMode.LOCAL
    assert command.knowledge_base_ids == (KNOWLEDGE_BASE_ID,)
    assert command.industry_id is None
    assert command.research_brief is not None
    assert command.research_brief.financial_scope == local_request().brief.financial_scope


@pytest.mark.asyncio
async def test_viewer_cannot_submit_research() -> None:
    starter = RecordingStarter()
    service = ResearchSubmissionService(starter, clock=lambda: NOW)

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.start(
            WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"),
            request(),
        )

    assert starter.commands == []
