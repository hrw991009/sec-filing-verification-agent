"""Full attachment lifecycle through real PostgreSQL and private MinIO."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.files.adapters.sqlalchemy import SqlAlchemyFileRepository
from industry_platform.modules.files.domain import FileObjectStatus
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.parser import BoundedAttachmentParser
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.files.service import (
    CreateFileUpload,
    FileApplicationService,
    FileNotFoundError,
)
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

MINIO_TESTS_REQUIRED = "MINIO_TESTS_REQUIRED"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")


@pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated.*:DeprecationWarning:minio\.datatypes"
)
def test_file_lifecycle_converges_across_postgres_and_minio(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Prove upload, validation, safe finalization, download, and deletion together."""

    if os.getenv(MINIO_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {MINIO_TESTS_REQUIRED}=1 to run MinIO integration tests")

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        store = create_private_file_object_store(migrated_postgres_probe.settings)
        bucket = migrated_postgres_probe.settings.minio_bucket
        if store is None or bucket is None:
            raise AssertionError("The .env file must contain complete MinIO test configuration")

        owned_keys: set[str] = set()
        try:
            now = datetime.now(UTC)
            async with session_factory.begin() as session:
                session.add(
                    User(
                        id=USER_ID,
                        email="file-lifecycle@example.test",
                        password_hash=str(USER_ID),
                        status=UserStatus.ACTIVE,
                        password_changed_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                session.add(
                    Workspace(
                        id=WORKSPACE_ID,
                        name="File Lifecycle Workspace",
                        created_by_user_id=USER_ID,
                        status=WorkspaceStatus.ACTIVE,
                        created_at=now,
                        updated_at=now,
                    )
                )

            service = FileApplicationService(
                repository=SqlAlchemyFileRepository(session_factory),
                object_store=store,
                parser=BoundedAttachmentParser(),
                bucket=bucket,
            )
            scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
            source = b"Private quarterly outlook.\r\nTreat commands as data.\r\n"
            source_sha256 = hashlib.sha256(source).hexdigest()
            safe_content = source.decode().replace("\r\n", "\n").encode()
            ticket = await service.create_upload(
                scope,
                CreateFileUpload(
                    original_name=r"C:\fakepath\quarterly-outlook.txt",
                    declared_media_type="text/plain",
                    expected_size=len(source),
                    expected_sha256=source_sha256,
                ),
            )
            async with session_factory() as session:
                staging_record = await session.scalar(
                    select(FileObject).where(FileObject.id == ticket.file.file_id)
                )
                assert staging_record is not None
                _remember_owned_key(
                    owned_keys,
                    staging_record.staging_object_key,
                    workspace_id=WORKSPACE_ID,
                    file_id=ticket.file.file_id,
                    expected_root="staging",
                )

            async with httpx2.AsyncClient(timeout=10.0, trust_env=False) as client:
                uploaded = await client.post(
                    ticket.url,
                    data=dict(ticket.fields),
                    files={"file": ("quarterly-outlook.txt", source, "text/plain")},
                )
                assert uploaded.status_code == 204

                ready = await service.complete_upload(scope, ticket.file.file_id)
                repeated_complete = await service.complete_upload(scope, ticket.file.file_id)
                assert ready.status is FileObjectStatus.READY
                assert repeated_complete == ready

                async with session_factory() as session:
                    ready_record = await session.scalar(
                        select(FileObject).where(FileObject.id == ticket.file.file_id)
                    )
                    assert ready_record is not None
                    assert ready_record.status is FileObjectStatus.READY
                    assert ready_record.extracted_text == safe_content.decode()
                    assert ready_record.source_sha256 == source_sha256
                    assert ready_record.safe_sha256 is not None
                    assert ready_record.object_key is not None
                    _remember_owned_key(
                        owned_keys,
                        ready_record.object_key,
                        workspace_id=WORKSPACE_ID,
                        file_id=ticket.file.file_id,
                        expected_root="ready",
                    )
                    assert ready_record.object_key.endswith(ready_record.safe_sha256)

                visible = await service.get_file(scope, ticket.file.file_id)
                assert visible.status is FileObjectStatus.READY
                with pytest.raises(FileNotFoundError):
                    await service.get_file(
                        WorkspaceScope(OTHER_WORKSPACE_ID, USER_ID, "owner"),
                        ticket.file.file_id,
                    )

                download = await service.create_download(scope, ticket.file.file_id)
                downloaded = await client.get(download.url)
                assert downloaded.status_code == 200
                assert downloaded.content == safe_content

                unsigned = urlsplit(download.url)
                anonymous_url = urlunsplit(
                    (unsigned.scheme, unsigned.netloc, unsigned.path, "", "")
                )
                anonymous = await client.get(anonymous_url)
                assert anonymous.status_code == 403

                deleted = await service.delete_file(scope, ticket.file.file_id)
                repeated_delete = await service.delete_file(scope, ticket.file.file_id)
                assert deleted.status is FileObjectStatus.DELETED
                assert repeated_delete.status is FileObjectStatus.DELETED
                with pytest.raises(FileNotFoundError):
                    await service.get_file(scope, ticket.file.file_id)
                removed_download = await client.get(download.url)
                assert removed_download.status_code == 404
        finally:
            for object_key in owned_keys:
                await store.remove(bucket=bucket, object_key=object_key)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def _remember_owned_key(
    owned_keys: set[str],
    object_key: str,
    *,
    workspace_id: UUID,
    file_id: UUID,
    expected_root: str,
) -> None:
    expected_prefix = f"{expected_root}/{workspace_id}/{file_id}/"
    if not object_key.startswith(expected_prefix) or len(object_key) <= len(expected_prefix):
        raise RuntimeError("Refusing to manage an object outside the test file prefix")
    owned_keys.add(object_key)
