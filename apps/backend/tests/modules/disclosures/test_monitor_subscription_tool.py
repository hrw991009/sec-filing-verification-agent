"""SEC Monitor write intent is typed and always stops at durable approval."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.disclosures.tool import SecMonitorSubscribeTool
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.tools.domain import (
    ToolAction,
    ToolApprovalOutcome,
    ToolApprovalPolicy,
    ToolSideEffectClass,
)
from industry_platform.modules.tools.registry import (
    ToolPreparationError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

NOW = datetime(2026, 8, 29, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
STEP_ID = UUID("44444444-4444-4444-8444-444444444444")
CALL_ID = UUID("55555555-5555-4555-8555-555555555555")


def _context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "SEC", "member"),),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset(
            {WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL, WorkspaceAction.RUN_RESEARCH}
        ),
        budget=RunBudget(
            schema_version=1,
            max_steps=20,
            max_total_tokens=10_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=10),
        ),
    )


def _arguments() -> dict[str, object]:
    return {
        "cik": "0000320193",
        "knowledge_base_id": "66666666-6666-4666-8666-666666666666",
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


def test_monitor_subscribe_is_the_single_approval_gated_write_contract() -> None:
    definition = SecMonitorSubscribeTool().definition

    assert definition.name == "sec.monitor.subscribe"
    assert definition.version == "v1"
    assert definition.approval_policy is ToolApprovalPolicy.REQUIRE_APPROVAL
    assert definition.side_effect_class is ToolSideEffectClass.IDEMPOTENT_WRITE


def test_valid_monitor_request_is_rejected_only_with_approval_required() -> None:
    registry = ToolRegistry((SecMonitorSubscribeTool(),))
    action = ToolAction(1, "sec.monitor.subscribe", "v1", _arguments())

    with pytest.raises(ToolPreparationError) as raised:
        registry.prepare(
            ToolRequestAudit(call_id=CALL_ID, action=action),
            allowed_tools=(SecMonitorSubscribeTool().definition.reference,),
            run_id=RUN_ID,
            requested_by_step_id=STEP_ID,
            runtime_context=_context(),
            requested_at=NOW,
        )

    assert raised.value.outcome is ToolApprovalOutcome.APPROVAL_REQUIRED
    assert raised.value.code == "tool_approval_required"


def test_invalid_monitor_rule_is_denied_before_approval() -> None:
    registry = ToolRegistry((SecMonitorSubscribeTool(),))
    arguments = _arguments()
    arguments["rules"] = [
        {
            "kind": "fact_absolute_change",
            "section_query": "revenue",
            "taxonomy": "us-gaap",
            "concept": "Revenue",
            "unit": "USD",
            "threshold": None,
            "comparator": "absolute_delta_gte",
        }
    ]
    action = ToolAction(1, "sec.monitor.subscribe", "v1", arguments)

    with pytest.raises(ToolPreparationError) as raised:
        registry.prepare(
            ToolRequestAudit(call_id=CALL_ID, action=action),
            allowed_tools=(SecMonitorSubscribeTool().definition.reference,),
            run_id=RUN_ID,
            requested_by_step_id=STEP_ID,
            runtime_context=_context(),
            requested_at=NOW,
        )

    assert raised.value.outcome is ToolApprovalOutcome.DENY
    assert raised.value.code == "tool_arguments_invalid"
