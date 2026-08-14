"""Prove the Workbench Trace reads formal, Workspace-scoped PostgreSQL facts."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.context import (
    ContextBudgetSnapshot,
    ContextDecisionReason,
    ContextManifest,
    ContextSourceKind,
    ContextSourceManifestEntry,
)
from industry_platform.modules.agent_runtime.domain import RunBudget, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.model import ModelRole
from industry_platform.modules.agent_runtime.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.trace_query import (
    AgentTraceNotFoundError,
    SqlAlchemyAgentTraceQuery,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_WORKSPACE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def test_trace_reads_timeline_and_hides_raw_text_across_workspace_boundary(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await _seed_workspace(session_factory)
            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            ).start_direct_answer(_command())
            await _append_trace_facts(session_factory, run_id=receipt.run_id)

            query = SqlAlchemyAgentTraceQuery(session_factory)
            trace = await query.get(
                scope=WorkspaceScope(
                    workspace_id=WORKSPACE_ID,
                    user_id=USER_ID,
                    role="owner",
                ),
                run_id=receipt.run_id,
            )

            assert trace.run.status.value == "completed"
            assert trace.run.stop_reason is RunStopReason.FINAL
            assert trace.run.usage.total_tokens == 15
            assert trace.run.usage.cached_input_tokens == 3
            assert trace.run.usage.cost_micro_usd == 25
            assert tuple(step.kind.value for step in trace.steps) == ("model", "final")
            assert trace.context_manifests[0].sources[-1].source_id == "current-question"
            delta = next(
                event for event in trace.events if event.event_type is AgentEventType.MODEL_DELTA
            )
            assert delta.details == {
                "step_id": str(_model_step_id(receipt.run_id)),
                "model_sequence": 1,
                "delta_character_count": len("private model output"),
            }
            rendered = repr(trace)
            assert "private model output" not in rendered
            assert _command().question not in rendered
            assert "do-not-return-this" not in rendered

            with pytest.raises(AgentTraceNotFoundError):
                await query.get(
                    scope=WorkspaceScope(
                        workspace_id=OTHER_WORKSPACE_ID,
                        user_id=USER_ID,
                        role="owner",
                    ),
                    run_id=receipt.run_id,
                )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


async def _seed_workspace(session_factory: object) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        raise TypeError("Expected an async SQLAlchemy session factory")
    async with session_factory.begin() as session:
        assert isinstance(session, AsyncSession)
        session.add(
            User(
                id=USER_ID,
                email="agent-trace@example.test",
                password_hash=str(USER_ID),
                status=UserStatus.ACTIVE,
                password_changed_at=NOW,
            )
        )
        session.add(
            Workspace(
                id=WORKSPACE_ID,
                name="Agent Trace",
                created_by_user_id=USER_ID,
                status=WorkspaceStatus.ACTIVE,
            )
        )
        session.add(
            WorkspaceMembership(
                id=uuid4(),
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                role=WorkspaceRole.OWNER,
            )
        )


def _command() -> StartDirectAnswerTurn:
    return StartDirectAnswerTurn(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        trace_id=TraceId("postgres-agent-trace"),
        budget=RunBudget(
            schema_version=1,
            max_steps=2,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        runtime_version="direct-answer-runtime-v0",
        harness_version="harness-v0",
        idempotency_key="postgres-trace-request-1",
        question="private user question",
        new_conversation_title="Trace test",
    )


def _model_step_id(run_id: UUID) -> UUID:
    return UUID(bytes=run_id.bytes[:15] + b"\x01")


def _final_step_id(run_id: UUID) -> UUID:
    return UUID(bytes=run_id.bytes[:15] + b"\x02")


def _manifest_id(run_id: UUID) -> UUID:
    return UUID(bytes=run_id.bytes[:15] + b"\x03")


async def _append_trace_facts(session_factory: object, *, run_id: UUID) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from industry_platform.modules.agent_runtime.models import AgentRunRecord

    if not isinstance(session_factory, async_sessionmaker):
        raise TypeError("Expected an async SQLAlchemy session factory")
    async with session_factory() as session:
        stream_id = await session.scalar(
            select(AgentRunRecord.event_stream_id).where(AgentRunRecord.id == run_id)
        )
    if not isinstance(stream_id, UUID):
        raise AssertionError("Expected persisted Event stream ID")

    committer = SqlAlchemyAgentEventCommitter(session_factory)
    model_step_id = _model_step_id(run_id)
    final_step_id = _final_step_id(run_id)
    manifest_id = _manifest_id(run_id)

    await _append(
        committer,
        stream_id,
        run_id,
        2,
        AgentEventType.RUN_STARTED,
        {"state_revision": 1},
    )
    await _append(
        committer,
        stream_id,
        run_id,
        3,
        AgentEventType.STEP_STARTED,
        {"step_id": str(model_step_id), "step_sequence": 1, "step_kind": "model"},
    )
    await SqlAlchemyContextManifestStore(session_factory).save(
        _context_manifest(run_id, model_step_id, manifest_id)
    )
    await _append(
        committer,
        stream_id,
        run_id,
        4,
        AgentEventType.MODEL_STARTED,
        {
            "step_id": str(model_step_id),
            "model": "test-model",
            "context_manifest_id": str(manifest_id),
        },
    )
    await _append(
        committer,
        stream_id,
        run_id,
        5,
        AgentEventType.MODEL_DELTA,
        {
            "step_id": str(model_step_id),
            "model_sequence": 1,
            "delta": "private model output",
            "unexpected_sensitive_field": "do-not-return-this",
        },
    )
    await _append(
        committer,
        stream_id,
        run_id,
        6,
        AgentEventType.MODEL_COMPLETED,
        {
            "step_id": str(model_step_id),
            "model": "test-model",
            "finish_reason": "stop",
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 3,
            "cost_micro_usd": 25,
            "pricing_version": "test-pricing-v1",
        },
    )
    await _append(
        committer,
        stream_id,
        run_id,
        7,
        AgentEventType.STEP_COMPLETED,
        {
            "step_id": str(model_step_id),
            "step_kind": "model",
            "input_tokens": 10,
            "output_tokens": 5,
            "cached_input_tokens": 3,
            "cost_micro_usd": 25,
        },
    )
    await _append(
        committer,
        stream_id,
        run_id,
        8,
        AgentEventType.STEP_STARTED,
        {"step_id": str(final_step_id), "step_sequence": 2, "step_kind": "final"},
    )
    await _append(
        committer,
        stream_id,
        run_id,
        9,
        AgentEventType.STEP_COMPLETED,
        {
            "step_id": str(final_step_id),
            "step_kind": "final",
            "contract_version": "final-markdown-v1",
            "format": "markdown",
            "content_markdown": "private model output",
        },
    )
    await _append(
        committer,
        stream_id,
        run_id,
        10,
        AgentEventType.RUN_COMPLETED,
        {"stop_reason": "final"},
    )


def _context_manifest(run_id: UUID, step_id: UUID, manifest_id: UUID) -> ContextManifest:
    return ContextManifest(
        schema_version=1,
        manifest_id=manifest_id,
        workspace_id=WORKSPACE_ID,
        run_id=run_id,
        step_id=step_id,
        compiler_version="context-v0",
        prompt_version="prompt-v0",
        runtime_projection_version="runtime-context-projection-v0",
        token_counter_version="test-counter-v1",  # noqa: S106 - version, not a credential
        created_at=NOW + timedelta(seconds=2),
        budget=ContextBudgetSnapshot(
            run_max_total_tokens=1_000,
            tokens_used_before_step=0,
            max_input_tokens=300,
            estimated_input_tokens=100,
            allowed_output_tokens=500,
            unreserved_run_tokens=400,
        ),
        sources=(
            _source(
                1, ContextSourceKind.SYSTEM_INSTRUCTIONS, "system-instructions", ModelRole.SYSTEM
            ),
            _source(
                2,
                ContextSourceKind.RUNTIME_CONTEXT_PROJECTION,
                "runtime-context",
                ModelRole.SYSTEM,
            ),
            ContextSourceManifestEntry(
                ordinal=3,
                source_kind=ContextSourceKind.CONVERSATION_SUMMARY,
                source_id="conversation-summary",
                source_version="none-v1",
                included=False,
                decision_reason=ContextDecisionReason.NOT_AVAILABLE,
                estimated_token_count=0,
                message_role=None,
            ),
            _source(4, ContextSourceKind.USER_QUESTION, "current-question", ModelRole.USER),
        ),
    )


def _source(
    ordinal: int,
    kind: ContextSourceKind,
    source_id: str,
    role: ModelRole,
) -> ContextSourceManifestEntry:
    return ContextSourceManifestEntry(
        ordinal=ordinal,
        source_kind=kind,
        source_id=source_id,
        source_version="v1",
        included=True,
        decision_reason=ContextDecisionReason.INCLUDED,
        estimated_token_count={1: 40, 2: 20, 4: 40}[ordinal],
        message_role=role,
    )


async def _append(
    committer: SqlAlchemyAgentEventCommitter,
    stream_id: UUID,
    run_id: UUID,
    sequence: int,
    event_type: AgentEventType,
    payload: dict[str, object],
) -> None:
    await committer.append(
        AgentEvent(
            schema_version=1,
            stream_id=stream_id,
            run_id=run_id,
            workspace_id=WORKSPACE_ID,
            sequence=sequence,
            occurred_at=NOW + timedelta(seconds=sequence),
            trace_id=TraceId("postgres-agent-trace"),
            event_type=event_type,
            payload=payload,
        )
    )
