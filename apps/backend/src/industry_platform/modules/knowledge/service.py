"""Authorized orchestration for private Knowledge ingestion acceptance."""

import hashlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID, uuid4

from anyio import to_thread

from industry_platform.modules.files.domain import (
    AttachmentValidationCode,
    AttachmentValidationError,
)
from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    FileObjectStoreError,
    PrivateFileObjectStore,
)
from industry_platform.modules.files.service import (
    FileServiceUnavailableError,
    FileStorageConfigurationError,
    FileUploadExpiredError,
    FileValidationRejectedError,
)
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    JobDefinition,
    PreparedJobSubmission,
    fingerprint_job_request,
    hash_job_idempotency_key,
)
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_INGESTION_QUEUE_NAME,
    KNOWLEDGE_INGESTION_TASK_NAME,
    KNOWLEDGE_SCHEMA_VERSION,
    CompleteKnowledgeUpload,
    CreateDocumentVersion,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteKnowledgeBase,
    DocumentDetail,
    DocumentView,
    KnowledgeAcceptanceReceipt,
    KnowledgeBase,
    KnowledgeIngestionEvent,
    KnowledgeUploadTicket,
    PreparedDocumentVersion,
    PreparedKnowledgeAcceptance,
    StagingKnowledgeUpload,
    UpdateKnowledgeBase,
    VerifiedKnowledgeUpload,
    fingerprint_document_version_request,
    fingerprint_knowledge_request,
    hash_knowledge_idempotency_key,
)
from industry_platform.modules.knowledge.ports import (
    KnowledgeAcceptanceTransactionFactory,
    KnowledgeRepository,
)
from industry_platform.modules.knowledge.source_validation import validate_knowledge_source
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

