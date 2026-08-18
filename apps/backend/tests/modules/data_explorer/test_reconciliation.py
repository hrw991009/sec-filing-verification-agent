"""Recovery contract for Query Runs left running by a crashed process."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest

from industry_platform.modules.data_explorer.ports import DataExplorerRepository
from industry_platform.modules.data_explorer.service import (
    StaleQueryRunReconciliationService,
)


@dataclass(slots=True)
class RecordingRepository:
    stale_before: datetime | None = None
    reconciled_at: datetime | None = None
    batch_size: int | None = None

    async def reconcile_stale_queries(
        self,
        *,
        stale_before: datetime,
        reconciled_at: datetime,
        batch_size: int,
    ) -> int:
        self.stale_before = stale_before
        self.reconciled_at = reconciled_at
        self.batch_size = batch_size
        return 2


@pytest.mark.asyncio
async def test_reconciliation_uses_one_bounded_utc_cutoff() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    repository = RecordingRepository()
    service = StaleQueryRunReconciliationService(
        cast(DataExplorerRepository, repository),
        stale_after_seconds=300,
        clock=lambda: now,
    )

    assert await service.reconcile_stale(batch_size=25) == 2
    assert repository.stale_before == datetime(2026, 8, 17, 11, 55, tzinfo=UTC)
    assert repository.reconciled_at == now
    assert repository.batch_size == 25


@pytest.mark.asyncio
async def test_reconciliation_rejects_invalid_batch_and_non_utc_clock() -> None:
    repository = RecordingRepository()
    service = StaleQueryRunReconciliationService(
        cast(DataExplorerRepository, repository),
        clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC).replace(tzinfo=None),
    )

    with pytest.raises(ValueError, match="batch size"):
        await service.reconcile_stale(batch_size=0)
    with pytest.raises(ValueError, match="must return UTC"):
        await service.reconcile_stale(batch_size=1)
