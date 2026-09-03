from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from industry_platform.modules.disclosures.monitor import SecMonitorStatus
from industry_platform.modules.disclosures.subscription import (
    SecMonitorRevisionConflictError,
    SecMonitorSubscriptionRepository,
    SecMonitorSubscriptionService,
    SecMonitorView,
    TriggerSecMonitorRun,
)
from industry_platform.modules.jobs.domain import (
    EnsuredSchedule,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    ScheduleDefinition,
    ScheduleTickResult,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 9, 1, tzinfo=UTC)


class MonitorRepository:
    def __init__(self, monitor: SecMonitorView) -> None:
        self.monitor = monitor

    async def get_monitor(self, scope: WorkspaceScope, monitor_id: UUID) -> SecMonitorView:
        assert scope.workspace_id == self.monitor.workspace_id
        assert monitor_id == self.monitor.monitor_id
        return self.monitor

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Unexpected repository operation: {name}")


class TrackingSchedules:
    def __init__(self, result: ManualScheduleTriggerResult) -> None:
        self.result = result
        self.commands: list[ManualScheduleTriggerCommand] = []

    async def ensure_schedule(self, definition: ScheduleDefinition) -> EnsuredSchedule:
        raise AssertionError(definition)

    async def run_due_once(self) -> ScheduleTickResult:
        raise AssertionError

    async def trigger_manual(
        self, command: ManualScheduleTriggerCommand
    ) -> ManualScheduleTriggerResult:
        self.commands.append(command)
        return self.result


def _monitor(scope: WorkspaceScope, *, status: SecMonitorStatus) -> SecMonitorView:
    return SecMonitorView(
        monitor_id=uuid4(),
        workspace_id=scope.workspace_id,
        owner_user_id=scope.user_id,
        cik="0000320193",
        canonical_name="Apple Inc.",
        knowledge_base_id=uuid4(),
        schedule_id=uuid4(),
        cron_expression="0 3 * * *",
        timezone_name="Asia/Shanghai",
        allowed_forms=("10-K",),
        rules=(),
        status=status,
        revision=3,
        watermark_revision=1,
        watermark_coverage_version="sec-monitor-reviewed-filing-v1",
        watermark_accepted_at=NOW,
        watermark_accession="0000320193-23-000106",
        created_from_approval_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_active_monitor_run_forwards_one_idempotent_manual_trigger() -> None:
    scope = WorkspaceScope(uuid4(), uuid4(), "owner")
    monitor = _monitor(scope, status=SecMonitorStatus.ACTIVE)
    result = ManualScheduleTriggerResult(uuid4(), uuid4(), True)
    schedules = TrackingSchedules(result)
    service = SecMonitorSubscriptionService(
        repository=cast(SecMonitorSubscriptionRepository, MonitorRepository(monitor)),
        schedules=schedules,
    )
    trigger_id = uuid4()

    actual = await service.trigger_run(
        scope,
        TriggerSecMonitorRun(
            monitor_id=monitor.monitor_id,
            expected_revision=monitor.revision,
            trigger_id=trigger_id,
        ),
    )

    assert actual == result
    assert schedules.commands == [
        ManualScheduleTriggerCommand(schedule_id=monitor.schedule_id, trigger_id=trigger_id)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SecMonitorStatus.PAUSED, SecMonitorStatus.DELETED])
async def test_non_active_or_stale_monitor_run_is_rejected_before_scheduling(
    status: SecMonitorStatus,
) -> None:
    scope = WorkspaceScope(uuid4(), uuid4(), "owner")
    monitor = _monitor(scope, status=status)
    schedules = TrackingSchedules(ManualScheduleTriggerResult(uuid4(), uuid4(), True))
    service = SecMonitorSubscriptionService(
        repository=cast(SecMonitorSubscriptionRepository, MonitorRepository(monitor)),
        schedules=schedules,
    )

    with pytest.raises(SecMonitorRevisionConflictError):
        await service.trigger_run(
            scope,
            TriggerSecMonitorRun(
                monitor_id=monitor.monitor_id,
                expected_revision=monitor.revision,
                trigger_id=uuid4(),
            ),
        )
    assert schedules.commands == []

    active = _monitor(scope, status=SecMonitorStatus.ACTIVE)
    stale_service = SecMonitorSubscriptionService(
        repository=cast(SecMonitorSubscriptionRepository, MonitorRepository(active)),
        schedules=schedules,
    )
    with pytest.raises(SecMonitorRevisionConflictError):
        await stale_service.trigger_run(
            scope,
            TriggerSecMonitorRun(
                monitor_id=active.monitor_id,
                expected_revision=active.revision + 1,
                trigger_id=uuid4(),
            ),
        )
    assert schedules.commands == []
