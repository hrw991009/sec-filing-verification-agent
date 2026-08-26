"""SQLAlchemy adapter that loads one fresh Direct Answer Runtime execution."""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import (
    MAX_CONTEXT_ATTACHMENTS,
    AttachmentContextSource,
    BackgroundRunPrincipal,
    MemoryContextBundle,
    ToolObservationContextSource,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    AgentStep,
    AgentStepKind,
    AgentStepStatus,
    RunBudget,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerExecutionInput,
    ProductionAgentRunCommand,
)
from industry_platform.modules.agent_runtime.model import (
    MAX_MODEL_IMAGE_BYTES,
    ModelFinishReason,
    ModelImageMediaType,
    ModelImagePart,
    ModelResponse,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import (
    AgentCheckpointRecord,
    AgentEventRecord,
    AgentRunRecord,
)
from industry_platform.modules.agent_runtime.runtime_contracts import (
    DirectAnswerRunCommand,
    DirectAnswerRuntimePolicy,
)
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    ToolL2RunCommand,
    ToolL2RuntimePolicy,
    ToolLoopFinalDecision,
)
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.models import (
    Message,
    MessageAttachment,
    MessageRole,
    MessageStatus,
    Turn,
)
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.financial_verification.domain import FinancialScope
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
from industry_platform.modules.industry.models import IndustryRecord
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_NODE_ORDER,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchApprovalStatus,
    ResearchBrief,
    ResearchBriefInput,
    ResearchNode,
)
from industry_platform.modules.research.models import (
    ResearchApprovalRequestRecord,
    ResearchBriefRecord,
    ResearchRunRecord,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows
from industry_platform.workflows.research.contracts import (
    ResearchGraphState,
    ResearchL3RunCommand,
    ResearchResumeKind,
    ResearchResumeSnapshot,
)


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


class MemoryContextLoader(Protocol):
    """Reload authorized Memory candidates for one queued Run."""

    async def load(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        current_goal: str,
        max_input_tokens: int,
    ) -> MemoryContextBundle: ...


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
    tool_policy: ToolL2RuntimePolicy | None = None
    tool_policies: Mapping[TurnSearchMode, ToolL2RuntimePolicy] | None = None
    attachment_object_reader: AttachmentObjectReader | None = None
    memory_context_loader: MemoryContextLoader | None = None

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
                            Turn.search_mode,
                            Turn.industry_id,
                            Turn.knowledge_base_ids,
                            IndustryRecord.code,
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
                        .join(
                            Turn,
                            and_(
                                Turn.id == AgentRunRecord.turn_id,
                                Turn.workspace_id == AgentRunRecord.workspace_id,
                            ),
                        )
                        .outerjoin(IndustryRecord, IndustryRecord.id == Turn.industry_id)
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
                            or_(
                                AgentRunRecord.status == AgentRunStatus.QUEUED,
                                and_(
                                    AgentRunRecord.run_type == AgentRunType.RESEARCH,
                                    AgentRunRecord.status.in_(
                                        (AgentRunStatus.PAUSED, AgentRunStatus.RUNNING)
                                    ),
                                ),
                            ),
                            User.status == UserStatus.ACTIVE,
                            Workspace.status == WorkspaceStatus.ACTIVE,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise DirectAnswerRunNotExecutableError
                (
                    record,
                    message_id,
                    question,
                    workspace_name,
                    stored_role,
                    search_mode,
                    industry_id,
                    stored_knowledge_base_ids,
                    industry_code,
                ) = row
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
                research_row = None
                resume_checkpoint = None
                resume_approval = None
                resume_events: tuple[AgentEventRecord, ...] = ()
                if record.run_type is AgentRunType.RESEARCH:
                    research_row = (
                        await session.execute(
                            select(ResearchRunRecord, ResearchBriefRecord)
                            .join(
                                ResearchBriefRecord,
                                and_(
                                    ResearchBriefRecord.research_run_id == ResearchRunRecord.id,
                                    ResearchBriefRecord.workspace_id
                                    == ResearchRunRecord.workspace_id,
                                ),
                            )
                            .where(
                                ResearchRunRecord.agent_run_id == record.id,
                                ResearchRunRecord.workspace_id == record.workspace_id,
                                ResearchRunRecord.owner_user_id == record.user_id,
                                ResearchBriefRecord.revision == 1,
                            )
                        )
                    ).one_or_none()
                    if record.status in {AgentRunStatus.PAUSED, AgentRunStatus.RUNNING}:
                        resume_checkpoint = await session.scalar(
                            select(AgentCheckpointRecord)
                            .where(
                                AgentCheckpointRecord.run_id == record.id,
                                AgentCheckpointRecord.workspace_id == record.workspace_id,
                            )
                            .order_by(AgentCheckpointRecord.revision.desc())
                            .limit(1)
                        )
                        if record.status is AgentRunStatus.PAUSED:
                            resume_approval = await session.scalar(
                                select(ResearchApprovalRequestRecord)
                                .where(
                                    ResearchApprovalRequestRecord.run_id == record.id,
                                    ResearchApprovalRequestRecord.workspace_id
                                    == record.workspace_id,
                                    ResearchApprovalRequestRecord.status
                                    == ResearchApprovalStatus.ALLOWED,
                                    ResearchApprovalRequestRecord.resume_claimed.is_(True),
                                )
                                .order_by(ResearchApprovalRequestRecord.created_at.desc())
                                .limit(1)
                            )
                        resume_events = tuple(
                            await session.scalars(
                                select(AgentEventRecord)
                                .where(
                                    AgentEventRecord.run_id == record.id,
                                    AgentEventRecord.workspace_id == record.workspace_id,
                                    AgentEventRecord.sequence <= record.event_count,
                                )
                                .order_by(AgentEventRecord.sequence)
                            )
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
        research_enabled = record.run_type is AgentRunType.RESEARCH
        tool_enabled = record.run_type is AgentRunType.TOOL_LOOP or research_enabled
        policies = dict(self.tool_policies or {})
        if self.tool_policy is not None:
            policies.setdefault(TurnSearchMode.WEB, self.tool_policy)
        selected_tool_policy = policies.get(search_mode)
        knowledge_base_ids = tuple(stored_knowledge_base_ids)
        financial_scope = None
        if research_row is not None:
            _research_record, stored_brief = research_row
            if stored_brief.financial_scope is not None:
                try:
                    financial_scope = FinancialScope.from_mapping(stored_brief.financial_scope)
                except ValueError:
                    raise DirectAnswerRunNotExecutableError from None
        if tool_enabled and (
            selected_tool_policy is None
            or (
                search_mode is TurnSearchMode.WEB
                and (industry_id is None or not isinstance(industry_code, str))
            )
            or (
                search_mode is TurnSearchMode.LOCAL
                and (
                    not research_enabled
                    or industry_id is not None
                    or not knowledge_base_ids
                    or financial_scope is None
                )
            )
            or search_mode not in {TurnSearchMode.WEB, TurnSearchMode.LOCAL}
        ):
            raise DirectAnswerRunNotExecutableError
        if not tool_enabled and search_mode is not TurnSearchMode.NONE:
            raise DirectAnswerRunNotExecutableError
        if research_enabled and research_row is None:
            raise DirectAnswerRunNotExecutableError
        runtime_context = TrustedRuntimeContext(
            principal=BackgroundRunPrincipal(
                user_id=record.user_id,
                workspaces=(AuthenticatedWorkspace(record.workspace_id, workspace_name, role),),
            ),
            workspace_scope=scope,
            capabilities=frozenset(
                {
                    WorkspaceAction.VIEW,
                    WorkspaceAction.RUN_TOOL,
                    *({WorkspaceAction.RUN_RESEARCH} if research_enabled else set()),
                }
                if tool_enabled
                else {WorkspaceAction.VIEW}
            ),
            budget=budget,
            knowledge_base_ids=knowledge_base_ids,
            financial_scope=financial_scope,
        )
        attachments = await self._load_attachments(
            workspace_id=record.workspace_id,
            rows=attachment_rows,
        )
        memory_context = (
            MemoryContextBundle()
            if self.memory_context_loader is None
            else await self.memory_context_loader.load(
                scope,
                conversation_id=record.conversation_id,
                current_goal=question,
                max_input_tokens=(
                    selected_tool_policy.max_input_tokens
                    if tool_enabled and selected_tool_policy is not None
                    else self.policy.max_input_tokens
                ),
            )
        )
        command: ProductionAgentRunCommand
        if tool_enabled:
            if selected_tool_policy is None:
                raise DirectAnswerRunNotExecutableError
            if search_mode is TurnSearchMode.WEB:
                if not isinstance(industry_code, str):
                    raise DirectAnswerRunNotExecutableError
                conversation_summary = "Current industry snapshot for this Turn: " + industry_code
                summary_version = "turn-industry-snapshot-v1"
            else:
                if financial_scope is None:
                    raise DirectAnswerRunNotExecutableError
                conversation_summary = (
                    "Pinned SEC filing scope: "
                    f"CIK {financial_scope.cik}; accession {financial_scope.accession}; "
                    f"form {financial_scope.form.value}; report period "
                    f"{financial_scope.report_period.isoformat()}; as_of "
                    f"{financial_scope.as_of.isoformat()}; unit {financial_scope.unit}; "
                    f"scale {financial_scope.scale}."
                )
                summary_version = "turn-financial-scope-v1"
            loop_command = ToolL2RunCommand(
                run=run,
                state=state,
                policy=selected_tool_policy,
                decision_model_step_ids=tuple(
                    uuid5(run_id, f"tool-l2-decision-step-{index}-v1")
                    for index in range(selected_tool_policy.model_call_limit)
                ),
                tool_step_ids=tuple(
                    uuid5(run_id, f"tool-l2-tool-step-{index}-v1")
                    for index in range(selected_tool_policy.tool_call_limit)
                ),
                decision_manifest_ids=tuple(
                    uuid5(run_id, f"tool-l2-context-manifest-{index}-v1")
                    for index in range(selected_tool_policy.model_call_limit)
                ),
                tool_call_ids=tuple(
                    uuid5(run_id, f"tool-l2-call-{index}-v1")
                    for index in range(selected_tool_policy.tool_call_limit)
                ),
                approval_request_ids=tuple(
                    uuid5(run_id, f"tool-l2-approval-{index}-v1")
                    for index in range(selected_tool_policy.tool_call_limit)
                ),
                final_step_id=uuid5(run_id, "tool-l2-final-step-v1"),
                user_question=question,
                conversation_summary=conversation_summary,
                conversation_summary_version=summary_version,
                attachments=attachments,
                memory_context=memory_context,
                side_effect_idempotency_keys=(None,) * selected_tool_policy.tool_call_limit,
                embedded_in_research=research_enabled,
            )
            if research_enabled:
                if research_row is None:
                    raise DirectAnswerRunNotExecutableError
                research_record, brief_record = research_row
                brief = ResearchBrief(
                    brief_id=brief_record.id,
                    research_run_id=research_record.id,
                    workspace_id=research_record.workspace_id,
                    revision=brief_record.revision,
                    input=ResearchBriefInput(
                        original_question=brief_record.original_question,
                        confirmed_scope=tuple(brief_record.confirmed_scope),
                        exclusions=tuple(brief_record.exclusions),
                        completion_criteria=tuple(brief_record.completion_criteria),
                        financial_scope=financial_scope,
                        approval_reason=brief_record.approval_reason,
                    ),
                    budget=budget,
                    confirmed_by_user_id=brief_record.confirmed_by_user_id,
                    confirmed_at=brief_record.confirmed_at,
                    created_at=brief_record.created_at,
                )
                resume = None
                if record.status in {AgentRunStatus.PAUSED, AgentRunStatus.RUNNING}:
                    resume = _resume_snapshot(
                        record=record,
                        checkpoint=resume_checkpoint,
                        approval=resume_approval,
                        events=resume_events,
                        financial_scope=financial_scope,
                    )
                command = ResearchL3RunCommand(
                    run=run,
                    state=state,
                    research_run_id=research_record.id,
                    brief=brief,
                    loop_command=loop_command,
                    plan_id=uuid5(run_id, "research-plan-v1"),
                    draft_id=uuid5(run_id, "research-draft-v1"),
                    resume=resume,
                )
            else:
                command = loop_command
        else:
            command = DirectAnswerRunCommand(
                run=run,
                state=state,
                policy=self.policy,
                model_step_id=uuid5(run_id, "direct-answer-model-step-v1"),
                final_step_id=uuid5(run_id, "direct-answer-final-step-v1"),
                manifest_id=uuid5(run_id, "direct-answer-context-manifest-v1"),
                user_question=question,
                attachments=attachments,
                memory_context=memory_context,
            )
        return DirectAnswerExecutionInput(
            command=command,
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


def _resume_snapshot(
    *,
    record: AgentRunRecord,
    checkpoint: AgentCheckpointRecord | None,
    approval: ResearchApprovalRequestRecord | None,
    events: tuple[AgentEventRecord, ...],
    financial_scope: FinancialScope | None,
) -> ResearchResumeSnapshot:
    if checkpoint is None or not events or events[-1].sequence != record.event_count:
        raise DirectAnswerRunNotExecutableError
    if record.status is AgentRunStatus.PAUSED:
        kind = ResearchResumeKind.APPROVAL
        tail = events[-1]
        if (
            approval is None
            or approval.checkpoint_id != checkpoint.id
            or approval.checkpoint_revision != checkpoint.revision
            or approval.resume_job_id != record.job_id
            or tail.event_type is not AgentEventType.APPROVAL_DECIDED
            or tail.payload.get("approval_request_id") != str(approval.id)
            or tail.payload.get("checkpoint_revision") != approval.checkpoint_revision
            or tail.payload.get("outcome") != "allow"
            or not any(
                event.event_type is AgentEventType.RUN_PAUSED
                and event.payload.get("approval_request_id") == str(approval.id)
                and event.payload.get("checkpoint_revision") == approval.checkpoint_revision
                for event in events
            )
        ):
            raise DirectAnswerRunNotExecutableError
    elif record.status is AgentRunStatus.RUNNING:
        kind = ResearchResumeKind.RECOVERY
        tail = events[-1]
        if (
            approval is not None
            or tail.event_type is not AgentEventType.CHECKPOINT_SAVED
            or tail.payload.get("checkpoint_id") != str(checkpoint.id)
            or tail.payload.get("revision") != checkpoint.revision
        ):
            raise DirectAnswerRunNotExecutableError
    else:
        raise DirectAnswerRunNotExecutableError
    raw_payload = checkpoint.state.get("payload")
    if not isinstance(raw_payload, dict):
        raise DirectAnswerRunNotExecutableError
    graph = raw_payload.get("graph_state")
    raw_financial_scope = raw_payload.get("financial_scope")
    raw_node = raw_payload.get("node")
    raw_next_node = raw_payload.get("next_node")
    if (
        raw_payload.get("kind") != "research_l4_v1"
        or raw_payload.get("graph_version") != RESEARCH_GRAPH_VERSION
        or raw_payload.get("research_state_schema_version") != RESEARCH_STATE_SCHEMA_VERSION
        or financial_scope is None
        or not isinstance(raw_financial_scope, dict)
        or not isinstance(graph, dict)
        or not isinstance(raw_node, str)
    ):
        raise DirectAnswerRunNotExecutableError
    required_graph_keys = {
        "schema_version",
        "graph_version",
        "research_run_id",
        "run_id",
        "workspace_id",
        "brief_revision",
        "plan_id",
        "current_node",
        "pending_actions",
        "evidence_refs",
        "claim_refs",
        "artifact_refs",
        "status",
        "step_count",
        "input_tokens_used",
        "output_tokens_used",
        "cost_micro_usd",
        "revise_count",
        "approval_status",
        "approval_reason",
        "cancel_requested",
        "stop_reason",
        "error_summary",
    }
    if (
        set(graph) != required_graph_keys
        or graph.get("schema_version") != RESEARCH_STATE_SCHEMA_VERSION
        or graph.get("graph_version") != RESEARCH_GRAPH_VERSION
        or graph.get("run_id") != str(record.id)
        or graph.get("workspace_id") != str(record.workspace_id)
    ):
        raise DirectAnswerRunNotExecutableError
    try:
        if FinancialScope.from_mapping(raw_financial_scope) != financial_scope:
            raise ValueError("Checkpoint Financial Scope is invalid")
        node = ResearchNode(raw_node)
        node_index = RESEARCH_NODE_ORDER.index(node)
        expected_next = (
            None
            if node_index + 1 == len(RESEARCH_NODE_ORDER)
            else RESEARCH_NODE_ORDER[node_index + 1]
        )
        next_node = None if raw_next_node is None else ResearchNode(cast(str, raw_next_node))
        if next_node is not expected_next:
            raise ValueError("Checkpoint next node is invalid")
        execution = raw_payload.get("execution")
        if not isinstance(execution, dict):
            raise ValueError("Checkpoint execution payload is missing")
        observations = _restore_observations(execution.get("observations"), record.workspace_id)
        steps = _restore_steps(execution.get("steps"), record.id, record.workspace_id)
        decision = _restore_final_decision(execution.get("final_decision"))
        response = _restore_model_response(execution.get("final_response"))
        final_markdown = execution.get("final_markdown")
        raw_outline = execution.get("outline")
        if final_markdown is not None and not isinstance(final_markdown, str):
            raise ValueError("Checkpoint final Markdown is invalid")
        if not isinstance(raw_outline, list) or not all(
            isinstance(value, str) for value in raw_outline
        ):
            raise ValueError("Checkpoint outline is invalid")
        if node_index >= RESEARCH_NODE_ORDER.index(ResearchNode.RESEARCH_LOOP) and (
            decision is None or response is None or not steps
        ):
            raise ValueError("Checkpoint Tool loop result is incomplete")
        return ResearchResumeSnapshot(
            kind=kind,
            checkpoint_revision=checkpoint.revision,
            next_node=next_node,
            graph=cast(ResearchGraphState, graph),
            event_history=tuple(_domain_event(event) for event in events),
            steps=steps,
            observations=observations,
            final_decision=decision,
            final_response=response,
            final_markdown=final_markdown,
            outline=tuple(cast(list[str], raw_outline)),
        )
    except (KeyError, TypeError, ValueError):
        raise DirectAnswerRunNotExecutableError from None


def _restore_observations(
    raw: object,
    workspace_id: UUID,
) -> tuple[ToolObservationContextSource, ...]:
    if not isinstance(raw, list):
        raise ValueError("Checkpoint observations are invalid")
    restored: list[ToolObservationContextSource] = []
    for ordinal, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError("Checkpoint observation is invalid")
        tool = item.get("tool")
        source = item.get("source")
        locator = item.get("locator")
        if (
            not isinstance(tool, dict)
            or not isinstance(source, dict)
            or not isinstance(locator, dict)
        ):
            raise ValueError("Checkpoint observation envelope is invalid")
        restored.append(
            ToolObservationContextSource(
                observation_id=UUID(_required_checkpoint_str(item, "observation_id")),
                tool_call_id=UUID(_required_checkpoint_str(item, "tool_call_id")),
                workspace_id=workspace_id,
                ordinal=ordinal,
                tool_name=_required_checkpoint_str(tool, "name"),
                tool_version=_required_checkpoint_str(tool, "version"),
                source_name=_required_checkpoint_str(source, "name"),
                source_version=_required_checkpoint_str(source, "version"),
                observed_at=_checkpoint_datetime(item.get("observed_at")),
                locator=locator,
                content_sha256=_required_checkpoint_str(item, "content_sha256"),
                model_text=_required_checkpoint_str(item, "content"),
            )
        )
    return tuple(restored)


def _restore_steps(raw: object, run_id: UUID, workspace_id: UUID) -> tuple[AgentStep, ...]:
    if not isinstance(raw, list):
        raise ValueError("Checkpoint Steps are invalid")
    restored: list[AgentStep] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Checkpoint Step is invalid")
        input_summary = item.get("input_summary")
        output_summary = item.get("output_summary")
        input_artifact_ids = item.get("input_artifact_ids")
        output_artifact_ids = item.get("output_artifact_ids")
        if (
            not isinstance(input_summary, dict)
            or not isinstance(output_summary, dict)
            or not isinstance(input_artifact_ids, list)
            or not isinstance(output_artifact_ids, list)
        ):
            raise ValueError("Checkpoint Step projection is invalid")
        completed_at = item.get("completed_at")
        restored.append(
            AgentStep(
                schema_version=_required_checkpoint_int(item, "schema_version"),
                step_id=UUID(_required_checkpoint_str(item, "step_id")),
                run_id=UUID(_required_checkpoint_str(item, "run_id")),
                workspace_id=UUID(_required_checkpoint_str(item, "workspace_id")),
                sequence=_required_checkpoint_int(item, "sequence"),
                kind=AgentStepKind(_required_checkpoint_str(item, "kind")),
                status=AgentStepStatus(_required_checkpoint_str(item, "status")),
                state_revision=_required_checkpoint_int(item, "state_revision"),
                started_at=_checkpoint_datetime(item.get("started_at")),
                completed_at=(None if completed_at is None else _checkpoint_datetime(completed_at)),
                input_summary=input_summary,
                output_summary=output_summary,
                input_artifact_ids=tuple(UUID(cast(str, value)) for value in input_artifact_ids),
                output_artifact_ids=tuple(UUID(cast(str, value)) for value in output_artifact_ids),
                input_tokens=_required_checkpoint_int(item, "input_tokens"),
                output_tokens=_required_checkpoint_int(item, "output_tokens"),
                cost_micro_usd=_required_checkpoint_int(item, "cost_micro_usd"),
                latency_ms=_required_checkpoint_int(item, "latency_ms"),
                error_code=cast(str | None, item.get("error_code")),
            )
        )
    if any(step.run_id != run_id or step.workspace_id != workspace_id for step in restored):
        raise ValueError("Checkpoint Step belongs to another Run")
    return tuple(restored)


def _restore_final_decision(raw: object) -> ToolLoopFinalDecision | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint final decision is invalid")
    return ToolLoopFinalDecision(
        schema_version=_required_checkpoint_int(raw, "schema_version"),
        content_markdown=_required_checkpoint_str(raw, "content_markdown"),
    )


def _restore_model_response(raw: object) -> ModelResponse | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint Model response is invalid")
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Checkpoint Model usage is invalid")
    pricing_version = usage.get("pricing_version")
    provider_request_id = raw.get("provider_request_id")
    if pricing_version is not None and not isinstance(pricing_version, str):
        raise ValueError("Checkpoint pricing version is invalid")
    if provider_request_id is not None and not isinstance(provider_request_id, str):
        raise ValueError("Checkpoint Provider request ID is invalid")
    return ModelResponse(
        schema_version=_required_checkpoint_int(raw, "schema_version"),
        model=_required_checkpoint_str(raw, "model"),
        finish_reason=ModelFinishReason(_required_checkpoint_str(raw, "finish_reason")),
        usage=ModelUsage(
            input_tokens=_required_checkpoint_int(usage, "input_tokens"),
            output_tokens=_required_checkpoint_int(usage, "output_tokens"),
            cached_input_tokens=_required_checkpoint_int(usage, "cached_input_tokens"),
            cost_micro_usd=_required_checkpoint_int(usage, "cost_micro_usd"),
            pricing_version=pricing_version,
        ),
        output_text=_required_checkpoint_str(raw, "output_text"),
        provider_request_id=provider_request_id,
    )


def _required_checkpoint_str(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Checkpoint {key} is invalid")
    return value


def _required_checkpoint_int(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Checkpoint {key} is invalid")
    return value


def _checkpoint_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Checkpoint timestamp is invalid")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _domain_event(record: AgentEventRecord) -> AgentEvent:
    return AgentEvent(
        schema_version=record.schema_version,
        stream_id=record.stream_id,
        run_id=record.run_id,
        workspace_id=record.workspace_id,
        sequence=record.sequence,
        occurred_at=record.occurred_at,
        trace_id=TraceId(record.trace_id),
        event_type=record.event_type,
        payload=record.payload,
    )
