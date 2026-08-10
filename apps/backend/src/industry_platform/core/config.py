"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    """Supported backend execution environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


MAX_ARGON2_PROCESS_MEMORY_KIB = 1_048_576


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

    argon2_memory_cost_kib: Annotated[int, Field(ge=65_536, le=1_048_576)] = 65_536
    argon2_time_cost: Annotated[int, Field(ge=3, le=10)] = 3
    argon2_parallelism: Annotated[int, Field(ge=1, le=16)] = 1
    argon2_salt_length: Annotated[int, Field(ge=16, le=64)] = 16
    argon2_hash_length: Annotated[int, Field(ge=32, le=128)] = 32
    argon2_max_concurrency: Annotated[int, Field(ge=1, le=16)] = 2

    @model_validator(mode="after")
    def validate_argon2_process_memory_budget(self) -> Self:
        """Reject Argon2 settings that could reserve over 1 GiB per process."""

        total_memory_kib = self.argon2_memory_cost_kib * self.argon2_max_concurrency

        if total_memory_kib > MAX_ARGON2_PROCESS_MEMORY_KIB:
            raise ValueError("Argon2 process memory budget exceeds the allowed maximum")

        return self


@lru_cache
def get_settings() -> Settings:
    """Load and cache one validated settings object for this process."""

    return Settings()
