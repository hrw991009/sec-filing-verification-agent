"""HTTP contracts for database browsing, audited queries, and Artifacts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    ChartType,
    DataConnectionStatus,
    QueryRunStatus,
)


class DataConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    dialect: str
    status: DataConnectionStatus
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class DataConnectionCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: list[DataConnectionResponse]


class ColumnSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str
    nullable: bool
    ordinal: int


class IndexSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    columns: list[str]
    unique: bool
    primary: bool


class TableSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: str
    table_name: str
    columns: list[ColumnSchemaResponse]
    indexes: list[IndexSchemaResponse]
    estimated_rows: int
    total_bytes: int


class TableCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[TableSchemaResponse]


class DatabaseRowsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    limit: int
    offset: int


class ChartRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: ChartType = ChartType.TABLE
    x_column: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    y_column: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    series_column: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    title: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_encoding(self) -> "ChartRequestBody":
        ChartRequest(
            chart_type=self.chart_type,
            x_column=self.x_column,
            y_column=self.y_column,
            series_column=self.series_column,
            title=self.title,
        )
        return self


class ExecuteQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    question: str = Field(min_length=1, max_length=2_000)
    generated_sql: str = Field(min_length=1, max_length=20_000)
    chart: ChartRequestBody = Field(default_factory=ChartRequestBody)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 and character not in {"\n", "\t"} for character in value
        ):
            raise ValueError("Question is invalid")
        return value

    @field_validator("generated_sql")
    @classmethod
    def validate_generated_sql(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("Generated SQL is invalid")
        return value


class TableArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    content_sha256: str
    created_at: datetime


class ChartArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    chart_type: ChartType
    option: dict[str, Any]
    content_sha256: str
    created_at: datetime


class QueryRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    connection_id: UUID
    status: QueryRunStatus
    question: str
    generated_sql: str
    validated_sql: str | None
    schema_snapshot_id: UUID | None
    row_count: int
    plan_cost: float | None
    plan_rows: int | None
    error_code: str | None
    table_artifact: TableArtifactResponse | None
    chart_artifact: ChartArtifactResponse | None
    created_at: datetime | None
    terminal_at: datetime | None


class QueryRunCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_runs: list["QueryRunSummaryResponse"]


class QueryRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    connection_id: UUID
    status: QueryRunStatus
    row_count: int
    error_code: str | None
    created_at: datetime
    terminal_at: datetime | None
