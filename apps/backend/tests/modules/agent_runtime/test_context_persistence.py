"""Regression coverage for Context manifest persistence projections."""

from datetime import UTC, datetime
from uuid import UUID

from industry_platform.modules.agent_runtime.adapters.persistence import _manifest_values
from industry_platform.modules.agent_runtime.context import (
    ContextBudgetSnapshot,
    ContextDecisionReason,
    ContextManifest,
    ContextSourceKind,
    ContextSourceManifestEntry,
)
from industry_platform.modules.agent_runtime.model import ModelRole


def test_manifest_values_projects_frozen_source_identity_to_plain_json() -> None:
    manifest = ContextManifest(
        schema_version=1,
        manifest_id=UUID("10000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000002"),
        run_id=UUID("30000000-0000-4000-8000-000000000003"),
        step_id=UUID("40000000-0000-4000-8000-000000000004"),
        compiler_version="financial-context-v1",
        prompt_version="prompt-v0",
        runtime_projection_version="runtime-context-projection-v0",
        token_counter_version="test-counter-v1",  # noqa: S106 - version, not a credential
        budget=ContextBudgetSnapshot(
            run_max_total_tokens=1_000,
            tokens_used_before_step=0,
            max_input_tokens=300,
            estimated_input_tokens=10,
            allowed_output_tokens=500,
            unreserved_run_tokens=490,
        ),
        sources=(
            ContextSourceManifestEntry(
                ordinal=1,
                source_kind=ContextSourceKind.SYSTEM_INSTRUCTIONS,
                source_id="system-instructions",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=1,
                message_role=ModelRole.SYSTEM,
            ),
            ContextSourceManifestEntry(
                ordinal=2,
                source_kind=ContextSourceKind.RUNTIME_CONTEXT_PROJECTION,
                source_id="runtime-context",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=1,
                message_role=ModelRole.SYSTEM,
            ),
            ContextSourceManifestEntry(
                ordinal=3,
                source_kind=ContextSourceKind.FINANCIAL_SCOPE,
                source_id="financial-scope:0000320193-25-000079",
                source_version="financial-scope-v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=10,
                message_role=ModelRole.USER,
                source_identity={
                    "accession": "0000320193-25-000079",
                    "nested": {"unit": "USD"},
                },
            ),
            ContextSourceManifestEntry(
                ordinal=4,
                source_kind=ContextSourceKind.CONVERSATION_SUMMARY,
                source_id="conversation-summary",
                source_version="none-v1",
                included=False,
                decision_reason=ContextDecisionReason.NOT_AVAILABLE,
                estimated_token_count=0,
                message_role=None,
            ),
            ContextSourceManifestEntry(
                ordinal=5,
                source_kind=ContextSourceKind.USER_QUESTION,
                source_id="current-question",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=1,
                message_role=ModelRole.USER,
            ),
        ),
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    values = _manifest_values(manifest)

    sources = values["sources"]
    assert isinstance(sources, list)
    assert sources[2] == {
        "ordinal": 3,
        "source_kind": "financial_scope",
        "source_id": "financial-scope:0000320193-25-000079",
        "source_version": "financial-scope-v1",
        "included": True,
        "decision_reason": "included",
        "estimated_token_count": 10,
        "message_role": "user",
        "source_sha256": None,
        "source_revision_id": None,
        "source_scope": None,
        "relevance_score": None,
        "feedback_score": None,
        "source_identity": {
            "accession": "0000320193-25-000079",
            "nested": {"unit": "USD"},
        },
    }
    assert isinstance(sources[2], dict)
    assert isinstance(sources[2]["source_identity"], dict)
