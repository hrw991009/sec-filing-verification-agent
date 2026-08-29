"""Workspace-scoped PostgreSQL query adapter for Research L3 facts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.research.domain import (
    ResearchBrief,
    ResearchBriefInput,
    ResearchDraft,
    ResearchNode,
    ResearchPlan,
    ResearchPlanAction,
    ResearchRun,
    ResearchRunView,
)
from industry_platform.modules.research.models import (
    ResearchBriefRecord,
    ResearchDraftRecord,
    ResearchPlanRecord,
    ResearchRunRecord,
)
from industry_platform.modules.research.service import ResearchNotFoundError
from industry_platform.modules.workspaces.domain import WorkspaceScope


class ResearchPersistenceError(RuntimeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Research persistence is unavailable")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SqlAlchemyResearchQueryRepository:
    session_factory: AsyncSessionFactory

    async def save_state(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        node: ResearchNode,
        state: Mapping[str, object],
        updated_at: datetime,
    ) -> None:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(ResearchRunRecord)
                    .where(
                        ResearchRunRecord.id == research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise ResearchNotFoundError
                record.current_node = node
                record.state = dict(state)
                record.revision += 1
                record.updated_at = updated_at
        except ResearchNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ResearchPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def save_plan(self, scope: WorkspaceScope, plan: ResearchPlan) -> None:
        if plan.workspace_id != scope.workspace_id:
            raise ResearchNotFoundError
        try:
            async with self.session_factory.begin() as session:
                owner = await session.scalar(
                    select(ResearchRunRecord.id).where(
                        ResearchRunRecord.id == plan.research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                )
                if owner is None:
                    raise ResearchNotFoundError
                existing = await session.scalar(
                    select(ResearchPlanRecord).where(ResearchPlanRecord.id == plan.plan_id)
                )
                if existing is not None:
                    return
                session.add(
                    ResearchPlanRecord(
                        id=plan.plan_id,
                        workspace_id=plan.workspace_id,
                        research_run_id=plan.research_run_id,
                        brief_revision=plan.brief_revision,
                        revision=plan.revision,
                        actions=[
                            {
                                "ordinal": action.ordinal,
                                "objective": action.objective,
                                "allowed_tool_names": list(action.allowed_tool_names),
                            }
                            for action in plan.actions
                        ],
                        planner_summary=plan.planner_summary,
                        created_at=plan.created_at,
                    )
                )
        except ResearchNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ResearchPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def save_draft(self, scope: WorkspaceScope, draft: ResearchDraft) -> None:
        if draft.workspace_id != scope.workspace_id:
            raise ResearchNotFoundError
        try:
            async with self.session_factory.begin() as session:
                owner = await session.scalar(
                    select(ResearchRunRecord.id).where(
                        ResearchRunRecord.id == draft.research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                )
                if owner is None:
                    raise ResearchNotFoundError
                existing = await session.scalar(
                    select(ResearchDraftRecord).where(
                        ResearchDraftRecord.research_run_id == draft.research_run_id,
                        ResearchDraftRecord.revision == draft.revision,
                    )
                )
                if existing is None:
                    session.add(
                        ResearchDraftRecord(
                            id=draft.draft_id,
                            workspace_id=draft.workspace_id,
                            research_run_id=draft.research_run_id,
                            revision=draft.revision,
                            plan_id=draft.plan_id,
                            status=draft.status,
                            content_markdown=draft.content_markdown,
                            outline=list(draft.outline),
                            evidence_refs=[str(item) for item in draft.evidence_refs],
                            claim_refs=[str(item) for item in draft.claim_refs],
                            uncertainty_summary=draft.uncertainty_summary,
                            content_bytes=len(draft.content_markdown.encode("utf-8")),
                            created_at=draft.created_at,
                            updated_at=draft.updated_at,
                        )
                    )
                elif (
                    existing.id != draft.draft_id
                    or existing.workspace_id != draft.workspace_id
                    or existing.plan_id != draft.plan_id
                    or existing.status is not draft.status
                    or existing.content_markdown != draft.content_markdown
                    or tuple(existing.outline) != draft.outline
                    or tuple(UUID(value) for value in existing.evidence_refs) != draft.evidence_refs
                    or tuple(UUID(value) for value in existing.claim_refs) != draft.claim_refs
                    or existing.uncertainty_summary != draft.uncertainty_summary
                ):
                    raise ResearchPersistenceError
        except (ResearchNotFoundError, ResearchPersistenceError):
            raise
        except SQLAlchemyError as error:
            raise ResearchPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchRunView:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(ResearchRunRecord).where(
                        ResearchRunRecord.id == research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                )
                if record is None:
                    raise ResearchNotFoundError
                return await self._view(session, record)
        except ResearchNotFoundError:
            raise
        except (TypeError, ValueError):
            raise ResearchPersistenceError from None
        except SQLAlchemyError as error:
            raise ResearchPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list(self, scope: WorkspaceScope, *, limit: int) -> tuple[ResearchRunView, ...]:
        try:
            async with self.session_factory() as session:
                records = tuple(
                    await session.scalars(
                        select(ResearchRunRecord)
                        .where(
                            ResearchRunRecord.workspace_id == scope.workspace_id,
                            ResearchRunRecord.owner_user_id == scope.user_id,
                        )
                        .order_by(ResearchRunRecord.created_at.desc(), ResearchRunRecord.id)
                        .limit(limit)
                    )
                )
                return tuple([await self._view(session, record) for record in records])
        except (TypeError, ValueError):
            raise ResearchPersistenceError from None
        except SQLAlchemyError as error:
            raise ResearchPersistenceError(sqlstate=safe_sqlstate(error)) from None

    @staticmethod
    async def _view(session: object, record: ResearchRunRecord) -> ResearchRunView:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(session, AsyncSession):
            raise ResearchPersistenceError
        agent = await session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.id == record.agent_run_id,
                AgentRunRecord.workspace_id == record.workspace_id,
                AgentRunRecord.user_id == record.owner_user_id,
            )
        )
        brief_record = await session.scalar(
            select(ResearchBriefRecord)
            .where(
                ResearchBriefRecord.research_run_id == record.id,
                ResearchBriefRecord.workspace_id == record.workspace_id,
            )
            .order_by(ResearchBriefRecord.revision.desc())
            .limit(1)
        )
        plan_record = await session.scalar(
            select(ResearchPlanRecord)
            .where(
                ResearchPlanRecord.research_run_id == record.id,
                ResearchPlanRecord.workspace_id == record.workspace_id,
            )
            .order_by(ResearchPlanRecord.revision.desc())
            .limit(1)
        )
        draft_record = await session.scalar(
            select(ResearchDraftRecord)
            .where(
                ResearchDraftRecord.research_run_id == record.id,
                ResearchDraftRecord.workspace_id == record.workspace_id,
            )
            .order_by(ResearchDraftRecord.revision.desc())
            .limit(1)
        )
        if agent is None or brief_record is None:
            raise ResearchPersistenceError
        budget = RunBudget(
            schema_version=agent.schema_version,
            max_steps=agent.max_steps,
            max_total_tokens=agent.max_total_tokens,
            max_cost_micro_usd=agent.max_cost_micro_usd,
            deadline=agent.deadline,
        )
        run = ResearchRun(
            research_run_id=record.id,
            workspace_id=record.workspace_id,
            owner_user_id=record.owner_user_id,
            agent_run_id=record.agent_run_id,
            status=record.status,
            revision=record.revision,
            graph_version=record.graph_version,
            state_schema_version=record.state_schema_version,
            current_node=record.current_node,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        brief = ResearchBrief(
            brief_id=brief_record.id,
            research_run_id=brief_record.research_run_id,
            workspace_id=brief_record.workspace_id,
            revision=brief_record.revision,
            input=ResearchBriefInput(
                original_question=brief_record.original_question,
                confirmed_scope=tuple(brief_record.confirmed_scope),
                exclusions=tuple(brief_record.exclusions),
                completion_criteria=tuple(brief_record.completion_criteria),
                financial_scope=(
                    None
                    if brief_record.financial_scope is None
                    else FinancialScope.from_mapping(brief_record.financial_scope)
                ),
                approval_reason=brief_record.approval_reason,
            ),
            budget=budget,
            confirmed_by_user_id=brief_record.confirmed_by_user_id,
            confirmed_at=brief_record.confirmed_at,
            created_at=brief_record.created_at,
        )
        plan = None if plan_record is None else _plan_snapshot(plan_record)
        draft = None if draft_record is None else _draft_snapshot(draft_record)
        return ResearchRunView(
            research_run=run,
            brief=brief,
            plan=plan,
            draft=draft,
            agent_status=agent.status,
            stop_reason=agent.stop_reason,
            step_count=agent.step_count,
            event_count=agent.event_count,
            input_tokens_used=agent.input_tokens_used,
            output_tokens_used=agent.output_tokens_used,
            cost_micro_usd=agent.cost_micro_usd,
        )


def _plan_snapshot(record: ResearchPlanRecord) -> ResearchPlan:
    actions: list[ResearchPlanAction] = []
    for raw in _mapping_sequence(record.actions):
        ordinal = raw.get("ordinal")
        objective = raw.get("objective")
        allowed_tools = raw.get("allowed_tool_names")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or not isinstance(objective, str)
            or not isinstance(allowed_tools, list)
            or not all(isinstance(item, str) for item in allowed_tools)
        ):
            raise ResearchPersistenceError
        actions.append(
            ResearchPlanAction(
                ordinal=ordinal,
                objective=objective,
                allowed_tool_names=tuple(allowed_tools),
            )
        )
    return ResearchPlan(
        plan_id=record.id,
        research_run_id=record.research_run_id,
        workspace_id=record.workspace_id,
        brief_revision=record.brief_revision,
        revision=record.revision,
        actions=tuple(actions),
        planner_summary=record.planner_summary,
        created_at=record.created_at,
    )


def _draft_snapshot(record: ResearchDraftRecord) -> ResearchDraft:
    try:
        evidence_refs = tuple(UUID(value) for value in _string_sequence(record.evidence_refs))
        claim_refs = tuple(UUID(value) for value in _string_sequence(record.claim_refs))
    except ValueError:
        raise ResearchPersistenceError from None
    return ResearchDraft(
        draft_id=record.id,
        research_run_id=record.research_run_id,
        workspace_id=record.workspace_id,
        plan_id=record.plan_id,
        status=record.status,
        content_markdown=record.content_markdown,
        outline=tuple(_string_sequence(record.outline)),
        evidence_refs=evidence_refs,
        claim_refs=claim_refs,
        uncertainty_summary=record.uncertainty_summary,
        created_at=record.created_at,
        updated_at=record.updated_at,
        revision=record.revision,
    )


def _mapping_sequence(value: object) -> Sequence[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ResearchPersistenceError
    return value


def _string_sequence(value: object) -> Sequence[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ResearchPersistenceError
    return value
