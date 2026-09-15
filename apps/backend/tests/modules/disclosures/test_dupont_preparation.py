"""Preparation reuses imports and stops on missing, amended, or unauthorized data."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from industry_platform.modules.disclosures.domain import (
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSelectionStatus,
)
from industry_platform.modules.disclosures.dupont import DuPontSelection
from industry_platform.modules.disclosures.dupont_preparation import DuPontPreparationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError

from .test_filing_content_service import (
    KNOWLEDGE_BASE_ID,
    NOW,
    canonical_filing,
    scope,
    workspace_import,
)


def service() -> DuPontPreparationService:
    current = canonical_filing()
    prior = replace(current, accession="0000320193-22-000108", report_date=date(2022, 9, 24))
    imports = AsyncMock()
    imports.knowledge_service.get_knowledge_base = AsyncMock()
    imports.list_imports.return_value = ()
    imports.import_filing.side_effect = [
        workspace_import(),
        replace(workspace_import(), accession=prior.accession),
    ]
    selection = AsyncMock()
    selection.select.return_value = SimpleNamespace(
        filings=(current, prior), status=SecFilingSelectionStatus.OK, error_code=None
    )
    xbrl = AsyncMock()
    xbrl.get_dupont_facts.return_value = DuPontSelection()
    return DuPontPreparationService(selection, imports, xbrl)


async def prepare(selected: DuPontPreparationService) -> object:
    return await selected.prepare(
        scope(),
        cik="0000320193",
        fiscal_year=2023,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("dupont-test"),
    )


@pytest.mark.asyncio
async def test_preparation_imports_exactly_two_then_returns_ready() -> None:
    selected = service()
    result = await selected.prepare(
        scope(),
        cik="0000320193",
        fiscal_year=2023,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("dupont-test"),
    )
    assert result.status == "ready"
    assert len(result.imports) == 2
    assert result.financial_scope is not None
    assert result.financial_scope.accession == canonical_filing().accession


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SecFilingImportStatus.QUEUED, SecFilingImportStatus.FAILED])
async def test_pending_and_failed_ingestion_are_not_ready(status: SecFilingImportStatus) -> None:
    selected = service()
    selected.imports.import_filing.side_effect = [  # type: ignore[attr-defined]
        replace(
            workspace_import(),
            status=status,
            error_code="ingestion_failed" if status is SecFilingImportStatus.FAILED else None,
        ),
        workspace_import(),
    ]
    result = await selected.prepare(
        scope(),
        cik="0000320193",
        fiscal_year=2023,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("dupont-test"),
    )
    assert result.status == (
        "awaiting_ingestion" if status is SecFilingImportStatus.QUEUED else "insufficient_data"
    )
    selected.xbrl.get_dupont_facts.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unknown_knowledge_base_stops_before_sec_calls() -> None:
    selected = service()
    selected.imports.knowledge_service.get_knowledge_base.side_effect = (  # type: ignore[attr-defined]
        WorkspaceAccessDeniedError
    )
    with pytest.raises(WorkspaceAccessDeniedError):
        await prepare(selected)
    selected.selection.select.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_amended_selection_is_explicitly_unsupported() -> None:
    selected = service()
    selected.selection.select.return_value.filings = (  # type: ignore[attr-defined]
        replace(canonical_filing(), form=SecFilingForm.TEN_K_AMENDMENT),
    )
    result = await selected.prepare(
        scope(),
        cik="0000320193",
        fiscal_year=2023,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("dupont-test"),
    )
    assert result.status == "insufficient_data"
    selected.imports.import_filing.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_existing_imports_are_reused_and_missing_facts_sync_once_per_filing() -> None:
    selected = service()
    selected.imports.list_imports.return_value = (  # type: ignore[attr-defined]
        workspace_import(),
        replace(workspace_import(), accession="0000320193-22-000108"),
    )
    selected.xbrl.get_dupont_facts.side_effect = [  # type: ignore[attr-defined]
        DuPontSelection(issues=("missing",)),
        DuPontSelection(issues=("still_missing",)),
    ]
    result = await selected.prepare(
        scope(),
        cik="0000320193",
        fiscal_year=2023,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("dupont-test"),
    )
    assert result.status == "insufficient_data"
    assert result.issues == ("still_missing",)
    selected.imports.import_filing.assert_not_awaited()  # type: ignore[attr-defined]
    assert selected.xbrl.sync.await_count == 2  # type: ignore[attr-defined]
