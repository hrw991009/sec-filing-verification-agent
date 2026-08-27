"""Application and persistence boundaries for Knowledge acceptance."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from industry_platform.modules.knowledge.domain import (
    ActivateDocumentVersion,
    CancelDocumentVersion,
    ClaimedKnowledgeUpload,
    CompleteKnowledgeUpload,
    CreateDocumentVersion,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteDocument,
    DeleteKnowledgeBase,
    Document,
    DocumentDetail,
    DocumentVersion,
    DocumentView,
    ImportKnowledgeTextSource,
    KnowledgeAcceptanceReceipt,
    KnowledgeBase,
    KnowledgeDeletionReceipt,
    KnowledgeIngestionEvent,
    KnowledgeUploadTicket,
    PreparedDocumentDeletion,
    PreparedDocumentVersion,
    PreparedKnowledgeAcceptance,
    StagingKnowledgeUpload,
    UpdateKnowledgeBase,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class KnowledgeRepository(Protocol):
    async def create_knowledge_base(
        self, scope: WorkspaceScope, command: CreateKnowledgeBase
    ) -> KnowledgeBase: ...

    async def list_knowledge_bases(
        self, scope: WorkspaceScope, *, limit: int
    ) -> tuple[KnowledgeBase, ...]: ...

    async def get_knowledge_base(
        self, scope: WorkspaceScope, knowledge_base_id: UUID
    ) -> KnowledgeBase: ...

    async def update_knowledge_base(
        self, scope: WorkspaceScope, command: UpdateKnowledgeBase
    ) -> KnowledgeBase: ...

    async def delete_empty_knowledge_base(
        self, scope: WorkspaceScope, command: DeleteKnowledgeBase
    ) -> None: ...

    async def create_staging_upload(self, upload: StagingKnowledgeUpload) -> None: ...

    async def ensure_internal_staging_upload(self, upload: StagingKnowledgeUpload) -> None: ...

    async def existing_receipt(
        self,
        *,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        file_id: UUID,
        idempotency_key_hash: bytes,
        request_fingerprint: bytes,
        allow_file_reuse: bool = False,
    ) -> KnowledgeAcceptanceReceipt | None: ...

    async def claim_upload(
        self,
        *,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        file_id: UUID,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> ClaimedKnowledgeUpload: ...

    async def reject_upload(self, claim: ClaimedKnowledgeUpload, *, code: str) -> None: ...

    async def list_documents(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, limit: int
    ) -> tuple[DocumentView, ...]: ...

    async def get_document(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDetail: ...

    async def list_ingestion_events(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> tuple[KnowledgeIngestionEvent, ...]: ...

    async def request_document_deletion(
        self,
        scope: WorkspaceScope,
        prepared: PreparedDocumentDeletion,
    ) -> KnowledgeDeletionReceipt: ...

    async def activate_document_version(
        self,
        scope: WorkspaceScope,
        command: ActivateDocumentVersion,
    ) -> Document: ...

    async def cancel_document_version(
        self,
        scope: WorkspaceScope,
        command: CancelDocumentVersion,
    ) -> DocumentVersion: ...

    async def list_deletion_events(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> tuple[KnowledgeIngestionEvent, ...]: ...


class KnowledgeAcceptanceWriter(Protocol):
    async def submit(self, prepared: PreparedKnowledgeAcceptance) -> KnowledgeAcceptanceReceipt: ...

    async def submit_document_version(
        self, prepared: PreparedDocumentVersion
    ) -> KnowledgeAcceptanceReceipt: ...


class KnowledgeAcceptanceTransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[KnowledgeAcceptanceWriter]: ...


class KnowledgeUseCase(Protocol):
    async def create_knowledge_base(
        self, scope: WorkspaceScope, command: CreateKnowledgeBase
    ) -> KnowledgeBase: ...

    async def list_knowledge_bases(
        self, scope: WorkspaceScope, *, limit: int = 100
    ) -> tuple[KnowledgeBase, ...]: ...

    async def get_knowledge_base(
        self, scope: WorkspaceScope, knowledge_base_id: UUID
    ) -> KnowledgeBase: ...

    async def update_knowledge_base(
        self, scope: WorkspaceScope, command: UpdateKnowledgeBase
    ) -> KnowledgeBase: ...

    async def delete_knowledge_base(
        self, scope: WorkspaceScope, command: DeleteKnowledgeBase
    ) -> None: ...

    async def create_upload(
        self, scope: WorkspaceScope, command: CreateKnowledgeUpload
    ) -> KnowledgeUploadTicket: ...

    async def complete_upload(
        self, scope: WorkspaceScope, command: CompleteKnowledgeUpload
    ) -> KnowledgeAcceptanceReceipt: ...

    async def import_text_source(
        self, scope: WorkspaceScope, command: ImportKnowledgeTextSource
    ) -> KnowledgeAcceptanceReceipt: ...

    async def create_document_version(
        self, scope: WorkspaceScope, command: CreateDocumentVersion
    ) -> KnowledgeAcceptanceReceipt: ...

    async def list_documents(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, limit: int = 100
    ) -> tuple[DocumentView, ...]: ...

    async def get_document(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDetail: ...

    async def delete_document(
        self, scope: WorkspaceScope, command: DeleteDocument
    ) -> KnowledgeDeletionReceipt: ...

    async def activate_document_version(
        self, scope: WorkspaceScope, command: ActivateDocumentVersion
    ) -> Document: ...
