"""Shared pytest fixtures for backend tests."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import SecretBytes, SecretStr

from industry_platform.core.config import AppEnvironment, Settings

_TEST_SIGNING_KEY_ID = "test-current-key"
_TEST_SIGNING_SEED = bytes(range(32))
_TEST_VERIFYING_KEY = (
    Ed25519PrivateKey.from_private_bytes(_TEST_SIGNING_SEED)
    .public_key()
    .public_bytes(Encoding.Raw, PublicFormat.Raw)
)


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
        refresh_token_hmac_key=SecretBytes(b"r" * 32),
        csrf_token_hmac_key=SecretBytes(b"c" * 32),
        device_token_hmac_key=SecretBytes(b"d" * 32),
        login_rate_limit_hmac_key=SecretBytes(b"l" * 32),
        refresh_recovery_aead_key=SecretBytes(b"e" * 32),
        browser_trusted_origins=("https://localhost:5173",),
        agent_model_provider_base_url=None,
        agent_model_provider_api_key=None,
        agent_model_route=None,
        minio_endpoint=None,
        minio_access_key=None,
        minio_secret_key=None,
        minio_bucket=None,
        access_token_current_kid=_TEST_SIGNING_KEY_ID,
        access_token_private_key=SecretBytes(_TEST_SIGNING_SEED),
        access_token_public_keys=((_TEST_SIGNING_KEY_ID, _TEST_VERIFYING_KEY),),
        health_check_timeout_seconds=0.05,
    )
