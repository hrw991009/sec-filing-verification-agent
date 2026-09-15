"""Evidence drill-down preserves availability and bounded workspace reads."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from industry_platform.modules.evidence.service import EvidenceApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceScope

from .test_domain import evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
async def test_source_drilldown_keeps_unavailable_evidence_distinct(available: bool) -> None:
    item = evidence()
    scope = WorkspaceScope(item.workspace_id, uuid4(), "owner")
    repository = AsyncMock()
    repository.get_evidence.return_value = item
    repository.is_evidence_available.return_value = available
    repository.resolve_evidence.return_value = (item, available)
    service = EvidenceApplicationService(repository)
    assert await service.get_evidence(scope, item.evidence_id) == item
    assert await service.is_evidence_available(scope, item.evidence_id) is available
    assert await service.resolve_evidence(scope, item.evidence_id) == (item, available)
    repository.get_evidence.assert_awaited_once_with(scope, item.evidence_id)
    repository.is_evidence_available.assert_awaited_once_with(scope, item.evidence_id)
    repository.resolve_evidence.assert_awaited_once_with(scope, item.evidence_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, 101])
async def test_invalid_ledger_limit_does_not_reach_repository(limit: int) -> None:
    repository = AsyncMock()
    service = EvidenceApplicationService(repository)
    with pytest.raises(ValueError, match="page size"):
        await service.list_evidence(WorkspaceScope(uuid4(), uuid4(), "owner"), limit=limit)
    repository.list_evidence.assert_not_awaited()
