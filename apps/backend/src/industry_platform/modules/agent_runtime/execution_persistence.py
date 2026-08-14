"""PostgreSQL loader for one fresh Direct Answer Runtime execution."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import AgentRun, AgentRunStatus, RunBudget
from industry_platform.modules.agent_runtime.execution import DirectAnswerExecutionInput
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.agent_runtime.runtime_contracts import (
    DirectAnswerRunCommand,
    DirectAnswerRuntimePolicy,
)
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
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


@dataclass(frozen=True, slots=True)
class SqlAlchemyDirectAnswerRunLoader:
    """Load one trusted, bounded Runtime input without reading Provider Secrets."""

    session_factory: AsyncSessionFactory
    policy: DirectAnswerRuntimePolicy

    async def load(self, run_id: UUID) -> DirectAnswerExecutionInput:
        if run_id.int == 0:
            raise DirectAnswerRunNotExecutableError
        try:
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(
                            AgentRunRecord,
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
        except SQLAlchemyError as error:
            raise DirectAnswerRunLoadError(sqlstate=safe_sqlstate(error)) from None

        if row is None:
            raise DirectAnswerRunNotExecutableError
        record, question, workspace_name, stored_role = row
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
        return DirectAnswerExecutionInput(
            command=DirectAnswerRunCommand(
                run=run,
                state=state,
                policy=self.policy,
                model_step_id=uuid5(run_id, "direct-answer-model-step-v1"),
                final_step_id=uuid5(run_id, "direct-answer-final-step-v1"),
                manifest_id=uuid5(run_id, "direct-answer-context-manifest-v1"),
                user_question=question,
            ),
            runtime_context=runtime_context,
        )
