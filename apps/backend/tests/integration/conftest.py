"""Disposable PostgreSQL fixtures shared by backend integration tests."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command as alembic_command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from industry_platform.core.config import Settings
from industry_platform.core.database import build_database_url

from .postgres import PostgresProbe

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parents[1]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic.ini"
ENV_FILE_PATH = REPOSITORY_ROOT / ".env"

POSTGRES_TESTS_REQUIRED = "POSTGRES_TESTS_REQUIRED"
LEGACY_MIGRATION_SMOKE_REQUIRED = "MIGRATION_SMOKE_REQUIRED"
PROBE_DATABASE_PREFIX = "iip_postgres_test_"


def _postgres_tests_enabled() -> bool:
    """Keep the original migration flag compatible with the broader gate."""

    return (
        os.getenv(POSTGRES_TESTS_REQUIRED) == "1"
        or os.getenv(LEGACY_MIGRATION_SMOKE_REQUIRED) == "1"
    )


@pytest.fixture
def postgres_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[PostgresProbe]:
    """Create and always remove one randomly named PostgreSQL database."""

    if not _postgres_tests_enabled():
        pytest.skip(f"Set {POSTGRES_TESTS_REQUIRED}=1 to run PostgreSQL integration tests")

    admin_settings = Settings(_env_file=ENV_FILE_PATH)
    probe_database = f"{PROBE_DATABASE_PREFIX}{uuid4().hex}"

    with psycopg.connect(
        host=admin_settings.postgres_host,
        port=admin_settings.postgres_port,
        dbname="postgres",
        user=admin_settings.postgres_user,
        password=admin_settings.postgres_password.get_secret_value(),
        autocommit=True,
    ) as admin_connection:
        admin_connection.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(probe_database),
            )
        )

    engine: Engine | None = None

    try:
        monkeypatch.setenv("APP_ENVIRONMENT", "test")
        monkeypatch.setenv("POSTGRES_DB", probe_database)

        probe_settings = Settings(_env_file=ENV_FILE_PATH)
        engine = create_engine(
            build_database_url(probe_settings),
            pool_pre_ping=True,
        )

        yield PostgresProbe(
            config=Config(str(ALEMBIC_CONFIG_PATH)),
            engine=engine,
            settings=probe_settings,
        )
    finally:
        if engine is not None:
            engine.dispose()

        if not probe_database.startswith(PROBE_DATABASE_PREFIX):
            raise RuntimeError(f"Refusing to remove unexpected database: {probe_database}")

        with psycopg.connect(
            host=admin_settings.postgres_host,
            port=admin_settings.postgres_port,
            dbname="postgres",
            user=admin_settings.postgres_user,
            password=admin_settings.postgres_password.get_secret_value(),
            autocommit=True,
        ) as admin_connection:
            admin_connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(probe_database),
                )
            )


@pytest.fixture
def migrated_postgres_probe(postgres_probe: PostgresProbe) -> PostgresProbe:
    """Apply the real migration history to a disposable PostgreSQL database."""

    alembic_command.upgrade(postgres_probe.config, "head")
    return postgres_probe
