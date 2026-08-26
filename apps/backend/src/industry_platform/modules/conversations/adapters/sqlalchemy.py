"""SQLAlchemy transaction that accepts a chat turn and its durable Job atomically."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.domain import AgentRunType
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.models import AgentEventRecord, AgentRunRecord
from industry_platform.modules.conversations.domain import (
    CONVERSATION_WEB_TOOL_CALL_LIMIT,
    DirectAnswerTurnReceipt,
    PreparedDirectAnswerTurn,
)
from industry_platform.modules.conversations.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageAttachment,
    MessageRole,
    MessageStatus,
    Turn,
)
from industry_platform.modules.conversations.service import (
    ConversationAttachmentNotReadyError,
    ConversationAttachmentNotSupportedError,
    ConversationNotFoundError,
    ConversationPersistenceError,
    DirectAnswerTurnWriter,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    FileObjectPurpose,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyJobWriter
from industry_platform.modules.jobs.models import OutboxEvent
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchRunStatus,
    initial_research_state_document,
    research_brief_id_for_run,
    research_run_id_for_agent_run,
)
from industry_platform.modules.research.models import ResearchBriefRecord, ResearchRunRecord


@dataclass(slots=True)
class SqlAlchemyDirectAnswerTurnWriter:
    """Use the same AsyncSession for business rows and Day 1 Job/Outbox rows."""

    session: AsyncSession
    supports_image_input: bool = False

    async def submit(self, prepared: PreparedDirectAnswerTurn) -> DirectAnswerTurnReceipt:
        job_record = await SqlAlchemyJobWriter(self.session).submit(prepared.job)
        if not job_record.created:
            return await self._existing_receipt(prepared, job_record.job_id)

        if prepared.attachment_ids:
            attachments = tuple(
                await self.session.scalars(
                    select(FileObject)
                    .where(
                        FileObject.id.in_(prepared.attachment_ids),
                        FileObject.workspace_id == prepared.run.workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .order_by(FileObject.id)
                    .with_for_update()
                )
            )
            by_id = {attachment.id: attachment for attachment in attachments}
            if len(by_id) != len(prepared.attachment_ids):
                raise ConversationAttachmentNotReadyError
            if any(
                by_id[attachment_id].status is not FileObjectStatus.READY
                or by_id[attachment_id].attached_at is not None
                for attachment_id in prepared.attachment_ids
            ):
                raise ConversationAttachmentNotReadyError
            if not self.supports_image_input and any(
                by_id[attachment_id].kind is AttachmentKind.IMAGE
                for attachment_id in prepared.attachment_ids
            ):
                raise ConversationAttachmentNotSupportedError
            for attachment_id in prepared.attachment_ids:
                by_id[attachment_id].attached_at = prepared.run.created_at

        if prepared.create_conversation:
            if prepared.conversation_title is None:
                raise ConversationPersistenceError()
            self.session.add(
                Conversation(
                    id=prepared.conversation_id,
                    workspace_id=prepared.run.workspace_id,
                    created_by_user_id=prepared.run.user_id,
                    title=prepared.conversation_title,
                    status=ConversationStatus.ACTIVE,
                    created_at=prepared.run.created_at,
                    updated_at=prepared.run.created_at,
                )
            )
            await self.session.flush()
            turn_sequence = 1
        else:
            conversation = await self.session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == prepared.conversation_id,
                    Conversation.workspace_id == prepared.run.workspace_id,
                    Conversation.status == ConversationStatus.ACTIVE,
                )
                .with_for_update()
            )
            if conversation is None:
                raise ConversationNotFoundError
            previous_sequence = await self.session.scalar(
                select(func.coalesce(func.max(Turn.sequence), 0)).where(
                    Turn.conversation_id == prepared.conversation_id,
                    Turn.workspace_id == prepared.run.workspace_id,
                )
            )
            if previous_sequence is None:
                raise ConversationPersistenceError()
            turn_sequence = previous_sequence + 1
            conversation.updated_at = prepared.run.created_at

        run = prepared.run
        queued_payload: dict[str, object] = {
            "run_type": run.run_type.value,
            "runtime_version": run.runtime_version,
            "harness_version": run.harness_version,
        }
        if run.run_type is AgentRunType.TOOL_LOOP:
            queued_payload.update(
                loop_level="l2",
                tool_call_limit=CONVERSATION_WEB_TOOL_CALL_LIMIT,
            )
        elif run.run_type is AgentRunType.RESEARCH:
            queued_payload.update(
                loop_level="l3",
                graph_version=RESEARCH_GRAPH_VERSION,
                tool_call_limit=CONVERSATION_WEB_TOOL_CALL_LIMIT,
            )
        self.session.add(
            Turn(
                id=prepared.turn_id,
                workspace_id=run.workspace_id,
                conversation_id=prepared.conversation_id,
                created_by_user_id=run.user_id,
                sequence=turn_sequence,
                search_mode=prepared.search_mode,
                industry_id=prepared.industry_id,
                knowledge_base_ids=list(prepared.knowledge_base_ids),
                created_at=run.created_at,
                updated_at=run.created_at,
            )
        )
        await self.session.flush()
        self.session.add(
            AgentRunRecord(
                id=run.run_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                conversation_id=prepared.conversation_id,
                turn_id=prepared.turn_id,
                job_id=prepared.job.job_id,
                event_stream_id=run.event_stream_id,
                trace_id=str(run.trace_id),
                run_type=run.run_type,
                status=run.status,
                stop_reason=None,
                runtime_version=run.runtime_version,
                harness_version=run.harness_version,
                state_revision=0,
                schema_version=run.schema_version,
                max_steps=run.budget.max_steps,
                max_total_tokens=run.budget.max_total_tokens,
                max_cost_micro_usd=run.budget.max_cost_micro_usd,
                deadline=run.budget.deadline,
                event_count=1,
                step_count=0,
                input_tokens_used=0,
                output_tokens_used=0,
                cached_input_tokens_used=0,
                cost_micro_usd=0,
                started_at=None,
                terminal_at=None,
                cancel_requested_at=None,
                created_at=run.created_at,
                updated_at=run.created_at,
            )
        )
        await self.session.flush()
        message = Message(
            id=prepared.user_message_id,
            workspace_id=run.workspace_id,
            turn_id=prepared.turn_id,
            agent_run_id=run.run_id,
            created_by_user_id=run.user_id,
            role=MessageRole.USER,
            status=MessageStatus.COMMITTED,
            content_markdown=prepared.question,
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        self.session.add(message)
        await self.session.flush()
        self.session.add_all(
            MessageAttachment(
                workspace_id=run.workspace_id,
                message_id=message.id,
                file_id=file_id,
                ordinal=ordinal,
                created_at=run.created_at,
            )
            for ordinal, file_id in enumerate(prepared.attachment_ids)
        )
        self.session.add(
            AgentEventRecord(
                workspace_id=run.workspace_id,
                run_id=run.run_id,
                stream_id=run.event_stream_id,
                sequence=1,
                occurred_at=run.created_at,
                trace_id=str(run.trace_id),
                schema_version=run.schema_version,
                event_type=AgentEventType.RUN_QUEUED,
                payload=queued_payload,
            )
        )
        if prepared.research_brief is not None:
            research_run_id = research_run_id_for_agent_run(run.run_id)
            self.session.add(
                ResearchRunRecord(
                    id=research_run_id,
                    workspace_id=run.workspace_id,
                    owner_user_id=run.user_id,
                    agent_run_id=run.run_id,
                    status=ResearchRunStatus.DRAFT,
                    revision=1,
                    graph_version=RESEARCH_GRAPH_VERSION,
                    state_schema_version=RESEARCH_STATE_SCHEMA_VERSION,
                    current_node=None,
                    state=initial_research_state_document(
                        research_run_id=research_run_id,
                        agent_run_id=run.run_id,
                        workspace_id=run.workspace_id,
                        approval_reason=prepared.research_brief.approval_reason,
                    ),
                    error_summary=None,
                    created_at=run.created_at,
                    updated_at=run.created_at,
                )
            )
            await self.session.flush()
            self.session.add(
                ResearchBriefRecord(
                    id=research_brief_id_for_run(research_run_id),
                    workspace_id=run.workspace_id,
                    research_run_id=research_run_id,
                    revision=1,
                    original_question=prepared.research_brief.original_question,
                    confirmed_scope=list(prepared.research_brief.confirmed_scope),
                    exclusions=list(prepared.research_brief.exclusions),
                    completion_criteria=list(prepared.research_brief.completion_criteria),
                    financial_scope=(
                        None
                        if prepared.research_brief.financial_scope is None
                        else dict(prepared.research_brief.financial_scope.to_mapping())
                    ),
                    approval_reason=prepared.research_brief.approval_reason,
                    budget={
                        "schema_version": run.budget.schema_version,
                        "max_steps": run.budget.max_steps,
                        "max_total_tokens": run.budget.max_total_tokens,
                        "max_cost_micro_usd": run.budget.max_cost_micro_usd,
                        "deadline": run.budget.deadline.isoformat(),
                    },
                    confirmed_by_user_id=run.user_id,
                    confirmed_at=run.created_at,
                    created_at=run.created_at,
                )
            )
        await self.session.flush()
        return DirectAnswerTurnReceipt(
            conversation_id=prepared.conversation_id,
            turn_id=prepared.turn_id,
            user_message_id=prepared.user_message_id,
            run_id=run.run_id,
            job_id=job_record.job_id,
            outbox_event_id=job_record.outbox_event_id,
            created=True,
        )

    async def _existing_receipt(
        self,
        prepared: PreparedDirectAnswerTurn,
        job_id: UUID,
    ) -> DirectAnswerTurnReceipt:
        existing = await self.session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.job_id == job_id,
                AgentRunRecord.workspace_id == prepared.run.workspace_id,
                AgentRunRecord.user_id == prepared.run.user_id,
            )
        )
        if existing is None:
            raise ConversationPersistenceError()
        message_id = await self.session.scalar(
            select(Message.id).where(
                Message.agent_run_id == existing.id,
                Message.workspace_id == prepared.run.workspace_id,
                Message.role == MessageRole.USER,
            )
        )
        if not isinstance(message_id, UUID):
            raise ConversationPersistenceError()
        outbox_event_id = await self.session.scalar(
            select(OutboxEvent.id).where(OutboxEvent.source_job_id == existing.job_id)
        )
        if not isinstance(outbox_event_id, UUID):
            raise ConversationPersistenceError()
        return DirectAnswerTurnReceipt(
            conversation_id=existing.conversation_id,
            turn_id=existing.turn_id,
            user_message_id=message_id,
            run_id=existing.id,
            job_id=existing.job_id,
            outbox_event_id=outbox_event_id,
            created=False,
        )


@dataclass(frozen=True, slots=True)
class SqlAlchemyDirectAnswerTurnTransactionFactory:
    """Commit all accepted-turn facts together or roll all of them back."""

    session_factory: AsyncSessionFactory
    supports_image_input: bool = False

    def __call__(self) -> AbstractAsyncContextManager[DirectAnswerTurnWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[DirectAnswerTurnWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyDirectAnswerTurnWriter(
                    session,
                    supports_image_input=self.supports_image_input,
                )
        except (
            ConversationAttachmentNotReadyError,
            ConversationAttachmentNotSupportedError,
            ConversationNotFoundError,
            ConversationPersistenceError,
        ):
            raise
        except SQLAlchemyError as error:
            raise ConversationPersistenceError(sqlstate=safe_sqlstate(error)) from None
