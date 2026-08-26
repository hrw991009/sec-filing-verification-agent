"""Application services for deterministic SEC identity and filing selection."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from industry_platform.modules.disclosures.domain import (
    SEC_MAX_FILER_CANDIDATES,
    SEC_MAX_FILING_CANDIDATES,
    FilingSelectionScope,
    SecAliasKind,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFiler,
    SecFilerAlias,
    SecFilerCandidate,
    SecFilerMatchKind,
    SecFilerResolution,
    SecFilerResolutionStatus,
    SecFilingCandidate,
    SecFilingSelection,
    SecFilingSelectionStatus,
    normalize_filer_query,
)
from industry_platform.modules.disclosures.ports import (
    SecEdgarPort,
    SecFilerCatalogRepository,
    SecFilingRepository,
    SecSubmissionSnapshotStore,
    SecSubmissionsPort,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


@dataclass(frozen=True, slots=True)
class SecFilerResolutionService:
    repository: SecFilerCatalogRepository = field(repr=False)
    source: SecEdgarPort = field(repr=False)

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        query: str,
        limit: int = 5,
    ) -> SecFilerResolution:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        if not 1 <= limit <= SEC_MAX_FILER_CANDIDATES:
            raise ValueError("SEC filer candidate limit is invalid")
        normalized_query, cik, ticker = normalize_filer_query(query)
        snapshot = await self.source.fetch_filer_catalog()
        await self.repository.replace_catalog(snapshot)
        filers = await self.repository.search(
            cik=cik,
            normalized_name=normalized_query,
            ticker=ticker,
            limit=limit,
        )
        ranked_candidates = tuple(
            sorted(
                (
                    candidate
                    for filer in filers
                    if (
                        candidate := _candidate(
                            filer,
                            normalized_query=normalized_query,
                            cik=cik,
                            ticker=ticker,
                        )
                    )
                    is not None
                ),
                key=lambda item: (-item.confidence, item.cik),
            )[:limit]
        )
        candidates = ranked_candidates
        if ranked_candidates and ranked_candidates[0].confidence >= 0.9:
            candidates = tuple(
                candidate
                for candidate in ranked_candidates
                if candidate.confidence == ranked_candidates[0].confidence
            )
        if not candidates:
            resolution_status = SecFilerResolutionStatus.NO_RESULT
        elif len(candidates) == 1 and candidates[0].confidence >= 0.9:
            resolution_status = SecFilerResolutionStatus.RESOLVED
        else:
            resolution_status = SecFilerResolutionStatus.AMBIGUOUS
        return SecFilerResolution(
            status=resolution_status,
            query=" ".join(query.strip().split()),
            normalized_query=normalized_query,
            candidates=candidates,
            catalog_source_version=snapshot.source_version,
            catalog_content_sha256=snapshot.content_sha256,
            catalog_retrieved_at=snapshot.retrieved_at,
        )


@dataclass(frozen=True, slots=True)
class SecFilingSelectionService:
    repository: SecFilingRepository = field(repr=False)
    source: SecSubmissionsPort = field(repr=False)
    snapshot_store: SecSubmissionSnapshotStore = field(repr=False)
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(UTC),
        repr=False,
    )

    async def select(
        self,
        workspace_scope: WorkspaceScope,
        *,
        selection_scope: FilingSelectionScope,
    ) -> SecFilingSelection:
        if not scope_allows(workspace_scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("SEC filing selection clock is invalid")
        if selection_scope.as_of > now.astimezone(UTC):
            raise ValueError("SEC filing selection as_of cannot be in the future")

        submission_set = await self.source.fetch_submission_set(selection_scope)
        object_keys = {
            source.source_version: await self.snapshot_store.persist(source)
            for source in submission_set.sources
        }
        coverage_version = await self.repository.replace_submission_set(
            submission_set,
            object_keys=object_keys,
            scope=selection_scope,
        )
        dataset = await self.repository.load_dataset(
            coverage_version=coverage_version,
            scope=selection_scope,
        )
        invisible_sources = tuple(
            source
            for source in dataset.sources
            if source.source_available_at > selection_scope.as_of
        )
        if invisible_sources:
            return SecFilingSelection(
                status=SecFilingSelectionStatus.INCOMPLETE,
                scope=selection_scope,
                filings=(),
                coverage_version=coverage_version,
                sources=dataset.sources,
                error_code="source_version_not_visible_at_as_of",
            )

        visible = tuple(
            filing
            for filing in dataset.filings
            if filing.form in selection_scope.allowed_forms
            and selection_scope.report_period_start
            <= filing.report_date
            <= selection_scope.report_period_end
            and filing.accepted_at <= selection_scope.as_of
            and filing.public_available_at <= selection_scope.as_of
            and filing.source_available_at <= selection_scope.as_of
        )
        if any(
            filing.amendment_relation_status is SecAmendmentRelationStatus.UNRESOLVED
            for filing in visible
        ):
            return SecFilingSelection(
                status=SecFilingSelectionStatus.INCOMPLETE,
                scope=selection_scope,
                filings=(),
                coverage_version=coverage_version,
                sources=dataset.sources,
                error_code="amendment_relation_unresolved",
            )
        selected = (
            _latest_amendments(visible)
            if selection_scope.amendment_policy is SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF
            else visible
        )
        selected = tuple(
            sorted(selected, key=lambda item: (item.report_date, item.accepted_at, item.accession))
        )
        if len(selected) > SEC_MAX_FILING_CANDIDATES:
            return SecFilingSelection(
                status=SecFilingSelectionStatus.INCOMPLETE,
                scope=selection_scope,
                filings=(),
                coverage_version=coverage_version,
                sources=dataset.sources,
                error_code="candidate_limit_exceeded",
            )
        return SecFilingSelection(
            status=(
                SecFilingSelectionStatus.OK if selected else SecFilingSelectionStatus.NO_RESULT
            ),
            scope=selection_scope,
            filings=selected,
            coverage_version=coverage_version,
            sources=dataset.sources,
        )


def _latest_amendments(
    filings: tuple[SecFilingCandidate, ...],
) -> tuple[SecFilingCandidate, ...]:
    latest: dict[str, SecFilingCandidate] = {}
    for filing in filings:
        identity = filing.base_accession or filing.accession
        current = latest.get(identity)
        if current is None or (filing.accepted_at, filing.accession) > (
            current.accepted_at,
            current.accession,
        ):
            latest[identity] = filing
    return tuple(latest.values())


def _candidate(
    filer: SecFiler,
    *,
    normalized_query: str,
    cik: str | None,
    ticker: str | None,
) -> SecFilerCandidate | None:
    matches: list[tuple[float, SecFilerMatchKind, str, SecFilerAlias | None]] = []
    if cik is not None and filer.cik == cik:
        matches.append((1.0, SecFilerMatchKind.CIK, filer.cik, None))
    for alias in filer.aliases:
        if (
            alias.kind is SecAliasKind.TICKER
            and ticker is not None
            and alias.normalized_value == ticker
        ):
            matches.append((0.99, SecFilerMatchKind.TICKER, alias.display_value, alias))
        if alias.kind is not SecAliasKind.NAME:
            continue
        if alias.normalized_value == normalized_query:
            matches.append((0.95, SecFilerMatchKind.NAME_EXACT, alias.display_value, alias))
        elif alias.normalized_value.startswith(normalized_query):
            matches.append((0.82, SecFilerMatchKind.NAME_PREFIX, alias.display_value, alias))
        elif normalized_query in alias.normalized_value:
            matches.append((0.7, SecFilerMatchKind.NAME_CONTAINS, alias.display_value, alias))
    if not matches:
        return None
    confidence, matched_by, matched_value, raw_alias = max(
        matches,
        key=lambda item: (
            item[0],
            item[3] is None or item[3].valid_to is None,
            filer.observed_at if item[3] is None else item[3].observed_at,
            item[1].value,
            item[2],
        ),
    )
    selected_alias = raw_alias
    if selected_alias is None:
        selected_alias = next(
            (
                item
                for item in filer.aliases
                if item.valid_to is None and item.source_version == filer.source_version
            ),
            filer.aliases[0],
        )
    return SecFilerCandidate(
        cik=filer.cik,
        canonical_name=filer.canonical_name,
        tickers=tuple(
            sorted(
                {
                    item.display_value
                    for item in filer.aliases
                    if item.kind is SecAliasKind.TICKER and item.valid_to is None
                }
            )
        ),
        matched_by=matched_by,
        matched_value=matched_value,
        confidence=confidence,
        source_version=selected_alias.source_version,
        source_url=selected_alias.source_url,
        content_sha256=selected_alias.content_sha256,
        source_observed_at=selected_alias.observed_at,
        alias_valid_from=selected_alias.valid_from,
        alias_valid_to=selected_alias.valid_to,
    )
