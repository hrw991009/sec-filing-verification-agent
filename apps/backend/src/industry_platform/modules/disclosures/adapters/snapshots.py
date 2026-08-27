"""Immutable object storage for official SEC response snapshots."""

from industry_platform.modules.disclosures.domain import (
    SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    SEC_MAX_XBRL_RESPONSE_BYTES,
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    SecFilingSnapshotReference,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSourceSnapshot,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    sec_filing_snapshot_object_key,
    sec_submission_object_key,
    sec_xbrl_object_key,
    sec_xbrl_source_version,
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


class MinioSecFilingDocumentSnapshotStore:
    def __init__(self, store: PrivateFileObjectStore, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("SEC snapshot bucket is invalid")
        self._store = store
        self._bucket = bucket

    async def persist(self, source: SecFilingDocumentSnapshot) -> str:
        object_key = sec_filing_snapshot_object_key(source)
        try:
            await self._store.put_private(
                bucket=self._bucket,
                object_key=object_key,
                content_type=source.content_type,
                content=source.body,
            )
            stat = await self._store.stat(bucket=self._bucket, object_key=object_key)
            stored = await self._store.read_bounded(
                bucket=self._bucket,
                object_key=object_key,
                maximum_bytes=SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
            )
            if stat.size != source.byte_size or sha256_hex(stored) != source.content_sha256:
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


class UnavailableSecFilingDocumentSnapshotStore:
    async def persist(self, source: SecFilingDocumentSnapshot) -> str:
        del source
        raise SecSourceError(
            SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
            retryable=False,
        )


class MinioSecXbrlSnapshotStore:
    def __init__(self, store: PrivateFileObjectStore, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("SEC XBRL snapshot bucket is invalid")
        self._store = store
        self._bucket = bucket

    async def persist_aggregate(self, source: SecXbrlSourceSnapshot) -> str:
        if source.source_kind is not SecXbrlSourceKind.COMPANYFACTS_AGGREGATE:
            raise ValueError("Only aggregate XBRL responses own a separate object")
        object_key = sec_xbrl_object_key(source)
        try:
            await self._store.put_private(
                bucket=self._bucket,
                object_key=object_key,
                content_type=source.content_type,
                content=source.body,
            )
            stat = await self._store.stat(bucket=self._bucket, object_key=object_key)
            stored = await self._store.read_bounded(
                bucket=self._bucket,
                object_key=object_key,
                maximum_bytes=SEC_MAX_XBRL_RESPONSE_BYTES,
            )
            if stat.size != source.byte_size or sha256_hex(stored) != source.content_sha256:
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

    async def read_raw(
        self,
        source: SecFilingSnapshotReference,
        *,
        cik: str,
    ) -> SecXbrlSourceSnapshot:
        source_kind = {
            SecFilingDocumentKind.PRIMARY_DOCUMENT: SecXbrlSourceKind.RAW_INLINE,
            SecFilingDocumentKind.XBRL_INSTANCE: SecXbrlSourceKind.RAW_INSTANCE,
        }.get(source.kind)
        if source_kind is None or source.object_bucket != self._bucket:
            raise ValueError("SEC raw XBRL source is invalid")
        try:
            body = await self._store.read_bounded(
                bucket=source.object_bucket,
                object_key=source.object_key,
                maximum_bytes=SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
            )
        except FileObjectStoreError:
            raise SecSourceError(
                SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                retryable=True,
            ) from None
        if len(body) != source.byte_size or sha256_hex(body) != source.content_sha256:
            raise SecSourceError(
                SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
                retryable=True,
            )
        return SecXbrlSourceSnapshot(
            source_kind=source_kind,
            cik=cik,
            source_url=source.source_url,
            source_version=sec_xbrl_source_version(source_kind, source.content_sha256),
            content_type=source.content_type,
            content_sha256=source.content_sha256,
            byte_size=source.byte_size,
            retrieved_at=source.retrieved_at,
            source_available_at=source.source_available_at,
            body=body,
            filing_snapshot_id=source.snapshot_id,
        )


class UnavailableSecXbrlSnapshotStore:
    async def persist_aggregate(self, source: SecXbrlSourceSnapshot) -> str:
        del source
        raise SecSourceError(
            SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
            retryable=False,
        )

    async def read_raw(
        self,
        source: SecFilingSnapshotReference,
        *,
        cik: str,
    ) -> SecXbrlSourceSnapshot:
        del source, cik
        raise SecSourceError(
            SecSourceErrorCode.SNAPSHOT_STORE_UNAVAILABLE,
            retryable=False,
        )
