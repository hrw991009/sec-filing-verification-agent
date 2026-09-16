"""Idempotent two-phase preparation using existing SEC import/ingestion jobs."""

from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from typing import Literal
from uuid import UUID

from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSelectionStatus,
    SecWorkspaceFilingImport,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingImportService
from industry_platform.modules.disclosures.service import SecFilingSelectionService
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


@dataclass(frozen=True, slots=True)
class DuPontPreparation:
    status: Literal["ready", "awaiting_ingestion", "insufficient_data"]
    financial_scope: FinancialScope | None = None
    imports: tuple[SecWorkspaceFilingImport, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DuPontPreparationService:
    selection: SecFilingSelectionService
    imports: SecFilingImportService
    xbrl: SecXbrlService

    async def prepare(
        self,
        scope: WorkspaceScope,
        *,
        cik: str,
        fiscal_year: int,
        knowledge_base_id: UUID,
        as_of: datetime,
        trace_id: TraceId,
        years: int = 2,
    ) -> DuPontPreparation:
        if not scope_allows(scope, WorkspaceAction.CREATE_RESOURCE):
            raise WorkspaceAccessDeniedError
        if isinstance(fiscal_year, bool) or not 2009 <= fiscal_year <= as_of.year:
            raise ValueError("DuPont fiscal year is invalid")
        if isinstance(years, bool) or not 2 <= years <= 5 or fiscal_year - years + 1 < 2009:
            raise ValueError("DuPont annual comparison range is invalid")
        await self.imports.knowledge_service.get_knowledge_base(scope, knowledge_base_id)
        selection = await self.selection.select(
            scope,
            selection_scope=FilingSelectionScope(
                cik=cik,
                allowed_forms=(SecFilingForm.TEN_K, SecFilingForm.TEN_K_AMENDMENT),
                report_period_start=date(fiscal_year - years + 1, 1, 1),
                report_period_end=date(fiscal_year, 12, 31),
                as_of=as_of,
                amendment_policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF,
            ),
        )
        filings = sorted(selection.filings, key=lambda filing: filing.report_date, reverse=True)
        if (
            selection.status is not SecFilingSelectionStatus.OK
            or len(filings) != years
            or any(filing.form is not SecFilingForm.TEN_K for filing in filings)
            or filings[0].report_date.year != fiscal_year
            or any(
                not 350 <= (newer.report_date - older.report_date).days <= 380
                for newer, older in pairwise(filings)
            )
        ):
            return DuPontPreparation(
                "insufficient_data",
                issues=(
                    selection.error_code
                    or (
                        "two_unamended_consecutive_10k_filings_required"
                        if years == 2
                        else "requested_unamended_consecutive_10k_filings_required"
                    ),
                ),
            )
        latest = filings[0]
        financial_scope = FinancialScope(
            cik=cik,
            accession=latest.accession,
            form=FinancialForm.TEN_K,
            report_period=latest.report_date,
            as_of=as_of,
            unit="USD",
            scale=6,
            schema_version=1 if years == 2 else 2,
            analysis_years=None if years == 2 else years,
        )
        existing: dict[str, SecWorkspaceFilingImport] = {}
        for existing_item in await self.imports.list_imports(scope):
            if existing_item.knowledge_base_id == knowledge_base_id:
                existing.setdefault(existing_item.accession, existing_item)
        imported: list[SecWorkspaceFilingImport] = []
        for filing in filings:
            item = existing.get(filing.accession)
            if item is None:
                item = await self.imports.import_filing(
                    scope,
                    accession=filing.accession,
                    knowledge_base_id=knowledge_base_id,
                    as_of=as_of,
                    trace_id=trace_id,
                )
            imported.append(item)
        if any(
            item.status in {SecFilingImportStatus.FAILED, SecFilingImportStatus.CANCELLED}
            for item in imported
        ):
            return DuPontPreparation(
                "insufficient_data", financial_scope, tuple(imported), ("filing_ingestion_failed",)
            )
        if any(item.status is not SecFilingImportStatus.READY for item in imported):
            return DuPontPreparation("awaiting_ingestion", financial_scope, tuple(imported))
        # Retry/reload reuses immutable snapshots and jobs; HTTP requests never poll.
        selection_result = await self.xbrl.get_dupont_facts(
            scope,
            knowledge_base_ids=(knowledge_base_id,),
            financial_scope=financial_scope,
        )
        if selection_result.issues:
            for filing in filings:
                await self.xbrl.sync(
                    scope, accession=filing.accession, knowledge_base_id=knowledge_base_id
                )
            selection_result = await self.xbrl.get_dupont_facts(
                scope,
                knowledge_base_ids=(knowledge_base_id,),
                financial_scope=financial_scope,
            )
        return DuPontPreparation(
            "insufficient_data" if selection_result.issues else "ready",
            financial_scope,
            tuple(imported),
            selection_result.issues,
        )
