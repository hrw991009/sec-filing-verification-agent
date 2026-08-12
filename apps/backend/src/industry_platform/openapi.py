"""Deterministically export the public HTTP API contract without starting services."""

import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from industry_platform.core.config import AppEnvironment, Settings
from industry_platform.main import create_app


def _contract_settings() -> Settings:
    """Build complete, inert settings that cannot depend on the host environment."""

    private_key = bytes(range(32))
    inert_credential = "-".join(("unused", "openapi", "value"))
    public_key = (
        Ed25519PrivateKey.from_private_bytes(private_key)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )

    return Settings.model_validate(
        {
            "app_environment": AppEnvironment.TEST,
            "postgres_host": "127.0.0.1",
            "postgres_port": 5432,
            "postgres_db": "openapi_contract",
            "postgres_user": "openapi_contract",
            "postgres_password": inert_credential,
            "redis_host": "127.0.0.1",
            "redis_port": 6379,
            "redis_password": inert_credential,
            "refresh_token_hmac_key": b"r" * 32,
            "csrf_token_hmac_key": b"c" * 32,
            "device_token_hmac_key": b"d" * 32,
            "login_rate_limit_hmac_key": b"l" * 32,
            "refresh_recovery_aead_key": b"e" * 32,
            "browser_trusted_origins": ("https://localhost:5173",),
            "access_token_current_kid": "openapi-contract-key",
            "access_token_private_key": private_key,
            "access_token_public_keys": (("openapi-contract-key", public_key),),
            "health_check_timeout_seconds": 1.0,
            "argon2_memory_cost_kib": 65_536,
            "argon2_time_cost": 3,
            "argon2_parallelism": 1,
            "argon2_salt_length": 16,
            "argon2_hash_length": 32,
            "argon2_max_concurrency": 2,
            "login_rate_limit_ip_max_attempts": 20,
            "login_rate_limit_ip_window_seconds": 300,
            "login_rate_limit_account_max_attempts": 5,
            "login_rate_limit_account_window_seconds": 300,
        }
    )


def export_openapi(output: Path) -> None:
    """Write a stable, human-readable OpenAPI document to ``output``."""

    schema = create_app(settings=_contract_settings()).openapi()
    serialized = json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary_output = output.with_suffix(f"{output.suffix}.tmp")
    temporary_output.write_text(f"{serialized}\n", encoding="utf-8", newline="\n")
    temporary_output.replace(output)


def main() -> None:
    """Parse the required destination and export the application contract."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to the generated OpenAPI JSON document.",
    )
    arguments = parser.parse_args()
    export_openapi(arguments.output)


if __name__ == "__main__":
    main()
