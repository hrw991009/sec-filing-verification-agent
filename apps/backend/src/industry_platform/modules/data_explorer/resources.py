"""Composition root for the separate read-only database capability."""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.data_explorer.adapters.postgresql import (
    PostgresReadOnlyDatabase,
)
from industry_platform.modules.data_explorer.adapters.sqlalchemy import (
    SqlAlchemyDataExplorerRepository,
)
from industry_platform.modules.data_explorer.domain import QueryBudgets
from industry_platform.modules.data_explorer.service import DataExplorerService
from industry_platform.modules.data_explorer.tool import Text2SqlTool


@dataclass(frozen=True, slots=True)
class DataExplorerResources:
    service: DataExplorerService
    database: PostgresReadOnlyDatabase
    text2sql_tool: Text2SqlTool

    async def close(self) -> None:
        await self.database.close()


def _create_read_only_engine(settings: Settings) -> AsyncEngine | None:
    secret = settings.text2sql_database_url
    if secret is None:
        return None
    try:
        url = make_url(secret.get_secret_value())
    except Exception:
        raise ValueError("Text2SQL database URL is invalid") from None
    if (
        url.drivername != "postgresql+psycopg"
        or not url.username
        or not url.database
        or url.username == settings.postgres_user
    ):
        raise ValueError("Text2SQL database URL must use a distinct PostgreSQL account")
    return create_async_engine(url, pool_pre_ping=True)


def create_data_explorer_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
) -> DataExplorerResources:
    repository = SqlAlchemyDataExplorerRepository(session_factory)
    database = PostgresReadOnlyDatabase(
        _create_read_only_engine(settings),
        metadata_timeout_ms=settings.text2sql_statement_timeout_ms,
    )
    service = DataExplorerService(
        repository,
        database,
        QueryBudgets(
            statement_timeout_ms=settings.text2sql_statement_timeout_ms,
            max_rows=settings.text2sql_max_rows,
            max_plan_cost=settings.text2sql_max_plan_cost,
            max_plan_rows=settings.text2sql_max_plan_rows,
        ),
    )
    return DataExplorerResources(
        service=service,
        database=database,
        text2sql_tool=Text2SqlTool(service),
    )


def get_data_explorer_resources(request: Request) -> DataExplorerResources:
    resources = getattr(request.app.state, "data_explorer_resources", None)
    if not isinstance(resources, DataExplorerResources):
        raise RuntimeError("Application lifespan has not initialized Data Explorer resources")
    return resources
