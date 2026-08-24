"""HTTP contract tests for Knowledge acceptance."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.jobs.domain import JobStatus
from industry_platform.modules.knowledge.domain import (
    CompleteKnowledgeUpload,
    CreateDocumentVersion,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteKnowledgeBase,
    Document,
    DocumentAsset,
    DocumentAssetKind,
    DocumentChunk,
    DocumentDetail,
    DocumentPage,
    DocumentPageTextSource,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    DocumentView,
    IngestionCheckpoint,
    IngestionCheckpointStage,
    KnowledgeAcceptanceReceipt,
    KnowledgeBase,
    KnowledgeBaseStatus,
    KnowledgeIngestionEvent,
    KnowledgeSource,
    KnowledgeUploadTicket,
    UpdateKnowledgeBase,
)
from industry_platform.modules.knowledge.router import get_knowledge_service
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
SESSION_ID = UUID("44444444-4444-4444-8444-444444444444")
KNOWLEDGE_BASE_ID = UUID("55555555-5555-4555-8555-555555555555")
FILE_ID = UUID("66666666-6666-4666-8666-666666666666")
DOCUMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
VERSION_ID = UUID("88888888-8888-4888-8888-888888888888")
JOB_ID = UUID("99999999-9999-4999-8999-999999999999")
OUTBOX_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EVENT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PAGE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CHUNK_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
ASSET_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
CHECKPOINT_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
VERSION_2_ID = UUID("12121212-1212-4212-8212-121212121212")
JOB_2_ID = UUID("13131313-1313-4313-8313-131313131313")
OUTBOX_2_ID = UUID("14141414-1414-4414-8414-141414141414")
RAW_ACCESS_VALUE = "header.payload.signature"


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubKnowledgeService:
    calls: list[tuple[str, WorkspaceScope, object]] = field(default_factory=list)

    async def create_knowledge_base(
        self, scope: WorkspaceScope, command: CreateKnowledgeBase
    ) -> KnowledgeBase:
        self.calls.append(("create", scope, command))
        return knowledge_base()

    async def list_knowledge_bases(
        self, scope: WorkspaceScope, *, limit: int = 100
    ) -> tuple[KnowledgeBase, ...]:
        self.calls.append(("list", scope, limit))
        return (knowledge_base(),)

    async def get_knowledge_base(
        self, scope: WorkspaceScope, knowledge_base_id: UUID
    ) -> KnowledgeBase:
        self.calls.append(("get", scope, knowledge_base_id))
        return knowledge_base()

    async def update_knowledge_base(
        self, scope: WorkspaceScope, command: UpdateKnowledgeBase
    ) -> KnowledgeBase:
        self.calls.append(("update", scope, command))
        return knowledge_base(revision=2)

    async def delete_knowledge_base(
        self, scope: WorkspaceScope, command: DeleteKnowledgeBase
    ) -> None:
        self.calls.append(("delete", scope, command))

    async def create_upload(
        self, scope: WorkspaceScope, command: CreateKnowledgeUpload
    ) -> KnowledgeUploadTicket:
        self.calls.append(("presign", scope, command))
        return KnowledgeUploadTicket(
            file_id=FILE_ID,
            original_name="market.pdf",
            declared_media_type=AttachmentMediaType.APPLICATION_PDF,
            expected_size=128,
            method="POST",
            url="http://127.0.0.1:19000/private",
            fields={"key": "staging/private", "Content-Type": "application/pdf"},
            expires_at=NOW + timedelta(minutes=10),
        )

    async def complete_upload(
        self, scope: WorkspaceScope, command: CompleteKnowledgeUpload
    ) -> KnowledgeAcceptanceReceipt:
        self.calls.append(("complete", scope, command))
        return receipt()

    async def create_document_version(
        self, scope: WorkspaceScope, command: CreateDocumentVersion
    ) -> KnowledgeAcceptanceReceipt:
        self.calls.append(("version", scope, command))
        view = document_view(status=DocumentVersionStatus.PARSED)
        version = replace(
            view.latest_version,
            id=VERSION_2_ID,
            ingestion_job_id=JOB_2_ID,
            version=2,
            status=DocumentVersionStatus.QUEUED,
            revision=1,
            processing_started_at=None,
        )
        return KnowledgeAcceptanceReceipt(
            document=replace(view.document, latest_version_number=2, revision=2),
            version=version,
            source=view.source,
            job_id=JOB_2_ID,
            job_status=JobStatus.PENDING,
            outbox_event_id=OUTBOX_2_ID,
            created=True,
        )

    async def list_documents(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, limit: int = 100
    ) -> tuple[DocumentView, ...]:
        self.calls.append(("documents", scope, (knowledge_base_id, limit)))
        return (document_view(),)

    async def get_document(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDetail:
        self.calls.append(("document", scope, (knowledge_base_id, document_id)))
        view = document_view(status=DocumentVersionStatus.PARSED)
        return DocumentDetail(
            document=view.document,
            versions=(view.latest_version,),
            sources=(view.source,),
            pages=(
                DocumentPage(
                    id=PAGE_ID,
                    document_version_id=VERSION_ID,
                    page_number=1,
                    width_points=612.0,
                    height_points=792.0,
                    text="North capacity reached 18 units.",
                    text_source=DocumentPageTextSource.DIGITAL,
                    bbox=(0.0, 0.0, 612.0, 792.0),
                    title_path=("Capacity",),
                    content_hash="c" * 64,
                ),
            ),
            chunks=(
                DocumentChunk(
                    id=CHUNK_ID,
                    document_version_id=VERSION_ID,
                    ordinal=1,
                    page_number=1,
                    text="North capacity reached 18 units.",
                    token_count=6,
                    bbox=(0.0, 0.0, 612.0, 792.0),
                    title_path=("Capacity",),
                    content_hash="d" * 64,
                    asset_ids=(ASSET_ID,),
                ),
            ),
            assets=(
                DocumentAsset(
                    id=ASSET_ID,
                    document_version_id=VERSION_ID,
                    ordinal=1,
                    page_number=1,
                    kind=DocumentAssetKind.TABLE,
                    bbox=(60.0, 180.0, 540.0, 348.0),
                    title_path=("Capacity",),
                    content_hash="e" * 64,
                    preview_sha256="f" * 64,
                    preview_mime_type="image/png",
                    html="<table><tbody><tr><td>North</td><td>18</td></tr></tbody></table>",
                    preview_bucket="private",
                    preview_object_key="derived/private/table.png",
                    preview_url="http://127.0.0.1:19000/private-table?signature=test",
                ),
            ),
            ingestion_checkpoints=(
                IngestionCheckpoint(
                    id=CHECKPOINT_ID,
                    document_version_id=VERSION_ID,
                    ingestion_job_id=JOB_ID,
                    stage=IngestionCheckpointStage.CHUNKING,
                    stage_sequence=4,
                    fencing_token=1,
                    attempt_count=1,
                    input_hash="1" * 64,
                    output_hash="2" * 64,
                    stats={"chunk_count": 1},
                    completed_at=NOW,
                ),
            ),
        )

    async def list_ingestion_events(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> tuple[KnowledgeIngestionEvent, ...]:
        self.calls.append(("events", scope, (knowledge_base_id, document_id, version_id)))
        return (
            KnowledgeIngestionEvent(
                id=EVENT_ID,
                event_type="created",
                generation=0,
                event_sequence=0,
                occurred_at=NOW,
            ),
        )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def knowledge_base(*, revision: int = 1) -> KnowledgeBase:
    return KnowledgeBase(
        id=KNOWLEDGE_BASE_ID,
        workspace_id=WORKSPACE_ID,
        created_by_user_id=USER_ID,
        name="市场资料",
        description="私有行业材料",
        status=KnowledgeBaseStatus.ACTIVE,
        document_count=1,
        revision=revision,
        created_at=NOW,
        updated_at=NOW,
    )


def document_view(*, status: DocumentVersionStatus = DocumentVersionStatus.QUEUED) -> DocumentView:
    document = Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        created_by_user_id=USER_ID,
        title="市场报告",
        status=DocumentStatus.ACTIVE,
        active_version_id=None,
        latest_version_number=1,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    version = DocumentVersion(
        id=VERSION_ID,
        document_id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        file_id=FILE_ID,
        ingestion_job_id=JOB_ID,
        version=1,
        status=status,
        revision=1,
        error_code=None,
        uploaded_at=NOW,
        queued_at=NOW,
        processing_started_at=None,
        ready_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    source = KnowledgeSource(
        file_id=FILE_ID,
        original_name="market.pdf",
        declared_media_type=AttachmentMediaType.APPLICATION_PDF,
        expected_size=128,
        actual_size=128,
    )
    return DocumentView(document=document, latest_version=version, source=source)


def receipt() -> KnowledgeAcceptanceReceipt:
    view = document_view()
    return KnowledgeAcceptanceReceipt(
        document=view.document,
        version=view.latest_version,
        source=view.source,
        job_id=JOB_ID,
        job_status=JobStatus.PENDING,
        outbox_event_id=OUTBOX_ID,
        created=True,
    )


@contextmanager
def knowledge_client(settings: Settings, service: StubKnowledgeService) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_knowledge_service] = lambda: service
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def test_crud_upload_and_refresh_contracts_use_trusted_workspace_scope(
    test_settings: Settings,
) -> None:
    service = StubKnowledgeService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases"
    with knowledge_client(test_settings, service) as client:
        created = client.post(
            root,
            headers=auth(),
            json={"name": "市场资料", "description": "私有行业材料"},
        )
        listed = client.get(root, headers=auth())
        updated = client.patch(
            f"{root}/{KNOWLEDGE_BASE_ID}",
            headers={**auth(), "If-Match": '"1"'},
            json={"name": "市场资料", "description": "私有行业材料"},
        )
        upload = client.post(
            f"{root}/{KNOWLEDGE_BASE_ID}/uploads/presign",
            headers=auth(),
            json={
                "original_name": "market.pdf",
                "declared_media_type": "application/pdf",
                "expected_size": 128,
                "expected_sha256": "a" * 64,
            },
        )
        accepted = client.post(
            f"{root}/{KNOWLEDGE_BASE_ID}/uploads/{FILE_ID}/complete",
            headers={**auth(), "Idempotency-Key": "browser-upload-1"},
            json={"title": "市场报告"},
        )
        documents = client.get(f"{root}/{KNOWLEDGE_BASE_ID}/documents", headers=auth())
        detail = client.get(f"{root}/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}", headers=auth())
        next_version = client.post(
            f"{root}/{KNOWLEDGE_BASE_ID}/documents/{DOCUMENT_ID}/versions",
            headers={
                **auth(),
                "Idempotency-Key": "parser-upgrade-1",
                "If-Match": '"1"',
            },
        )
        events = client.get(accepted.json()["job"]["events_url"], headers=auth())
        deleted = client.delete(
            f"{root}/{KNOWLEDGE_BASE_ID}",
            headers={**auth(), "If-Match": '"1"'},
        )

    assert created.status_code == 201
    assert created.headers["etag"] == '"1"'
    assert listed.json()["knowledge_bases"][0]["document_count"] == 1
    assert updated.headers["etag"] == '"2"'
    assert upload.status_code == 201
    assert upload.json()["file"]["status"] == "uploaded"
    assert accepted.status_code == 202
    assert accepted.json()["version"]["status"] == "queued"
    assert accepted.json()["document"]["active_version_id"] is None
    assert documents.json()["documents"][0]["latest_version"]["status"] == "queued"
    assert detail.status_code == 200
    assert detail.json()["document"]["latest_version"]["status"] == "parsed"
    assert detail.json()["pages"][0]["text_source"] == "digital"
    assert detail.json()["chunks"][0]["asset_ids"] == [str(ASSET_ID)]
    assert detail.json()["assets"][0]["preview_url"].endswith("signature=test")
    assert detail.json()["ingestion_checkpoints"][0]["stage"] == "chunking"
    assert next_version.status_code == 202
    assert next_version.json()["version"]["id"] == str(VERSION_2_ID)
    assert next_version.json()["version"]["version"] == 2
    assert events.json()["events"][0]["event_type"] == "created"
    assert deleted.status_code == 204
    expected_scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert all(call[1] == expected_scope for call in service.calls)
    complete_command = next(call[2] for call in service.calls if call[0] == "complete")
    assert isinstance(complete_command, CompleteKnowledgeUpload)
    assert "browser-upload-1" not in repr(complete_command)
    version_command = next(call[2] for call in service.calls if call[0] == "version")
    assert isinstance(version_command, CreateDocumentVersion)
    assert "parser-upgrade-1" not in repr(version_command)


def test_cross_workspace_and_invalid_media_fail_before_service(test_settings: Settings) -> None:
    service = StubKnowledgeService()
    with knowledge_client(test_settings, service) as client:
        outside = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/knowledge-bases", headers=auth()
        )
        invalid = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases/{KNOWLEDGE_BASE_ID}/uploads/presign",
            headers=auth(),
            json={
                "original_name": "image.png",
                "declared_media_type": "image/png",
                "expected_size": 128,
                "expected_sha256": "a" * 64,
            },
        )

    assert outside.status_code == 403
    assert outside.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert invalid.status_code == 422
    assert service.calls == []


def test_invalid_text_fields_fail_as_request_validation(test_settings: Settings) -> None:
    service = StubKnowledgeService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-bases"
    with knowledge_client(test_settings, service) as client:
        empty_description = client.post(
            root,
            headers=auth(),
            json={"name": "市场资料", "description": "   "},
        )
        control_character = client.post(
            root,
            headers=auth(),
            json={"name": "市场\t资料", "description": None},
        )
        invalid_title = client.post(
            f"{root}/{KNOWLEDGE_BASE_ID}/uploads/{FILE_ID}/complete",
            headers={**auth(), "Idempotency-Key": "invalid-title"},
            json={"title": "市场\t报告"},
        )

    assert empty_description.status_code == 422
    assert control_character.status_code == 422
    assert invalid_title.status_code == 422
    assert service.calls == []
