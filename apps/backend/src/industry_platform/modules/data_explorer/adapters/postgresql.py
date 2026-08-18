"""PostgreSQL adapter that enforces a separate least-privilege read path."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import TextClause

from industry_platform.modules.data_explorer.domain import (
    MAX_RESULT_COLUMNS,
    MAX_RESULT_ROWS,
    SCHEMA_SNAPSHOT_VERSION,
    ColumnSchema,
    DatabaseRows,
    DataExplorerError,
    IndexSchema,
    QueryBudgets,
    SchemaSnapshot,
    TableSchema,
    require_identifier,
    schema_snapshot_sha256,
)
from industry_platform.modules.data_explorer.sql_validator import ValidatedSql

_COLUMN_SQL = text(
    """
    SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position
    FROM information_schema.columns
    WHERE __ALLOWLIST__
    ORDER BY table_schema, table_name, ordinal_position
    """
)
_TABLE_SQL = text(
    """
    SELECT ns.nspname AS schema_name,
           cls.relname AS table_name,
           GREATEST(cls.reltuples::bigint, 0) AS estimated_rows,
           pg_total_relation_size(cls.oid)::bigint AS total_bytes
    FROM pg_catalog.pg_class AS cls
    JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
    WHERE cls.relkind IN ('r', 'p') AND __ALLOWLIST__
    ORDER BY ns.nspname, cls.relname
    """
)
_INDEX_SQL = text(
    """
    SELECT ns.nspname AS schema_name,
           tbl.relname AS table_name,
           idx.relname AS index_name,
           ind.indisunique,
           ind.indisprimary,
           array_agg(att.attname ORDER BY key_position.ordinality) AS columns
    FROM pg_catalog.pg_class AS tbl
    JOIN pg_catalog.pg_namespace AS ns ON ns.oid = tbl.relnamespace
    JOIN pg_catalog.pg_index AS ind ON ind.indrelid = tbl.oid
    JOIN pg_catalog.pg_class AS idx ON idx.oid = ind.indexrelid
    JOIN LATERAL unnest(ind.indkey) WITH ORDINALITY AS key_position(attnum, ordinality)
      ON key_position.attnum > 0
    JOIN pg_catalog.pg_attribute AS att
      ON att.attrelid = tbl.oid AND att.attnum = key_position.attnum
    WHERE __ALLOWLIST__
    GROUP BY ns.nspname, tbl.relname, idx.relname, ind.indisunique, ind.indisprimary
    ORDER BY ns.nspname, tbl.relname, idx.relname
    """
)


def _allowlist_predicate(
    allowed_tables: tuple[tuple[str, str], ...],
    *,
    schema_column: str,
    table_column: str,
) -> tuple[str, dict[str, str]]:
    clauses: list[str] = []
    parameters: dict[str, str] = {}
    for index, (schema_name, table_name) in enumerate(allowed_tables):
        clauses.append(f"({schema_column} = :schema_{index} AND {table_column} = :table_{index})")
        parameters[f"schema_{index}"] = schema_name
        parameters[f"table_{index}"] = table_name
    return " OR ".join(clauses), parameters


def _statement_with_allowlist(
    template: str,
    allowed_tables: tuple[tuple[str, str], ...],
    *,
    schema_column: str,
    table_column: str,
) -> tuple[TextClause, dict[str, str]]:
    predicate, parameters = _allowlist_predicate(
        allowed_tables,
        schema_column=schema_column,
        table_column=table_column,
    )
    return text(template.replace("__ALLOWLIST__", predicate)), parameters


def _quote_identifier(value: str) -> str:
    require_identifier(value, field_name="Database identifier")
    return f'"{value}"'


def _json_cell(value: object) -> object:
    if value is None or isinstance(value, bool | int | str):
        if isinstance(value, str) and (
            len(value) > 10_000
            or any(ord(character) < 32 and character not in {"\n", "\t"} for character in value)
        ):
            raise DataExplorerError("query_result_value_invalid")
        return value
    if isinstance(value, Decimal):
        converted = float(value)
        if not math.isfinite(converted):
            raise DataExplorerError("query_result_value_invalid")
        return converted
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataExplorerError("query_result_value_invalid")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise DataExplorerError("query_result_value_invalid")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise DataExplorerError("query_result_value_invalid")


def _sqlstate(error: SQLAlchemyError) -> str | None:
    if not isinstance(error, DBAPIError):
        return None
    value = getattr(error.orig, "sqlstate", None)
    return value if isinstance(value, str) and len(value) == 5 else None


def _map_database_error(error: SQLAlchemyError) -> DataExplorerError:
    sqlstate = _sqlstate(error)
    if sqlstate == "57014":
        return DataExplorerError("query_timeout")
    if sqlstate == "25006":
        return DataExplorerError("query_write_blocked")
    return DataExplorerError("database_query_unavailable")


class PostgresReadOnlyDatabase:
    """Execute only validated SQL under transaction and role-level read-only checks."""

    def __init__(
        self,
        engine: AsyncEngine | None,
        *,
        allowed_tables: Sequence[str] = ("public.sample_company_metrics",),
        metadata_timeout_ms: int = 2_000,
    ) -> None:
        parsed: list[tuple[str, str]] = []
        for qualified in allowed_tables:
            pieces = qualified.split(".")
            if len(pieces) != 2:
                raise ValueError("Allowed database table is invalid")
            parsed.append(
                (
                    require_identifier(pieces[0], field_name="Allowed schema"),
                    require_identifier(pieces[1], field_name="Allowed table"),
                )
            )
        if (
            not parsed
            or len(parsed) != len(set(parsed))
            or not 100 <= metadata_timeout_ms <= 30_000
        ):
            raise ValueError("Read-only database configuration is invalid")
        self._engine = engine
        self._allowed_tables = tuple(parsed)
        self._metadata_timeout_ms = metadata_timeout_ms

    @property
    def configured(self) -> bool:
        return self._engine is not None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def _guard_transaction(self, connection: AsyncConnection, *, timeout_ms: int) -> None:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        readonly = await connection.scalar(
            text("SELECT current_setting('transaction_read_only')::boolean")
        )
        if readonly is not True:
            raise DataExplorerError("database_read_only_enforcement_failed")
        for schema_name, table_name in self._allowed_tables:
            qualified_name = f"{schema_name}.{table_name}"
            privileges = (
                await connection.execute(
                    text(
                        "SELECT has_table_privilege(current_user, :table_name, 'SELECT'), "
                        "has_table_privilege(current_user, :table_name, 'INSERT') OR "
                        "has_table_privilege(current_user, :table_name, 'UPDATE') OR "
                        "has_table_privilege(current_user, :table_name, 'DELETE') OR "
                        "has_table_privilege(current_user, :table_name, 'TRUNCATE') OR "
                        "has_table_privilege(current_user, :table_name, 'REFERENCES') OR "
                        "has_table_privilege(current_user, :table_name, 'TRIGGER')"
                    ),
                    {"table_name": qualified_name},
                )
            ).one()
            if privileges[0] is not True:
                raise DataExplorerError("database_select_privilege_missing")
            if privileges[1] is True:
                raise DataExplorerError("database_role_not_read_only")

    def _require_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise DataExplorerError("database_provider_not_configured")
        return self._engine

    async def probe(self) -> None:
        engine = self._require_engine()
        try:
            async with engine.connect() as connection, connection.begin():
                await self._guard_transaction(connection, timeout_ms=self._metadata_timeout_ms)
                if await connection.scalar(text("SELECT 1")) != 1:
                    raise DataExplorerError("database_probe_failed")
        except DataExplorerError:
            raise
        except SQLAlchemyError as error:
            raise _map_database_error(error) from None

    async def discover_schema(
        self,
        *,
        snapshot_id: UUID,
        connection_id: UUID,
        workspace_id: UUID,
    ) -> SchemaSnapshot:
        engine = self._require_engine()
        try:
            async with engine.connect() as connection, connection.begin():
                await self._guard_transaction(connection, timeout_ms=self._metadata_timeout_ms)
                column_statement, column_params = _statement_with_allowlist(
                    str(_COLUMN_SQL.text),
                    self._allowed_tables,
                    schema_column="table_schema",
                    table_column="table_name",
                )
                table_statement, table_params = _statement_with_allowlist(
                    str(_TABLE_SQL.text),
                    self._allowed_tables,
                    schema_column="ns.nspname",
                    table_column="cls.relname",
                )
                index_statement, index_params = _statement_with_allowlist(
                    str(_INDEX_SQL.text),
                    self._allowed_tables,
                    schema_column="ns.nspname",
                    table_column="tbl.relname",
                )
                column_rows = tuple(
                    (await connection.execute(column_statement, column_params)).mappings()
                )
                table_rows = tuple(
                    (await connection.execute(table_statement, table_params)).mappings()
                )
                index_rows = tuple(
                    (await connection.execute(index_statement, index_params)).mappings()
                )
        except DataExplorerError:
            raise
        except SQLAlchemyError as error:
            raise _map_database_error(error) from None

        columns_by_table: dict[tuple[str, str], list[ColumnSchema]] = {}
        for row in column_rows:
            key = (str(row["table_schema"]), str(row["table_name"]))
            columns_by_table.setdefault(key, []).append(
                ColumnSchema(
                    name=str(row["column_name"]),
                    data_type=str(row["data_type"]),
                    nullable=row["is_nullable"] == "YES",
                    ordinal=int(row["ordinal_position"]),
                )
            )
        indexes_by_table: dict[tuple[str, str], list[IndexSchema]] = {}
        for row in index_rows:
            key = (str(row["schema_name"]), str(row["table_name"]))
            indexes_by_table.setdefault(key, []).append(
                IndexSchema(
                    name=str(row["index_name"]),
                    columns=tuple(str(item) for item in row["columns"]),
                    unique=bool(row["indisunique"]),
                    primary=bool(row["indisprimary"]),
                )
            )
        tables = tuple(
            TableSchema(
                schema_name=str(row["schema_name"]),
                table_name=str(row["table_name"]),
                columns=tuple(
                    columns_by_table.get((str(row["schema_name"]), str(row["table_name"])), ())
                ),
                indexes=tuple(
                    indexes_by_table.get((str(row["schema_name"]), str(row["table_name"])), ())
                ),
                estimated_rows=int(row["estimated_rows"]),
                total_bytes=int(row["total_bytes"]),
            )
            for row in table_rows
        )
        if {table.qualified_name for table in tables} != {
            f"{schema}.{table}" for schema, table in self._allowed_tables
        }:
            raise DataExplorerError("database_schema_incomplete")
        captured_at = datetime.now(UTC)
        return SchemaSnapshot(
            snapshot_id=snapshot_id,
            connection_id=connection_id,
            workspace_id=workspace_id,
            version=SCHEMA_SNAPSHOT_VERSION,
            tables=tables,
            captured_at=captured_at,
            content_sha256=schema_snapshot_sha256(tables),
        )

    async def browse_rows(
        self,
        table: TableSchema,
        *,
        limit: int,
        offset: int,
    ) -> DatabaseRows:
        if table.qualified_name not in {
            f"{schema}.{name}" for schema, name in self._allowed_tables
        }:
            raise DataExplorerError("database_table_not_allowed")
        if not 1 <= limit <= MAX_RESULT_ROWS or not 0 <= offset <= 10_000:
            raise DataExplorerError("database_pagination_invalid")
        columns = tuple(column.name for column in table.columns)
        primary_columns = tuple(
            column for index in table.indexes if index.primary for column in index.columns
        )
        order_columns = primary_columns or columns
        # Identifiers come exclusively from the validated schema snapshot and are
        # quoted component-by-component; values remain bound parameters.
        statement = text(
            "SELECT "  # noqa: S608 -- every identifier passed strict validation above
            + ", ".join(_quote_identifier(column) for column in columns)
            + f" FROM {_quote_identifier(table.schema_name)}.{_quote_identifier(table.table_name)}"
            + " ORDER BY "
            + ", ".join(_quote_identifier(column) for column in order_columns)
            + " LIMIT :limit OFFSET :offset"
        )
        engine = self._require_engine()
        try:
            async with engine.connect() as connection, connection.begin():
                await self._guard_transaction(connection, timeout_ms=self._metadata_timeout_ms)
                result = await connection.execute(statement, {"limit": limit, "offset": offset})
                rows = tuple(tuple(_json_cell(value) for value in row) for row in result.fetchall())
        except DataExplorerError:
            raise
        except SQLAlchemyError as error:
            raise _map_database_error(error) from None
        return DatabaseRows(
            columns=columns,
            rows=rows,
            truncated=len(rows) == limit,
            plan_cost=0.0,
            plan_rows=len(rows),
        )

    async def execute(self, validated: ValidatedSql, budgets: QueryBudgets) -> DatabaseRows:
        engine = self._require_engine()
        try:
            async with engine.connect() as connection, connection.begin():
                await self._guard_transaction(
                    connection,
                    timeout_ms=budgets.statement_timeout_ms,
                )
                plan_result = await connection.scalar(
                    text("EXPLAIN (FORMAT JSON) " + validated.sql)
                )
                plan_cost, plan_rows = _plan_metrics(plan_result)
                if plan_cost > budgets.max_plan_cost or plan_rows > budgets.max_plan_rows:
                    raise DataExplorerError("query_scan_budget_exceeded")
                result = await connection.execute(text(validated.sql))
                columns = tuple(map(str, result.keys()))
                if not columns or len(columns) > MAX_RESULT_COLUMNS:
                    raise DataExplorerError("query_result_columns_invalid")
                rows = tuple(tuple(_json_cell(value) for value in row) for row in result.fetchall())
        except DataExplorerError:
            raise
        except SQLAlchemyError as error:
            raise _map_database_error(error) from None
        if len(rows) > budgets.max_rows:
            rows = rows[: budgets.max_rows]
        return DatabaseRows(
            columns=columns,
            rows=rows,
            truncated=len(rows) == validated.result_limit,
            plan_cost=plan_cost,
            plan_rows=plan_rows,
        )


def _plan_metrics(value: object) -> tuple[float, int]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise DataExplorerError("query_plan_invalid")
    root = value[0].get("Plan")
    if not isinstance(root, Mapping):
        raise DataExplorerError("query_plan_invalid")
    raw_plan_cost = root.get("Total Cost")
    if isinstance(raw_plan_cost, bool) or not isinstance(raw_plan_cost, int | float):
        raise DataExplorerError("query_plan_invalid")
    plan_cost = float(raw_plan_cost)
    if not math.isfinite(plan_cost) or plan_cost < 0:
        raise DataExplorerError("query_plan_invalid")

    pending: list[Mapping[str, object]] = [root]
    plan_rows = 0
    node_count = 0
    while pending:
        node = pending.pop()
        node_count += 1
        if node_count > 1_000:
            raise DataExplorerError("query_plan_invalid")
        raw_rows = node.get("Plan Rows")
        if isinstance(raw_rows, bool) or not isinstance(raw_rows, int) or raw_rows < 0:
            raise DataExplorerError("query_plan_invalid")
        plan_rows += raw_rows
        if plan_rows > 10_000_000_000:
            raise DataExplorerError("query_scan_budget_exceeded")
        children = node.get("Plans", ())
        if not isinstance(children, list | tuple):
            raise DataExplorerError("query_plan_invalid")
        for child in children:
            if not isinstance(child, Mapping):
                raise DataExplorerError("query_plan_invalid")
            pending.append(child)
    return plan_cost, plan_rows
