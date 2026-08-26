"""Prove canonical SEC filer identity versioning against real PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.disclosures.adapters.sec_edgar import FrozenSecEdgarAdapter
from industry_platform.modules.disclosures.adapters.sqlalchemy import (
    SqlAlchemySecFilerCatalogRepository,
)
from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_SOURCE_KIND,
    SEC_COMPANY_TICKERS_URL,
    SecAliasKind,
    SecFiler,
    SecFilerAlias,
    SecFilerCatalogSnapshot,
    SecFilerResolutionStatus,
)
from industry_platform.modules.disclosures.models import (
    SecCatalogSyncRecord,
    SecFilerAliasRecord,
    SecFilerRecord,
)
from industry_platform.modules.disclosures.service import SecFilerResolutionService
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 8, 26, 5, 0, tzinfo=UTC)


def snapshot(
    *,
    version: str,
    content_hash: str,
    observed_at: datetime,
    name: str,
    ticker: str,
) -> SecFilerCatalogSnapshot:
    name_alias = SecFilerAlias(
        kind=SecAliasKind.NAME,
        display_value=name,
        normalized_value=" ".join(
            "".join(
                character if character.isalnum() else " " for character in name.casefold()
            ).split()
        ),
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        observed_at=observed_at,
    )
    ticker_alias = SecFilerAlias(
        kind=SecAliasKind.TICKER,
        display_value=ticker,
        normalized_value=ticker,
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        observed_at=observed_at,
    )
    filer = SecFiler(
        cik="0000123456",
        canonical_name=name,
        normalized_name=name_alias.normalized_value,
        aliases=(name_alias, ticker_alias),
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        observed_at=observed_at,
    )
    return SecFilerCatalogSnapshot(
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        retrieved_at=observed_at,
        filers=(filer,),
    )


def broad_name_snapshot() -> SecFilerCatalogSnapshot:
    version = "sec-company-tickers-broad-name"
    content_hash = "3" * 64
    filers = (
        *(
            _filer(
                cik=f"{index:010d}",
                name=f"Alpha Example Company {index}",
                ticker=f"X{index}",
                version=version,
                content_hash=content_hash,
            )
            for index in range(1, 31)
        ),
        _filer(
            cik="9999999999",
            name="Agilent Technologies Inc.",
            ticker="A",
            version=version,
            content_hash=content_hash,
        ),
    )
    return SecFilerCatalogSnapshot(
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        retrieved_at=NOW,
        filers=filers,
    )


def _filer(
    *,
    cik: str,
    name: str,
    ticker: str,
    version: str,
    content_hash: str,
) -> SecFiler:
    normalized_name = " ".join(
        "".join(character if character.isalnum() else " " for character in name.casefold()).split()
    )
    aliases = (
        SecFilerAlias(
            kind=SecAliasKind.NAME,
            display_value=name,
            normalized_value=normalized_name,
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=version,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=content_hash,
            observed_at=NOW,
        ),
        SecFilerAlias(
            kind=SecAliasKind.TICKER,
            display_value=ticker,
            normalized_value=ticker,
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=version,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=content_hash,
            observed_at=NOW,
        ),
    )
    return SecFiler(
        cik=cik,
        canonical_name=name,
        normalized_name=normalized_name,
        aliases=aliases,
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_hash,
        observed_at=NOW,
    )


def test_catalog_versions_are_idempotent_and_historical_aliases_remain_explainable(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        repository = SqlAlchemySecFilerCatalogRepository(session_factory)
        first = snapshot(
            version="sec-company-tickers-first",
            content_hash="1" * 64,
            observed_at=NOW,
            name="Example Corporation",
            ticker="OLD",
        )
        second = snapshot(
            version="sec-company-tickers-second",
            content_hash="2" * 64,
            observed_at=NOW + timedelta(days=1),
            name="Example Holdings",
            ticker="NEW",
        )
        try:
            await repository.replace_catalog(first)
            await repository.replace_catalog(first)
            await repository.replace_catalog(second)

            resolution = await SecFilerResolutionService(
                repository=repository,
                source=FrozenSecEdgarAdapter(second),
            ).resolve(
                WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"),
                query="OLD",
            )

            assert resolution.status is SecFilerResolutionStatus.RESOLVED
            assert resolution.candidates[0].cik == "0000123456"
            assert resolution.candidates[0].tickers == ("NEW",)
            assert resolution.candidates[0].alias_valid_to == NOW + timedelta(days=1)

            async with session_factory() as session:
                assert await session.scalar(select(func.count()).select_from(SecFilerRecord)) == 1
                assert (
                    await session.scalar(select(func.count()).select_from(SecCatalogSyncRecord))
                    == 2
                )
                assert (
                    await session.scalar(select(func.count()).select_from(SecFilerAliasRecord)) == 4
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SecFilerAliasRecord)
                        .where(SecFilerAliasRecord.valid_to.is_(None))
                    )
                    == 2
                )
        finally:
            await engine.dispose()

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()


def test_exact_ticker_is_ranked_before_broad_name_matches(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        repository = SqlAlchemySecFilerCatalogRepository(session_factory)
        source_snapshot = broad_name_snapshot()
        try:
            resolution = await SecFilerResolutionService(
                repository=repository,
                source=FrozenSecEdgarAdapter(source_snapshot),
            ).resolve(
                WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"),
                query="A",
            )

            assert resolution.status is SecFilerResolutionStatus.RESOLVED
            assert resolution.candidates[0].cik == "9999999999"
            assert resolution.candidates[0].tickers == ("A",)
        finally:
            await engine.dispose()

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()
