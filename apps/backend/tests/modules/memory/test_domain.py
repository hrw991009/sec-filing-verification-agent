"""Domain tests for candidate policy and write commands."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.memory.domain import (
    CreateMemoryCandidate,
    MemoryKind,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryScope,
    MemorySourceMessage,
    MemoryWriteAction,
    ResolveMemoryCandidate,
    assess_memory_candidate,
    build_candidate_content,
    hash_idempotency_key,
)

CONVERSATION_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_MESSAGE_ID = UUID("22222222-2222-4222-8222-222222222222")
ASSISTANT_MESSAGE_ID = UUID("33333333-3333-4333-8333-333333333333")
MEMORY_ID = UUID("44444444-4444-4444-8444-444444444444")
CANDIDATE_ID = UUID("55555555-5555-4555-8555-555555555555")


def source(message_id: UUID, role: str, content: str) -> MemorySourceMessage:
    return MemorySourceMessage(
        message_id=message_id,
        conversation_id=CONVERSATION_ID,
        role=role,
        content_markdown=content,
    )


def test_user_and_mixed_sources_produce_explainable_allowed_candidates() -> None:
    user_source = source(USER_MESSAGE_ID, "user", "以后优先使用中文回答。")
    assistant_source = source(ASSISTANT_MESSAGE_ID, "assistant", "我会使用中文回答。")

    user_content = build_candidate_content((user_source,))
    mixed_content = build_candidate_content((user_source, assistant_source))
    user_policy = assess_memory_candidate((user_source,), user_content)
    mixed_policy = assess_memory_candidate((user_source, assistant_source), mixed_content)

    assert user_content == "以后优先使用中文回答。"
    assert "用户：以后优先使用中文回答。" in mixed_content  # noqa: RUF001
    assert "助手：我会使用中文回答。" in mixed_content  # noqa: RUF001
    assert user_policy.decision is MemoryPolicyDecision.ALLOWED
    assert user_policy.reason is MemoryPolicyReason.USER_AUTHORED
    assert user_policy.confidence == 0.95
    assert mixed_policy.decision is MemoryPolicyDecision.ALLOWED
    assert mixed_policy.reason is MemoryPolicyReason.MIXED_SOURCES


def test_assistant_only_requires_edit_and_sensitive_content_is_rejected() -> None:
    assistant_source = source(ASSISTANT_MESSAGE_ID, "assistant", "这家公司偏好稳健增长。")
    sensitive_source = source(
        USER_MESSAGE_ID,
        "user",
        "api_key = sk-do-not-store-this-value",
    )

    assistant_policy = assess_memory_candidate(
        (assistant_source,), build_candidate_content((assistant_source,))
    )
    sensitive_policy = assess_memory_candidate(
        (sensitive_source,), build_candidate_content((sensitive_source,))
    )

    assert assistant_policy.decision is MemoryPolicyDecision.REQUIRES_EDIT
    assert assistant_policy.reason is MemoryPolicyReason.ASSISTANT_ONLY_REQUIRES_EDIT
    assert sensitive_policy.decision is MemoryPolicyDecision.REJECTED
    assert sensitive_policy.reason is MemoryPolicyReason.SENSITIVE_CONTENT
    assert sensitive_policy.confidence == 0


def test_commands_reject_duplicate_sources_and_mismatched_update_targets() -> None:
    with pytest.raises(ValueError, match="unique"):
        CreateMemoryCandidate(
            conversation_id=CONVERSATION_ID,
            message_ids=(USER_MESSAGE_ID, USER_MESSAGE_ID),
            scope=MemoryScope.USER,
            idempotency_key="memory-candidate-1",
            trace_id=TraceId("memory-domain-test"),
        )

    with pytest.raises(ValueError, match="target"):
        ResolveMemoryCandidate(
            candidate_id=CANDIDATE_ID,
            expected_candidate_revision=1,
            action=MemoryWriteAction.UPDATE,
            content="保留的偏好",
            scope=MemoryScope.USER,
            kind=MemoryKind.PREFERENCE,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            target_memory_id=None,
            expected_target_revision=None,
            trace_id=TraceId("memory-domain-test"),
        )


def test_idempotency_hash_does_not_retain_the_original_key() -> None:
    raw = "memory-candidate-idempotency-1"

    digest = hash_idempotency_key(raw)

    assert len(digest) == 32
    assert raw.encode() not in digest
