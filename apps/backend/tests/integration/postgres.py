"""Shared values for disposable PostgreSQL integration tests."""

from dataclasses import dataclass

from alembic.config import Config
from sqlalchemy.engine import Engine

from industry_platform.core.config import Settings


@dataclass(frozen=True, slots=True)
class PostgresProbe:
    """Resources belonging to one isolated, disposable PostgreSQL database."""

    config: Config
    engine: Engine
    settings: Settings
