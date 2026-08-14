"""SQLAlchemy adapter that loads one fresh Direct Answer Runtime execution."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import (
    MAX_CONTEXT_ATTACHMENTS,
    AttachmentContextSource,
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import AgentRun, AgentRunStatus, RunBudget
from industry_platform.modules.agent_runtime.execution import DirectAnswerExecutionInput
from industry_platform.modules.agent_runtime.model import (
    MAX_MODEL_IMAGE_BYTES,
    ModelImageMediaType,
    ModelImagePart,
)
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.agent_runtime.runtime_contracts import (
    DirectAnswerRunCommand,
    DirectAnswerRuntimePolicy,
)
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.conversations.models import (
    Message,
    MessageAttachment,
    MessageRole,
    MessageStatus,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.identity.domain import (
    AuthenticatedWorkspace,
    TraceId,
    WorkspaceRoleName,
)
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


class DirectAnswerRunNotExecutableError(RuntimeError):
    """The requested Run is absent, stale, unauthorized, or no longer fresh."""


class DirectAnswerRunLoadError(RuntimeError):
    """A sanitized failure to read one Runtime input snapshot."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Direct Answer Run loading failed")
        self.sqlstate = sqlstate


class AttachmentObjectReader(Protocol):
    """Small private-object read shape needed by Runtime image loading."""

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes: ...


type AttachmentRow = tuple[
    int,
    UUID,
    UUID,
    FileObjectStatus,
    AttachmentKind | None,
    AttachmentMediaType | None,
    str,
    str | None,
    int | None,
    str | None,
    str | None,
    str | None,
    int | None,
    int | None,
]


