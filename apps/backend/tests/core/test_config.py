"""Tests for typed application settings."""

import pytest
from pydantic import ValidationError

from industry_platform.core.config import AppEnvironment, Settings

VALID_ENVIRONMENT = {
    "APP_ENVIRONMENT": "development",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "15432",
    "POSTGRES_DB": "industry_platform",
    "POSTGRES_USER": "industry_platform",
    "POSTGRES_PASSWORD": "placeholder",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "16379",
    "REDIS_PASSWORD": "placeholder",
    "HEALTH_CHECK_TIMEOUT_SECONDS": "1.0",
}


def configure_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a complete controlled environment for one test."""

    for variable_name, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(variable_name, value)


def test_settings_load_and_convert_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.app_environment is AppEnvironment.DEVELOPMENT
    assert settings.postgres_port == 15432
    assert settings.redis_port == 16379
    assert settings.health_check_timeout_seconds == 1.0


def test_settings_hide_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_valid_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert "placeholder" not in repr(settings)
    assert str(settings.postgres_password) == "**********"
    assert str(settings.redis_password) == "**********"


def test_settings_reject_missing_required_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.delenv("POSTGRES_PASSWORD")

    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("variable_name", "invalid_value"),
    [
        ("APP_ENVIRONMENT", "staging"),
        ("POSTGRES_PORT", "70000"),
        ("REDIS_PORT", "0"),
        ("HEALTH_CHECK_TIMEOUT_SECONDS", "0"),
    ],
)
def test_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    variable_name: str,
    invalid_value: str,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv(variable_name, invalid_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
