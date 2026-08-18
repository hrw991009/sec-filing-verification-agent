"""Typed contracts for read-only database exploration and query artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope

DATA_EXPLORER_SCHEMA_VERSION: Final = 1
SCHEMA_SNAPSHOT_VERSION: Final = "postgres-schema-v1"
QUERY_VALIDATOR_VERSION: Final = "sqlglot-postgres-policy-v1"
TABLE_ARTIFACT_VERSION: Final = "query-table-v1"
CHART_ARTIFACT_VERSION: Final = "echarts-safe-option-v1"
TEXT2SQL_SECRET_REFERENCE: Final = "settings:text2sql_database_url"  # noqa: S105
TEXT2SQL_TOOL_NAME: Final = "database.text2sql"
TEXT2SQL_TOOL_VERSION: Final = "v1"
MAX_QUESTION_LENGTH: Final = 2_000
MAX_SQL_LENGTH: Final = 20_000
MAX_RESULT_COLUMNS: Final = 64
MAX_RESULT_ROWS: Final = 200
MAX_RESULT_JSON_BYTES: Final = 512 * 1_024
MAX_SCHEMA_TABLES: Final = 32
MAX_SCHEMA_COLUMNS_PER_TABLE: Final = 128

_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")


class DataConnectionStatus(StrEnum):
    CONFIGURATION_REQUIRED = "configuration_required"
    READY = "ready"
    ERROR = "error"


class QueryRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QueryBudgets:
    statement_timeout_ms: int
    max_rows: int
    max_plan_cost: int
    max_plan_rows: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.statement_timeout_ms, bool)
            or not 100 <= self.statement_timeout_ms <= 30_000
            or isinstance(self.max_rows, bool)
            or not 1 <= self.max_rows <= MAX_RESULT_ROWS
            or isinstance(self.max_plan_cost, bool)
            or not 1 <= self.max_plan_cost <= 10_000_000
            or isinstance(self.max_plan_rows, bool)
            or not 1 <= self.max_plan_rows <= 10_000_000
        ):
            raise ValueError("Query budgets are invalid")


class ChartType(StrEnum):
    TABLE = "table"
    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    SCATTER = "scatter"


class DataExplorerError(RuntimeError):
    """Stable, sanitized database exploration failure."""

    def __init__(self, code: str) -> None:
        if not _ERROR_CODE_PATTERN.fullmatch(code):
            raise ValueError("Data explorer error code is invalid")
        super().__init__("Database exploration failed")
        self.code = code


class DataConnectionNotFoundError(DataExplorerError):
    def __init__(self) -> None:
        super().__init__("data_connection_not_found")


class QueryRunNotFoundError(DataExplorerError):
    def __init__(self) -> None:
        super().__init__("query_run_not_found")


class DataExplorerPersistenceError(DataExplorerError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("data_explorer_persistence_error")
        self.sqlstate = sqlstate if sqlstate is not None and len(sqlstate) == 5 else None


def require_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
    return value


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("Value is not canonical JSON") from None


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ColumnSchema:
    name: str
    data_type: str
    nullable: bool
    ordinal: int

    def __post_init__(self) -> None:
        require_identifier(self.name, field_name="Column name")
        if (
            not self.data_type
            or self.data_type != self.data_type.strip()
            or len(self.data_type) > 128
            or any(character in self.data_type for character in "\r\n")
        ):
            raise ValueError("Column data type is invalid")
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= MAX_SCHEMA_COLUMNS_PER_TABLE:
            raise ValueError("Column ordinal is invalid")


@dataclass(frozen=True, slots=True)
class IndexSchema:
    name: str
    columns: tuple[str, ...]
    unique: bool
    primary: bool

    def __post_init__(self) -> None:
        require_identifier(self.name, field_name="Index name")
        columns = tuple(self.columns)
        if not columns or len(columns) > MAX_SCHEMA_COLUMNS_PER_TABLE:
            raise ValueError("Index columns are invalid")
        for column in columns:
            require_identifier(column, field_name="Index column")
        if len(columns) != len(set(columns)):
            raise ValueError("Index columns contain duplicates")
        object.__setattr__(self, "columns", columns)


@dataclass(frozen=True, slots=True)
class TableSchema:
    schema_name: str
    table_name: str
    columns: tuple[ColumnSchema, ...]
    indexes: tuple[IndexSchema, ...]
    estimated_rows: int
    total_bytes: int

    def __post_init__(self) -> None:
        require_identifier(self.schema_name, field_name="Schema name")
        require_identifier(self.table_name, field_name="Table name")
        columns = tuple(self.columns)
        indexes = tuple(self.indexes)
        if not columns or len(columns) > MAX_SCHEMA_COLUMNS_PER_TABLE:
            raise ValueError("Table columns are invalid")
        if len({column.name for column in columns}) != len(columns):
            raise ValueError("Table columns contain duplicates")
        if tuple(column.ordinal for column in columns) != tuple(range(1, len(columns) + 1)):
            raise ValueError("Table column ordinals are not contiguous")
        available = {column.name for column in columns}
        if len({index.name for index in indexes}) != len(indexes) or any(
            not set(index.columns).issubset(available) for index in indexes
        ):
            raise ValueError("Table indexes are invalid")
        for value, field_name in (
            (self.estimated_rows, "Estimated row count"),
            (self.total_bytes, "Table byte size"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} is invalid")
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "indexes", indexes)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    snapshot_id: UUID
    connection_id: UUID
    workspace_id: UUID
    version: str
    tables: tuple[TableSchema, ...]
    captured_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.snapshot_id, "Schema snapshot ID"),
            (self.connection_id, "Schema snapshot connection ID"),
            (self.workspace_id, "Schema snapshot Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.version != SCHEMA_SNAPSHOT_VERSION:
            raise ValueError("Schema snapshot version is unsupported")
        tables = tuple(self.tables)
        if not tables or len(tables) > MAX_SCHEMA_TABLES:
            raise ValueError("Schema snapshot tables are invalid")
        if len({table.qualified_name for table in tables}) != len(tables):
            raise ValueError("Schema snapshot tables contain duplicates")
        require_utc(self.captured_at, field_name="Schema snapshot capture time")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Schema snapshot hash is invalid")
        if self.content_sha256 != schema_snapshot_sha256(tables):
            raise ValueError("Schema snapshot content does not match its hash")
        object.__setattr__(self, "tables", tables)

    @property
    def table_by_name(self) -> MappingProxyType[str, TableSchema]:
        return MappingProxyType({table.qualified_name: table for table in self.tables})


def table_schema_document(table: TableSchema) -> dict[str, object]:
    return {
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "columns": [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "ordinal": column.ordinal,
            }
            for column in table.columns
        ],
        "indexes": [
            {
                "name": index.name,
                "columns": list(index.columns),
                "unique": index.unique,
                "primary": index.primary,
            }
            for index in table.indexes
        ],
        "estimated_rows": table.estimated_rows,
        "total_bytes": table.total_bytes,
    }


def schema_snapshot_sha256(tables: tuple[TableSchema, ...]) -> str:
    return canonical_json_sha256([table_schema_document(table) for table in tables])


@dataclass(frozen=True, slots=True)
class DataConnectionSummary:
    connection_id: UUID
    workspace_id: UUID
    name: str
    dialect: str
    secret_reference: str = field(repr=False)
    status: DataConnectionStatus
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.connection_id, field_name="Data connection ID")
        require_non_nil_uuid(self.workspace_id, field_name="Data connection Workspace ID")
        if not self.name.strip() or self.name != self.name.strip() or len(self.name) > 120:
            raise ValueError("Data connection name is invalid")
        if self.dialect != "postgres" or self.secret_reference != TEXT2SQL_SECRET_REFERENCE:
            raise ValueError("Data connection contract is unsupported")
        if (self.status is DataConnectionStatus.ERROR) != (self.last_error_code is not None):
            raise ValueError("Data connection status and error are inconsistent")
        if self.last_error_code is not None and not _ERROR_CODE_PATTERN.fullmatch(
            self.last_error_code
        ):
            raise ValueError("Data connection error code is invalid")
        require_utc(self.created_at, field_name="Data connection creation time")
        require_utc(self.updated_at, field_name="Data connection update time")


@dataclass(frozen=True, slots=True)
class ChartRequest:
    chart_type: ChartType
    x_column: str | None = None
    y_column: str | None = None
    series_column: str | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.x_column, "Chart x column"),
            (self.y_column, "Chart y column"),
            (self.series_column, "Chart series column"),
        ):
            if value is not None:
                require_identifier(value, field_name=field_name)
        if self.chart_type is ChartType.TABLE:
            if any(
                value is not None for value in (self.x_column, self.y_column, self.series_column)
            ):
                raise ValueError("Table Artifact does not accept chart encodings")
        elif self.x_column is None or self.y_column is None:
            raise ValueError("Chart Artifact requires x and y columns")
        if self.chart_type is ChartType.PIE and self.series_column is not None:
            raise ValueError("Pie charts do not accept a series column")
        if self.title is not None and (
            not self.title.strip()
            or self.title != self.title.strip()
            or len(self.title) > 120
            or any(character in self.title for character in "\r\n")
        ):
            raise ValueError("Chart title is invalid")


@dataclass(frozen=True, slots=True)
class DatabaseRows:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...] = field(repr=False)
    truncated: bool
    plan_cost: float
    plan_rows: int

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        rows = tuple(tuple(row) for row in self.rows)
        if (
            not columns
            or len(columns) > MAX_RESULT_COLUMNS
            or len(columns) != len(set(columns))
            or len(rows) > MAX_RESULT_ROWS
            or any(len(row) != len(columns) for row in rows)
        ):
            raise ValueError("Database result shape is invalid")
        for column in columns:
            require_identifier(column, field_name="Database result column")
        if self.plan_cost < 0 or self.plan_rows < 0:
            raise ValueError("Database plan facts are invalid")
        canonical_json_bytes({"columns": list(columns), "rows": [list(row) for row in rows]})
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True, slots=True)
class QueryExecutionRequest:
    scope: WorkspaceScope
    connection_id: UUID
    question: str = field(repr=False)
    generated_sql: str = field(repr=False)
    chart: ChartRequest
    trace_id: TraceId
    agent_run_id: UUID | None = None
    tool_call_id: UUID | None = None

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.connection_id, field_name="Query connection ID")
        if (
            not self.question.strip()
            or self.question != self.question.strip()
            or len(self.question) > MAX_QUESTION_LENGTH
            or any(
                ord(character) < 32 and character not in {"\n", "\t"} for character in self.question
            )
        ):
            raise ValueError("Query question is invalid")
        if (
            not self.generated_sql.strip()
            or self.generated_sql != self.generated_sql.strip()
            or len(self.generated_sql) > MAX_SQL_LENGTH
            or "\x00" in self.generated_sql
        ):
            raise ValueError("Generated SQL is invalid")
        if (
            not isinstance(self.trace_id, str)
            or not self.trace_id.strip()
            or len(self.trace_id) > 128
        ):
            raise ValueError("Query trace ID is invalid")
        if (self.agent_run_id is None) != (self.tool_call_id is None):
            raise ValueError("Query Agent Run and Tool Call references must be paired")
        if self.agent_run_id is not None:
            require_non_nil_uuid(self.agent_run_id, field_name="Query Agent Run ID")
            require_non_nil_uuid(cast(UUID, self.tool_call_id), field_name="Query Tool Call ID")


@dataclass(frozen=True, slots=True)
class QueryResultArtifact:
    artifact_id: UUID
    query_run_id: UUID
    workspace_id: UUID
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...] = field(repr=False)
    truncated: bool
    content_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.artifact_id, "Table Artifact ID"),
            (self.query_run_id, "Table Artifact Query Run ID"),
            (self.workspace_id, "Table Artifact Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        columns = tuple(self.columns)
        rows = tuple(tuple(row) for row in self.rows)
        if not columns or len(columns) > MAX_RESULT_COLUMNS:
            raise ValueError("Table Artifact columns are invalid")
        for column in columns:
            require_identifier(column, field_name="Table Artifact column")
        if len(columns) != len(set(columns)) or len(rows) > MAX_RESULT_ROWS:
            raise ValueError("Table Artifact shape is invalid")
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("Table Artifact rows do not match its columns")
        document = {
            "columns": list(columns),
            "rows": [list(row) for row in rows],
            "truncated": self.truncated,
        }
        encoded = canonical_json_bytes(document)
        if len(encoded) > MAX_RESULT_JSON_BYTES:
            raise ValueError("Table Artifact exceeds its byte budget")
        if (
            not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or self.content_sha256 != hashlib.sha256(encoded).hexdigest()
        ):
            raise ValueError("Table Artifact hash is invalid")
        require_utc(self.created_at, field_name="Table Artifact creation time")
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True, slots=True)
class ChartArtifact:
    artifact_id: UUID
    query_run_id: UUID
    workspace_id: UUID
    chart_type: ChartType
    option: MappingProxyType[str, object] = field(repr=False)
    content_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.artifact_id, "Chart Artifact ID"),
            (self.query_run_id, "Chart Artifact Query Run ID"),
            (self.workspace_id, "Chart Artifact Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        document = dict(self.option)
        encoded = canonical_json_bytes(document)
        if len(encoded) > MAX_RESULT_JSON_BYTES:
            raise ValueError("Chart Artifact exceeds its byte budget")
        if (
            not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or self.content_sha256 != hashlib.sha256(encoded).hexdigest()
        ):
            raise ValueError("Chart Artifact hash is invalid")
        require_utc(self.created_at, field_name="Chart Artifact creation time")
        object.__setattr__(self, "option", MappingProxyType(document))


@dataclass(frozen=True, slots=True)
class QueryRunResult:
    query_run_id: UUID
    connection_id: UUID
    workspace_id: UUID
    status: QueryRunStatus
    question: str = field(repr=False)
    generated_sql: str = field(repr=False)
    validated_sql: str | None = field(default=None, repr=False)
    schema_snapshot_id: UUID | None = None
    row_count: int = 0
    plan_cost: float | None = None
    plan_rows: int | None = None
    error_code: str | None = None
    table_artifact: QueryResultArtifact | None = None
    chart_artifact: ChartArtifact | None = None
    created_at: datetime | None = None
    terminal_at: datetime | None = None

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.query_run_id, "Query Run ID"),
            (self.connection_id, "Query Run connection ID"),
            (self.workspace_id, "Query Run Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.status is QueryRunStatus.COMPLETED:
            if (
                self.validated_sql is None
                or self.schema_snapshot_id is None
                or self.table_artifact is None
                or self.plan_cost is None
                or self.plan_rows is None
                or self.error_code is not None
                or self.terminal_at is None
            ):
                raise ValueError("Completed Query Run is incomplete")
        elif self.status is QueryRunStatus.FAILED and (
            self.error_code is None or self.table_artifact is not None or self.terminal_at is None
        ):
            raise ValueError("Failed Query Run is incomplete")
        if self.error_code is not None and not _ERROR_CODE_PATTERN.fullmatch(self.error_code):
            raise ValueError("Query Run error code is invalid")
        if isinstance(self.row_count, bool) or not 0 <= self.row_count <= MAX_RESULT_ROWS:
            raise ValueError("Query Run row count is invalid")
        if self.plan_cost is not None and self.plan_cost < 0:
            raise ValueError("Query Run plan cost is invalid")
        if self.plan_rows is not None and (isinstance(self.plan_rows, bool) or self.plan_rows < 0):
            raise ValueError("Query Run plan rows are invalid")
        if self.created_at is not None:
            require_utc(self.created_at, field_name="Query Run creation time")
        if self.terminal_at is not None:
            require_utc(self.terminal_at, field_name="Query Run terminal time")


@dataclass(frozen=True, slots=True)
class QueryRunSummary:
    query_run_id: UUID
    connection_id: UUID
    workspace_id: UUID
    status: QueryRunStatus
    row_count: int
    error_code: str | None
    created_at: datetime
    terminal_at: datetime | None

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.query_run_id, "Query Run summary ID"),
            (self.connection_id, "Query Run summary connection ID"),
            (self.workspace_id, "Query Run summary Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if isinstance(self.row_count, bool) or not 0 <= self.row_count <= MAX_RESULT_ROWS:
            raise ValueError("Query Run summary row count is invalid")
        if (self.status is QueryRunStatus.FAILED) != (self.error_code is not None):
            raise ValueError("Query Run summary status is inconsistent")
        if self.status is QueryRunStatus.RUNNING and self.terminal_at is not None:
            raise ValueError("Running Query Run summary cannot be terminal")
        if self.status is not QueryRunStatus.RUNNING and self.terminal_at is None:
            raise ValueError("Terminal Query Run summary requires terminal time")
        if self.error_code is not None and not _ERROR_CODE_PATTERN.fullmatch(self.error_code):
            raise ValueError("Query Run summary error code is invalid")
        require_utc(self.created_at, field_name="Query Run summary creation time")
        if self.terminal_at is not None:
            require_utc(self.terminal_at, field_name="Query Run summary terminal time")
