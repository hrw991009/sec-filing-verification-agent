"""Domain tests for durable direct-answer turn acceptance."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.conversations.domain import (
    StartDirectAnswerTurn,
    TurnSearchMode,
    derive_conversation_title,
    deterministic_run_id,
    fingerprint_direct_answer_turn,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.research.domain import ResearchBriefInput

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
INDUSTRY_ID = UUID("55555555-5555-4555-8555-555555555555")
ATTACHMENT_IDS = tuple(UUID(f"00000000-0000-4000-8000-{value:012d}") for value in range(1, 6))


def financial_scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=datetime(2023, 11, 3, tzinfo=UTC),
        unit="USD",
        scale=6,
    )


def research_brief() -> ResearchBriefInput:
    return ResearchBriefInput(
        original_question="请总结这家公司的主要风险。",
        confirmed_scope=("Apple 2023 10-K",),
        exclusions=("实时行情",),
        completion_criteria=("给出可追溯证据",),
        financial_scope=financial_scope(),
    )


def budget() -> RunBudget:
    return RunBudget(
        schema_version=1,
        max_steps=2,
        max_total_tokens=1_000,
        max_cost_micro_usd=100_000,
        deadline=NOW + timedelta(minutes=5),
    )


def command(**changes: object) -> StartDirectAnswerTurn:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE_ID,
        "user_id": USER_ID,
        "trace_id": TraceId("turn-trace"),
        "budget": budget(),
        "runtime_version": "direct-answer-runtime-v0",
        "harness_version": "harness-v0",
        "idempotency_key": "browser-request-1",
        "question": "请总结这家公司的主要风险。",
        "new_conversation_title": "公司风险分析",
    }
    values.update(changes)
    return StartDirectAnswerTurn(**values)  # type: ignore[arg-type]


def test_turn_accepts_only_one_new_or_existing_conversation_target() -> None:
    existing = command(
        conversation_id=UUID("44444444-4444-4444-8444-444444444444"),
        new_conversation_title=None,
    )

    assert existing.conversation_id is not None

    with pytest.raises(ValueError, match="cannot declare"):
        command(conversation_id=existing.conversation_id)


def test_new_conversation_can_derive_a_bounded_one_line_title() -> None:
    automatic = command(new_conversation_title=None)
    long_question = "  first line\n" + "detail " * 40

    assert automatic.conversation_id is None
    title = derive_conversation_title(long_question)
    assert "\n" not in title
    assert len(title) == 160
    assert title.endswith("...")


def test_combined_search_mode_stays_unavailable() -> None:
    with pytest.raises(ValueError, match="Search mode is not ready"):
        command(search_mode=TurnSearchMode.BOTH)


def test_local_search_requires_the_pinned_research_scope() -> None:
    with pytest.raises(ValueError, match="Knowledge Base allowlist"):
        command(search_mode=TurnSearchMode.LOCAL)

    with pytest.raises(ValueError, match="only ready for Research"):
        command(
            search_mode=TurnSearchMode.LOCAL,
            knowledge_base_ids=(INDUSTRY_ID,),
        )

    accepted = command(
        search_mode=TurnSearchMode.LOCAL,
        knowledge_base_ids=(INDUSTRY_ID,),
        research_brief=research_brief(),
        runtime_version="agent-runtime-v1",
        harness_version="harness-research-v1",
    )

    assert accepted.research_brief is not None
    assert accepted.research_brief.financial_scope == financial_scope()


def test_web_mode_requires_an_industry_and_the_l2_runtime_version() -> None:
    with pytest.raises(ValueError, match="industry snapshot"):
        command(
            search_mode=TurnSearchMode.WEB,
            runtime_version="agent-runtime-v1",
            harness_version="harness-v1",
        )

    accepted = command(
        search_mode=TurnSearchMode.WEB,
        industry_id=INDUSTRY_ID,
        runtime_version="agent-runtime-v1",
        harness_version="harness-v1",
    )
    assert accepted.search_mode is TurnSearchMode.WEB
    assert accepted.industry_id == INDUSTRY_ID


def test_non_local_turn_rejects_a_knowledge_base_allowlist() -> None:
    with pytest.raises(ValueError, match="requires local search mode"):
        command(knowledge_base_ids=(INDUSTRY_ID,))


def test_run_id_is_stable_per_workspace_and_idempotency_key() -> None:
    first = deterministic_run_id(
        workspace_id=WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )
    repeated = deterministic_run_id(
        workspace_id=WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )
    another_workspace = deterministic_run_id(
        workspace_id=OTHER_WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )

    assert first == repeated
    assert first != another_workspace


def test_sensitive_user_input_is_hidden_from_command_repr() -> None:
    request = command(question="sensitive-customer-plan")

    assert "sensitive-customer-plan" not in repr(request)


def test_changed_user_input_changes_the_private_request_fingerprint() -> None:
    first = command(question="first question")
    changed = command(question="changed question")
    run_id = deterministic_run_id(
        workspace_id=WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )

    assert fingerprint_direct_answer_turn(first, run_id=run_id) != fingerprint_direct_answer_turn(
        changed, run_id=run_id
    )


def test_attachment_ids_are_ordered_unique_and_bounded() -> None:
    accepted = command(attachment_ids=ATTACHMENT_IDS[:4])

    assert accepted.attachment_ids == ATTACHMENT_IDS[:4]
    with pytest.raises(ValueError, match="must be unique"):
        command(attachment_ids=(ATTACHMENT_IDS[0], ATTACHMENT_IDS[0]))
    with pytest.raises(ValueError, match="limit exceeded"):
        command(attachment_ids=ATTACHMENT_IDS)


def test_attachment_order_is_part_of_the_idempotency_fingerprint() -> None:
    first = command(attachment_ids=ATTACHMENT_IDS[:2])
    reversed_order = command(attachment_ids=tuple(reversed(ATTACHMENT_IDS[:2])))
    run_id = deterministic_run_id(
        workspace_id=WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )

    assert fingerprint_direct_answer_turn(first, run_id=run_id) != fingerprint_direct_answer_turn(
        reversed_order, run_id=run_id
    )


def test_server_generated_deadline_does_not_break_an_idempotent_retry() -> None:
    first = command()
    retried = replace(
        first,
        budget=replace(first.budget, deadline=first.budget.deadline + timedelta(seconds=1)),
    )
    run_id = deterministic_run_id(
        workspace_id=WORKSPACE_ID,
        idempotency_key="browser-request-1",
    )

    assert fingerprint_direct_answer_turn(first, run_id=run_id) == fingerprint_direct_answer_turn(
        retried, run_id=run_id
    )
