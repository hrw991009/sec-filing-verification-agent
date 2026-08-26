"""Deterministic SEC identity fixtures for module tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_SOURCE_KIND,
    SEC_COMPANY_TICKERS_URL,
    SecAliasKind,
    SecFiler,
    SecFilerAlias,
    SecFilerCatalogSnapshot,
)

NOW = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
CATALOG_HASH = "a" * 64
CATALOG_VERSION = "sec-company-tickers-aaaaaaaaaaaaaaaaaaaaaaaa"


def filer(cik: str, name: str, *tickers: str) -> SecFiler:
    aliases = [
        SecFilerAlias(
            kind=SecAliasKind.NAME,
            display_value=name,
            normalized_value=" ".join(
                "".join(
                    character if character.isalnum() else " " for character in name.casefold()
                ).split()
            ),
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=CATALOG_VERSION,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=CATALOG_HASH,
            observed_at=NOW,
        )
    ]
    aliases.extend(
        SecFilerAlias(
            kind=SecAliasKind.TICKER,
            display_value=ticker,
            normalized_value=ticker,
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=CATALOG_VERSION,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=CATALOG_HASH,
            observed_at=NOW,
        )
        for ticker in tickers
    )
    return SecFiler(
        cik=cik,
        canonical_name=name,
        normalized_name=aliases[0].normalized_value,
        aliases=tuple(aliases),
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=CATALOG_VERSION,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=CATALOG_HASH,
        observed_at=NOW,
    )


def catalog_snapshot() -> SecFilerCatalogSnapshot:
    return SecFilerCatalogSnapshot(
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=CATALOG_VERSION,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=CATALOG_HASH,
        retrieved_at=NOW,
        filers=(
            filer("0000320193", "Apple Inc.", "AAPL"),
            filer("0001601712", "Apple Hospitality REIT, Inc.", "APLE"),
            filer("0000789019", "Microsoft Corporation", "MSFT"),
        ),
    )


@dataclass(slots=True)
class InMemoryFilerCatalogRepository:
    current: SecFilerCatalogSnapshot | None = None
    versions: list[str] = field(default_factory=list)

    async def replace_catalog(self, snapshot: SecFilerCatalogSnapshot) -> None:
        self.current = snapshot
        if snapshot.source_version not in self.versions:
            self.versions.append(snapshot.source_version)

    async def search(
        self,
        *,
        cik: str | None,
        normalized_name: str,
        ticker: str | None,
        limit: int,
    ) -> tuple[SecFiler, ...]:
        del cik, normalized_name, ticker
        if self.current is None:
            return ()
        return self.current.filers[: limit * 4]
