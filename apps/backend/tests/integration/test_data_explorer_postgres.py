"""Prove safe Text2SQL, Artifacts, and least privilege against real PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import URL, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.data_explorer.adapters.postgresql import (
    PostgresReadOnlyDatabase,
)
from industry_platform.modules.data_explorer.adapters.sqlalchemy import (
    SqlAlchemyDataExplorerRepository,
)
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    ChartType,
    QueryBudgets,
    QueryExecutionRequest,
    QueryRunNotFoundError,
    QueryRunStatus,
)
from industry_platform.modules.data_explorer.models import (
    ChartSpecRecord,
    QueryResultRecord,
    QueryRunRecord,
    SampleCompanyMetricRecord,
)
from industry_platform.modules.data_explorer.service import (
    DataExplorerService,
    StaleQueryRunReconciliationService,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


def test_safe_text2sql_and_artifacts_use_a_real_read_only_role(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    role_name = f"iip_text2sql_ro_{uuid4().hex}"
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
                sql.Identifier(role_name),
                sql.Literal(role_password),
            )
        )
        owner_connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.postgres_db),
                sql.Identifier(role_name),
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
        application_engine = create_database_engine(settings)
        session_factory = create_database_session_factory(application_engine)
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
        repository = SqlAlchemyDataExplorerRepository(session_factory)
        budgets = QueryBudgets(
            statement_timeout_ms=2_000,
            max_rows=20,
            max_plan_cost=100_000,
            max_plan_rows=100_000,
        )
        service = DataExplorerService(
            repository,
            database,
            budgets,
        )
        user_id = uuid4()
        workspace_id = uuid4()
        other_workspace_id = uuid4()
        scope = WorkspaceScope(workspace_id, user_id, "owner")
        other_scope = WorkspaceScope(other_workspace_id, user_id, "owner")
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"text2sql-owner-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Text2SQL tenant",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        Workspace(
                            id=other_workspace_id,
                            name="Text2SQL other tenant",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                    )
                )

            connection = await service.ensure_sample_connection(scope)
            assert connection.status.value == "ready"
            tables = await service.list_tables(scope, connection.connection_id)
            assert tuple(table.qualified_name for table in tables) == (
                "public.sample_company_metrics",
            )
            assert any(index.primary for index in tables[0].indexes)
            page = await service.browse_rows(
                scope,
                connection.connection_id,
                schema_name="public",
                table_name="sample_company_metrics",
                limit=2,
                offset=0,
            )
            assert len(page.rows) == 2
            assert page.columns == (
                "company_name",
                "metric_date",
                "industry",
                "revenue",
                "employees",
            )

            completed = await service.execute_direct_query(
                scope,
                connection_id=connection.connection_id,
                question="What is the total revenue by industry?",
                generated_sql=(
                    "SELECT industry, SUM(revenue) AS total_revenue "
                    "FROM public.sample_company_metrics GROUP BY industry ORDER BY industry"
                ),
                chart=ChartRequest(
                    chart_type=ChartType.BAR,
                    x_column="industry",
                    y_column="total_revenue",
                    title="Revenue by industry",
                ),
                trace_id=TraceId("text2sql-integration-success"),
            )
            assert completed.status is QueryRunStatus.COMPLETED
            assert completed.row_count == 4
            assert completed.plan_rows is not None
            assert completed.plan_rows >= 4
            assert completed.table_artifact is not None
            assert completed.chart_artifact is not None
            assert set(completed.chart_artifact.option) <= {
                "dataset",
                "series",
                "title",
                "tooltip",
                "xAxis",
                "yAxis",
            }

            rejected = await service.execute_direct_query(
                scope,
                connection_id=connection.connection_id,
                question="Delete the data",
                generated_sql="DELETE FROM public.sample_company_metrics",
                chart=ChartRequest(chart_type=ChartType.TABLE),
                trace_id=TraceId("text2sql-integration-rejected"),
            )
            assert rejected.status is QueryRunStatus.FAILED
            assert rejected.error_code == "sql_statement_type_rejected"
            artifact_rejected = await service.execute_direct_query(
                scope,
                connection_id=connection.connection_id,
                question="Plot a non-numeric value on the y axis",
                generated_sql=("SELECT company_name, industry FROM public.sample_company_metrics"),
                chart=ChartRequest(
                    chart_type=ChartType.BAR,
                    x_column="company_name",
                    y_column="industry",
                ),
                trace_id=TraceId("text2sql-integration-artifact-rejected"),
            )
            assert artifact_rejected.status is QueryRunStatus.FAILED
            assert artifact_rejected.error_code == "chart_data_type_invalid"
            summaries = await service.list_queries(scope, limit=20)
            assert tuple(item.status for item in summaries) == (
                QueryRunStatus.FAILED,
                QueryRunStatus.FAILED,
                QueryRunStatus.COMPLETED,
            )

            stale_request = QueryExecutionRequest(
                scope=scope,
                connection_id=connection.connection_id,
                question="A query interrupted by process loss",
                generated_sql="SELECT industry FROM public.sample_company_metrics",
                chart=ChartRequest(chart_type=ChartType.TABLE),
                trace_id=TraceId("text2sql-integration-stale"),
            )
            stale = await repository.start_query(stale_request, budgets)
            reconciled_at = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
            async with session_factory.begin() as session:
                stale_record = await session.get(QueryRunRecord, stale.query_run_id)
                assert stale_record is not None
                stale_record.started_at = reconciled_at - timedelta(minutes=10)
                stale_record.updated_at = stale_record.started_at
            reconciler = StaleQueryRunReconciliationService(
                repository,
                stale_after_seconds=300,
                clock=lambda: reconciled_at,
            )
            assert await reconciler.reconcile_stale(batch_size=20) == 1
            reconciled = await service.get_query(scope, stale.query_run_id)
            assert reconciled.status is QueryRunStatus.FAILED
            assert reconciled.error_code == "query_execution_interrupted"
            assert reconciled.terminal_at == reconciled_at

            async with session_factory() as session:
                assert await session.scalar(select(func.count()).select_from(QueryRunRecord)) == 4
                assert (
                    await session.scalar(select(func.count()).select_from(QueryResultRecord)) == 1
                )
                assert await session.scalar(select(func.count()).select_from(ChartSpecRecord)) == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SampleCompanyMetricRecord)
                    )
                    == 4
                )
                failed_artifact_run = await session.get(
                    QueryRunRecord,
                    artifact_rejected.query_run_id,
                )
                assert failed_artifact_run is not None
                assert failed_artifact_run.plan_cost is not None
                assert failed_artifact_run.plan_rows is not None
            with pytest.raises(QueryRunNotFoundError):
                await service.get_query(other_scope, completed.query_run_id)
        finally:
            await database.close()
            await application_engine.dispose()

    try:
        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(exercise())
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            psycopg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=role_name,
                password=role_password,
                autocommit=True,
            ) as read_only_connection,
        ):
            read_only_connection.execute("DELETE FROM public.sample_company_metrics")
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
