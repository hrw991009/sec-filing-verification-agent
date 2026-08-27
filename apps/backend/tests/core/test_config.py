"""Tests for typed application settings."""

import json
from base64 import urlsafe_b64encode

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from industry_platform.core.config import AgentModelRouteSettings, AppEnvironment, Settings

REFRESH_TOKEN_HMAC_KEY_BYTES = b"r" * 32
CSRF_TOKEN_HMAC_KEY_BYTES = b"c" * 32
DEVICE_TOKEN_HMAC_KEY_BYTES = b"d" * 32
LOGIN_RATE_LIMIT_HMAC_KEY_BYTES = b"l" * 32
REFRESH_RECOVERY_AEAD_KEY_BYTES = b"e" * 32
SIGNING_KEY_ID = "test-current-key"
SIGNING_SEED = bytes(range(32))
VERIFYING_KEY = (
    Ed25519PrivateKey.from_private_bytes(SIGNING_SEED)
    .public_key()
    .public_bytes(Encoding.Raw, PublicFormat.Raw)
)


def encode_key(raw_value: bytes) -> str:
    """Encode deterministic test bytes as canonical unpadded base64url."""

    return urlsafe_b64encode(raw_value).rstrip(b"=").decode("ascii")


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
    "REFRESH_TOKEN_HMAC_KEY_B64": encode_key(REFRESH_TOKEN_HMAC_KEY_BYTES),
    "CSRF_TOKEN_HMAC_KEY_B64": encode_key(CSRF_TOKEN_HMAC_KEY_BYTES),
    "DEVICE_TOKEN_HMAC_KEY_B64": encode_key(DEVICE_TOKEN_HMAC_KEY_BYTES),
    "LOGIN_RATE_LIMIT_HMAC_KEY_B64": encode_key(LOGIN_RATE_LIMIT_HMAC_KEY_BYTES),
    "REFRESH_RECOVERY_AEAD_KEY_B64": encode_key(REFRESH_RECOVERY_AEAD_KEY_BYTES),
    "BROWSER_TRUSTED_ORIGINS_JSON": '["https://localhost:5173"]',
    "ACCESS_TOKEN_CURRENT_KID": SIGNING_KEY_ID,
    "ACCESS_TOKEN_PRIVATE_KEY_B64": encode_key(SIGNING_SEED),
    "ACCESS_TOKEN_PUBLIC_KEYS_JSON": json.dumps(
        {SIGNING_KEY_ID: encode_key(VERIFYING_KEY)},
        separators=(",", ":"),
    ),
    "HEALTH_CHECK_TIMEOUT_SECONDS": "1.0",
    "ARGON2_MEMORY_COST_KIB": "65536",
    "ARGON2_TIME_COST": "3",
    "ARGON2_PARALLELISM": "1",
    "ARGON2_SALT_LENGTH": "16",
    "ARGON2_HASH_LENGTH": "32",
    "ARGON2_MAX_CONCURRENCY": "2",
    "LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS": "20",
    "LOGIN_RATE_LIMIT_IP_WINDOW_SECONDS": "300",
    "LOGIN_RATE_LIMIT_ACCOUNT_MAX_ATTEMPTS": "5",
    "LOGIN_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS": "300",
    "CELERY_BROKER_REDIS_DB": "1",
    "CELERY_WORKER_PREFETCH_MULTIPLIER": "1",
    "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": "3600",
    "JOB_DEFAULT_QUEUE": "default",
    "JOB_LEASE_SECONDS": "120",
    "JOB_HEARTBEAT_SECONDS": "30",
    "JOB_UNSTARTED_TIMEOUT_SECONDS": "300",
    "JOB_DEFAULT_SOFT_TIME_LIMIT_SECONDS": "1500",
    "JOB_DEFAULT_HARD_TIME_LIMIT_SECONDS": "1800",
    "JOB_DISPATCH_BATCH_SIZE": "100",
    "JOB_RECONCILE_BATCH_SIZE": "100",
    "OUTBOX_CLAIM_SECONDS": "60",
    "OUTBOX_DISPATCH_BATCH_SIZE": "100",
    "SCHEDULER_SCAN_INTERVAL_SECONDS": "15",
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
    assert settings.refresh_token_hmac_key.get_secret_value() == REFRESH_TOKEN_HMAC_KEY_BYTES
    assert settings.csrf_token_hmac_key.get_secret_value() == CSRF_TOKEN_HMAC_KEY_BYTES
    assert settings.device_token_hmac_key.get_secret_value() == DEVICE_TOKEN_HMAC_KEY_BYTES
    assert settings.login_rate_limit_hmac_key.get_secret_value() == LOGIN_RATE_LIMIT_HMAC_KEY_BYTES
    assert settings.refresh_recovery_aead_key.get_secret_value() == REFRESH_RECOVERY_AEAD_KEY_BYTES
    assert settings.browser_trusted_origins == ("https://localhost:5173",)
    assert settings.access_token_current_kid == SIGNING_KEY_ID
    assert settings.access_token_private_key.get_secret_value() == SIGNING_SEED
    assert settings.access_token_public_keys == ((SIGNING_KEY_ID, VERIFYING_KEY),)
    assert settings.health_check_timeout_seconds == 1.0
    assert settings.argon2_memory_cost_kib == 65_536
    assert settings.argon2_time_cost == 3
    assert settings.argon2_parallelism == 1
    assert settings.argon2_salt_length == 16
    assert settings.argon2_hash_length == 32
    assert settings.argon2_max_concurrency == 2
    assert settings.login_rate_limit_ip_max_attempts == 20
    assert settings.login_rate_limit_ip_window_seconds == 300
    assert settings.login_rate_limit_account_max_attempts == 5
    assert settings.login_rate_limit_account_window_seconds == 300
    assert settings.celery_broker_redis_db == 1
    assert settings.celery_worker_prefetch_multiplier == 1
    assert settings.celery_broker_visibility_timeout_seconds == 3_600
    assert settings.job_default_queue == "default"
    assert settings.job_lease_seconds == 120
    assert settings.job_heartbeat_seconds == 30
    assert settings.job_unstarted_timeout_seconds == 300
    assert settings.job_default_soft_time_limit_seconds == 1_500
    assert settings.job_default_hard_time_limit_seconds == 1_800
    assert settings.job_dispatch_batch_size == 100
    assert settings.job_reconcile_batch_size == 100
    assert settings.outbox_claim_seconds == 60
    assert settings.outbox_dispatch_batch_size == 100
    assert settings.scheduler_scan_interval_seconds == 15
    assert settings.sec_source_configured is False
    assert settings.sec_requests_per_second == 8
    assert settings.sec_catalog_cache_ttl_seconds == 3_600
    assert settings.sec_request_timeout_seconds == 20.0
    assert settings.sec_request_max_attempts == 3