type UtcClock = Callable[[], datetime]
type IdSource = Callable[[], UUID]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class KnowledgeApplicationService:
    repository: KnowledgeRepository
    transaction_factory: KnowledgeAcceptanceTransactionFactory
    object_store: PrivateFileObjectStore | None = field(repr=False)
    bucket: str | None
    presign_expiry_seconds: int = 600
    clock: UtcClock = utc_now
    id_source: IdSource = uuid4

    def _store(self) -> tuple[PrivateFileObjectStore, str]:
        if self.object_store is None or self.bucket is None:
            raise FileStorageConfigurationError
        return self.object_store, self.bucket

    async def create_knowledge_base(
        self, scope: WorkspaceScope, command: CreateKnowledgeBase
    ) -> KnowledgeBase:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        return await self.repository.create_knowledge_base(scope, command)

    async def list_knowledge_bases(
        self, scope: WorkspaceScope, *, limit: int = 100
    ) -> tuple[KnowledgeBase, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        return await self.repository.list_knowledge_bases(scope, limit=limit)

    async def get_knowledge_base(
        self, scope: WorkspaceScope, knowledge_base_id: UUID
    ) -> KnowledgeBase:
        self._require(scope, WorkspaceAction.VIEW)
        return await self.repository.get_knowledge_base(scope, knowledge_base_id)

    async def update_knowledge_base(
        self, scope: WorkspaceScope, command: UpdateKnowledgeBase
    ) -> KnowledgeBase:
        self._require(scope, WorkspaceAction.UPDATE_RESOURCE)
        return await self.repository.update_knowledge_base(scope, command)

    async def delete_knowledge_base(
        self, scope: WorkspaceScope, command: DeleteKnowledgeBase
    ) -> None:
        self._require(scope, WorkspaceAction.DELETE_RESOURCE)
        await self.repository.delete_empty_knowledge_base(scope, command)

    async def create_upload(
        self, scope: WorkspaceScope, command: CreateKnowledgeUpload
    ) -> KnowledgeUploadTicket:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        store, bucket = self._store()
        now = self.clock()
        expires_at = now + timedelta(seconds=self.presign_expiry_seconds)
        file_id = self.id_source()
        staging_key = f"staging/{scope.workspace_id}/knowledge/{file_id}/{self.id_source()}"
        upload = StagingKnowledgeUpload(
            file_id=file_id,
            workspace_id=scope.workspace_id,
            created_by_user_id=scope.user_id,
            knowledge_base_id=command.knowledge_base_id,
            original_name=command.original_name,
            declared_media_type=command.declared_media_type,
            bucket=bucket,
            staging_key=staging_key,
            expected_size=command.expected_size,
            expected_sha256=command.expected_sha256,
            expires_at=expires_at,
            created_at=now,
        )
        await self.repository.create_staging_upload(upload)
        try:
            signed = await store.presign_post(
                bucket=bucket,
                object_key=staging_key,
                content_type=command.declared_media_type.value,
                exact_size=command.expected_size,
                expires_at=expires_at,
            )
        except FileObjectStoreError:
            raise FileServiceUnavailableError from None
        return KnowledgeUploadTicket(
            file_id=file_id,
            original_name=command.original_name,
            declared_media_type=command.declared_media_type,
            expected_size=command.expected_size,
            method="POST",
            url=signed.url,
            fields=dict(signed.fields),
            expires_at=signed.expires_at,
        )

    async def complete_upload(
        self, scope: WorkspaceScope, command: CompleteKnowledgeUpload
    ) -> KnowledgeAcceptanceReceipt:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        store, _bucket = self._store()
        key_hash = hash_knowledge_idempotency_key(command.idempotency_key)
        request_hash = fingerprint_knowledge_request(
            knowledge_base_id=command.knowledge_base_id,
            file_id=command.file_id,
            title=command.title,
        )
        existing = await self.repository.existing_receipt(
            workspace_id=scope.workspace_id,
            knowledge_base_id=command.knowledge_base_id,
            file_id=command.file_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_hash,
        )
        if existing is not None:
            return existing

        try:
            claim = await self.repository.claim_upload(
                workspace_id=scope.workspace_id,
                knowledge_base_id=command.knowledge_base_id,
                file_id=command.file_id,
                claimed_at=self.clock(),
                stale_before=self.clock() - timedelta(minutes=5),
            )
        except FileUploadExpiredError as error:
            await self._remove_best_effort(store, bucket=error.bucket, key=error.object_key)
            raise

        try:
            stat = await store.stat(bucket=claim.bucket, object_key=claim.staging_key)
            if stat.size != claim.expected_size:
                raise AttachmentValidationError(AttachmentValidationCode.SIZE_MISMATCH)
            if stat.content_type != claim.declared_media_type.value:
                raise AttachmentValidationError(AttachmentValidationCode.UPLOAD_METADATA_MISMATCH)
            content = await store.read_bounded(
                bucket=claim.bucket,
                object_key=claim.staging_key,
                maximum_bytes=claim.expected_size,
            )
            source_sha256 = hashlib.sha256(content).hexdigest()
            await to_thread.run_sync(
                partial(
                    validate_knowledge_source,
                    claim,
                    content,
                    actual_sha256=source_sha256,
                )
            )
            final_key = f"ready/{scope.workspace_id}/knowledge/{claim.file_id}/{source_sha256}"
            await store.put_private(
                bucket=claim.bucket,
                object_key=final_key,
                content_type=claim.declared_media_type.value,
                content=content,
            )
            now = self.clock()
            version_id = self.id_source()
            definition = JobDefinition(
                scope=ExecutionScope(workspace_id=scope.workspace_id),
                task_name=KNOWLEDGE_INGESTION_TASK_NAME,
                queue_name=KNOWLEDGE_INGESTION_QUEUE_NAME,
                payload={
                    "document_version_id": str(version_id),
                    "file_id": str(claim.file_id),
                    "schema_version": KNOWLEDGE_SCHEMA_VERSION,
                },
                available_at=now,
                max_attempts=5,
                idempotency_key=command.idempotency_key,
                soft_time_limit_seconds=1_500,
                hard_time_limit_seconds=1_800,
            )
            prepared = PreparedKnowledgeAcceptance(
                document_id=self.id_source(),
                version_id=version_id,
                knowledge_base_id=command.knowledge_base_id,
                workspace_id=scope.workspace_id,
                created_by_user_id=scope.user_id,
                title=command.title,
                idempotency_key_hash=key_hash,
                request_fingerprint=request_hash,
                upload=VerifiedKnowledgeUpload(
                    claim=claim,
                    final_key=final_key,
                    source_etag=stat.etag,
                    media_type=claim.declared_media_type,
                    kind=claim.declared_media_type.kind,
                    actual_size=len(content),
                    sha256=source_sha256,
                ),
                job=PreparedJobSubmission(
                    job_id=self.id_source(),
                    outbox_event_id=self.id_source(),
                    scope=definition.scope,
                    task_name=definition.task_name,
                    queue_name=definition.queue_name,
                    payload=definition.payload,
                    available_at=definition.available_at,
                    max_attempts=definition.max_attempts,
                    priority=definition.priority,
                    soft_time_limit_seconds=definition.soft_time_limit_seconds,
                    hard_time_limit_seconds=definition.hard_time_limit_seconds,
                    trace_id=command.trace_id,
                    idempotency_key_hash=hash_job_idempotency_key(command.idempotency_key),
                    request_fingerprint=fingerprint_job_request(definition),
                    submitted_at=now,
                ),
                accepted_at=now,
            )
            async with self.transaction_factory() as writer:
                receipt = await writer.submit(prepared)
            await self._remove_best_effort(store, bucket=claim.bucket, key=claim.staging_key)
            return receipt
        except AttachmentValidationError as error:
            await self.repository.reject_upload(claim, code=error.code.value)
            await self._remove_best_effort(store, bucket=claim.bucket, key=claim.staging_key)
            raise FileValidationRejectedError(error.code) from None
        except FileObjectNotFoundError:
            await self.repository.reject_upload(claim, code="upload_missing")
            raise FileValidationRejectedError(AttachmentValidationCode.EMPTY_FILE) from None
        except FileObjectStoreError:
            raise FileServiceUnavailableError from None

    async def list_documents(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, limit: int = 100
    ) -> tuple[DocumentView, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        return await self.repository.list_documents(
            scope, knowledge_base_id=knowledge_base_id, limit=limit
        )

    async def create_document_version(
        self,
        scope: WorkspaceScope,
        command: CreateDocumentVersion,
    ) -> KnowledgeAcceptanceReceipt:
        self._require(scope, WorkspaceAction.CREATE_RESOURCE)
        detail = await self.repository.get_document(
            scope,
            knowledge_base_id=command.knowledge_base_id,
            document_id=command.document_id,
        )
        latest_version = detail.versions[0]
        latest_source = detail.sources[0]
        key_hash = hash_knowledge_idempotency_key(command.idempotency_key)
        request_hash = fingerprint_document_version_request(
            knowledge_base_id=command.knowledge_base_id,
            document_id=command.document_id,
            file_id=latest_source.file_id,
        )
        existing = await self.repository.existing_receipt(
            workspace_id=scope.workspace_id,
            knowledge_base_id=command.knowledge_base_id,
            file_id=latest_source.file_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_hash,
            allow_file_reuse=True,
        )
        if existing is not None:
            return existing

        now = self.clock()
        version_id = self.id_source()
        definition = JobDefinition(
            scope=ExecutionScope(workspace_id=scope.workspace_id),
            task_name=KNOWLEDGE_INGESTION_TASK_NAME,
            queue_name=KNOWLEDGE_INGESTION_QUEUE_NAME,
            payload={
                "document_version_id": str(version_id),
                "file_id": str(latest_source.file_id),
                "schema_version": KNOWLEDGE_SCHEMA_VERSION,
            },
            available_at=now,
            max_attempts=5,
            idempotency_key=command.idempotency_key,
            soft_time_limit_seconds=1_500,
            hard_time_limit_seconds=1_800,
        )
        prepared = PreparedDocumentVersion(
            version_id=version_id,
            document_id=command.document_id,
            knowledge_base_id=command.knowledge_base_id,
            workspace_id=scope.workspace_id,
            created_by_user_id=scope.user_id,
            file_id=latest_source.file_id,
            expected_document_revision=command.expected_revision,
            expected_latest_version_id=latest_version.id,
            expected_latest_version_number=latest_version.version,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_hash,
            job=PreparedJobSubmission(
                job_id=self.id_source(),
                outbox_event_id=self.id_source(),
                scope=definition.scope,
                task_name=definition.task_name,
                queue_name=definition.queue_name,
                payload=definition.payload,
                available_at=definition.available_at,
                max_attempts=definition.max_attempts,
                priority=definition.priority,
                soft_time_limit_seconds=definition.soft_time_limit_seconds,
                hard_time_limit_seconds=definition.hard_time_limit_seconds,
                trace_id=command.trace_id,
                idempotency_key_hash=hash_job_idempotency_key(command.idempotency_key),
                request_fingerprint=fingerprint_job_request(definition),
                submitted_at=now,
            ),
            created_at=now,
        )
        async with self.transaction_factory() as writer:
            return await writer.submit_document_version(prepared)

    async def get_document(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDetail:
        self._require(scope, WorkspaceAction.VIEW)
        detail = await self.repository.get_document(
            scope, knowledge_base_id=knowledge_base_id, document_id=document_id
        )
        if not detail.assets:
            return detail
        store, _bucket = self._store()
        expires_at = self.clock() + timedelta(seconds=self.presign_expiry_seconds)
        signed_assets = []
        for asset in detail.assets:
            try:
                preview_url = await store.presign_get(
                    bucket=asset.preview_bucket,
                    object_key=asset.preview_object_key,
                    expires_at=expires_at,
                )
            except FileObjectStoreError:
                raise FileServiceUnavailableError from None
            signed_assets.append(replace(asset, preview_url=preview_url))
        return replace(detail, assets=tuple(signed_assets))

    async def list_ingestion_events(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> tuple[KnowledgeIngestionEvent, ...]:
        self._require(scope, WorkspaceAction.VIEW)
        return await self.repository.list_ingestion_events(
            scope,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            version_id=version_id,
        )

    @staticmethod
    def _require(scope: WorkspaceScope, action: WorkspaceAction) -> None:
        if not scope_allows(scope, action):
            raise WorkspaceAccessDeniedError

    @staticmethod
    async def _remove_best_effort(store: PrivateFileObjectStore, *, bucket: str, key: str) -> None:
        with suppress(FileObjectStoreError):
            await store.remove(bucket=bucket, object_key=key)
