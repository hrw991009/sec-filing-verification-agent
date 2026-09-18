"""Contracts for the complete local deployment and its private first-run configuration."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from dotenv import dotenv_values

from industry_platform.modules.files.adapters.minio import MinioPrivateFileObjectStore

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "local_bootstrap", ROOT / "infra/local/bootstrap.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_preserves_secrets_and_tls_and_imports_only_provider_settings(
    bootstrap: ModuleType, tmp_path: Path
) -> None:
    imported = tmp_path / "import.env"
    imported.write_text(
        "POSTGRES_PASSWORD=must-not-import\nSEC_USER_AGENT_APP=LocalDemo/1\nSEC_USER_AGENT_EMAIL=demo@example.com\n"
    )
    configuration = tmp_path / "config"
    bootstrap.initialize(configuration, imported)
    secrets = (configuration / "secrets.env").read_bytes()
    certificate = (configuration / "certs/localhost.crt").read_bytes()
    values = dotenv_values(configuration / "runtime.env")
    assert values["POSTGRES_PASSWORD"] != dotenv_values(imported)["POSTGRES_PASSWORD"]
    assert values["MINIO_PUBLIC_ENDPOINT"] == "localhost:8443"
    assert values["MINIO_PUBLIC_SECURE"] == "true"
    assert values["MINIO_ENDPOINT"] == "minio:9000"
    assert values["SEC_USER_AGENT_EMAIL"] == "demo@example.com"
    assert "SEC_CONTROLLED_SOURCE_MANIFEST_PATH" not in values
    assert len(json.loads(str(values["ACCESS_TOKEN_PUBLIC_KEYS_JSON"]))) == 1
    bootstrap.initialize(configuration, demo=True)
    assert (configuration / "secrets.env").read_bytes() == secrets
    assert (configuration / "certs/localhost.crt").read_bytes() == certificate
    assert dotenv_values(configuration / "runtime.env")["APP_ENVIRONMENT"] == "test"
    bootstrap.initialize(configuration)
    assert "SEC_CONTROLLED_SOURCE_MANIFEST_PATH" not in dotenv_values(configuration / "runtime.env")


def test_bootstrap_refuses_partial_tls_without_rotating_secrets(
    bootstrap: ModuleType, tmp_path: Path
) -> None:
    bootstrap.initialize(tmp_path)
    original = (tmp_path / "secrets.env").read_bytes()
    (tmp_path / "certs/localhost.crt").unlink()
    with pytest.raises(ValueError, match="Incomplete local TLS"):
        bootstrap.initialize(tmp_path)
    assert (tmp_path / "secrets.env").read_bytes() == original


def test_bootstrap_validation_does_not_echo_private_configuration(
    bootstrap: ModuleType, tmp_path: Path
) -> None:
    imported = tmp_path / "import.env"
    imported.write_text("AGENT_MODEL_ROUTE_JSON=private-value-must-not-be-printed\n")
    with pytest.raises(ValueError, match="Invalid local configuration") as caught:
        bootstrap.initialize(tmp_path / "config", imported)
    assert "private-value-must-not-be-printed" not in str(caught.value)


def test_local_compose_has_all_actors_without_publishing_internal_services() -> None:
    source = (ROOT / "infra/local/compose.yaml").read_text()
    for service in ("api", "worker", "dispatcher", "reconciler", "beat", "web", "migrate"):
        assert f"  {service}:" in source
    assert source.count("ports: !reset []") == 5
    assert '"127.0.0.1:8443:8443"' in source
    assert "service_completed_successfully" in source
    assert "postgres_data:" in source
    assert "minio_data:" in source
    assert "../compose/compose.yaml" in source
    ignore = (ROOT / ".dockerignore").read_text()
    assert "**/.env" in ignore
    assert "**/*.key" in ignore
    proxy = (ROOT / "infra/local/nginx.conf.template").read_text()
    assert "proxy_buffering off" in proxy
    assert "proxy_set_header Host $http_host" in proxy
    assert "location ~ ^/${MINIO_BUCKET}(?:/|$)" in proxy
    assert "access_log off" in proxy


@pytest.mark.asyncio
async def test_download_signs_the_public_origin_while_storage_remains_internal() -> None:
    internal, external = MagicMock(), MagicMock()
    external.presigned_get_object.return_value = (
        "https://localhost:8443/private/file?signature=test"
    )
    internal.presigned_post_policy.return_value = {"policy": "signed-policy"}
    store = MinioPrivateFileObjectStore(
        client=internal, public_endpoint="localhost:8443", secure=True, presign_client=external
    )
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        await store.presign_get(bucket="private", object_key="file", expires_at=expiry)
    ).startswith("https://localhost:8443/")
    internal.presigned_get_object.assert_not_called()
    external.presigned_get_object.assert_called_once()
    upload = await store.presign_post(
        bucket="private",
        object_key="file",
        content_type="text/plain",
        exact_size=4,
        expires_at=expiry,
    )
    assert upload.url == "https://localhost:8443/private"
    internal.presigned_post_policy.assert_called_once()