def test_settings_hide_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_valid_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert "placeholder" not in repr(settings)
    assert str(settings.postgres_password) == "**********"
    assert str(settings.redis_password) == "**********"
    assert VALID_ENVIRONMENT["REFRESH_TOKEN_HMAC_KEY_B64"] not in repr(settings)
    assert REFRESH_TOKEN_HMAC_KEY_BYTES.decode("ascii") not in repr(settings)
    assert VALID_ENVIRONMENT["LOGIN_RATE_LIMIT_HMAC_KEY_B64"] not in repr(settings)
    assert VALID_ENVIRONMENT["REFRESH_RECOVERY_AEAD_KEY_B64"] not in repr(settings)
    assert VALID_ENVIRONMENT["ACCESS_TOKEN_PRIVATE_KEY_B64"] not in repr(settings)
    assert SIGNING_SEED.hex() not in repr(settings)


def test_optional_agent_model_configuration_is_strict_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    provider_key = "provider-secret-value"
    monkeypatch.setenv("AGENT_MODEL_PROVIDER_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER_API_KEY", provider_key)
    monkeypatch.setenv(
        "AGENT_MODEL_ROUTE_JSON",
        json.dumps(
            {
                "model": "openai-compatible/example-model",
                "upstream_model": "example-model-2026-08-14",
                "response_models": ["example-model-2026-08-14"],
                "pricing_version": "example-pricing-v1",
                "input_micro_usd_per_million": 1_000_000,
                "cached_input_micro_usd_per_million": 100_000,
                "output_micro_usd_per_million": 2_000_000,
                "supports_image_input": True,
            }
        ),
    )

    settings = Settings(_env_file=None)

    assert settings.agent_model_provider_configured is True
    assert settings.agent_model_route == AgentModelRouteSettings(
        model="openai-compatible/example-model",
        upstream_model="example-model-2026-08-14",
        response_models=("example-model-2026-08-14",),
        pricing_version="example-pricing-v1",
        input_micro_usd_per_million=1_000_000,
        cached_input_micro_usd_per_million=100_000,
        output_micro_usd_per_million=2_000_000,
        supports_image_input=True,
    )
    assert provider_key not in repr(settings)


