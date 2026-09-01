"""MinIO adapter for private, short-lived attachment object operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from io import BytesIO
from typing import BinaryIO, TypeVar

from anyio import to_thread
from minio import Minio
from minio.datatypes import PostPolicy
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    FileObjectStoreError,
    PresignedPost,
    StoredObjectStat,
)

_ResultT = TypeVar("_ResultT")
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NotFound"})
_MAX_PREFIX_DELETE_OBJECTS = 10_000


def utc_now() -> datetime:
    return datetime.now(UTC)


class MinioPrivateFileObjectStore:
    """Run the synchronous MinIO SDK off the asyncio event loop."""

    def __init__(
        self,
        *,
        client: Minio,
        public_endpoint: str,
        secure: bool,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if "://" in public_endpoint or "/" in public_endpoint:
            raise ValueError("MinIO public endpoint must be a host and port")
        self._client = client
        self._origin = f"{'https' if secure else 'http'}://{public_endpoint}"
        self._clock = clock

    async def _call(
        self,
        operation: Callable[[], _ResultT],
        *,
        abandon_on_cancel: bool = False,
    ) -> _ResultT:
        try:
            return await to_thread.run_sync(operation, abandon_on_cancel=abandon_on_cancel)
        except S3Error as error:
            if error.code in _NOT_FOUND_CODES:
                raise FileObjectNotFoundError from None
            raise FileObjectStoreError from None
        except (MinioException, Urllib3HTTPError, OSError, ValueError):
            raise FileObjectStoreError from None

    async def presign_post(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        exact_size: int,
        expires_at: datetime,
    ) -> PresignedPost:
        policy = PostPolicy(bucket, expires_at)
        policy.add_equals_condition("key", object_key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(exact_size, exact_size)
        fields = await self._call(partial(self._client.presigned_post_policy, policy))
        fields["key"] = object_key
        fields["Content-Type"] = content_type
        return PresignedPost(
            url=f"{self._origin}/{bucket}",
            fields=fields,
            expires_at=expires_at,
        )

    async def stat(self, *, bucket: str, object_key: str) -> StoredObjectStat:
        result = await self._call(partial(self._client.stat_object, bucket, object_key))
        if result.size is None or result.etag is None:
            raise FileObjectStoreError
        return StoredObjectStat(
            size=result.size,
            etag=result.etag,
            content_type=result.content_type,
        )

    async def bucket_exists(self, *, bucket: str) -> bool:
        """Probe the configured private bucket without exposing its contents."""

        return await self._call(
            partial(self._client.bucket_exists, bucket),
            abandon_on_cancel=True,
        )

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes:
        if isinstance(maximum_bytes, bool) or maximum_bytes < 1:
            raise ValueError("Object read limit is invalid")

        def read() -> bytes:
            response = self._client.get_object(bucket, object_key)
            try:
                chunks: list[bytes] = []
                remaining = maximum_bytes + 1
                while remaining > 0:
                    chunk = response.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(bytes(chunk))
                    remaining -= len(chunk)
                content = b"".join(chunks)
                if len(content) > maximum_bytes:
                    raise FileObjectStoreError
                return content
            finally:
                response.close()
                response.release_conn()

        return await self._call(read)

    async def put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None:
        snapshot = bytes(content)
        if not snapshot:
            raise ValueError("Private object content must not be empty")
        await self._call(
            partial(
                self._client.put_object,
                bucket,
                object_key,
                BytesIO(snapshot),
                len(snapshot),
                content_type=content_type,
            )
        )

    async def put_private_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        stream: BinaryIO,
        exact_size: int,
    ) -> None:
        """Upload a bounded seekable stream without copying it into process memory."""

        if isinstance(exact_size, bool) or exact_size < 1:
            raise ValueError("Private object stream size is invalid")
        if not all(hasattr(stream, method) for method in ("read", "seek")):
            raise ValueError("Private object stream is invalid")
        stream.seek(0)
        await self._call(
            partial(
                self._client.put_object,
                bucket,
                object_key,
                stream,
                exact_size,
                content_type=content_type,
            )
        )

    async def remove(self, *, bucket: str, object_key: str) -> None:
        await self._call(partial(self._client.remove_object, bucket, object_key))

    async def remove_prefix(self, *, bucket: str, object_prefix: str) -> None:
        if not object_prefix or not object_prefix.endswith("/"):
            raise ValueError("Private object prefix is invalid")

        def remove() -> None:
            for count, item in enumerate(
                self._client.list_objects(bucket, prefix=object_prefix, recursive=True),
                start=1,
            ):
                if count > _MAX_PREFIX_DELETE_OBJECTS:
                    raise FileObjectStoreError
                self._client.remove_object(bucket, item.object_name)

        await self._call(remove)

    async def presign_get(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_at: datetime,
    ) -> str:
        duration = expires_at - self._clock()
        if duration.total_seconds() <= 0:
            raise ValueError("Download expiry must be in the future")
        return await self._call(
            partial(
                self._client.presigned_get_object,
                bucket,
                object_key,
                expires=duration,
            )
        )
