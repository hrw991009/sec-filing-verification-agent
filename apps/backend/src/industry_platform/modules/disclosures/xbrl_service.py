"""Application service for source-typed SEC XBRL synchronization and reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from industry_platform.modules.disclosures.adapters.xbrl import (
    parse_companyfacts,
    parse_raw_xbrl,
)
from industry_platform.modules.disclosures.domain import (
    SecFilingContentError,
    SecFilingContentStatus,
    SecSourceErrorCode,
    SecXbrlDataset,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlSyncResult,
)
from industry_platform.modules.disclosures.ports import (
    SecCompanyFactsPort,
    SecFilingContentRepository,
    SecXbrlRepository,
    SecXbrlSnapshotStore,
)
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.workspaces.domain import WorkspaceScope


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SecXbrlService:
    repository: SecXbrlRepository
    filing_repository: SecFilingContentRepository
    companyfacts_source: SecCompanyFactsPort
    snapshot_store: SecXbrlSnapshotStore
    clock: Callable[[], datetime] = utc_now

    async def sync(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
    ) -> SecXbrlSyncResult:
        preparation = await self.repository.prepare_sync(
            scope,
            accession=accession,
            knowledge_base_id=knowledge_base_id,
        )
        aggregate = await self.companyfacts_source.fetch(preparation.filing)
        aggregate_batch = parse_companyfacts(aggregate, preparation.filing)
        aggregate_object_key = await self.snapshot_store.persist_aggregate(aggregate)
        raw_batches = []
        for reference in preparation.raw_sources:
            source = await self.snapshot_store.read_raw(
                reference,
                cik=preparation.filing.cik,
            )
            raw_batches.append(parse_raw_xbrl(source, preparation.filing))
        try:
            dataset = SecXbrlDataset(
                filing=preparation.filing,
                batches=(aggregate_batch, *raw_batches),
            )
        except ValueError:
            raise SecFilingContentError(SecSourceErrorCode.RESPONSE_INVALID) from None
        return await self.repository.persist_dataset(
            dataset,
            aggregate_object_keys={aggregate.source_url: aggregate_object_key},
        )

    async def get_facts(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        query: SecXbrlFactQuery,
    ) -> SecXbrlFactResult:
        filing = await self.filing_repository.get_canonical_filing(financial_scope.accession)
        if (
            filing.cik != financial_scope.cik
            or filing.form.value != financial_scope.form.value
            or filing.report_date != financial_scope.report_period
        ):
            return SecXbrlFactResult(
                status=SecFilingContentStatus.PERMISSION_DENIED,
                accession=financial_scope.accession,
            )
        return await self.get_imported_facts(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            accession=financial_scope.accession,
            as_of=financial_scope.as_of,
            query=query,
        )

    async def get_imported_facts(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
        query: SecXbrlFactQuery,
    ) -> SecXbrlFactResult:
        if as_of > self.clock():
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)
        return await self.repository.query_facts(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            accession=accession,
            as_of=as_of,
            query=query,
        )