def test_partial_agent_model_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("AGENT_MODEL_PROVIDER_BASE_URL", "https://api.example.com/v1")

    with pytest.raises(ValidationError, match="must be complete"):
        Settings(_env_file=None)


def test_sec_user_agent_configuration_is_complete_and_below_official_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("SEC_USER_AGENT_APP", "IndustryIntelligencePlatform/0.1")
    monkeypatch.setenv("SEC_USER_AGENT_EMAIL", "edgar-ops@example.test")
    monkeypatch.setenv("SEC_REQUESTS_PER_SECOND", "9")

    settings = Settings(_env_file=None)

    assert settings.sec_source_configured is True
    assert settings.sec_user_agent == ("IndustryIntelligencePlatform/0.1 edgar-ops@example.test")
    assert settings.sec_requests_per_second == 9

    monkeypatch.delenv("SEC_USER_AGENT_EMAIL")
    with pytest.raises(ValidationError, match="SEC User-Agent configuration must be complete"):
        Settings(_env_file=None)

    monkeypatch.setenv("SEC_USER_AGENT_EMAIL", "edgar-ops@example.test")
    monkeypatch.setenv("SEC_REQUESTS_PER_SECOND", "10")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_optional_minio_configuration_is_complete_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    sensitive_storage_value = "local-object-store-secret"
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:19000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "industry-platform-files")
    monkeypatch.setenv("MINIO_SECRET_KEY", sensitive_storage_value)
    monkeypatch.setenv("MINIO_BUCKET", "industry-platform-private")
    monkeypatch.setenv("MINIO_REGION", "us-east-1")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setenv("MINIO_PRESIGN_EXPIRY_SECONDS", "600")

    settings = Settings(_env_file=None)

    assert settings.minio_configured is True
    assert settings.minio_endpoint == "127.0.0.1:19000"
    assert settings.minio_bucket == "industry-platform-private"
    assert settings.minio_presign_expiry_seconds == 600
    assert sensitive_storage_value not in repr(settings)


