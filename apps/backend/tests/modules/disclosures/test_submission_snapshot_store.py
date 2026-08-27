"""Immutable SEC response snapshot storage contract."""

from dataclasses import dataclass, field
from typing import cast

import pytest

from industry_platform.modules.disclosures.adapters.snapshots import (
    MinioSecSubmissionSnapshotStore,
)
from industry_platform.modules.disclosures.domain import SecSourceError, SecSourceErrorCode
from industry_platform.modules.files.ports import PrivateFileObjectStore, StoredObjectStat

from .test_filing_selection_service import source_snapshot


@dataclass(slots=True)
class MemoryObjectStore:
    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)
    corrupt_reads: bool = False

    async def put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None:
        assert content_type == "application/json"
        self.objects[(bucket, object_key)] = content

    async def stat(self, *, bucket: str, object_key: str) -> StoredObjectStat:
        value = self.objects[(bucket, object_key)]
        return StoredObjectStat(
            size=len(value), etag="fixture-etag", content_type="application/json"
        )

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes:
        value = self.objects[(bucket, object_key)]
        assert len(value) <= maximum_bytes
        return b"corrupt" if self.corrupt_reads else value


@pytest.mark.asyncio
async def test_snapshot_store_uses_content_addressed_key_and_verifies_round_trip() -> None:
    source = source_snapshot()
    object_store = MemoryObjectStore()

    key = await MinioSecSubmissionSnapshotStore(
        cast(PrivateFileObjectStore, object_store),
        bucket="private-fixtures",
    ).persist(source)

    assert key.endswith(f"/{source.content_sha256}.json")
    assert object_store.objects[("private-fixtures", key)] == source.body


@pytest.mark.asyncio
async def test_snapshot_store_rejects_corrupted_round_trip() -> None:
    object_store = MemoryObjectStore(corrupt_reads=True)

    with pytest.raises(SecSourceError) as caught:
        await MinioSecSubmissionSnapshotStore(
            cast(PrivateFileObjectStore, object_store),
            bucket="private-fixtures",
        ).persist(source_snapshot())

    assert caught.value.code is SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE
