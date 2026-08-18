"""Security tests for the complete Text2SQL AST policy."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from industry_platform.modules.data_explorer.domain import (
    SCHEMA_SNAPSHOT_VERSION,
    ColumnSchema,
    IndexSchema,
    SchemaSnapshot,
    TableSchema,
    schema_snapshot_sha256,
)
from industry_platform.modules.data_explorer.sql_validator import (
    SqlValidationError,
    validate_read_only_sql,
)


def _snapshot() -> SchemaSnapshot:
    tables = (
        TableSchema(
            schema_name="analytics",
            table_name="company_metrics",
            columns=(
                ColumnSchema("company_name", "text", False, 1),
                ColumnSchema("industry", "text", False, 2),
                ColumnSchema("metric_date", "date", False, 3),
                ColumnSchema("revenue", "numeric", False, 4),
                ColumnSchema("employees", "integer", False, 5),
            ),
            indexes=(
                IndexSchema(
                    "pk_company_metrics",
                    ("company_name", "metric_date"),
                    True,
                    True,
                ),
            ),
            estimated_rows=4,
            total_bytes=16_384,
        ),
    )
    return SchemaSnapshot(
        snapshot_id=uuid4(),
        connection_id=uuid4(),
        workspace_id=uuid4(),
        version=SCHEMA_SNAPSHOT_VERSION,
        tables=tables,
        captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        content_sha256=schema_snapshot_sha256(tables),
    )


def test_validator_qualifies_columns_expands_star_and_caps_rows() -> None:
    validated = validate_read_only_sql(
        "WITH metrics AS (SELECT * FROM analytics.company_metrics) "
        "SELECT company_name, SUM(revenue) AS revenue "
        "FROM metrics GROUP BY company_name ORDER BY revenue DESC",
        _snapshot(),
        maximum_rows=25,
    )

    assert validated.selected_tables == ("analytics.company_metrics",)
    assert validated.result_limit == 25
    assert 'FROM "analytics"."company_metrics"' in validated.sql
    assert "LIMIT 25" in validated.sql
    assert '"metrics"."company_name"' in validated.sql


@pytest.mark.parametrize(
    ("sql", "expected_code"),
    [
        ("DELETE FROM analytics.company_metrics", "sql_statement_type_rejected"),
        ("UPDATE analytics.company_metrics SET revenue = 0", "sql_statement_type_rejected"),
        (
            "INSERT INTO analytics.company_metrics VALUES ('x','y',CURRENT_DATE,1,1)",
            "sql_statement_type_rejected",
        ),
        ("CREATE TABLE analytics.stolen(id int)", "sql_statement_type_rejected"),
        ("DROP TABLE analytics.company_metrics", "sql_statement_type_rejected"),
        (
            "ALTER TABLE analytics.company_metrics ADD COLUMN secret text",
            "sql_statement_type_rejected",
        ),
        ("COPY analytics.company_metrics TO STDOUT", "sql_statement_type_rejected"),
        ("CALL run_me()", "sql_statement_type_rejected"),
        (
            "SELECT * FROM analytics.company_metrics; DELETE FROM analytics.company_metrics",
            "sql_multiple_statements_rejected",
        ),
        (
            "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1) SELECT * FROM x",
            "sql_recursive_cte_rejected",
        ),
        ("SELECT pg_sleep(1) FROM analytics.company_metrics", "sql_function_rejected"),
        (
            "SELECT pg_read_file('/etc/passwd') FROM analytics.company_metrics",
            "sql_function_rejected",
        ),
        (
            "SELECT current_setting('data_directory') FROM analytics.company_metrics",
            "sql_function_rejected",
        ),
        ("SELECT nextval('secret') FROM analytics.company_metrics", "sql_function_rejected"),
        ("SELECT * FROM pg_catalog.pg_user", "sql_table_not_allowed"),
        ("SELECT * FROM company_metrics", "sql_table_qualification_required"),
        ("SELECT missing FROM analytics.company_metrics", "sql_column_not_allowed"),
        ("SELECT * FROM analytics.company_metrics OFFSET 10001", "sql_offset_budget_exceeded"),
        ("SELECT * FROM analytics.company_metrics LIMIT $1", "sql_parameter_rejected"),
        ("SELECT * INTO stolen FROM analytics.company_metrics", "sql_write_or_command_rejected"),
        ("SELECT * FROM analytics.company_metrics FOR UPDATE", "sql_write_or_command_rejected"),
    ],
)
def test_validator_rejects_every_dangerous_or_out_of_scope_shape(
    sql: str,
    expected_code: str,
) -> None:
    with pytest.raises(SqlValidationError) as captured:
        validate_read_only_sql(sql, _snapshot())
    assert captured.value.code == expected_code
    assert sql not in str(captured.value)


def test_validator_preserves_a_smaller_literal_limit() -> None:
    validated = validate_read_only_sql(
        "SELECT company_name FROM analytics.company_metrics LIMIT 3",
        _snapshot(),
    )
    assert validated.result_limit == 3
    assert validated.sql.endswith("LIMIT 3")
