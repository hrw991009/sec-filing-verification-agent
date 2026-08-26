"""Domain and resolver tests for SEC filer discovery."""

from datetime import date, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.adapters.sec_edgar import FrozenSecEdgarAdapter
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecFetchMode,
    SecFilerMatchKind,
    SecFilerResolutionStatus,
    SecFilingForm,
    normalize_cik,
    normalize_filer_query,
    plan_sec_cik_fetch,
)
from industry_platform.modules.disclosures.service import SecFilerResolutionService
from industry_platform.modules.workspaces.domain import WorkspaceScope

from .support import NOW, InMemoryFilerCatalogRepository, catalog_snapshot, filer

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def scope() -> WorkspaceScope:
    return WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer")


def service() -> SecFilerResolutionService:
    return SecFilerResolutionService(
        repository=InMemoryFilerCatalogRepository(),
        source=FrozenSecEdgarAdapter(catalog_snapshot()),
    )


def test_cik_query_and_batch_policy_are_canonical_and_fail_closed() -> None:
    assert normalize_cik(320193) == "0000320193"
    assert normalize_filer_query(" BRK.B ") == ("brk b", None, "BRK.B")
    assert plan_sec_cik_fetch(tuple(str(index) for index in range(1, 100))) is SecFetchMode.API
    assert plan_sec_cik_fetch(tuple(str(index) for index in range(1, 101))) is SecFetchMode.BULK
    assert plan_sec_cik_fetch(("320193",), full_refresh=True) is SecFetchMode.BULK

    with pytest.raises(ValueError, match="CIK is invalid"):
        normalize_cik("0")
    with pytest.raises(ValueError, match="request is empty"):
        plan_sec_cik_fetch(())


@pytest.mark.asyncio
async def test_exact_ticker_or_cik_resolves_to_one_attributed_candidate() -> None:
    ticker_result = await service().resolve(scope(), query="AAPL")
    cik_result = await service().resolve(scope(), query="320193")

    assert ticker_result.status is SecFilerResolutionStatus.RESOLVED
    assert ticker_result.candidates[0].cik == "0000320193"
    assert ticker_result.candidates[0].matched_by is SecFilerMatchKind.TICKER
    assert ticker_result.candidates[0].tickers == ("AAPL",)
    assert cik_result.status is SecFilerResolutionStatus.RESOLVED
    assert cik_result.candidates[0].matched_by is SecFilerMatchKind.CIK
    assert cik_result.catalog_content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_partial_name_is_ambiguous_and_unknown_name_is_no_result() -> None:
    ambiguous = await service().resolve(scope(), query="Apple")
    missing = await service().resolve(scope(), query="Definitely Missing Filer")

    assert ambiguous.status is SecFilerResolutionStatus.AMBIGUOUS
    assert [candidate.cik for candidate in ambiguous.candidates] == [
        "0000320193",
        "0001601712",
    ]
    assert all(candidate.confidence < 0.9 for candidate in ambiguous.candidates)
    assert missing.status is SecFilerResolutionStatus.NO_RESULT
    assert missing.candidates == ()


@pytest.mark.asyncio
async def test_exact_identity_is_not_diluted_but_duplicate_ticker_remains_ambiguous() -> None:
    snapshot = catalog_snapshot()
    duplicate_ticker_snapshot = type(snapshot)(
        source_kind=snapshot.source_kind,
        source_version=snapshot.source_version,
        source_url=snapshot.source_url,
        content_sha256=snapshot.content_sha256,
        retrieved_at=snapshot.retrieved_at,
        filers=(*snapshot.filers, filer("0001652044", "Alphabet Inc.", "AAPL")),
    )
    exact_name = await service().resolve(scope(), query="Apple Inc.")
    duplicate_ticker = await SecFilerResolutionService(
        repository=InMemoryFilerCatalogRepository(),
        source=FrozenSecEdgarAdapter(duplicate_ticker_snapshot),
    ).resolve(scope(), query="AAPL")

    assert exact_name.status is SecFilerResolutionStatus.RESOLVED
    assert exact_name.candidates[0].cik == "0000320193"
    assert duplicate_ticker.status is SecFilerResolutionStatus.AMBIGUOUS
    assert {candidate.cik for candidate in duplicate_ticker.candidates} == {
        "0000320193",
        "0001652044",
    }


def test_filing_selection_scope_round_trips_canonical_mapping() -> None:
    filing_scope = FilingSelectionScope(
        cik="0000320193",
        allowed_forms=(SecFilingForm.TEN_K, SecFilingForm.TEN_Q),
        report_period_start=date(2023, 1, 1),
        report_period_end=date(2025, 12, 31),
        as_of=NOW,
        amendment_policy=SecAmendmentPolicy.AS_FILED,
    )

    restored = FilingSelectionScope.from_mapping(dict(filing_scope.to_mapping()))

    assert restored == filing_scope


def test_filing_selection_scope_rejects_unsorted_forms_and_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="forms"):
        FilingSelectionScope(
            cik="0000320193",
            allowed_forms=(SecFilingForm.TEN_Q, SecFilingForm.TEN_K),
            report_period_start=date(2024, 1, 1),
            report_period_end=date(2024, 12, 31),
            as_of=NOW,
            amendment_policy=SecAmendmentPolicy.AS_FILED,
        )

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        FilingSelectionScope(
            cik="0000320193",
            allowed_forms=(SecFilingForm.TEN_K,),
            report_period_start=date(2024, 1, 1),
            report_period_end=date(2024, 12, 31),
            as_of=(NOW + timedelta(days=1)).replace(tzinfo=None),
            amendment_policy=SecAmendmentPolicy.AS_FILED,
        )
