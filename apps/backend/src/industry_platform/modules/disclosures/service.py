"""Application service for deterministic SEC filer resolution."""

from dataclasses import dataclass, field

from industry_platform.modules.disclosures.domain import (
    SEC_MAX_FILER_CANDIDATES,
    SecAliasKind,
    SecFiler,
    SecFilerAlias,
    SecFilerCandidate,
    SecFilerMatchKind,
    SecFilerResolution,
    SecFilerResolutionStatus,
    normalize_filer_query,
)
from industry_platform.modules.disclosures.ports import SecEdgarPort, SecFilerCatalogRepository
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
