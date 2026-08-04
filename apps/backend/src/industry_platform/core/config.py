"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    """Supported backend execution environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated configuration for one backend process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    app_environment: AppEnvironment

    postgres_host: str = Field(min_length=1)
    postgres_port: Annotated[int, Field(ge=1, le=65_535)]
    postgres_db: str = Field(min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr

    redis_host: str = Field(min_length=1)
    redis_port: Annotated[int, Field(ge=1, le=65_535)]
    redis_password: SecretStr

    health_check_timeout_seconds: Annotated[float, Field(gt=0, le=10)] = 1.0


@lru_cache
def get_settings() -> Settings:
    """Load and cache one validated settings object for this process."""

    return Settings()
