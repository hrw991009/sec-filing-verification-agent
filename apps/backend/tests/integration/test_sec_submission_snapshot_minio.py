"""Prove immutable SEC submissions response snapshots against real MinIO."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from industry_platform.core.config import Settings
from industry_platform.modules.disclosures.adapters.snapshots import (
    MinioSecSubmissionSnapshotStore,
)
from industry_platform.modules.disclosures.domain import (
    SecSubmissionSourceKind,
    SecSubmissionSourceSnapshot,
    sec_submissions_current_url,
    sec_submissions_source_version,
    sha256_hex,
)
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.server import create_selector_event_loop

ENV_FILE_PATH = Path(__file__).resolve().parents[4] / ".env"
MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
CIK = "0000320193"


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_sec_submission_snapshot_is_private_content_addressed_and_read_verified() -> None:
    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        settings = Settings(_env_file=ENV_FILE_PATH)
        store = create_private_file_object_store(settings)
        bucket = settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("MinIO test configuration is incomplete")
        now = datetime.now(UTC)
        body = f'{{"integration_fixture":"{uuid4().hex}"}}'.encode()
        content_hash = sha256_hex(body)
        source = SecSubmissionSourceSnapshot(
            cik=CIK,
            source_kind=SecSubmissionSourceKind.CURRENT,
            source_name=f"CIK{CIK}.json",
            source_url=sec_submissions_current_url(CIK),
            source_version=sec_submissions_source_version(
                SecSubmissionSourceKind.CURRENT,
                content_hash,
            ),
            content_sha256=content_hash,
            retrieved_at=now,
            source_available_at=now,
            body=body,
            filings=(),
        )
        object_key: str | None = None
        try:
            object_key = await MinioSecSubmissionSnapshotStore(
                store,
                bucket=bucket,
            ).persist(source)

            assert object_key.startswith(f"sec/submissions/{CIK}/current/")
            assert object_key.endswith(f"/{content_hash}.json")
            assert (
                await store.read_bounded(
                    bucket=bucket,
                    object_key=object_key,
                    maximum_bytes=len(body),
                )
                == body
            )
        finally:
            if object_key is not None:
                if not object_key.startswith(f"sec/submissions/{CIK}/current/"):
                    raise RuntimeError("Refusing to remove an unexpected SEC snapshot key")
                try:
                    await store.remove(bucket=bucket, object_key=object_key)
                except FileObjectStoreError:
                    raise AssertionError(
                        "Failed to remove the exact SEC snapshot fixture"
                    ) from None

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()
