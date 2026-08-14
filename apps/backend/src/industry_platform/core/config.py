"""Typed application settings loaded from environment variables."""

import hmac
import json
import re
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64DecodeError
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from industry_platform.core.origins import (
    decode_browser_trusted_origins as parse_browser_trusted_origins,
)


class AppEnvironment(StrEnum):
    """Supported backend execution environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


MAX_ARGON2_PROCESS_MEMORY_KIB = 1_048_576
SESSION_TOKEN_HMAC_KEY_BYTES = 32
SESSION_TOKEN_HMAC_KEY_TEXT_LENGTH = 43
ED25519_KEY_BYTES = 32
ED25519_KEY_TEXT_LENGTH = 43
REFRESH_RECOVERY_AEAD_KEY_BYTES = 32
type AccessTokenPublicKeys = tuple[tuple[str, bytes], ...]

_CANONICAL_SESSION_TOKEN_HMAC_KEY_PATTERN = re.compile(
    rf"^[A-Za-z0-9_-]{{{SESSION_TOKEN_HMAC_KEY_TEXT_LENGTH}}}$"
)
_CANONICAL_ED25519_KEY_PATTERN = re.compile(rf"^[A-Za-z0-9_-]{{{ED25519_KEY_TEXT_LENGTH}}}$")
_ACCESS_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUEUE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_INVALID_HMAC_CONFIGURATION_MESSAGE = (
    "Authentication HMAC key must be canonical unpadded base64url for 32 bytes"
)
_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE = (
    "Access signing keys must use the required canonical Ed25519 representation"
)
_INVALID_RECOVERY_CONFIGURATION_MESSAGE = (
    "Refresh recovery key must be canonical unpadded base64url for 32 bytes"
)


def _decode_canonical_key(
    value: object,
    *,
    pattern: re.Pattern[str],
    expected_bytes: int,
    error_message: str,
) -> bytes:
    """Decode one strict base64url key without echoing rejected material."""

    if isinstance(value, SecretBytes):
        key_bytes = value.get_secret_value()
    elif isinstance(value, bytes):
        key_bytes = value
    elif isinstance(value, str):
        if not pattern.fullmatch(value):
            raise ValueError(error_message)

        try:
            key_bytes = b64decode(
                value + "=",
                altchars=b"-_",
                validate=True,
            )
        except (Base64DecodeError, ValueError):
            raise ValueError(error_message) from None

        canonical_value = urlsafe_b64encode(key_bytes).rstrip(b"=").decode("ascii")

        if not hmac.compare_digest(canonical_value, value):
            raise ValueError(error_message)
    else:
        raise ValueError(error_message)

    if len(key_bytes) != expected_bytes:
        raise ValueError(error_message)

    return key_bytes


def _decode_session_token_hmac_key(value: object) -> SecretBytes:
    """Decode one external key without exposing rejected key material."""

    return SecretBytes(
        _decode_canonical_key(
            value,
            pattern=_CANONICAL_SESSION_TOKEN_HMAC_KEY_PATTERN,
            expected_bytes=SESSION_TOKEN_HMAC_KEY_BYTES,
            error_message=_INVALID_HMAC_CONFIGURATION_MESSAGE,
        )
    )


def _decode_access_private_key(value: object) -> SecretBytes:
    """Decode one Ed25519 private seed while keeping it out of errors."""

    return SecretBytes(
        _decode_canonical_key(
            value,
            pattern=_CANONICAL_ED25519_KEY_PATTERN,
            expected_bytes=ED25519_KEY_BYTES,
            error_message=_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE,
        )
    )


def _decode_refresh_recovery_key(value: object) -> SecretBytes:
    """Decode one AES-256 recovery key without exposing rejected material."""

    return SecretBytes(
        _decode_canonical_key(
            value,
            pattern=_CANONICAL_SESSION_TOKEN_HMAC_KEY_PATTERN,
            expected_bytes=REFRESH_RECOVERY_AEAD_KEY_BYTES,
            error_message=_INVALID_RECOVERY_CONFIGURATION_MESSAGE,
        )
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of silently keeping the last one."""

    result: dict[str, object] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)
        result[key] = value

    return result


