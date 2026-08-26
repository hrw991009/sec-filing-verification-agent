"""Ports for official SEC sources and the canonical filer catalog."""

from typing import Protocol

from industry_platform.modules.disclosures.domain import (
    SecFiler,
    SecFilerCatalogSnapshot,
)


class SecEdgarPort(Protocol):
    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot: ...


class SecFilerCatalogRepository(Protocol):
    async def replace_catalog(self, snapshot: SecFilerCatalogSnapshot) -> None: ...

    async def search(
        self,
        *,
        cik: str | None,
        normalized_name: str,
        ticker: str | None,
        limit: int,
    ) -> tuple[SecFiler, ...]: ...