def test_partial_or_url_shaped_minio_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("MINIO_ENDPOINT", "https://127.0.0.1:19000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "industry-platform-files")
    monkeypatch.setenv("MINIO_SECRET_KEY", "local-object-store-secret")
    monkeypatch.setenv("MINIO_BUCKET", "industry-platform-private")

    with pytest.raises(ValidationError, match="host and port"):
        Settings(_env_file=None)

    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:19000")
    monkeypatch.delenv("MINIO_BUCKET")
    with pytest.raises(ValidationError, match="must be complete"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "variable_name",
    [
        "POSTGRES_PASSWORD",
        "REFRESH_TOKEN_HMAC_KEY_B64",
        "CSRF_TOKEN_HMAC_KEY_B64",
        "DEVICE_TOKEN_HMAC_KEY_B64",
        "LOGIN_RATE_LIMIT_HMAC_KEY_B64",
        "REFRESH_RECOVERY_AEAD_KEY_B64",
        "BROWSER_TRUSTED_ORIGINS_JSON",
        "ACCESS_TOKEN_CURRENT_KID",
        "ACCESS_TOKEN_PRIVATE_KEY_B64",
        "ACCESS_TOKEN_PUBLIC_KEYS_JSON",
    ],
)
def test_settings_reject_missing_required_value(
    monkeypatch: pytest.MonkeyPatch,
    variable_name: str,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.delenv(variable_name)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("variable_name", "invalid_value"),
    [
        ("APP_ENVIRONMENT", "staging"),
        ("POSTGRES_PORT", "70000"),
        ("REDIS_PORT", "0"),
        ("HEALTH_CHECK_TIMEOUT_SECONDS", "0"),
        ("ARGON2_MEMORY_COST_KIB", "65535"),
        ("ARGON2_TIME_COST", "2"),
        ("ARGON2_PARALLELISM", "0"),
        ("ARGON2_SALT_LENGTH", "15"),
        ("ARGON2_HASH_LENGTH", "31"),
        ("ARGON2_MAX_CONCURRENCY", "0"),
        ("LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS", "0"),
        ("LOGIN_RATE_LIMIT_IP_WINDOW_SECONDS", "86401"),
        ("LOGIN_RATE_LIMIT_ACCOUNT_MAX_ATTEMPTS", "1001"),
        ("LOGIN_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS", "0"),
        ("CELERY_BROKER_REDIS_DB", "16"),
        ("CELERY_WORKER_PREFETCH_MULTIPLIER", "0"),
        ("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", "59"),
        ("JOB_DEFAULT_QUEUE", "invalid queue"),
        ("JOB_LEASE_SECONDS", "9"),
        ("JOB_HEARTBEAT_SECONDS", "0"),
        ("JOB_UNSTARTED_TIMEOUT_SECONDS", "3601"),
        ("JOB_DEFAULT_SOFT_TIME_LIMIT_SECONDS", "1800"),
        ("JOB_DEFAULT_HARD_TIME_LIMIT_SECONDS", "1801"),
        ("JOB_DISPATCH_BATCH_SIZE", "1001"),
        ("JOB_RECONCILE_BATCH_SIZE", "0"),
        ("OUTBOX_CLAIM_SECONDS", "9"),
        ("OUTBOX_DISPATCH_BATCH_SIZE", "1001"),
        ("SCHEDULER_SCAN_INTERVAL_SECONDS", "301"),
        ("REFRESH_TOKEN_HMAC_KEY_B64", encode_key(b"x" * 31)),
        ("REFRESH_TOKEN_HMAC_KEY_B64", encode_key(b"x" * 33)),
        ("LOGIN_RATE_LIMIT_HMAC_KEY_B64", encode_key(b"x" * 31)),
        ("REFRESH_RECOVERY_AEAD_KEY_B64", encode_key(b"x" * 31)),
        ("BROWSER_TRUSTED_ORIGINS_JSON", "[]"),
        ("BROWSER_TRUSTED_ORIGINS_JSON", '["http://localhost:5173"]'),
        ("BROWSER_TRUSTED_ORIGINS_JSON", '["https://localhost:5173/path"]'),
        (
            "BROWSER_TRUSTED_ORIGINS_JSON",
            '["https://localhost:5173","https://LOCALHOST:5173"]',
        ),
        (
            "REFRESH_TOKEN_HMAC_KEY_B64",
            encode_key(b"x" * 32) + "=",
        ),
        (
            "REFRESH_TOKEN_HMAC_KEY_B64",
            "*" + encode_key(b"x" * 32)[1:],
        ),
        (
            "REFRESH_TOKEN_HMAC_KEY_B64",
            encode_key(b"\x00" * 32)[:-1] + "B",
        ),
        ("ACCESS_TOKEN_CURRENT_KID", "invalid key id"),
        ("ACCESS_TOKEN_CURRENT_KID", "k" * 65),
        ("ACCESS_TOKEN_PRIVATE_KEY_B64", encode_key(b"x" * 31)),
        ("ACCESS_TOKEN_PRIVATE_KEY_B64", encode_key(b"x" * 33)),
        ("ACCESS_TOKEN_PRIVATE_KEY_B64", encode_key(b"x" * 32) + "="),
        ("ACCESS_TOKEN_PUBLIC_KEYS_JSON", "not-json"),
        ("ACCESS_TOKEN_PUBLIC_KEYS_JSON", "{}"),
        (
            "ACCESS_TOKEN_PUBLIC_KEYS_JSON",
            json.dumps({"invalid key id": encode_key(VERIFYING_KEY)}),
        ),
        (
            "ACCESS_TOKEN_PUBLIC_KEYS_JSON",
            json.dumps({SIGNING_KEY_ID: encode_key(b"x" * 31)}),
        ),
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


def test_settings_reject_reused_session_token_hmac_keys_without_leaking_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    repeated_value = VALID_ENVIRONMENT["REFRESH_TOKEN_HMAC_KEY_B64"]
    monkeypatch.setenv("CSRF_TOKEN_HMAC_KEY_B64", repeated_value)

    with pytest.raises(ValidationError, match="must be distinct") as exc_info:
        Settings(_env_file=None)

    assert repeated_value not in str(exc_info.value)


def test_settings_reject_access_private_key_reused_as_an_hmac_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    repeated_value = VALID_ENVIRONMENT["REFRESH_TOKEN_HMAC_KEY_B64"]
    monkeypatch.setenv("ACCESS_TOKEN_PRIVATE_KEY_B64", repeated_value)

    with pytest.raises(ValidationError, match="must be distinct") as exc_info:
        Settings(_env_file=None)

    assert repeated_value not in str(exc_info.value)


def test_settings_reject_rate_limit_key_reused_for_session_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    repeated_value = VALID_ENVIRONMENT["REFRESH_TOKEN_HMAC_KEY_B64"]
    monkeypatch.setenv("LOGIN_RATE_LIMIT_HMAC_KEY_B64", repeated_value)

    with pytest.raises(ValidationError, match="must be distinct") as exc_info:
        Settings(_env_file=None)

    assert repeated_value not in str(exc_info.value)


def test_settings_reject_recovery_key_reused_for_session_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    repeated_value = VALID_ENVIRONMENT["REFRESH_TOKEN_HMAC_KEY_B64"]
    monkeypatch.setenv("REFRESH_RECOVERY_AEAD_KEY_B64", repeated_value)

    with pytest.raises(ValidationError, match="must be distinct") as exc_info:
        Settings(_env_file=None)

    assert repeated_value not in str(exc_info.value)


def test_settings_hide_rejected_session_token_key_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    malformed_value = "sensitive-invalid-key-material-must-not-appear"
    monkeypatch.setenv("REFRESH_TOKEN_HMAC_KEY_B64", malformed_value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert malformed_value not in str(exc_info.value)


def test_settings_reject_duplicate_access_public_key_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    encoded_key = encode_key(VERIFYING_KEY)
    duplicate_ring = f'{{"{SIGNING_KEY_ID}":"{encoded_key}","{SIGNING_KEY_ID}":"{encoded_key}"}}'
    monkeypatch.setenv("ACCESS_TOKEN_PUBLIC_KEYS_JSON", duplicate_ring)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert encoded_key not in str(exc_info.value)


def test_settings_reject_duplicate_access_public_key_ids_from_python_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    settings_values = Settings(_env_file=None).model_dump()
    settings_values["access_token_public_keys"] = (
        (SIGNING_KEY_ID, VERIFYING_KEY),
        (SIGNING_KEY_ID, VERIFYING_KEY),
    )

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings_values)


def test_settings_require_current_access_key_in_public_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    other_key_id = "test-other-key"
    monkeypatch.setenv(
        "ACCESS_TOKEN_PUBLIC_KEYS_JSON",
        json.dumps({other_key_id: encode_key(VERIFYING_KEY)}),
    )

    with pytest.raises(ValidationError, match="absent from the public key ring"):
        Settings(_env_file=None)


def test_settings_require_current_private_and_public_keys_to_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    other_seed = bytes(range(32, 64))
    encoded_seed = encode_key(other_seed)
    monkeypatch.setenv("ACCESS_TOKEN_PRIVATE_KEY_B64", encoded_seed)

    with pytest.raises(ValidationError, match="private and public keys do not match") as exc_info:
        Settings(_env_file=None)

    assert encoded_seed not in str(exc_info.value)


def test_settings_hide_rejected_access_private_key_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    malformed_value = "sensitive-signing-material-must-not-appear"
    monkeypatch.setenv("ACCESS_TOKEN_PRIVATE_KEY_B64", malformed_value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert malformed_value not in str(exc_info.value)


def test_settings_reject_argon2_process_memory_overcommit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("ARGON2_MEMORY_COST_KIB", "1048576")
    monkeypatch.setenv("ARGON2_MAX_CONCURRENCY", "2")

    with pytest.raises(ValidationError, match="Argon2 process memory budget"):
        Settings(_env_file=None)


def test_settings_reject_heartbeat_that_cannot_refresh_job_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("JOB_LEASE_SECONDS", "30")
    monkeypatch.setenv("JOB_HEARTBEAT_SECONDS", "30")

    with pytest.raises(ValidationError, match="heartbeat interval"):
        Settings(_env_file=None)


def test_settings_reject_broker_redelivery_inside_active_job_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", "3599")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_require_soft_limit_before_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_valid_environment(monkeypatch)
    monkeypatch.setenv("JOB_DEFAULT_SOFT_TIME_LIMIT_SECONDS", "1500")
    monkeypatch.setenv("JOB_DEFAULT_HARD_TIME_LIMIT_SECONDS", "1500")

    with pytest.raises(ValidationError, match="soft time limit"):
        Settings(_env_file=None)