def _decode_access_public_keys(value: object) -> AccessTokenPublicKeys:
    """Parse an immutable local Ed25519 public-key ring from JSON or Python values."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value, object_pairs_hook=_unique_json_object)
        except (TypeError, ValueError):
            raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE) from None
    elif isinstance(value, dict):
        parsed = value
    elif isinstance(value, tuple):
        parsed = {}

        for item in value:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)

            key_id, encoded_key = item

            if not isinstance(key_id, str) or key_id in parsed:
                raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)

            parsed[key_id] = encoded_key
    else:
        raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)

    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)

    decoded: dict[str, bytes] = {}

    for key_id, encoded_key in parsed.items():
        if not isinstance(key_id, str) or not _ACCESS_KEY_ID_PATTERN.fullmatch(key_id):
            raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE)

        decoded[key_id] = _decode_canonical_key(
            encoded_key,
            pattern=_CANONICAL_ED25519_KEY_PATTERN,
            expected_bytes=ED25519_KEY_BYTES,
            error_message=_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE,
        )

    return tuple(sorted(decoded.items()))


class AgentModelRouteSettings(BaseModel):
    """One trusted canonical model, upstream name, and auditable price table."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: str = Field(pattern=_MODEL_NAME_PATTERN.pattern)
    upstream_model: str = Field(pattern=_MODEL_NAME_PATTERN.pattern)
    response_models: tuple[str, ...] = Field(min_length=1, max_length=10)
    pricing_version: str = Field(pattern=_VERSION_PATTERN.pattern)
    input_micro_usd_per_million: int = Field(ge=0, le=1_000_000_000_000)
    cached_input_micro_usd_per_million: int = Field(ge=0, le=1_000_000_000_000)
    output_micro_usd_per_million: int = Field(ge=0, le=1_000_000_000_000)

    @field_validator("response_models")
    @classmethod
    def validate_response_models(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        snapshot = tuple(values)
        if len(snapshot) != len(set(snapshot)) or any(
            not _MODEL_NAME_PATTERN.fullmatch(value) for value in snapshot
        ):
            raise ValueError("Agent model response names are invalid")
        return snapshot


def _decode_agent_model_route(value: object) -> AgentModelRouteSettings | None:
    if value is None or isinstance(value, AgentModelRouteSettings):
        return value
    if isinstance(value, str):
        try:
            document = json.loads(value, object_pairs_hook=_unique_json_object)
        except (TypeError, ValueError):
            raise ValueError("Agent model route JSON is invalid") from None
    elif isinstance(value, dict):
        document = value
    else:
        raise ValueError("Agent model route JSON is invalid")
    if not isinstance(document, dict):
        raise ValueError("Agent model route JSON is invalid")
    return AgentModelRouteSettings.model_validate(document)


class Settings(BaseSettings):
    """Validated configuration for one backend process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        populate_by_name=True,
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

    refresh_token_hmac_key: SecretBytes = Field(validation_alias="REFRESH_TOKEN_HMAC_KEY_B64")
    csrf_token_hmac_key: SecretBytes = Field(validation_alias="CSRF_TOKEN_HMAC_KEY_B64")
    device_token_hmac_key: SecretBytes = Field(validation_alias="DEVICE_TOKEN_HMAC_KEY_B64")
    login_rate_limit_hmac_key: SecretBytes = Field(validation_alias="LOGIN_RATE_LIMIT_HMAC_KEY_B64")
    refresh_recovery_aead_key: SecretBytes = Field(validation_alias="REFRESH_RECOVERY_AEAD_KEY_B64")
    browser_trusted_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        validation_alias="BROWSER_TRUSTED_ORIGINS_JSON"
    )
    access_token_current_kid: Annotated[
        str,
        Field(pattern=_ACCESS_KEY_ID_PATTERN.pattern),
    ] = Field(validation_alias="ACCESS_TOKEN_CURRENT_KID")
    access_token_private_key: SecretBytes = Field(validation_alias="ACCESS_TOKEN_PRIVATE_KEY_B64")
    access_token_public_keys: Annotated[AccessTokenPublicKeys, NoDecode] = Field(
        validation_alias="ACCESS_TOKEN_PUBLIC_KEYS_JSON"
    )

    health_check_timeout_seconds: Annotated[float, Field(gt=0, le=10)] = 1.0

    argon2_memory_cost_kib: Annotated[int, Field(ge=65_536, le=1_048_576)] = 65_536
    argon2_time_cost: Annotated[int, Field(ge=3, le=10)] = 3
    argon2_parallelism: Annotated[int, Field(ge=1, le=16)] = 1
    argon2_salt_length: Annotated[int, Field(ge=16, le=64)] = 16
    argon2_hash_length: Annotated[int, Field(ge=32, le=128)] = 32
    argon2_max_concurrency: Annotated[int, Field(ge=1, le=16)] = 2
    login_rate_limit_ip_max_attempts: Annotated[
        int,
        Field(ge=1, le=1_000),
    ] = 20
    login_rate_limit_ip_window_seconds: Annotated[
        int,
        Field(ge=1, le=86_400),
    ] = 300
    login_rate_limit_account_max_attempts: Annotated[
        int,
        Field(ge=1, le=1_000),
    ] = 5
    login_rate_limit_account_window_seconds: Annotated[
        int,
        Field(ge=1, le=86_400),
    ] = 300

    celery_broker_redis_db: Annotated[int, Field(ge=0, le=15)] = 1
    celery_worker_prefetch_multiplier: Annotated[int, Field(ge=1, le=16)] = 1
    celery_broker_visibility_timeout_seconds: Annotated[
        int,
        Field(ge=3_600, le=3_600),
    ] = 3_600
    job_default_queue: Annotated[
        str,
        Field(pattern=_QUEUE_NAME_PATTERN.pattern),
    ] = "default"
    job_lease_seconds: Annotated[int, Field(ge=10, le=86_400)] = 120
    job_heartbeat_seconds: Annotated[int, Field(ge=1, le=3_600)] = 30
    job_unstarted_timeout_seconds: Annotated[int, Field(ge=10, le=3_600)] = 300
    job_default_soft_time_limit_seconds: Annotated[
        int,
        Field(ge=1, le=1_799),
    ] = 1_500
    job_default_hard_time_limit_seconds: Annotated[
        int,
        Field(ge=2, le=1_800),
    ] = 1_800
    job_dispatch_batch_size: Annotated[int, Field(ge=1, le=1_000)] = 100
    job_reconcile_batch_size: Annotated[int, Field(ge=1, le=1_000)] = 100
    outbox_claim_seconds: Annotated[int, Field(ge=10, le=3_600)] = 60
    outbox_dispatch_batch_size: Annotated[int, Field(ge=1, le=1_000)] = 100
    scheduler_scan_interval_seconds: Annotated[
        int,
        Field(ge=1, le=300),
    ] = 15

    agent_model_provider_base_url: str | None = None
    agent_model_provider_api_key: SecretStr | None = None
    agent_model_route: Annotated[AgentModelRouteSettings | None, NoDecode] = Field(
        default=None,
        validation_alias="AGENT_MODEL_ROUTE_JSON",
    )
    agent_model_request_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0

    @field_validator(
        "refresh_token_hmac_key",
        "csrf_token_hmac_key",
        "device_token_hmac_key",
        "login_rate_limit_hmac_key",
        mode="before",
    )
    @classmethod
    def decode_authentication_hmac_key(cls, value: object) -> SecretBytes:
        """Accept one decoded internal key or strict external base64url text."""

        return _decode_session_token_hmac_key(value)

    @field_validator("refresh_recovery_aead_key", mode="before")
    @classmethod
    def decode_refresh_recovery_key(cls, value: object) -> SecretBytes:
        """Accept only one independent canonical AES-256 key."""

        return _decode_refresh_recovery_key(value)

    @field_validator("browser_trusted_origins", mode="before")
    @classmethod
    def decode_browser_trusted_origins(cls, value: object) -> tuple[str, ...]:
        """Require a non-empty exact allowlist of HTTPS browser origins."""

        return parse_browser_trusted_origins(value)

    @field_validator("access_token_private_key", mode="before")
    @classmethod
    def decode_access_private_key(cls, value: object) -> SecretBytes:
        """Accept only one canonical raw Ed25519 private seed."""

        return _decode_access_private_key(value)

    @field_validator("access_token_public_keys", mode="before")
    @classmethod
    def decode_access_public_keys(cls, value: object) -> AccessTokenPublicKeys:
        """Accept a non-empty immutable ring of canonical Ed25519 public keys."""

        return _decode_access_public_keys(value)

    @field_validator("agent_model_route", mode="before")
    @classmethod
    def decode_agent_model_route(cls, value: object) -> AgentModelRouteSettings | None:
        """Parse one strict route without logging rejected model configuration."""

        return _decode_agent_model_route(value)

    @model_validator(mode="after")
    def validate_argon2_process_memory_budget(self) -> Self:
        """Reject Argon2 settings that could reserve over 1 GiB per process."""

        total_memory_kib = self.argon2_memory_cost_kib * self.argon2_max_concurrency

        if total_memory_kib > MAX_ARGON2_PROCESS_MEMORY_KIB:
            raise ValueError("Argon2 process memory budget exceeds the allowed maximum")

        return self

    @model_validator(mode="after")
    def validate_agent_model_configuration(self) -> Self:
        """Allow a fully configured Provider or a deliberate not-configured state."""

        values = (
            self.agent_model_provider_base_url,
            self.agent_model_provider_api_key,
            self.agent_model_route,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("Agent model Provider configuration must be complete")
        return self

    @property
    def agent_model_provider_configured(self) -> bool:
        return self.agent_model_route is not None

    @model_validator(mode="after")
    def validate_reliable_job_timing(self) -> Self:
        """Keep heartbeat and broker redelivery windows outside the job lease."""

        if self.job_heartbeat_seconds >= self.job_lease_seconds:
            raise ValueError("Job heartbeat interval must be shorter than its lease")

        if self.job_default_soft_time_limit_seconds >= self.job_default_hard_time_limit_seconds:
            raise ValueError("Job soft time limit must be shorter than its hard limit")

        if (
            self.celery_broker_visibility_timeout_seconds
            <= self.job_default_hard_time_limit_seconds
        ):
            raise ValueError("Celery visibility timeout must exceed the hard time limit")

        return self

    @model_validator(mode="after")
    def validate_distinct_authentication_secret_keys(self) -> Self:
        """Prevent one compromised secret from crossing authentication purposes."""

        key_values = (
            self.refresh_token_hmac_key.get_secret_value(),
            self.csrf_token_hmac_key.get_secret_value(),
            self.device_token_hmac_key.get_secret_value(),
            self.login_rate_limit_hmac_key.get_secret_value(),
            self.refresh_recovery_aead_key.get_secret_value(),
            self.access_token_private_key.get_secret_value(),
        )

        if len(set(key_values)) != len(key_values):
            raise ValueError("Authentication secret keys must be distinct")

        return self

    @model_validator(mode="after")
    def validate_current_access_key_id(self) -> Self:
        """Require the active signing key to match its local verification entry."""

        public_keys = dict(self.access_token_public_keys)
        configured_public_key = public_keys.get(self.access_token_current_kid)

        if configured_public_key is None:
            raise ValueError("Current access signing key ID is absent from the public key ring")

        try:
            derived_public_key = (
                Ed25519PrivateKey.from_private_bytes(
                    self.access_token_private_key.get_secret_value()
                )
                .public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            )
        except (TypeError, ValueError):
            raise ValueError(_INVALID_ASYMMETRIC_CONFIGURATION_MESSAGE) from None

        if not hmac.compare_digest(derived_public_key, configured_public_key):
            raise ValueError("Current access signing private and public keys do not match")

        return self


@lru_cache
def get_settings() -> Settings:
    """Load and cache one validated settings object for this process."""

    return Settings()
