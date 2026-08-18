"""Composition and secret-boundary tests for the read-only database resource."""

import pytest
from pydantic import SecretStr

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.data_explorer.resources import create_data_explorer_resources


@pytest.mark.asyncio
async def test_unconfigured_database_is_explicit_and_tool_contract_remains_available(
    test_settings: Settings,
) -> None:
    application_engine = create_database_engine(test_settings)
    resources = create_data_explorer_resources(
        test_settings,
        create_database_session_factory(application_engine),
    )
    try:
        assert resources.database.configured is False
        assert resources.text2sql_tool.definition.name == "database.text2sql"
    finally:
        await resources.close()
        await application_engine.dispose()


@pytest.mark.parametrize(
    "configured_url",
    [
        "not-a-postgresql-url",
        (
            "postgresql+psycopg://industry_platform_test:do-not-leak@"
            "127.0.0.1:15432/industry_platform_test"
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_application_owner_dsn_is_rejected_without_secret_echo(
    test_settings: Settings,
    configured_url: str,
) -> None:
    settings = test_settings.model_copy(update={"text2sql_database_url": SecretStr(configured_url)})
    application_engine = create_database_engine(settings)
    try:
        with pytest.raises(ValueError, match="Text2SQL database URL") as captured:
            create_data_explorer_resources(
                settings,
                create_database_session_factory(application_engine),
            )
        assert "do-not-leak" not in str(captured.value)
        assert configured_url not in str(captured.value)
    finally:
        await application_engine.dispose()
