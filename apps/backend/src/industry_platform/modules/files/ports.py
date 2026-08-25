"""Technology-independent storage boundary for private attachment objects."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol


class FileObjectStoreError(RuntimeError):
    """Sanitized object-store failure that never contains a key or signed URL."""


class FileObjectNotFoundError(FileObjectStoreError):
    """The expected private staging object does not exist."""


@dataclass(frozen=True, slots=True)
class PresignedPost:
    """Short-lived browser POST details restricted to one private object."""

    url: str = field(repr=False)
    fields: Mapping[str, str] = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("Presigned upload URL is invalid")
        fields = dict(self.fields)
        if not fields or any(not key or not value for key, value in fields.items()):
            raise ValueError("Presigned upload fields are invalid")
        object.__setattr__(self, "fields", MappingProxyType(fields))


@dataclass(frozen=True, slots=True)
class StoredObjectStat:
    """Small trusted projection returned by a server-side object HEAD."""

    size: int
    etag: str
    content_type: str | None

    def __post_init__(self) -> None:
        if isinstance(self.size, bool) or self.size < 1:
            raise ValueError("Stored object size is invalid")
        if not self.etag or len(self.etag) > 128:
            raise ValueError("Stored object ETag is invalid")


class PrivateFileObjectStore(Protocol):
    """Keep all object keys server-owned and all buckets private."""

    async def presign_post(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        exact_size: int,
        expires_at: datetime,
    ) -> PresignedPost: ...

    async def stat(self, *, bucket: str, object_key: str) -> StoredObjectStat: ...

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes: ...

    async def put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None: ...

    async def remove(self, *, bucket: str, object_key: str) -> None: ...

    async def remove_prefix(self, *, bucket: str, object_prefix: str) -> None: ...

    async def presign_get(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_at: datetime,
    ) -> str: ...