@dataclass(frozen=True, slots=True)
class SqlAlchemyDirectAnswerRunLoader:
    """Load one trusted, bounded Runtime input without reading Provider Secrets."""

    session_factory: AsyncSessionFactory
    policy: DirectAnswerRuntimePolicy
    attachment_object_reader: AttachmentObjectReader | None = None

    async def load(self, run_id: UUID) -> DirectAnswerExecutionInput:
        if run_id.int == 0:
            raise DirectAnswerRunNotExecutableError
        try:
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(
                            AgentRunRecord,
                            Message.id,
                            Message.content_markdown,
                            Workspace.name,
                            WorkspaceMembership.role,
                        )
                        .join(
                            Message,
                            and_(
                                Message.agent_run_id == AgentRunRecord.id,
                                Message.workspace_id == AgentRunRecord.workspace_id,
                                Message.role == MessageRole.USER,
                                Message.status == MessageStatus.COMMITTED,
                            ),
                        )
                        .join(User, User.id == AgentRunRecord.user_id)
                        .join(Workspace, Workspace.id == AgentRunRecord.workspace_id)
                        .join(
                            WorkspaceMembership,
                            and_(
                                WorkspaceMembership.workspace_id == AgentRunRecord.workspace_id,
                                WorkspaceMembership.user_id == AgentRunRecord.user_id,
                            ),
                        )
                        .where(
                            AgentRunRecord.id == run_id,
                            AgentRunRecord.status == AgentRunStatus.QUEUED,
                            User.status == UserStatus.ACTIVE,
                            Workspace.status == WorkspaceStatus.ACTIVE,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise DirectAnswerRunNotExecutableError
                record, message_id, question, workspace_name, stored_role = row
                attachment_rows = cast(
                    list[AttachmentRow],
                    (
                        await session.execute(
                            select(
                                MessageAttachment.ordinal,
                                FileObject.id,
                                FileObject.workspace_id,
                                FileObject.status,
                                FileObject.kind,
                                FileObject.detected_media_type,
                                FileObject.bucket,
                                FileObject.object_key,
                                FileObject.safe_size,
                                FileObject.safe_sha256,
                                FileObject.extracted_text,
                                FileObject.parser_version,
                                FileObject.width,
                                FileObject.height,
                            )
                            .join(
                                FileObject,
                                and_(
                                    FileObject.id == MessageAttachment.file_id,
                                    FileObject.workspace_id == MessageAttachment.workspace_id,
                                ),
                            )
                            .where(
                                MessageAttachment.message_id == message_id,
                                MessageAttachment.workspace_id == record.workspace_id,
                            )
                            .order_by(
                                MessageAttachment.ordinal,
                                MessageAttachment.file_id,
                            )
                        )
                    )
                    .tuples()
                    .all(),
                )
        except SQLAlchemyError as error:
            raise DirectAnswerRunLoadError(sqlstate=safe_sqlstate(error)) from None

        if not isinstance(question, str) or not isinstance(workspace_name, str):
            raise DirectAnswerRunNotExecutableError
        role = cast(WorkspaceRoleName, stored_role.value)
        scope = WorkspaceScope(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            role=role,
        )
        if not scope_allows(scope, WorkspaceAction.CREATE_RESOURCE):
            raise WorkspaceAccessDeniedError

        budget = RunBudget(
            schema_version=record.schema_version,
            max_steps=record.max_steps,
            max_total_tokens=record.max_total_tokens,
            max_cost_micro_usd=record.max_cost_micro_usd,
            deadline=record.deadline,
        )
        run = AgentRun(
            schema_version=record.schema_version,
            run_id=record.id,
            event_stream_id=record.event_stream_id,
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            run_type=record.run_type,
            runtime_version=record.runtime_version,
            harness_version=record.harness_version,
            budget=budget,
            trace_id=TraceId(record.trace_id),
            status=record.status,
            state_revision=record.state_revision,
            created_at=record.created_at,
            started_at=record.started_at,
            terminal_at=record.terminal_at,
            stop_reason=record.stop_reason,
            thread_id=record.conversation_id,
            turn_id=record.turn_id,
            job_id=record.job_id,
        )
        state = RunState(
            schema_version=record.schema_version,
            run_id=record.id,
            workspace_id=record.workspace_id,
            revision=record.state_revision,
            status=record.status,
            step_count=record.step_count,
            event_count=record.event_count,
            input_tokens_used=record.input_tokens_used,
            output_tokens_used=record.output_tokens_used,
            cost_micro_usd=record.cost_micro_usd,
            updated_at=record.updated_at,
            stop_reason=record.stop_reason,
        )
        runtime_context = TrustedRuntimeContext(
            principal=BackgroundRunPrincipal(
                user_id=record.user_id,
                workspaces=(AuthenticatedWorkspace(record.workspace_id, workspace_name, role),),
            ),
            workspace_scope=scope,
            capabilities=frozenset({WorkspaceAction.VIEW}),
            budget=budget,
        )
        attachments = await self._load_attachments(
            workspace_id=record.workspace_id,
            rows=attachment_rows,
        )
        return DirectAnswerExecutionInput(
            command=DirectAnswerRunCommand(
                run=run,
                state=state,
                policy=self.policy,
                model_step_id=uuid5(run_id, "direct-answer-model-step-v1"),
                final_step_id=uuid5(run_id, "direct-answer-final-step-v1"),
                manifest_id=uuid5(run_id, "direct-answer-context-manifest-v1"),
                user_question=question,
                attachments=attachments,
            ),
            runtime_context=runtime_context,
        )

    async def _load_attachments(
        self,
        *,
        workspace_id: UUID,
        rows: Sequence[AttachmentRow],
    ) -> tuple[AttachmentContextSource, ...]:
        if len(rows) > MAX_CONTEXT_ATTACHMENTS:
            raise DirectAnswerRunNotExecutableError

        attachments: list[AttachmentContextSource] = []
        for expected_ordinal, row in enumerate(rows):
            (
                stored_ordinal,
                file_id,
                stored_workspace_id,
                status,
                kind,
                detected_media_type,
                bucket,
                object_key,
                safe_size,
                safe_sha256,
                extracted_text,
                parser_version,
                width,
                height,
            ) = row
            if (
                stored_ordinal != expected_ordinal
                or stored_workspace_id != workspace_id
                or status is not FileObjectStatus.READY
                or detected_media_type is None
                or not isinstance(safe_sha256, str)
                or not isinstance(parser_version, str)
                or not isinstance(safe_size, int)
                or isinstance(safe_size, bool)
                or safe_size < 1
            ):
                raise DirectAnswerRunNotExecutableError

            try:
                if kind is AttachmentKind.TEXT:
                    if (
                        not isinstance(extracted_text, str)
                        or len(extracted_text.encode("utf-8")) != safe_size
                    ):
                        raise DirectAnswerRunNotExecutableError
                    attachment = AttachmentContextSource(
                        file_id=file_id,
                        workspace_id=stored_workspace_id,
                        ordinal=expected_ordinal + 1,
                        media_type=detected_media_type.value,
                        sha256=safe_sha256,
                        parser_version=parser_version,
                        extracted_text=extracted_text,
                    )
                elif kind is AttachmentKind.IMAGE:
                    attachment = AttachmentContextSource(
                        file_id=file_id,
                        workspace_id=stored_workspace_id,
                        ordinal=expected_ordinal + 1,
                        media_type=detected_media_type.value,
                        sha256=safe_sha256,
                        parser_version=parser_version,
                        image_part=await self._load_image_part(
                            file_id=file_id,
                            media_type=detected_media_type.value,
                            bucket=bucket,
                            object_key=object_key,
                            actual_size=safe_size,
                            sha256=safe_sha256,
                            width=width,
                            height=height,
                        ),
                    )
                else:
                    raise DirectAnswerRunNotExecutableError
            except (TypeError, ValueError):
                raise DirectAnswerRunNotExecutableError from None
            attachments.append(attachment)
        return tuple(attachments)

    async def _load_image_part(
        self,
        *,
        file_id: UUID,
        media_type: str,
        bucket: str,
        object_key: str | None,
        actual_size: int,
        sha256: str,
        width: int | None,
        height: int | None,
    ) -> ModelImagePart:
        if (
            self.attachment_object_reader is None
            or not isinstance(bucket, str)
            or not isinstance(object_key, str)
            or not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or actual_size > MAX_MODEL_IMAGE_BYTES
        ):
            raise DirectAnswerRunLoadError
        try:
            data = await self.attachment_object_reader.read_bounded(
                bucket=bucket,
                object_key=object_key,
                maximum_bytes=MAX_MODEL_IMAGE_BYTES,
            )
        except Exception:
            raise DirectAnswerRunLoadError from None
        if (
            not isinstance(data, bytes)
            or len(data) != actual_size
            or hashlib.sha256(data).hexdigest() != sha256
        ):
            raise DirectAnswerRunLoadError
        return ModelImagePart(
            file_id=file_id,
            media_type=ModelImageMediaType(media_type),
            data=data,
            sha256=sha256,
            width=width,
            height=height,
        )
