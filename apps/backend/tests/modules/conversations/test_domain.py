"""Domain tests for durable direct-answer turn acceptance."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.conversations.domain import (
    StartDirectAnswerTurn,
    TurnSearchMode,
    deterministic_run_id,
    fingerprint_direct_answer_turn,
)
from industry_platform.modules.identity.domain import TraceId

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")


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

    with pytest.raises(ValueError, match="Exactly one"):
        command(conversation_id=existing.conversation_id)


@pytest.mark.parametrize("mode", [TurnSearchMode.WEB, TurnSearchMode.LOCAL, TurnSearchMode.BOTH])
def test_unready_search_modes_fail_instead_of_returning_mock_results(
    mode: TurnSearchMode,
) -> None:
    with pytest.raises(ValueError, match="Only search mode 'none'"):
        command(search_mode=mode)


def test_local_knowledge_selection_is_rejected_until_its_real_tool_is_ready() -> None:
    with pytest.raises(ValueError, match="Local knowledge mode is not ready"):
        command(knowledge_base_ids=(UUID("55555555-5555-4555-8555-555555555555"),))


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
