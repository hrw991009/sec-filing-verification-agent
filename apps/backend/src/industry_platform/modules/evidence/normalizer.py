"""Pure validation helpers at the untrusted Observation to Evidence boundary."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from industry_platform.modules.tools.domain import ToolObservation, ToolReference, ToolSource

INDUSTRY_SOURCE_TYPE = "industry_public_source"
SQL_SOURCE_TYPE = "sql_query_result"
SQL_SOURCE_VERSION = "query-table-v1"
PROHIBITED_TERMS_MARKERS = (
    "evidence-use-prohibited",
    "evidence use prohibited",
)


@dataclass(frozen=True, slots=True)
class ParsedSqlSource:
    connection_id: UUID
    table: str
    query_run_id: UUID


def parse_persisted_observation(
    value: Mapping[str, object],
    *,
    run_id: UUID,
    workspace_id: UUID,
) -> ToolObservation:
    """Rebuild the exact ToolObservation contract and reject extra or coerced fields."""

    document = dict(value)
    expected = {
        "schema_version",
        "observation_id",
        "call_id",
        "tool_name",
        "tool_version",
        "normalizer_version",
        "model_text",
        "content_sha256",
        "observed_at",
        "sources",
    }
    if set(document) != expected:
        raise ValueError("Persisted Tool Observation fields are invalid")
    sources_value = document["sources"]
    if not isinstance(sources_value, list):
        raise ValueError("Persisted Tool Observation sources are invalid")
    sources = tuple(_parse_source(source) for source in sources_value)
    schema_version = document["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError("Persisted Tool Observation schema is invalid")
    return ToolObservation(
        schema_version=schema_version,
        observation_id=_uuid(document["observation_id"]),
        call_id=_uuid(document["call_id"]),
        run_id=run_id,
        workspace_id=workspace_id,
        tool=ToolReference(
            name=_string(document["tool_name"]),
            version=_string(document["tool_version"]),
        ),
        normalizer_version=_string(document["normalizer_version"]),
        model_text=_string(document["model_text"]),
        sources=sources,
        observed_at=_datetime(document["observed_at"]),
        content_sha256=_string(document["content_sha256"]),
    )


def parse_sql_source_locator(locator: str) -> ParsedSqlSource:
    parsed = urlsplit(locator)
    path = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "sql"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(path) != 3
        or path[1] != "query-runs"
    ):
        raise ValueError("SQL Evidence source locator is invalid")
    return ParsedSqlSource(
        connection_id=UUID(parsed.hostname),
        table=path[0],
        query_run_id=UUID(path[2]),
    )


def schema_columns_for_table(
    tables: list[dict[str, object]],
    table: str,
) -> tuple[str, ...]:
    matching: list[tuple[str, ...]] = []
    for document in tables:
        schema_name = document.get("schema_name")
        table_name = document.get("table_name")
        columns = document.get("columns")
        if not isinstance(schema_name, str) or not isinstance(table_name, str):
            raise ValueError("Schema Snapshot table identity is invalid")
        if not isinstance(columns, list):
            raise ValueError("Schema Snapshot columns are invalid")
        names: list[str] = []
        for column in columns:
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                raise ValueError("Schema Snapshot column is invalid")
            names.append(column["name"])
        if f"{schema_name}.{table_name}" == table:
            matching.append(tuple(names))
    if len(matching) != 1 or not matching[0]:
        raise ValueError("SQL Evidence table is absent from its Schema Snapshot")
    return matching[0]


def license_allows_evidence(terms: str) -> bool:
    normalized = " ".join(terms.strip().lower().split())
    return bool(normalized) and not any(marker in normalized for marker in PROHIBITED_TERMS_MARKERS)


def referenced_sql_columns(sql: str, available: tuple[str, ...]) -> tuple[str, ...]:
    """Return conservative source-column lineage from already validated SQL."""

    try:
        expression = sqlglot.parse_one(sql, read="postgres")
    except SqlglotError:
        raise ValueError("Validated SQL cannot be reconstructed") from None
    names = {column.name.lower() for column in expression.find_all(exp.Column)}
    selected = tuple(column for column in available if column in names)
    return selected or available


def _parse_source(value: object) -> ToolSource:
    if not isinstance(value, dict):
        raise ValueError("Persisted Tool source is invalid")
    expected = {
        "source_type",
        "source_version",
        "locator",
        "observed_at",
        "content_sha256",
    }
    if set(value) != expected:
        raise ValueError("Persisted Tool source fields are invalid")
    return ToolSource(
        source_type=_string(value["source_type"]),
        source_version=_string(value["source_version"]),
        locator=_string(value["locator"]),
        observed_at=_datetime(value["observed_at"]),
        content_sha256=_string(value["content_sha256"]),
    )


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("Persisted UUID is invalid")
    return UUID(value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Persisted string is invalid")
    return value


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Persisted datetime is invalid")
    return datetime.fromisoformat(value)
