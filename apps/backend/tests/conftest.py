"""Shared pytest fixtures for backend tests."""

import pytest
from pydantic import SecretStr

from industry_platform.core.config import AppEnvironment, Settings


@pytest.fixture
def test_settings() -> Settings:
    """Return deterministic settings that never read the developer's .env."""

    return Settings(
        _env_file=None,
        app_environment=AppEnvironment.TEST,
        postgres_host="127.0.0.1",
        postgres_port=15432,
        postgres_db="industry_platform_test",
        postgres_user="industry_platform_test",
        postgres_password=SecretStr("test-only-placeholder"),
        redis_host="127.0.0.1",
        redis_port=16379,
        redis_password=SecretStr("test-only-placeholder"),
        health_check_timeout_seconds=0.05,
    )
