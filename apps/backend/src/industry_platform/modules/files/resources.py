"""FastAPI composition root for private attachment resources."""

from dataclasses import dataclass

from fastapi import Request
from minio import Minio

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.adapters.minio import MinioPrivateFileObjectStore
from industry_platform.modules.files.adapters.sqlalchemy import SqlAlchemyFileRepository
from industry_platform.modules.files.parser import BoundedAttachmentParser
from industry_platform.modules.files.service import FileApplicationService


@dataclass(frozen=True, slots=True)
class FileResources:
    service: FileApplicationService
    object_store: MinioPrivateFileObjectStore | None


def create_file_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
) -> FileResources:
    object_store = create_private_file_object_store(settings)
    return FileResources(
        service=FileApplicationService(
            repository=SqlAlchemyFileRepository(session_factory),
            object_store=object_store,
            parser=BoundedAttachmentParser(),
            bucket=settings.minio_bucket,
            presign_expiry_seconds=settings.minio_presign_expiry_seconds,
        ),
        object_store=object_store,
    )


def create_private_file_object_store(
    settings: Settings,
) -> MinioPrivateFileObjectStore | None:
    object_store: MinioPrivateFileObjectStore | None = None
    if settings.minio_configured:
        endpoint = settings.minio_endpoint
        access_key = settings.minio_access_key
        secret_key = settings.minio_secret_key
        if endpoint is None or access_key is None or secret_key is None:
            raise RuntimeError("Validated MinIO settings are incomplete")
        object_store = MinioPrivateFileObjectStore(
            client=Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key.get_secret_value(),
                secure=settings.minio_secure,
                region=settings.minio_region,
            ),
            public_endpoint=endpoint,
            secure=settings.minio_secure,
        )
    return object_store


def get_file_resources(request: Request) -> FileResources:
    resources = getattr(request.app.state, "file_resources", None)
    if not isinstance(resources, FileResources):
        raise RuntimeError("Application lifespan has not initialized file resources")
    return resources
