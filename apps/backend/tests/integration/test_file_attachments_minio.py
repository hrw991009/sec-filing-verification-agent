"""Exercise the private attachment object contract against the real MinIO service."""

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx2
import pytest

from industry_platform.core.config import Settings
from industry_platform.modules.files.adapters.minio import MinioPrivateFileObjectStore
from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    FileObjectStoreError,
)
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.server import create_selector_event_loop

ENV_FILE_PATH = Path(__file__).resolve().parents[4] / ".env"
MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
TEST_PREFIX_ROOT = "integration-tests/file-attachments/"
_TEST_PREFIX_PATTERN = re.compile(rf"^{re.escape(TEST_PREFIX_ROOT)}[0-9a-f]{{32}}/$")


@dataclass(frozen=True, slots=True)
class ObjectKeys:
    """The exact random keys this test owns and is permitted to remove."""

    prefix: str
    staging: str
    final: str

    def __post_init__(self) -> None:
        if not _TEST_PREFIX_PATTERN.fullmatch(self.prefix):
            raise ValueError("MinIO integration-test prefix is invalid")
        expected_keys = {
            f"{self.prefix}staging/source.txt",
            f"{self.prefix}ready/final.txt",
        }
        if {self.staging, self.final} != expected_keys:
            raise ValueError("MinIO integration-test object keys are invalid")

    @classmethod
    def create(cls) -> "ObjectKeys":
        prefix = f"{TEST_PREFIX_ROOT}{uuid4().hex}/"
        return cls(
            prefix=prefix,
            staging=f"{prefix}staging/source.txt",
            final=f"{prefix}ready/final.txt",
        )

    def cleanup_targets(self) -> tuple[str, str]:
        """Return only exact keys; cleanup must never enumerate or clear a bucket."""

        return (self.staging, self.final)


async def _post_presigned_upload(
    client: httpx2.AsyncClient,
    *,
    url: str,
    fields: dict[str, str],
    content: bytes,
) -> httpx2.Response:
    try:
        return await client.post(
            url,
            data=fields,
            files={"file": ("source.txt", content, "text/plain")},
        )
    except httpx2.RequestError:
        raise AssertionError("Real MinIO presigned upload request failed") from None


async def _get_without_exposing_signed_url(
    client: httpx2.AsyncClient,
    url: str,
) -> httpx2.Response:
    try:
        return await client.get(url)
    except httpx2.RequestError:
        raise AssertionError("Real MinIO download request failed") from None


async def _cleanup_exact_objects(
    store: MinioPrivateFileObjectStore,
    *,
    bucket: str,
    keys: ObjectKeys,
) -> None:
    failures = 0
    for object_key in keys.cleanup_targets():
        if not object_key.startswith(keys.prefix):
            raise RuntimeError("Refusing to remove an object outside the test prefix")
        try:
            await store.remove(bucket=bucket, object_key=object_key)
        except FileObjectStoreError:
            failures += 1
    if failures:
        raise AssertionError(f"Failed to remove {failures} exact MinIO test object(s)")


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_real_minio_presign_private_round_trip_and_exact_cleanup() -> None:
    """Prove signed upload/download, bounded reads, privacy, and precise deletion."""

    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        settings = Settings(_env_file=ENV_FILE_PATH)
        store = create_private_file_object_store(settings)
        bucket = settings.minio_bucket
        endpoint = settings.minio_endpoint
        if not isinstance(store, MinioPrivateFileObjectStore) or bucket is None or endpoint is None:
            raise AssertionError("The .env file must contain complete MinIO test configuration")

        keys = ObjectKeys.create()
        source = b"Private attachment source fixture.\r\n"
        safe_final = b"Private attachment source fixture.\n"
        scheme = "https" if settings.minio_secure else "http"
        anonymous_final_url = (
            f"{scheme}://{endpoint}/{quote(bucket, safe='')}/{quote(keys.final, safe='/')}"
        )

        try:
            upload_expires_at = datetime.now(UTC) + timedelta(minutes=5)
            upload = await store.presign_post(
                bucket=bucket,
                object_key=keys.staging,
                content_type="text/plain",
                exact_size=len(source),
                expires_at=upload_expires_at,
            )

            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                uploaded = await _post_presigned_upload(
                    client,
                    url=upload.url,
                    fields=dict(upload.fields),
                    content=source,
                )
                assert uploaded.status_code == 204

                staging_stat = await store.stat(bucket=bucket, object_key=keys.staging)
                assert staging_stat.size == len(source)
                assert staging_stat.content_type == "text/plain"
                assert staging_stat.etag
                assert (
                    await store.read_bounded(
                        bucket=bucket,
                        object_key=keys.staging,
                        maximum_bytes=len(source),
                    )
                    == source
                )
                with pytest.raises(FileObjectStoreError):
                    await store.read_bounded(
                        bucket=bucket,
                        object_key=keys.staging,
                        maximum_bytes=len(source) - 1,
                    )

                await store.put_private(
                    bucket=bucket,
                    object_key=keys.final,
                    content_type="text/plain",
                    content=safe_final,
                )
                final_stat = await store.stat(bucket=bucket, object_key=keys.final)
                assert final_stat.size == len(safe_final)
                assert final_stat.content_type == "text/plain"
                assert (
                    await store.read_bounded(
                        bucket=bucket,
                        object_key=keys.final,
                        maximum_bytes=len(safe_final),
                    )
                    == safe_final
                )

                download_url = await store.presign_get(
                    bucket=bucket,
                    object_key=keys.final,
                    expires_at=datetime.now(UTC) + timedelta(seconds=30),
                )
                downloaded = await _get_without_exposing_signed_url(client, download_url)
                assert downloaded.status_code == 200
                assert downloaded.content == safe_final

                anonymous = await _get_without_exposing_signed_url(
                    client,
                    anonymous_final_url,
                )
                assert anonymous.status_code == 403

                await store.remove(bucket=bucket, object_key=keys.final)
                with pytest.raises(FileObjectNotFoundError):
                    await store.stat(bucket=bucket, object_key=keys.final)
                with pytest.raises(FileObjectNotFoundError):
                    await store.read_bounded(
                        bucket=bucket,
                        object_key=keys.final,
                        maximum_bytes=len(safe_final),
                    )
                removed_download = await _get_without_exposing_signed_url(client, download_url)
                assert removed_download.status_code == 404

                await store.remove(bucket=bucket, object_key=keys.staging)
                with pytest.raises(FileObjectNotFoundError):
                    await store.stat(bucket=bucket, object_key=keys.staging)
        finally:
            await _cleanup_exact_objects(store, bucket=bucket, keys=keys)

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
