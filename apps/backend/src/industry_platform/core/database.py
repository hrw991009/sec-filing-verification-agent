"""PostgreSQL engine construction and connectivity checks."""

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from industry_platform.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create the process-wide asynchronous PostgreSQL engine."""

    database_url = URL.create(
        drivername="postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )

    return create_async_engine(
        database_url,
        pool_pre_ping=True,
    )


async def check_database_connection(engine: AsyncEngine) -> None:
    """Raise when PostgreSQL cannot execute a minimal query."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
