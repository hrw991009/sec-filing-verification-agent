"""Prove immutable SEC filing document snapshots against real MinIO."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from industry_platform.core.config import Settings
from industry_platform.modules.disclosures.adapters.snapshots import (
    MinioSecFilingDocumentSnapshotStore,
)
from industry_platform.modules.disclosures.domain import (
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    sec_primary_document_url,
    sha256_hex,
)
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.server import create_selector_event_loop

ENV_FILE_PATH = Path(__file__).resolve().parents[4] / ".env"
MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
CIK = "0000320193"
ACCESSION = "0000320193-23-000106"
FILENAME = "aapl-20230930.htm"


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_sec_filing_snapshot_is_content_addressed_and_read_verified() -> None:
    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        settings = Settings(_env_file=ENV_FILE_PATH)
        store = create_private_file_object_store(settings)
        bucket = settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("MinIO test configuration is incomplete")
        now = datetime.now(UTC)
        body = f"<html><p>{uuid4().hex}</p></html>".encode()
        content_hash = sha256_hex(body)
        source = SecFilingDocumentSnapshot(
            kind=SecFilingDocumentKind.PRIMARY_DOCUMENT,
            cik=CIK,
            accession=ACCESSION,
            filename=FILENAME,
            source_url=sec_primary_document_url(CIK, ACCESSION, FILENAME),
            source_version=f"sec-filing-primary-{content_hash[:24]}",
            content_type="text/html",
            content_sha256=content_hash,
            byte_size=len(body),
            retrieved_at=now,
            source_available_at=now,
            body=body,
        )
        object_key: str | None = None
        try:
            object_key = await MinioSecFilingDocumentSnapshotStore(
                store,
                bucket=bucket,
            ).persist(source)

            prefix = f"sec/filings/{CIK}/{ACCESSION.replace('-', '')}/primary_document/"
            assert object_key.startswith(prefix)
            assert content_hash in object_key
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
                expected_prefix = (
                    f"sec/filings/{CIK}/{ACCESSION.replace('-', '')}/primary_document/"
                )
                if not object_key.startswith(expected_prefix):
                    raise RuntimeError("Refusing to remove an unexpected SEC filing snapshot key")
                try:
                    await store.remove(bucket=bucket, object_key=object_key)
                except FileObjectStoreError:
                    raise AssertionError(
                        "Failed to remove the exact SEC filing snapshot fixture"
                    ) from None

    loop = create_selector_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()
