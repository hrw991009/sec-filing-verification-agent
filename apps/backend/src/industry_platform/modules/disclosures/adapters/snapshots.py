"""Immutable object storage for official SEC response snapshots."""

from industry_platform.modules.disclosures.domain import (
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSourceSnapshot,
    sec_submission_object_key,
    sha256_hex,
)
from industry_platform.modules.files.ports import FileObjectStoreError, PrivateFileObjectStore


class MinioSecSubmissionSnapshotStore:
    def __init__(self, store: PrivateFileObjectStore, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("SEC snapshot bucket is invalid")
        self._store = store
        self._bucket = bucket

    async def persist(self, source: SecSubmissionSourceSnapshot) -> str:
        object_key = sec_submission_object_key(source)
        try:
            await self._store.put_private(
                bucket=self._bucket,
                object_key=object_key,
                content_type="application/json",
                content=source.body,
            )
            stat = await self._store.stat(bucket=self._bucket, object_key=object_key)
            if stat.size != len(source.body):
                raise SecSourceError(
                    SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                    retryable=True,
                )
            stored = await self._store.read_bounded(
                bucket=self._bucket,
                object_key=object_key,
                maximum_bytes=SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
            )
            if sha256_hex(stored) != source.content_sha256:
                raise SecSourceError(
                    SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                    retryable=True,
                )
            return object_key
        except SecSourceError:
            raise
        except FileObjectStoreError:
            raise SecSourceError(
                SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                retryable=True,
            ) from None


class UnavailableSecSubmissionSnapshotStore:
    async def persist(self, source: SecSubmissionSourceSnapshot) -> str:
        del source
        raise SecSourceError(
            SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
            retryable=False,
        )
