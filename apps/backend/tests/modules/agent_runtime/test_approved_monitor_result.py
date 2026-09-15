"""Approved side effects become explicit, integrity-bound model observations."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from industry_platform.modules.agent_runtime.adapters.execution import _approved_monitor_tool_result
from industry_platform.modules.research.domain import (
    ResearchApprovalReason,
    ResearchSideEffectStatus,
)
from industry_platform.modules.research.models import (
    ResearchApprovalRequestRecord,
    ResearchSideEffectRecord,
)
from industry_platform.modules.tools.domain import canonical_mapping_sha256


@pytest.mark.parametrize("tamper_result", [False, True])
def test_completed_monitor_result_is_explicit_and_digest_checked(tamper_result: bool) -> None:
    workspace_id, run_id = uuid4(), uuid4()
    arguments = {"cik": "0000320193"}
    resource_ref = f"sec-monitor:{uuid4()}"
    approval = ResearchApprovalRequestRecord(
        id=uuid4(),
        run_id=run_id,
        reason=ResearchApprovalReason.MONITOR_SUBSCRIPTION,
        tool_call_id=uuid4(),
        tool_name="sec.monitor.subscribe",
        tool_version="v1",
        tool_arguments=arguments,
        tool_arguments_sha256=canonical_mapping_sha256(arguments),
        resumed_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    effect = ResearchSideEffectRecord(
        workspace_id=workspace_id,
        run_id=run_id,
        effect_kind="monitor_subscription",
        status=ResearchSideEffectStatus.COMPLETED,
        resource_ref=resource_ref,
        result_sha256=(
            "0" * 64 if tamper_result else hashlib.sha256(resource_ref.encode("ascii")).hexdigest()
        ),
    )
    if tamper_result:
        with pytest.raises(ValueError, match="digest"):
            _approved_monitor_tool_result(approval, effect, workspace_id=workspace_id)
        return

    action, observation = _approved_monitor_tool_result(
        approval, effect, workspace_id=workspace_id, observation_ordinal=4
    )
    assert action is not None
    assert observation is not None
    assert observation.ordinal == 4
    assert observation.source_version == "approved-tool-result-v2"
    assert json.loads(observation.model_text) == {
        "approval_status": "approved",
        "execution_status": "completed",
        "resource_ref": resource_ref,
    }
    assert (
        observation.content_sha256
        == hashlib.sha256(observation.model_text.encode("utf-8")).hexdigest()
    )
