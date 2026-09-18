"""Generate a private, repeatable local configuration without host Python or leaked secrets."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from dotenv import dotenv_values
from pydantic import ValidationError

PROVIDER_KEYS = (
    "AGENT_MODEL_PROVIDER_BASE_URL",
    "AGENT_MODEL_PROVIDER_API_KEY",
    "AGENT_MODEL_ROUTE_JSON",
    "AGENT_MODEL_REQUEST_TIMEOUT_SECONDS",
    "SEC_USER_AGENT_APP",
    "SEC_USER_AGENT_EMAIL",
)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def write_env(path: Path, values: dict[str, str]) -> None:
    # Single quoting protects JSON, spaces and dollar signs in both dotenv and Compose.
    if any("\n" in value or "\r" in value or "'" in value for value in values.values()):
        raise ValueError("Configuration values must be single-line and contain no single quotes")
    path.write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items()), encoding="utf-8"
    )
    path.chmod(0o600)


def initialize(directory: Path, import_env: Path | None = None, demo: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    secret_file = directory / "secrets.env"
    if not secret_file.exists():
        signing = ed25519.Ed25519PrivateKey.generate()
        values = {
            "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            "REDIS_PASSWORD": secrets.token_urlsafe(32),
            "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
            "ACCESS_TOKEN_CURRENT_KID": "local",
            "ACCESS_TOKEN_PRIVATE_KEY_B64": encode(signing.private_bytes_raw()),
            "ACCESS_TOKEN_PUBLIC_KEYS_JSON": json.dumps(
                {"local": encode(signing.public_key().public_bytes_raw())}
            ),
        }
        for name in (
            "REFRESH_TOKEN_HMAC_KEY_B64",
            "CSRF_TOKEN_HMAC_KEY_B64",
            "DEVICE_TOKEN_HMAC_KEY_B64",
            "LOGIN_RATE_LIMIT_HMAC_KEY_B64",
            "REFRESH_RECOVERY_AEAD_KEY_B64",
        ):
            values[name] = encode(secrets.token_bytes(32))
        write_env(secret_file, values)
    values = {
        key: value
        for key, value in dotenv_values(secret_file, interpolate=False).items()
        if value is not None
    }
    provider_file = directory / "provider.env"
    if import_env is not None:
        imported = dotenv_values(import_env, interpolate=False)
        write_env(provider_file, {key: imported[key] for key in PROVIDER_KEYS if imported.get(key)})
    elif not provider_file.exists():
        write_env(provider_file, {key: "" for key in PROVIDER_KEYS})
    provider = dotenv_values(provider_file, interpolate=False)
    values.update({key: provider[key] for key in PROVIDER_KEYS if provider.get(key)})
    values.update(
        {
            "APP_ENVIRONMENT": "test" if demo else "development",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "sec_filing",
            "POSTGRES_USER": "sec_filing",
            "REDIS_HOST": "redis",
            "REDIS_PORT": "6379",
            "MINIO_ROOT_USER": "sec_filing",
            "MINIO_ACCESS_KEY": "sec_filing",
            "MINIO_SECRET_KEY": values["MINIO_ROOT_PASSWORD"],
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_BUCKET": "sec-filing-private",
            "MINIO_REGION": "us-east-1",
            "MINIO_SECURE": "false",
            "MINIO_PUBLIC_ENDPOINT": "localhost:8443",
            "MINIO_PUBLIC_SECURE": "true",
            "MILVUS_ENDPOINT": "http://milvus:19530",
            "ELASTICSEARCH_ENDPOINT": "http://elasticsearch:9200",
            "BROWSER_TRUSTED_ORIGINS_JSON": '["https://localhost:8443"]',
            "MINIO_API_CORS_ALLOW_ORIGIN": "https://localhost:8443",
            "HEALTH_CHECK_TIMEOUT_SECONDS": "5",
            "KNOWLEDGE_INDEX_TIMEOUT_SECONDS": "30",
            "PGADMIN_DEFAULT_EMAIL": "unused@example.test",
            "PGADMIN_DEFAULT_PASSWORD": "unused-not-started",
        }
    )
    if demo:
        values["SEC_CONTROLLED_SOURCE_MANIFEST_PATH"] = (
            "evals/fixtures/sec/sec-browser-v1/manifest.json"
        )
    write_env(directory / "runtime.env", values)
    cert_directory = directory / "certs"
    cert_directory.mkdir(exist_ok=True)
    key_file, cert_file = cert_directory / "localhost.key", cert_directory / "localhost.crt"
    if key_file.exists() != cert_file.exists():
        raise ValueError("Incomplete local TLS material; restore the matching certificate/key")
    if not key_file.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SEC Filing Agent localhost")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256())
        )
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_file.chmod(0o600)
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    # Validate the complete runtime configuration before any database/container is started.
    from industry_platform.core.config import Settings

    try:
        Settings(_env_file=directory / "runtime.env")
    except ValidationError as error:
        fields = {
            ".".join(str(part) for part in item["loc"]) or "cross-field constraints"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        }
        raise ValueError("Invalid local configuration: " + ", ".join(sorted(fields))) from None
    sys.stdout.write(
        "Local configuration ready. Credentials were not printed. "
        + (
            "Frozen SEC demo sources enabled; model remains real."
            if demo
            else "Live SEC mode; model and SEC identity must be configured."
        )
        + "\n"
    )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/config"))
    parser.add_argument("--import-env", type=Path)
    parser.add_argument("--demo", action="store_true")
    arguments = parser.parse_args()
    initialize(arguments.directory, arguments.import_env, arguments.demo)
