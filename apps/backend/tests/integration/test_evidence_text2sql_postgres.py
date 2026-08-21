"""Promote one formal Text2SQL Observation into re-resolvable SQL Evidence."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import create_async_engine

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L2_RUNTIME_VERSION,
    ToolL2RunCommand,
    ToolL2RuntimePolicy,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn, TurnSearchMode
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.data_explorer.adapters.postgresql import (
    PostgresReadOnlyDatabase,
)
from industry_platform.modules.data_explorer.adapters.sqlalchemy import (
    SqlAlchemyDataExplorerRepository,
)
from industry_platform.modules.data_explorer.domain import QueryBudgets
from industry_platform.modules.data_explorer.service import DataExplorerService
from industry_platform.modules.data_explorer.tool import Text2SqlTool
from industry_platform.modules.evidence.adapters.sqlalchemy import SqlAlchemyEvidenceRepository
from industry_platform.modules.evidence.domain import (
    EvidenceDecision,
    EvidenceKind,
    EvidenceNotFoundError,
    NormalizeObservation,
    SqlResultLocatorV1,
)
from industry_platform.modules.evidence.normalizer import parse_persisted_observation
from industry_platform.modules.evidence.service import EvidenceApplicationService
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.industry.domain import SMART_TRANSPORT_INDUSTRY_ID
from industry_platform.modules.tools.models import ToolCallRecord
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


@dataclass(slots=True)
class ScriptedText2SqlModel:
    connection_id: UUID
    requests: list[ModelRequest] = field(default_factory=list)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise AssertionError(f"Text2SQL L2 must not stream structured decisions: {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            output = json.dumps(
                {
                    "decision": {
                        "schema_version": 1,
                        "kind": "tool_call",
                        "name": "database.text2sql",
                        "version": "v1",
                        "arguments": {
                            "connection_id": str(self.connection_id),
                            "question": "What is total revenue by industry?",
                            "generated_sql": (
                                "SELECT industry, SUM(revenue) AS total_revenue "
                                "FROM public.sample_company_metrics "
                                "GROUP BY industry ORDER BY industry"
                            ),
                            "chart_type": "table",
                            "x_column": None,
                            "y_column": None,
                            "series_column": None,
                            "title": None,
                        },
                    }
                },
                separators=(",", ":"),
            )
        elif len(self.requests) == 2:
            output = (
                '{"decision":{"schema_version":1,"kind":"final",'
                '"content_markdown":"SQL result captured with governed Evidence lineage."}}'
            )
        else:
            raise AssertionError("Text2SQL model script exhausted")
        return ModelResponse(
            schema_version=1,
            model=request.model,
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                input_tokens=20,
                output_tokens=10,
                cached_input_tokens=0,
                cost_micro_usd=40,
                pricing_version="frozen-pricing-v1",
            ),
            output_text=output,
            provider_request_id=f"text2sql-evidence-{len(self.requests)}",
        )


@dataclass(slots=True)
class IncrementingClock:
    value: datetime

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


def test_text2sql_observation_becomes_query_run_evidence(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    role_name = f"iip_evidence_ro_{uuid4().hex}"
    role_password = f"readonly-{uuid4().hex}"
    settings = migrated_postgres_probe.settings
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        autocommit=True,
    ) as owner_connection:
        owner_connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(role_password)
            )
        )
        owner_connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.postgres_db), sql.Identifier(role_name)
            )
        )
        owner_connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role_name))
        )
        owner_connection.execute(
            sql.SQL("GRANT SELECT ON public.sample_company_metrics TO {}").format(
                sql.Identifier(role_name)
            )
        )

    async def exercise() -> None:
        now = datetime.now(UTC)
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        read_only_engine = create_async_engine(
            URL.create(
                "postgresql+psycopg",
                username=role_name,
                password=role_password,
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
            ),
            pool_pre_ping=True,
        )
        database = PostgresReadOnlyDatabase(read_only_engine)
        data_service = DataExplorerService(
            SqlAlchemyDataExplorerRepository(session_factory),
            database,
            QueryBudgets(
                statement_timeout_ms=2_000,
                max_rows=20,
                max_plan_cost=100_000,
                max_plan_rows=100_000,
            ),
        )
        workspace_id = uuid4()
        other_workspace_id = uuid4()
        user_id = uuid4()
        session_id = uuid4()
        scope = WorkspaceScope(workspace_id, user_id, "owner")
        budget = RunBudget(
            schema_version=1,
            max_steps=8,
            max_total_tokens=8_192,
            max_cost_micro_usd=250_000,
            deadline=now + timedelta(minutes=5),
        )
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"evidence-text2sql-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=now,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Evidence Text2SQL",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        Workspace(
                            id=other_workspace_id,
                            name="Evidence other Workspace",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )
            connection = await data_service.ensure_sample_connection(scope)
            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: now,
            ).start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    trace_id=TraceId("evidence-text2sql-run"),
                    budget=budget,
                    runtime_version=TOOL_L2_RUNTIME_VERSION,
                    harness_version="harness-v1",
                    idempotency_key=f"evidence-text2sql-{user_id}",
                    question="Find total revenue by industry.",
                    search_mode=TurnSearchMode.WEB,
                    industry_id=SMART_TRANSPORT_INDUSTRY_ID,
                )
            )
            async with session_factory() as session:
                persisted = await session.get(AgentRunRecord, receipt.run_id)
            assert persisted is not None
            assert persisted.run_type is AgentRunType.TOOL_LOOP
            run = AgentRun(
                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                run_id=persisted.id,
                event_stream_id=persisted.event_stream_id,
                workspace_id=persisted.workspace_id,
                user_id=persisted.user_id,
                run_type=persisted.run_type,
                runtime_version=persisted.runtime_version,
                harness_version=persisted.harness_version,
                budget=budget,
                trace_id=TraceId(persisted.trace_id),
                status=persisted.status,
                state_revision=persisted.state_revision,
                created_at=persisted.created_at,
                started_at=persisted.started_at,
                terminal_at=persisted.terminal_at,
                stop_reason=persisted.stop_reason,
                thread_id=persisted.conversation_id,
                turn_id=persisted.turn_id,
                job_id=persisted.job_id,
            )
            tool = Text2SqlTool(data_service)
            policy = ToolL2RuntimePolicy(
                schema_version=1,
                profile_version="evidence-text2sql-l2-v1",
                prompt_version="tool-l2-prompt-v1",
                context_compiler_version="context-v1",
                output_contract_version="final-markdown-v1",
                toolset_version="evidence-text2sql-toolset-v1",
                model="openai-compatible/frozen-text2sql",
                max_input_tokens=4_096,
                max_decision_output_tokens=768,
                max_tool_calls=2,
                system_instructions="Use the exact read-only Text2SQL Tool, then answer.",
                available_tools=(tool.definition.reference,),
            )
            ids = tuple(uuid4() for _ in range(13))
            command = ToolL2RunCommand(
                run=run,
                state=RunState(
                    schema_version=1,
                    run_id=run.run_id,
                    workspace_id=workspace_id,
                    revision=0,
                    status=AgentRunStatus.QUEUED,
                    step_count=0,
                    event_count=1,
                    input_tokens_used=0,
                    output_tokens_used=0,
                    cost_micro_usd=0,
                    updated_at=now,
                ),
                policy=policy,
                decision_model_step_ids=(ids[0], ids[1], ids[2]),
                tool_step_ids=(ids[3], ids[4]),
                decision_manifest_ids=(ids[5], ids[6], ids[7]),
                tool_call_ids=(ids[8], ids[9]),
                approval_request_ids=(ids[10], ids[11]),
                final_step_id=ids[12],
                user_question="Find total revenue by industry.",
                side_effect_idempotency_keys=(None, None),
            )
            runtime_context = TrustedRuntimeContext(
                principal=AuthenticatedPrincipal(
                    user_id=user_id,
                    session_id=session_id,
                    email=NormalizedEmail(f"evidence-text2sql-{user_id}@example.test"),
                    workspaces=(
                        AuthenticatedWorkspace(
                            workspace_id=workspace_id,
                            name="Evidence Text2SQL",
                            role="owner",
                        ),
                    ),
                ),
                workspace_scope=scope,
                capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
                budget=budget,
            )
            provider = ScriptedText2SqlModel(connection.connection_id)
            registry = ToolRegistry((tool,))
            clock = IncrementingClock(now + timedelta(seconds=1))
            events = [
                event
                async for event in ToolL2Runtime(
                    context_compiler=ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter()),
                    context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
                    model_provider=provider,
                    tool_registry=registry,
                    tool_executor=RegistryToolExecutor(registry, clock=clock),
                    event_committer=SqlAlchemyAgentEventCommitter(session_factory),
                    cancellation_probe=SqlAlchemyAgentRunControl(session_factory),
                    clock=clock,
                ).run(command, runtime_context)
            ]
            assert events[-1].event_type is AgentEventType.RUN_COMPLETED
            async with session_factory() as session:
                call = await session.scalar(
                    select(ToolCallRecord).where(ToolCallRecord.run_id == receipt.run_id)
                )
            assert call is not None
            assert call.observation is not None
            observation = parse_persisted_observation(
                call.observation,
                run_id=call.run_id,
                workspace_id=call.workspace_id,
            )
            normalized = await EvidenceApplicationService(
                SqlAlchemyEvidenceRepository(session_factory),
                clock=lambda: now + timedelta(minutes=1),
            ).normalize_observation(
                scope,
                NormalizeObservation(
                    tool_call_id=call.id,
                    observation_id=observation.observation_id,
                    trace_id=TraceId("evidence-text2sql-normalize"),
                ),
            )
            assert len(normalized.items) == 1
            assert normalized.items[0].decision is EvidenceDecision.ACCEPTED
            evidence = normalized.items[0].evidence
            assert evidence is not None
            assert evidence.kind is EvidenceKind.SQL_RESULT
            assert evidence.origin_run_id == receipt.run_id
            assert evidence.origin_tool_call_id == call.id
            assert evidence.source_resource_version.startswith("query-table-v1:")
            assert isinstance(evidence.locator, SqlResultLocatorV1)
            assert evidence.locator.connection_id == connection.connection_id
            assert evidence.locator.tables == ("public.sample_company_metrics",)
            assert evidence.locator.columns == ("industry", "revenue")
            assert evidence.locator.row_start == 0
            assert evidence.locator.row_end == 4

            repository = SqlAlchemyEvidenceRepository(session_factory)
            resolved = await repository.get_evidence(scope, evidence.evidence_id)
            assert resolved == evidence
            with pytest.raises(EvidenceNotFoundError):
                await repository.get_evidence(
                    WorkspaceScope(other_workspace_id, user_id, "owner"),
                    evidence.evidence_id,
                )
        finally:
            await database.close()
            await engine.dispose()

    try:
        asyncio.run(exercise(), loop_factory=create_selector_event_loop)
    finally:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            autocommit=True,
        ) as owner_connection:
            owner_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name)))
            owner_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role_name)))
