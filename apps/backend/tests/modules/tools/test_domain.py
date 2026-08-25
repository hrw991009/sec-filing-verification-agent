"""Contract and security tests for Tool Action, approval, and idempotency facts."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.agent_harness.tool_fakes import fake_lookup_definition
from industry_platform.modules.tools.domain import (
    ApprovalDecision,
    ToolAction,
    ToolApprovalOutcome,
    ToolApprovalPolicy,
    ToolCall,
    ToolCostClass,
    ToolObservation,
    ToolPolicyDecision,
    ToolReference,
    ToolRetryClassification,
    ToolSideEffectClass,
    ToolSource,
    sanitized_arguments_summary,
    tool_observation_envelope_sha256,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction

CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
STEP_ID = UUID("44444444-4444-4444-8444-444444444444")
USER_ID = UUID("55555555-5555-4555-8555-555555555555")
APPROVAL_ID = UUID("66666666-6666-4666-8666-666666666666")
NOW = datetime(2026, 8, 16, 3, 0, tzinfo=UTC)
OBSERVATION_TEXT = "Steel demand rose 3%."
OBSERVATION_SHA256 = hashlib.sha256(OBSERVATION_TEXT.encode("utf-8")).hexdigest()


def observation(*, locator: str = "fixture://industry/steel") -> ToolObservation:
    return ToolObservation(
        schema_version=1,
        observation_id=APPROVAL_ID,
        call_id=CALL_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        tool=ToolReference("fake.industry_lookup", "v1"),
        normalizer_version="tool-observation-v1",
        model_text=OBSERVATION_TEXT,
        sources=(
            ToolSource(
                source_type="fake_fixture",
                source_version="fixture-v1",
                locator=locator,
                observed_at=NOW,
                content_sha256=OBSERVATION_SHA256,
            ),
        ),
        observed_at=NOW,
        content_sha256=OBSERVATION_SHA256,
    )


def test_tool_action_rejects_duplicate_unknown_and_non_json_envelopes() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ToolAction.from_json(
            '{"schema_version":1,"kind":"tool_call","name":"fake.lookup",'
            '"name":"fake.lookup","version":"v1","arguments":{}}'
        )
    with pytest.raises(ValueError, match="fields"):
        ToolAction.from_json(
            '{"schema_version":1,"kind":"tool_call","name":"fake.lookup",'
            '"version":"v1","arguments":{},"secret":"must-not-pass"}'
        )
    with pytest.raises(ValueError, match="valid JSON"):
        ToolAction.from_json("run fake.lookup now")


def test_argument_sanitizer_never_persists_model_controlled_keys_or_values() -> None:
    model_controlled_key = "secret-value-disguised-as-key"
    summary = sanitized_arguments_summary({model_controlled_key: "and-a-secret-value"})

    assert summary == {"argument_count": 1, "canonical_bytes": 54}
    assert model_controlled_key not in repr(summary)
    assert "and-a-secret-value" not in repr(summary)


def test_tool_definition_requires_real_enum_instances_not_equal_raw_strings() -> None:
    definition = fake_lookup_definition()

    with pytest.raises(ValueError, match="Tool capability must be a WorkspaceAction instance"):
        replace(
            definition,
            capability=cast(WorkspaceAction, WorkspaceAction.RUN_TOOL.value),
        )
    with pytest.raises(ValueError, match="Tool cost class must be a ToolCostClass instance"):
        replace(definition, cost_class=cast(ToolCostClass, ToolCostClass.LOW.value))
    with pytest.raises(ValueError, match="side-effect class"):
        replace(
            definition,
            side_effect_class=cast(
                ToolSideEffectClass,
                ToolSideEffectClass.READ_ONLY.value,
            ),
        )
    with pytest.raises(ValueError, match="retry classification"):
        replace(
            definition,
            retry_classification=cast(
                ToolRetryClassification,
                ToolRetryClassification.NEVER.value,
            ),
        )
    with pytest.raises(ValueError, match="approval policy"):
        replace(
            definition,
            approval_policy=cast(
                ToolApprovalPolicy,
                ToolApprovalPolicy.AUTO_ALLOW.value,
            ),
        )


def test_tool_definition_bounds_description_and_retry_classification() -> None:
    definition = fake_lookup_definition()

    assert definition.description
    assert definition.retry_classification is ToolRetryClassification.NEVER
    with pytest.raises(ValueError, match="description"):
        replace(definition, description=" carries surrounding whitespace ")
    with pytest.raises(ValueError, match="Read-only retry"):
        replace(
            definition,
            side_effect_class=ToolSideEffectClass.IDEMPOTENT_WRITE,
            retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        )


def test_write_tool_call_requires_only_a_server_hashed_idempotency_key() -> None:
    definition = replace(
        fake_lookup_definition(),
        side_effect_class=ToolSideEffectClass.IDEMPOTENT_WRITE,
    )
    decision = ToolPolicyDecision(
        outcome=ToolApprovalOutcome.ALLOW,
        policy_version=definition.policy_version,
        reason_code="static_policy_allowed",
    )

    with pytest.raises(ValueError, match="idempotency"):
        ToolCall(
            schema_version=1,
            call_id=CALL_ID,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            requested_by_step_id=STEP_ID,
            requested_by_user_id=USER_ID,
            definition=definition,
            arguments={"query": "steel"},
            decision=decision,
            requested_at=NOW,
        )

    raw_key = "server-generated-idempotency-key"
    call = ToolCall(
        schema_version=1,
        call_id=CALL_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        requested_by_step_id=STEP_ID,
        requested_by_user_id=USER_ID,
        definition=definition,
        arguments={"query": "steel"},
        decision=decision,
        requested_at=NOW,
        side_effect_idempotency_key=raw_key,
        idempotency_key_sha256=hashlib.sha256(raw_key.encode()).hexdigest(),
    )

    assert raw_key not in repr(call)
    assert call.side_effect_idempotency_key == raw_key
    assert call.idempotency_key_sha256 == hashlib.sha256(raw_key.encode()).hexdigest()

    with pytest.raises(ValueError, match="hash"):
        replace(call, idempotency_key_sha256="0" * 64)


@pytest.mark.parametrize(
    "locator",
    [
        "https://user:secret@example.com/report",
        "https://example.com/report?access_token=secret",
        "https://example.com/report#private-fragment",
        "fixture://industry/steel\x00hidden",
        "data:text/plain,secret-token",
        "javascript:alert(1)",
        "file:///private/report",
        "https:///missing-host",
        "https://example.com/report with spaces",
        "https://example.com\\ambiguous",
        "https://example.com:invalid/report",
    ],
)
def test_tool_source_locator_rejects_secret_bearing_or_ambiguous_urls(locator: str) -> None:
    with pytest.raises(ValueError, match="locator"):
        observation(locator=locator)


def test_observation_envelope_digest_binds_content_and_full_provenance() -> None:
    first = observation(locator="fixture://industry/steel")
    equivalent = observation(locator="fixture://industry/steel")
    changed_provenance = observation(locator="fixture://industry/aluminium")

    assert first.content_sha256 == changed_provenance.content_sha256
    assert first.model_visible_envelope_sha256 == equivalent.model_visible_envelope_sha256
    assert first.model_visible_envelope_sha256 != changed_provenance.model_visible_envelope_sha256
    assert first.model_visible_envelope_sha256 == tool_observation_envelope_sha256(first)
    envelope = first.to_model_visible_envelope()
    assert envelope["content"] == OBSERVATION_TEXT
    assert envelope["content_sha256"] == OBSERVATION_SHA256
    assert envelope["locator"] == {
        "sources": [
            {
                "source_type": "fake_fixture",
                "source_version": "fixture-v1",
                "locator": "fixture://industry/steel",
                "observed_at": "2026-08-16T03:00:00Z",
                "content_sha256": OBSERVATION_SHA256,
            }
        ]
    }


def test_no_result_observation_is_valid_without_fabricated_sources() -> None:
    no_result_text = "knowledge_search status=no_result"
    no_result = replace(
        observation(),
        model_text=no_result_text,
        sources=(),
        content_sha256=hashlib.sha256(no_result_text.encode()).hexdigest(),
    )

    assert no_result.sources == ()
    assert no_result.sanitized_output_summary["source_count"] == 0
    assert no_result.to_model_visible_envelope()["locator"] == {"sources": []}


def test_approval_decision_cannot_remain_in_the_request_only_state() -> None:
    with pytest.raises(ValueError, match="allow or deny"):
        ApprovalDecision(
            schema_version=1,
            approval_request_id=APPROVAL_ID,
            decided_by_user_id=USER_ID,
            outcome=ToolApprovalOutcome.APPROVAL_REQUIRED,
            policy_version="static-policy-v1",
            reason_code="still_waiting",
            decided_at=NOW,
        )
