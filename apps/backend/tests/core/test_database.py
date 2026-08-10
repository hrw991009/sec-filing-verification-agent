"""Tests for shared SQLAlchemy database infrastructure."""

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, UniqueConstraint

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    NAMING_CONVENTION,
    Base,
    build_database_url,
    create_database_engine,
    create_database_session_factory,
)


def test_database_url_uses_validated_settings_without_leaking_password(
    test_settings: Settings,
) -> None:
    database_url = build_database_url(test_settings)
    expected_password = test_settings.postgres_password.get_secret_value()

    assert database_url.drivername == "postgresql+psycopg"
    assert database_url.host == test_settings.postgres_host
    assert database_url.port == test_settings.postgres_port
    assert database_url.database == test_settings.postgres_db
    assert database_url.username == test_settings.postgres_user
    assert database_url.password == expected_password
    assert expected_password not in str(database_url)


def test_base_uses_stable_constraint_names() -> None:
    probe_metadata = MetaData(naming_convention=NAMING_CONVENTION)
    probe_table = Table(
        "naming_probe",
        probe_metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String(100), nullable=False),
        UniqueConstraint("slug"),
    )

    unique_constraint = next(
        constraint
        for constraint in probe_table.constraints
        if isinstance(constraint, UniqueConstraint)
    )

    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert probe_table.primary_key.name == "pk_naming_probe"
    assert unique_constraint.name == "uq_naming_probe_slug"


@pytest.mark.asyncio
async def test_session_factory_uses_explicit_transaction_safe_defaults(
    test_settings: Settings,
) -> None:
    engine = create_database_engine(test_settings)

    try:
        session_factory = create_database_session_factory(engine)

        assert session_factory.kw["bind"] is engine
        assert session_factory.kw["autoflush"] is False
        assert session_factory.kw["expire_on_commit"] is False
    finally:
        await engine.dispose()
