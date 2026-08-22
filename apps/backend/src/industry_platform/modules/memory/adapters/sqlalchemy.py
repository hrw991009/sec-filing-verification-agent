"""Workspace- and user-scoped PostgreSQL persistence for Memory."""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.conversations.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageStatus,
    Turn,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import AuditLog, AuditOutcome
from industry_platform.modules.memory.domain import (
    CandidateCreationResult,
    ChangeMemoryStatus,
    CreateMemoryCandidate,
    DeleteMemory,
    Memory,
    MemoryCandidate,
    MemoryCandidateEditRequiredError,
    MemoryCandidateNotFoundError,
    MemoryCandidateStatus,
    MemoryConflictError,
    MemoryDetail,
    MemoryFeedback,
    MemoryIdempotencyConflictError,
    MemoryKind,
    MemoryNotFoundError,
    MemoryPersistenceError,
    MemoryPolicyAssessment,
    MemoryPolicyDecision,
    MemoryResolutionResult,
    MemoryRevision,
    MemoryRevisionValidity,
    MemoryScope,
    MemorySourceMessage,
    MemorySourceNotFoundError,
    MemoryStatus,
    MemoryWriteAction,
    RecordMemoryFeedback,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
    UpdateMemory,
    hash_idempotency_key,
    require_memory_content,
    utc_now,
)
from industry_platform.modules.memory.models import (
    MemoryCandidateRecord,
    MemoryCandidateSourceRecord,
    MemoryFeedbackRecord,
    MemoryRecord,
    MemoryRevisionRecord,
    MemoryRevisionSourceRecord,
    ThreadMemoryStateRecord,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

MEMORY_CANDIDATE_AUDIT_ACTION = "memory.candidate.created"
MEMORY_CANDIDATE_REJECTED_AUDIT_ACTION = "memory.candidate.rejected"
MEMORY_RESOLVED_AUDIT_ACTION = "memory.resolved"
MEMORY_UPDATED_AUDIT_ACTION = "memory.updated"
MEMORY_STATUS_AUDIT_ACTION = "memory.status_changed"
MEMORY_DELETED_AUDIT_ACTION = "memory.deleted"
MEMORY_FEEDBACK_AUDIT_ACTION = "memory.feedback.recorded"
MEMORY_RESOURCE_TYPE = "memory"
MEMORY_CANDIDATE_RESOURCE_TYPE = "memory_candidate"
MEMORY_WRITE_REASON = "user_selected_conversation_messages"
logger = logging.getLogger(__name__)


class SqlAlchemyMemoryRepository:
    """Keep candidate decisions and Memory revisions atomic in PostgreSQL."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def load_source_messages(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        message_ids: tuple[UUID, ...],
    ) -> tuple[MemorySourceMessage, ...]:
        try:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(Message)
                            .join(
                                Turn,
                                (Turn.id == Message.turn_id)
                                & (Turn.workspace_id == Message.workspace_id),
                            )
                            .join(
                                Conversation,
                                (Conversation.id == Turn.conversation_id)
                                & (Conversation.workspace_id == Turn.workspace_id),
                            )
                            .where(
                                Message.workspace_id == scope.workspace_id,
                                Message.id.in_(message_ids),
                                Message.status != MessageStatus.PARTIAL,
                                Turn.conversation_id == conversation_id,
                                Conversation.status == ConversationStatus.ACTIVE,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

        by_id = {row.id: row for row in rows}
        if len(by_id) != len(message_ids):
            raise MemorySourceNotFoundError
        return tuple(
            MemorySourceMessage(
                message_id=message_id,
                conversation_id=conversation_id,
                role=by_id[message_id].role.value,
                content_markdown=by_id[message_id].content_markdown,
            )
            for message_id in message_ids
        )

    async def create_candidate(
        self,
        scope: WorkspaceScope,
        command: CreateMemoryCandidate,
        *,
        sources: tuple[MemorySourceMessage, ...],
        suggested_content: str | None,
        assessment: MemoryPolicyAssessment,
        request_fingerprint: str,
    ) -> CandidateCreationResult:
        now = self._clock()
        key_hash = hash_idempotency_key(command.idempotency_key)
        try:
            async with self._session_factory() as session, session.begin():
                existing = await session.scalar(
                    select(MemoryCandidateRecord).where(
                        MemoryCandidateRecord.workspace_id == scope.workspace_id,
                        MemoryCandidateRecord.owner_user_id == scope.user_id,
                        MemoryCandidateRecord.idempotency_key_hash == key_hash,
                    )
                )
                if existing is not None:
                    if existing.request_fingerprint != request_fingerprint:
                        raise MemoryIdempotencyConflictError
                    return CandidateCreationResult(
                        candidate=await self._candidate_snapshot(session, existing),
                        created=False,
                    )

                rejected = assessment.decision is MemoryPolicyDecision.REJECTED
                candidate = MemoryCandidateRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    owner_user_id=scope.user_id,
                    conversation_id=command.conversation_id,
                    suggested_content=None if rejected else suggested_content,
                    suggested_expires_at=None,
                    scope=command.scope,
                    confidence=assessment.confidence,
                    write_reason=MEMORY_WRITE_REASON,
                    policy_decision=assessment.decision,
                    policy_reason=assessment.reason,
                    status=(
                        MemoryCandidateStatus.REJECTED
                        if rejected
                        else MemoryCandidateStatus.CANDIDATE
                    ),
                    revision=1,
                    idempotency_key_hash=key_hash,
                    request_fingerprint=request_fingerprint,
                    resolved_at=now if rejected else None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(candidate)
                session.add_all(
                    MemoryCandidateSourceRecord(
                        candidate_id=candidate.id,
                        message_id=source.message_id,
                        workspace_id=scope.workspace_id,
                        owner_user_id=scope.user_id,
                        ordinal=ordinal,
                        created_at=now,
                    )
                    for ordinal, source in enumerate(sources)
                )
                if not rejected and suggested_content is not None:
                    await self._upsert_thread_state(
                        session,
                        scope,
                        conversation_id=command.conversation_id,
                        source_message_ids=tuple(source.message_id for source in sources),
                        summary=suggested_content,
                        now=now,
                    )
                session.add(
                    AuditLog(
                        id=uuid4(),
                        workspace_id=scope.workspace_id,
                        actor_user_id=scope.user_id,
                        action=(
                            MEMORY_CANDIDATE_REJECTED_AUDIT_ACTION
                            if rejected
                            else MEMORY_CANDIDATE_AUDIT_ACTION
                        ),
                        resource_type=MEMORY_CANDIDATE_RESOURCE_TYPE,
                        resource_id=candidate.id,
                        outcome=AuditOutcome.DENIED if rejected else AuditOutcome.SUCCEEDED,
                        trace_id=command.trace_id,
                        sanitized_metadata={
                            "candidate_revision": 1,
                            "policy_decision": assessment.decision.value,
                            "policy_reason": assessment.reason.value,
                            "scope": command.scope.value,
                            "source_count": len(sources),
                        },
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                snapshot = await self._candidate_snapshot(session, candidate)
            return CandidateCreationResult(candidate=snapshot, created=True)
        except (MemoryIdempotencyConflictError, MemorySourceNotFoundError):
            raise
        except IntegrityError as error:
            logger.warning(
                "Memory candidate integrity conflict sqlstate=%s constraint=%s",
                safe_sqlstate(error) or "unknown",
                self._constraint_name(error) or "unknown",
            )
            raise MemoryConflictError from error
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def list_candidates(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID | None,
        limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        statement = select(MemoryCandidateRecord).where(
            MemoryCandidateRecord.workspace_id == scope.workspace_id,
            MemoryCandidateRecord.owner_user_id == scope.user_id,
        )
        if conversation_id is not None:
            statement = statement.where(MemoryCandidateRecord.conversation_id == conversation_id)
        statement = statement.order_by(
            MemoryCandidateRecord.created_at.desc(), MemoryCandidateRecord.id.desc()
        ).limit(limit)
        try:
            async with self._session_factory() as session:
                records = (await session.execute(statement)).scalars().all()
                return tuple(
                    [await self._candidate_snapshot(session, record) for record in records]
                )
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def get_candidate(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
    ) -> MemoryCandidate:
        try:
            async with self._session_factory() as session:
                candidate = await self._candidate_record(session, scope, candidate_id)
                return await self._candidate_snapshot(session, candidate)
        except MemoryCandidateNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def resolve_candidate(
        self,
        scope: WorkspaceScope,
        command: ResolveMemoryCandidate,
        *,
        resolution_fingerprint: str,
    ) -> MemoryResolutionResult:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                candidate = await self._candidate_record(
                    session, scope, command.candidate_id, lock=True
                )
                if candidate.status is MemoryCandidateStatus.CONFIRMED:
                    if (
                        candidate.resolution_fingerprint == resolution_fingerprint
                        and candidate.resolution_action is command.action
                        and candidate.resolved_memory_id is not None
                    ):
                        return MemoryResolutionResult(
                            detail=await self._memory_detail(
                                session, scope, candidate.resolved_memory_id
                            ),
                            action=command.action,
                            created=False,
                        )
                    raise MemoryConflictError
                if candidate.status is not MemoryCandidateStatus.CANDIDATE:
                    raise MemoryConflictError
                if candidate.revision != command.expected_candidate_revision:
                    raise MemoryConflictError
                if candidate.policy_decision is MemoryPolicyDecision.REJECTED:
                    raise MemoryConflictError

                normalized_content = require_memory_content(command.content)
                if (
                    candidate.policy_decision is MemoryPolicyDecision.REQUIRES_EDIT
                    and normalized_content == candidate.suggested_content
                ):
                    raise MemoryCandidateEditRequiredError

                source_ids = await self._candidate_source_ids(session, candidate.id)
                await self._require_candidate_sources_available(
                    session,
                    scope,
                    candidate=candidate,
                    source_ids=source_ids,
                )
                if command.action is MemoryWriteAction.CREATE:
                    memory, revision = self._new_memory(
                        scope,
                        candidate,
                        command,
                        now=now,
                    )
                    session.add_all((memory, revision))
                else:
                    if command.target_memory_id is None or command.expected_target_revision is None:
                        raise MemoryConflictError
                    memory = await self._memory_record(
                        session, scope, command.target_memory_id, lock=True
                    )
                    if (
                        memory.status is MemoryStatus.DELETED
                        or memory.revision != command.expected_target_revision
                    ):
                        raise MemoryConflictError
                    revision = MemoryRevisionRecord(
                        id=uuid4(),
                        memory_id=memory.id,
                        workspace_id=scope.workspace_id,
                        owner_user_id=scope.user_id,
                        version=memory.current_version + 1,
                        content=normalized_content,
                        scope=command.scope,
                        kind=command.kind,
                        write_action=command.action,
                        write_reason=candidate.write_reason,
                        policy_decision=candidate.policy_decision,
                        editor_user_id=scope.user_id,
                        validity=MemoryRevisionValidity.VALID,
                        expires_at=command.expires_at,
                        created_at=now,
                    )
                    session.add(revision)
                    memory.current_revision_id = revision.id
                    memory.current_version = revision.version
                    memory.revision += 1
                    memory.scope = command.scope
                    memory.kind = command.kind
                    memory.confidence = candidate.confidence
                    memory.expires_at = command.expires_at
                    memory.updated_at = now

                session.add_all(
                    MemoryRevisionSourceRecord(
                        revision_id=revision.id,
                        message_id=message_id,
                        memory_id=memory.id,
                        workspace_id=scope.workspace_id,
                        ordinal=ordinal,
                        created_at=now,
                    )
                    for ordinal, message_id in enumerate(source_ids)
                )
                candidate.status = MemoryCandidateStatus.CONFIRMED
                candidate.revision += 1
                candidate.resolution_action = command.action
                candidate.resolution_fingerprint = resolution_fingerprint
                candidate.resolved_memory_id = memory.id
                candidate.resolved_at = now
                candidate.updated_at = now
                session.add(
                    AuditLog(
                        id=uuid4(),
                        workspace_id=scope.workspace_id,
                        actor_user_id=scope.user_id,
                        action=MEMORY_RESOLVED_AUDIT_ACTION,
                        resource_type=MEMORY_RESOURCE_TYPE,
                        resource_id=memory.id,
                        outcome=AuditOutcome.SUCCEEDED,
                        trace_id=command.trace_id,
                        sanitized_metadata={
                            "action": command.action.value,
                            "candidate_id": str(candidate.id),
                            "memory_revision": revision.version,
                            "policy_decision": candidate.policy_decision.value,
                            "scope": command.scope.value,
                            "source_count": len(source_ids),
                        },
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                detail = await self._memory_detail(session, scope, memory.id)
            return MemoryResolutionResult(detail=detail, action=command.action, created=True)
        except (
            MemoryCandidateEditRequiredError,
            MemoryCandidateNotFoundError,
            MemoryConflictError,
            MemoryNotFoundError,
            MemorySourceNotFoundError,
        ):
            raise
        except IntegrityError as error:
            logger.warning(
                "Memory resolution integrity conflict sqlstate=%s constraint=%s",
                safe_sqlstate(error) or "unknown",
                self._constraint_name(error) or "unknown",
            )
            raise MemoryConflictError from error
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def reject_candidate(
        self,
        scope: WorkspaceScope,
        command: RejectMemoryCandidate,
    ) -> MemoryCandidate:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                candidate = await self._candidate_record(
                    session, scope, command.candidate_id, lock=True
                )
                if candidate.status is MemoryCandidateStatus.REJECTED:
                    return await self._candidate_snapshot(session, candidate)
                if (
                    candidate.status is not MemoryCandidateStatus.CANDIDATE
                    or candidate.revision != command.expected_candidate_revision
                ):
                    raise MemoryConflictError
                candidate.status = MemoryCandidateStatus.REJECTED
                candidate.revision += 1
                candidate.resolved_at = now
                candidate.updated_at = now
                source_count = len(await self._candidate_source_ids(session, candidate.id))
                session.add(
                    AuditLog(
                        id=uuid4(),
                        workspace_id=scope.workspace_id,
                        actor_user_id=scope.user_id,
                        action=MEMORY_CANDIDATE_REJECTED_AUDIT_ACTION,
                        resource_type=MEMORY_CANDIDATE_RESOURCE_TYPE,
                        resource_id=candidate.id,
                        outcome=AuditOutcome.SUCCEEDED,
                        trace_id=command.trace_id,
                        sanitized_metadata={
                            "candidate_revision": candidate.revision,
                            "reason": "user_rejected",
                            "source_count": source_count,
                        },
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                snapshot = await self._candidate_snapshot(session, candidate)
            return snapshot
        except (MemoryCandidateNotFoundError, MemoryConflictError):
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def list_memories(
        self,
        scope: WorkspaceScope,
        *,
        query: str | None,
        status: MemoryStatus | None,
        memory_scope: MemoryScope | None,
        kind: MemoryKind | None,
        limit: int,
    ) -> tuple[Memory, ...]:
        if status is MemoryStatus.DELETED:
            return ()
        now = self._clock()
        try:
            async with self._session_factory() as session:
                statement = (
                    select(MemoryRecord)
                    .join(
                        MemoryRevisionRecord,
                        and_(
                            MemoryRevisionRecord.id == MemoryRecord.current_revision_id,
                            MemoryRevisionRecord.memory_id == MemoryRecord.id,
                            MemoryRevisionRecord.workspace_id == MemoryRecord.workspace_id,
                        ),
                    )
                    .where(
                        MemoryRecord.workspace_id == scope.workspace_id,
                        or_(
                            MemoryRecord.owner_user_id == scope.user_id,
                            and_(
                                MemoryRecord.scope == MemoryScope.WORKSPACE,
                                MemoryRecord.status != MemoryStatus.DELETED,
                            ),
                        ),
                    )
                )
                statement = statement.where(MemoryRecord.status != MemoryStatus.DELETED)
                if status is MemoryStatus.EXPIRED:
                    statement = statement.where(
                        or_(
                            MemoryRecord.status == MemoryStatus.EXPIRED,
                            and_(
                                MemoryRecord.status == MemoryStatus.CONFIRMED,
                                MemoryRecord.expires_at.is_not(None),
                                MemoryRecord.expires_at <= now,
                            ),
                        )
                    )
                elif status is MemoryStatus.CONFIRMED:
                    statement = statement.where(
                        MemoryRecord.status == MemoryStatus.CONFIRMED,
                        or_(
                            MemoryRecord.expires_at.is_(None),
                            MemoryRecord.expires_at > now,
                        ),
                    )
                elif status is not None:
                    statement = statement.where(MemoryRecord.status == status)
                if query is not None:
                    statement = statement.where(
                        MemoryRevisionRecord.content.contains(query, autoescape=True)
                    )
                if memory_scope is not None:
                    statement = statement.where(MemoryRecord.scope == memory_scope)
                if kind is not None:
                    statement = statement.where(MemoryRecord.kind == kind)
                records = (
                    (
                        await session.execute(
                            statement.order_by(
                                MemoryRecord.updated_at.desc(), MemoryRecord.id.desc()
                            ).limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                return tuple(self._memory_snapshot(record, now=now) for record in records)
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def get_memory(self, scope: WorkspaceScope, memory_id: UUID) -> MemoryDetail:
        try:
            async with self._session_factory() as session:
                return await self._memory_detail(
                    session,
                    scope,
                    memory_id,
                    include_shared=True,
                )
        except MemoryNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def update_memory(
        self,
        scope: WorkspaceScope,
        command: UpdateMemory,
    ) -> MemoryDetail:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                memory = await self._memory_record(
                    session,
                    scope,
                    command.memory_id,
                    lock=True,
                )
                if memory.revision != command.expected_revision:
                    raise MemoryConflictError
                source_ids = await self._revision_source_ids(
                    session,
                    memory.current_revision_id,
                )
                revision = MemoryRevisionRecord(
                    id=uuid4(),
                    memory_id=memory.id,
                    workspace_id=scope.workspace_id,
                    owner_user_id=scope.user_id,
                    version=memory.current_version + 1,
                    content=command.content,
                    scope=command.scope,
                    kind=command.kind,
                    write_action=MemoryWriteAction.UPDATE,
                    write_reason="user_governance_update",
                    policy_decision=MemoryPolicyDecision.ALLOWED,
                    editor_user_id=scope.user_id,
                    validity=MemoryRevisionValidity.VALID,
                    expires_at=command.expires_at,
                    created_at=now,
                )
                session.add(revision)
                session.add_all(
                    MemoryRevisionSourceRecord(
                        revision_id=revision.id,
                        message_id=message_id,
                        memory_id=memory.id,
                        workspace_id=scope.workspace_id,
                        ordinal=ordinal,
                        created_at=now,
                    )
                    for ordinal, message_id in enumerate(source_ids)
                )
                memory.current_revision_id = revision.id
                memory.current_version = revision.version
                memory.revision += 1
                memory.scope = command.scope
                memory.kind = command.kind
                memory.status = MemoryStatus.CONFIRMED
                memory.expires_at = command.expires_at
                memory.updated_at = now
                self._audit(
                    session,
                    scope,
                    action=MEMORY_UPDATED_AUDIT_ACTION,
                    memory=memory,
                    trace_id=command.trace_id,
                    now=now,
                    metadata={"memory_revision": revision.version},
                )
                await session.flush()
                detail = await self._memory_detail(session, scope, memory.id)
            return detail
        except (MemoryConflictError, MemoryNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def change_status(
        self,
        scope: WorkspaceScope,
        command: ChangeMemoryStatus,
    ) -> MemoryDetail:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                memory = await self._memory_record(
                    session,
                    scope,
                    command.memory_id,
                    lock=True,
                )
                if memory.revision != command.expected_revision:
                    raise MemoryConflictError
                if (
                    command.status is MemoryStatus.CONFIRMED
                    and memory.expires_at is not None
                    and memory.expires_at <= now
                ):
                    raise MemoryConflictError
                if memory.status is not command.status:
                    memory.status = command.status
                    memory.revision += 1
                    memory.updated_at = now
                    self._audit(
                        session,
                        scope,
                        action=MEMORY_STATUS_AUDIT_ACTION,
                        memory=memory,
                        trace_id=command.trace_id,
                        now=now,
                        metadata={"status": command.status.value},
                    )
                await session.flush()
                detail = await self._memory_detail(session, scope, memory.id)
            return detail
        except (MemoryConflictError, MemoryNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def delete_memory(
        self,
        scope: WorkspaceScope,
        command: DeleteMemory,
    ) -> bool:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                memory = await self._memory_record(
                    session,
                    scope,
                    command.memory_id,
                    lock=True,
                    include_deleted=True,
                )
                if memory.status is MemoryStatus.DELETED:
                    return False
                if memory.revision != command.expected_revision:
                    raise MemoryConflictError
                memory.status = MemoryStatus.DELETED
                memory.deleted_at = now
                memory.revision += 1
                memory.updated_at = now
                await session.execute(
                    update(MemoryRevisionRecord)
                    .where(
                        MemoryRevisionRecord.memory_id == memory.id,
                        MemoryRevisionRecord.workspace_id == scope.workspace_id,
                    )
                    .values(
                        content="[deleted]",
                        validity=MemoryRevisionValidity.WITHDRAWN,
                    )
                )
                await session.execute(
                    delete(MemoryRevisionSourceRecord).where(
                        MemoryRevisionSourceRecord.memory_id == memory.id,
                        MemoryRevisionSourceRecord.workspace_id == scope.workspace_id,
                    )
                )
                await session.execute(
                    update(MemoryCandidateRecord)
                    .where(
                        MemoryCandidateRecord.resolved_memory_id == memory.id,
                        MemoryCandidateRecord.workspace_id == scope.workspace_id,
                    )
                    .values(suggested_content=None, updated_at=now)
                )
                self._audit(
                    session,
                    scope,
                    action=MEMORY_DELETED_AUDIT_ACTION,
                    memory=memory,
                    trace_id=command.trace_id,
                    now=now,
                    metadata={"deletion_residual": 0},
                )
            return True
        except (MemoryConflictError, MemoryNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def record_feedback(
        self,
        scope: WorkspaceScope,
        command: RecordMemoryFeedback,
    ) -> MemoryFeedback:
        now = self._clock()
        try:
            async with self._session_factory() as session, session.begin():
                memory = await self._memory_record(
                    session,
                    scope,
                    command.memory_id,
                    lock=True,
                    include_shared=True,
                )
                if (
                    memory.revision != command.expected_revision
                    or memory.current_revision_id != command.memory_revision_id
                ):
                    raise MemoryConflictError
                feedback = await session.scalar(
                    select(MemoryFeedbackRecord)
                    .where(
                        MemoryFeedbackRecord.memory_id == memory.id,
                        MemoryFeedbackRecord.memory_revision_id == command.memory_revision_id,
                        MemoryFeedbackRecord.actor_user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if feedback is None:
                    feedback = MemoryFeedbackRecord(
                        id=uuid4(),
                        workspace_id=scope.workspace_id,
                        memory_id=memory.id,
                        memory_revision_id=command.memory_revision_id,
                        actor_user_id=scope.user_id,
                        value=command.value,
                        reason=command.reason,
                        schema_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(feedback)
                else:
                    feedback.value = command.value
                    feedback.reason = command.reason
                    feedback.updated_at = now
                self._audit(
                    session,
                    scope,
                    action=MEMORY_FEEDBACK_AUDIT_ACTION,
                    memory=memory,
                    trace_id=command.trace_id,
                    now=now,
                    metadata={"feedback": command.value.value},
                )
                await session.flush()
                snapshot = self._feedback_snapshot(feedback)
            return snapshot
        except (MemoryConflictError, MemoryNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise MemoryPersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def _upsert_thread_state(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        source_message_ids: tuple[UUID, ...],
        summary: str,
        now: datetime,
    ) -> None:
        state = await session.scalar(
            select(ThreadMemoryStateRecord)
            .where(
                ThreadMemoryStateRecord.workspace_id == scope.workspace_id,
                ThreadMemoryStateRecord.owner_user_id == scope.user_id,
                ThreadMemoryStateRecord.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if state is None:
            session.add(
                ThreadMemoryStateRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    conversation_id=conversation_id,
                    owner_user_id=scope.user_id,
                    source_message_ids=list(source_message_ids),
                    summary=summary,
                    compaction_revision=1,
                    freshness_at=now,
                    schema_version=1,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        state.source_message_ids = list(source_message_ids)
        state.summary = summary
        state.compaction_revision += 1
        state.revision += 1
        state.freshness_at = now
        state.updated_at = now

    def _new_memory(
        self,
        scope: WorkspaceScope,
        candidate: MemoryCandidateRecord,
        command: ResolveMemoryCandidate,
        *,
        now: datetime,
    ) -> tuple[MemoryRecord, MemoryRevisionRecord]:
        memory_id = uuid4()
        revision_id = uuid4()
        memory = MemoryRecord(
            id=memory_id,
            workspace_id=scope.workspace_id,
            owner_user_id=scope.user_id,
            source_conversation_id=candidate.conversation_id,
            scope=command.scope,
            kind=command.kind,
            confidence=candidate.confidence,
            status=MemoryStatus.CONFIRMED,
            current_revision_id=revision_id,
            current_version=1,
            revision=1,
            expires_at=command.expires_at,
            created_at=now,
            updated_at=now,
        )
        revision = MemoryRevisionRecord(
            id=revision_id,
            memory_id=memory_id,
            workspace_id=scope.workspace_id,
            owner_user_id=scope.user_id,
            version=1,
            content=command.content,
            scope=command.scope,
            kind=command.kind,
            write_action=command.action,
            write_reason=candidate.write_reason,
            policy_decision=candidate.policy_decision,
            editor_user_id=scope.user_id,
            validity=MemoryRevisionValidity.VALID,
            expires_at=command.expires_at,
            created_at=now,
        )
        return memory, revision

    async def _candidate_record(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        candidate_id: UUID,
        *,
        lock: bool = False,
    ) -> MemoryCandidateRecord:
        statement = select(MemoryCandidateRecord).where(
            MemoryCandidateRecord.id == candidate_id,
            MemoryCandidateRecord.workspace_id == scope.workspace_id,
            MemoryCandidateRecord.owner_user_id == scope.user_id,
        )
        if lock:
            statement = statement.with_for_update()
        candidate = await session.scalar(statement)
        if candidate is None:
            raise MemoryCandidateNotFoundError
        return candidate

    async def _memory_record(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        memory_id: UUID,
        *,
        lock: bool = False,
        include_shared: bool = False,
        include_deleted: bool = False,
    ) -> MemoryRecord:
        authorization = (
            or_(
                MemoryRecord.owner_user_id == scope.user_id,
                MemoryRecord.scope == MemoryScope.WORKSPACE,
            )
            if include_shared
            else MemoryRecord.owner_user_id == scope.user_id
        )
        statement = select(MemoryRecord).where(
            MemoryRecord.id == memory_id,
            MemoryRecord.workspace_id == scope.workspace_id,
            authorization,
        )
        if not include_deleted:
            statement = statement.where(MemoryRecord.status != MemoryStatus.DELETED)
        if lock:
            statement = statement.with_for_update()
        memory = await session.scalar(statement)
        if memory is None:
            raise MemoryNotFoundError
        return memory

    async def _candidate_source_ids(
        self,
        session: AsyncSession,
        candidate_id: UUID,
    ) -> tuple[UUID, ...]:
        rows = (
            (
                await session.execute(
                    select(MemoryCandidateSourceRecord.message_id)
                    .where(MemoryCandidateSourceRecord.candidate_id == candidate_id)
                    .order_by(MemoryCandidateSourceRecord.ordinal)
                )
            )
            .scalars()
            .all()
        )
        return tuple(rows)

    async def _revision_source_ids(
        self,
        session: AsyncSession,
        revision_id: UUID,
    ) -> tuple[UUID, ...]:
        return tuple(
            (
                await session.execute(
                    select(MemoryRevisionSourceRecord.message_id)
                    .where(MemoryRevisionSourceRecord.revision_id == revision_id)
                    .order_by(MemoryRevisionSourceRecord.ordinal)
                )
            )
            .scalars()
            .all()
        )

    async def _require_candidate_sources_available(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        *,
        candidate: MemoryCandidateRecord,
        source_ids: tuple[UUID, ...],
    ) -> None:
        available = (
            (
                await session.execute(
                    select(Message.id)
                    .join(
                        Turn,
                        (Turn.id == Message.turn_id) & (Turn.workspace_id == Message.workspace_id),
                    )
                    .join(
                        Conversation,
                        (Conversation.id == Turn.conversation_id)
                        & (Conversation.workspace_id == Turn.workspace_id),
                    )
                    .where(
                        Message.workspace_id == scope.workspace_id,
                        Message.id.in_(source_ids),
                        Message.status != MessageStatus.PARTIAL,
                        Turn.conversation_id == candidate.conversation_id,
                        Conversation.status == ConversationStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(set(available)) != len(source_ids):
            raise MemorySourceNotFoundError

    async def _candidate_snapshot(
        self,
        session: AsyncSession,
        record: MemoryCandidateRecord,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            candidate_id=record.id,
            conversation_id=record.conversation_id,
            source_message_ids=await self._candidate_source_ids(session, record.id),
            suggested_content=record.suggested_content,
            suggested_scope=record.scope,
            suggested_expires_at=record.suggested_expires_at,
            confidence=record.confidence,
            write_reason=record.write_reason,
            policy_decision=record.policy_decision,
            policy_reason=record.policy_reason,
            status=record.status,
            revision=record.revision,
            resolved_memory_id=record.resolved_memory_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _memory_snapshot(self, record: MemoryRecord, *, now: datetime | None = None) -> Memory:
        selected_now = self._clock() if now is None else now
        status = record.status
        if (
            status is MemoryStatus.CONFIRMED
            and record.expires_at is not None
            and record.expires_at <= selected_now
        ):
            status = MemoryStatus.EXPIRED
        return Memory(
            memory_id=record.id,
            owner_user_id=record.owner_user_id,
            source_conversation_id=record.source_conversation_id,
            scope=record.scope,
            kind=record.kind,
            confidence=record.confidence,
            status=status,
            current_revision_id=record.current_revision_id,
            current_version=record.current_version,
            revision=record.revision,
            expires_at=record.expires_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def _memory_detail(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        memory_id: UUID,
        *,
        include_shared: bool = False,
    ) -> MemoryDetail:
        memory = await self._memory_record(
            session,
            scope,
            memory_id,
            include_shared=include_shared,
        )
        revisions = (
            (
                await session.execute(
                    select(MemoryRevisionRecord)
                    .where(
                        MemoryRevisionRecord.memory_id == memory.id,
                        MemoryRevisionRecord.workspace_id == scope.workspace_id,
                        MemoryRevisionRecord.owner_user_id == memory.owner_user_id,
                    )
                    .order_by(MemoryRevisionRecord.version.desc())
                )
            )
            .scalars()
            .all()
        )
        source_rows = (
            await session.execute(
                select(
                    MemoryRevisionSourceRecord.revision_id,
                    MemoryRevisionSourceRecord.message_id,
                )
                .where(MemoryRevisionSourceRecord.memory_id == memory.id)
                .order_by(
                    MemoryRevisionSourceRecord.revision_id,
                    MemoryRevisionSourceRecord.ordinal,
                )
            )
        ).all()
        sources: dict[UUID, list[UUID]] = {}
        for revision_id, message_id in source_rows:
            sources.setdefault(revision_id, []).append(message_id)
        snapshots = tuple(
            self._revision_snapshot(revision, tuple(sources.get(revision.id, [])))
            for revision in revisions
        )
        current = next(
            (
                revision
                for revision in snapshots
                if revision.revision_id == memory.current_revision_id
            ),
            None,
        )
        if current is None:
            raise MemoryPersistenceError
        return MemoryDetail(
            memory=self._memory_snapshot(memory),
            current_revision=current,
            revisions=snapshots,
        )

    @staticmethod
    def _feedback_snapshot(record: MemoryFeedbackRecord) -> MemoryFeedback:
        return MemoryFeedback(
            feedback_id=record.id,
            memory_id=record.memory_id,
            memory_revision_id=record.memory_revision_id,
            actor_user_id=record.actor_user_id,
            value=record.value,
            reason=record.reason,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _audit(
        session: AsyncSession,
        scope: WorkspaceScope,
        *,
        action: str,
        memory: MemoryRecord,
        trace_id: TraceId,
        now: datetime,
        metadata: dict[str, object],
    ) -> None:
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=scope.workspace_id,
                actor_user_id=scope.user_id,
                action=action,
                resource_type=MEMORY_RESOURCE_TYPE,
                resource_id=memory.id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id,
                sanitized_metadata={
                    **metadata,
                    "resource_revision": memory.revision,
                },
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _revision_snapshot(
        record: MemoryRevisionRecord,
        source_message_ids: tuple[UUID, ...],
    ) -> MemoryRevision:
        return MemoryRevision(
            revision_id=record.id,
            version=record.version,
            content=record.content,
            scope=record.scope,
            kind=record.kind,
            write_action=record.write_action,
            write_reason=record.write_reason,
            policy_decision=record.policy_decision,
            editor_user_id=record.editor_user_id,
            source_message_ids=source_message_ids,
            validity=record.validity,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        diagnostic = getattr(error.orig, "diag", None)
        value = getattr(diagnostic, "constraint_name", None)
        return value if isinstance(value, str) else None
