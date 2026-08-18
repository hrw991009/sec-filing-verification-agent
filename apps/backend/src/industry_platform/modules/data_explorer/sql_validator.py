"""Fail-closed PostgreSQL SELECT validator built over SQLGlot's full AST."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from industry_platform.modules.data_explorer.domain import (
    MAX_RESULT_ROWS,
    MAX_SQL_LENGTH,
    QUERY_VALIDATOR_VERSION,
    SchemaSnapshot,
    require_identifier,
)

MAX_AST_NODES: Final = 1_000
MAX_JOINS: Final = 4
MAX_OFFSET: Final = 10_000
_SAFE_FUNCTIONS: Final = frozenset(
    {
        "abs",
        "avg",
        "coalesce",
        "count",
        "date_trunc",
        "extract",
        "greatest",
        "least",
        "length",
        "lower",
        "max",
        "min",
        "nullif",
        "round",
        "sum",
        "upper",
    }
)
_FORBIDDEN_NODE_TYPES: Final = tuple(
    candidate
    for candidate in (
        getattr(exp, name, None)
        for name in (
            "Alter",
            "Analyze",
            "Attach",
            "Cache",
            "Command",
            "Commit",
            "Copy",
            "Create",
            "Delete",
            "Detach",
            "Drop",
            "Execute",
            "Grant",
            "Insert",
            "Into",
            "Kill",
            "LoadData",
            "Lock",
            "Merge",
            "Pragma",
            "Revoke",
            "Rollback",
            "Set",
            "Transaction",
            "TruncateTable",
            "Update",
            "Use",
        )
    )
    if isinstance(candidate, type)
)


class SqlValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Generated SQL was rejected")
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedSql:
    sql: str
    validator_version: str
    selected_tables: tuple[str, ...]
    result_limit: int


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return function.name.lower()
    return function.key.lower()


def _literal_integer(expression: exp.Expression | None, *, code: str) -> int | None:
    if expression is None:
        return None
    if not isinstance(expression, exp.Literal) or not expression.is_int:
        raise SqlValidationError(code)
    try:
        value = int(expression.this)
    except (TypeError, ValueError):
        raise SqlValidationError(code) from None
    if value < 0:
        raise SqlValidationError(code)
    return value


def validate_read_only_sql(
    sql: str,
    snapshot: SchemaSnapshot,
    *,
    maximum_rows: int = MAX_RESULT_ROWS,
) -> ValidatedSql:
    """Return canonical bounded SQL or one stable rejection without SQL text."""

    if (
        not isinstance(sql, str)
        or not sql.strip()
        or sql != sql.strip()
        or len(sql) > MAX_SQL_LENGTH
        or not 1 <= maximum_rows <= MAX_RESULT_ROWS
    ):
        raise SqlValidationError("sql_input_invalid")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except (ParseError, SqlglotError, ValueError):
        raise SqlValidationError("sql_parse_rejected") from None
    if len(statements) != 1 or statements[0] is None:
        raise SqlValidationError("sql_multiple_statements_rejected")
    expression = statements[0]
    if not isinstance(expression, exp.Select):
        raise SqlValidationError("sql_statement_type_rejected")
    nodes = tuple(expression.walk())
    if len(nodes) > MAX_AST_NODES:
        raise SqlValidationError("sql_ast_budget_exceeded")
    if any(isinstance(node, _FORBIDDEN_NODE_TYPES) for node in nodes):
        raise SqlValidationError("sql_write_or_command_rejected")
    if any(isinstance(node, (exp.Placeholder, exp.Parameter)) for node in nodes):
        raise SqlValidationError("sql_parameter_rejected")
    if any(isinstance(node, exp.With) and bool(node.args.get("recursive")) for node in nodes):
        raise SqlValidationError("sql_recursive_cte_rejected")
    if sum(isinstance(node, exp.Join) for node in nodes) > MAX_JOINS:
        raise SqlValidationError("sql_join_budget_exceeded")
    for function in expression.find_all(exp.Func):
        if _function_name(function) not in _SAFE_FUNCTIONS:
            raise SqlValidationError("sql_function_rejected")

    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    allowed = snapshot.table_by_name
    physical_tables: set[str] = set()
    for table in expression.find_all(exp.Table):
        if not table.db and not table.catalog and table.name.lower() in cte_names:
            continue
        if table.catalog or not table.db:
            raise SqlValidationError("sql_table_qualification_required")
        qualified_name = f"{table.db.lower()}.{table.name.lower()}"
        if qualified_name not in allowed:
            raise SqlValidationError("sql_table_not_allowed")
        physical_tables.add(qualified_name)
    if not physical_tables:
        raise SqlValidationError("sql_table_required")

    schema = {
        table.schema_name: {
            table.table_name: {column.name: column.data_type for column in table.columns}
        }
        for table in snapshot.tables
    }
    try:
        qualified = qualify(
            expression,
            dialect="postgres",
            schema=schema,
            allow_partial_qualification=False,
            validate_qualify_columns=True,
            quote_identifiers=True,
            identify=True,
        )
    except (OptimizeError, SqlglotError, ValueError):
        raise SqlValidationError("sql_column_not_allowed") from None

    limit = qualified.args.get("limit")
    requested_limit = _literal_integer(
        None if limit is None else limit.expression,
        code="sql_limit_rejected",
    )
    offset = qualified.args.get("offset")
    requested_offset = _literal_integer(
        None if offset is None else offset.expression,
        code="sql_offset_rejected",
    )
    if requested_offset is not None and requested_offset > MAX_OFFSET:
        raise SqlValidationError("sql_offset_budget_exceeded")
    effective_limit = (
        min(requested_limit, maximum_rows) if requested_limit is not None else maximum_rows
    )
    qualified_select = cast(exp.Select, qualified)
    selected_names = tuple(qualified_select.named_selects)
    try:
        for name in selected_names:
            require_identifier(name, field_name="SQL output column")
    except ValueError:
        raise SqlValidationError("sql_output_column_rejected") from None
    if not selected_names or len(selected_names) != len(set(selected_names)):
        raise SqlValidationError("sql_output_column_rejected")
    qualified_select = qualified_select.limit(effective_limit, copy=False)
    canonical = qualified_select.sql(dialect="postgres", pretty=False, normalize=True)
    if len(canonical) > MAX_SQL_LENGTH:
        raise SqlValidationError("sql_canonical_size_exceeded")
    return ValidatedSql(
        sql=canonical,
        validator_version=QUERY_VALIDATOR_VERSION,
        selected_tables=tuple(sorted(physical_tables)),
        result_limit=effective_limit,
    )
