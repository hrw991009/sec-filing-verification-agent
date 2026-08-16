"""HTTP contract tests for workspace-owned private chat attachments."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    AttachmentValidationCode,
    FileObjectStatus,
)
from industry_platform.modules.files.router import get_file_service
from industry_platform.modules.files.service import (
    CreateFileUpload,
    FileDownloadTicket,
    FileServiceUnavailableError,
    FileSnapshot,
    FileStateConflictError,
    FileStorageConfigurationError,
    FileUploadExpiredError,
    FileUploadTicket,
    FileValidationRejectedError,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
FILE_ID = UUID("55555555-5555-4555-8555-555555555555")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubFileService:
    failure: Exception | None = None
    calls: list[tuple[str, WorkspaceScope, object | None]] = field(default_factory=list)

    async def create_upload(
        self,
        scope: WorkspaceScope,
        command: CreateFileUpload,
    ) -> FileUploadTicket:
        self._record("presign", scope, command)
        return FileUploadTicket(
            file=file_snapshot(FileObjectStatus.STAGING),
            method="POST",
            url="http://127.0.0.1:19000/industry-platform-private",
            fields={"key": f"staging/{FILE_ID}", "Content-Type": "text/plain"},
            expires_at=NOW + timedelta(minutes=10),
        )

    async def complete_upload(self, scope: WorkspaceScope, file_id: UUID) -> FileSnapshot:
        self._record("complete", scope, file_id)
        return file_snapshot(FileObjectStatus.READY)

    async def get_file(self, scope: WorkspaceScope, file_id: UUID) -> FileSnapshot:
        self._record("get", scope, file_id)
        return file_snapshot(FileObjectStatus.READY)

    async def create_download(
        self,
        scope: WorkspaceScope,
        file_id: UUID,
    ) -> FileDownloadTicket:
        self._record("download", scope, file_id)
        return FileDownloadTicket(
            url="http://127.0.0.1:19000/private/signed-download",
            expires_at=NOW + timedelta(minutes=10),
        )

    async def delete_file(self, scope: WorkspaceScope, file_id: UUID) -> FileSnapshot:
        self._record("delete", scope, file_id)
        return file_snapshot(FileObjectStatus.DELETED)

    def _record(self, operation: str, scope: WorkspaceScope, value: object) -> None:
        self.calls.append((operation, scope, value))
        if self.failure is not None:
            raise self.failure


def file_snapshot(status: FileObjectStatus) -> FileSnapshot:
    ready = status is FileObjectStatus.READY
    return FileSnapshot(
        file_id=FILE_ID,
        workspace_id=WORKSPACE_ID,
        original_name="outlook.txt",
        declared_media_type="text/plain",
        detected_media_type=AttachmentMediaType.TEXT_PLAIN if ready else None,
        kind=AttachmentKind.TEXT if ready else None,
        status=status,
        expected_size=128,
        actual_size=128 if ready else None,
        upload_expires_at=NOW + timedelta(minutes=10),
        ready_at=NOW if ready else None,
    )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


@contextmanager
def file_client(settings: Settings, service: StubFileService) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_file_service] = lambda: service
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def assert_problem(response: HttpxResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == code


def test_file_routes_use_trusted_workspace_scope_and_safe_responses(
    test_settings: Settings,
) -> None:
    service = StubFileService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/files"
    with file_client(test_settings, service) as client:
        presign = client.post(
            f"{root}/presign",
            headers=bearer_header(),
            json={
                "original_name": "outlook.txt",
                "declared_media_type": "text/plain",
                "expected_size": 128,
                "expected_sha256": "a" * 64,
            },
        )
        completed = client.post(f"{root}/{FILE_ID}/complete", headers=bearer_header())
        detail = client.get(f"{root}/{FILE_ID}", headers=bearer_header())
        download = client.post(f"{root}/{FILE_ID}/download-url", headers=bearer_header())
        deleted = client.delete(f"{root}/{FILE_ID}", headers=bearer_header())

    assert presign.status_code == 201
    assert presign.headers["cache-control"] == "no-store"
    assert presign.json()["file"]["status"] == "staging"
    assert completed.json()["status"] == "ready"
    assert detail.json()["detected_media_type"] == "text/plain"
    assert "bucket" not in detail.json()
    assert "object_key" not in detail.json()
    assert download.json()["url"].endswith("signed-download")
    assert deleted.json()["status"] == "deleted"
    assert [call[0] for call in service.calls] == [
        "presign",
        "complete",
        "get",
        "download",
        "delete",
    ]
    assert all(call[1] == WorkspaceScope(WORKSPACE_ID, USER_ID, "member") for call in service.calls)


def test_auth_scope_and_request_validation_fail_before_the_file_service(
    test_settings: Settings,
) -> None:
    service = StubFileService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/files"
    with file_client(test_settings, service) as client:
        unauthenticated = client.get(f"{root}/{FILE_ID}")
        outside_scope = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/files/{FILE_ID}",
            headers=bearer_header(),
        )
        unsupported_shape = client.post(
            f"{root}/presign",
            headers=bearer_header(),
            json={
                "original_name": "outlook.pdf",
                "declared_media_type": "application/pdf",
                "expected_size": 128,
                "expected_sha256": "a" * 64,
                "trusted": True,
            },
        )

    assert_problem(unauthenticated, 401, "INVALID_AUTHENTICATED_SESSION")
    assert_problem(outside_scope, 403, "WORKSPACE_ACCESS_DENIED")
    assert_problem(unsupported_shape, 422, "REQUEST_VALIDATION_FAILED")
    assert service.calls == []


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (
            FileUploadExpiredError(bucket="private", object_key="staging/private"),
            409,
            "FILE_UPLOAD_EXPIRED",
        ),
        (FileStateConflictError(), 409, "FILE_STATE_CONFLICT"),
        (
            FileValidationRejectedError(AttachmentValidationCode.MAGIC_MISMATCH),
            422,
            "FILE_MAGIC_MISMATCH",
        ),
        (
            FileStorageConfigurationError(),
            503,
            "FILE_STORAGE_CONFIGURATION_REQUIRED",
        ),
        (FileServiceUnavailableError(sqlstate="08006"), 503, "FILE_SERVICE_UNAVAILABLE"),
    ],
)
def test_file_failures_use_stable_problem_contracts(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubFileService(failure=failure)
    with file_client(test_settings, service) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/files/{FILE_ID}",
            headers=bearer_header(),
        )

    assert_problem(response, status_code, code)
