"""Load one accepted direct-answer Run into trusted Runtime inputs from PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.agent_runtime.execution_persistence import (
    DirectAnswerRunNotExecutableError,
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
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
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 14, 13, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def policy() -> DirectAnswerRuntimePolicy:
    return DirectAnswerRuntimePolicy(
        schema_version=1,
        profile_version="direct-answer-v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/test-model",
        max_input_tokens=2_048,
        max_output_tokens=512,
        system_instructions="Answer the current question directly with safe Markdown.",
    )


def test_loader_rebuilds_stable_runtime_inputs_and_rechecks_current_access(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            async with session_factory.begin() as session:
                session.add(
                    User(
                        id=USER_ID,
                        email="agent-loader@example.test",
                        password_hash=str(USER_ID),
                        status=UserStatus.ACTIVE,
                        password_changed_at=NOW,
                    )
                )
                session.add(
                    Workspace(
                        id=WORKSPACE_ID,
                        name="Loader Workspace",
                        created_by_user_id=USER_ID,
                        status=WorkspaceStatus.ACTIVE,
                    )
                )
                session.add(
                    WorkspaceMembership(
                        id=uuid4(),
                        workspace_id=WORKSPACE_ID,
                        user_id=USER_ID,
                        role=WorkspaceRole.MEMBER,
                    )
                )

            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            ).start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=WORKSPACE_ID,
                    user_id=USER_ID,
                    trace_id=TraceId("postgres-execution-loader"),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=2,
                        max_total_tokens=4_096,
                        max_cost_micro_usd=250_000,
                        deadline=NOW + timedelta(minutes=5),
                    ),
                    runtime_version="direct-answer-runtime-v0",
                    harness_version="harness-v0",
                    idempotency_key="postgres-execution-loader-1",
                    question="Explain the current market structure.",
                )
            )
            loader = SqlAlchemyDirectAnswerRunLoader(session_factory, policy())

            first = await loader.load(receipt.run_id)
            repeated = await loader.load(receipt.run_id)

            assert first.command.run.run_id == receipt.run_id
            assert first.command.run.job_id == receipt.job_id
            assert first.command.user_question == "Explain the current market structure."
            assert first.command.model_step_id == repeated.command.model_step_id
            assert first.command.final_step_id == repeated.command.final_step_id
            assert first.command.manifest_id == repeated.command.manifest_id
            assert first.runtime_context.project_for_model().workspace_display_name == (
                "Loader Workspace"
            )
            assert not hasattr(first.runtime_context.principal, "session_id")
            assert "Explain the current market structure." not in repr(first)

            async with session_factory.begin() as session:
                await session.execute(
                    update(WorkspaceMembership)
                    .where(
                        WorkspaceMembership.workspace_id == WORKSPACE_ID,
                        WorkspaceMembership.user_id == USER_ID,
                    )
                    .values(role=WorkspaceRole.VIEWER)
                )

            with pytest.raises(WorkspaceAccessDeniedError):
                await loader.load(receipt.run_id)
            with pytest.raises(DirectAnswerRunNotExecutableError):
                await loader.load(uuid4())
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
