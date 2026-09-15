"""Fail-closed tests for untrusted Observation normalization helpers."""

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.evidence.normalizer import (
    license_allows_evidence,
    parse_calculation_source_locator,
    parse_persisted_observation,
    parse_sec_resource_locator,
    parse_sql_source_locator,
    referenced_sql_columns,
    schema_columns_for_table,
    sec_xbrl_resource_version,
)

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
CALL_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_xbrl_resource_version_is_bounded_and_preserves_full_identity() -> None:
    source_version = "sec-xbrl-companyfacts-" + "a" * 24
    locator_key = "us-gaap:Revenue:" + "long-dimension|" * 40
    version = sec_xbrl_resource_version(source_version=source_version, locator_key=locator_key)
    assert len(source_version + ":" + locator_key) > 128
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}", version)
    assert version == sec_xbrl_resource_version(
        source_version=source_version, locator_key=locator_key
    )
    assert version != sec_xbrl_resource_version(
        source_version=source_version, locator_key=locator_key + "different-tail"
    )
    assert version != sec_xbrl_resource_version(
        source_version=source_version + "b", locator_key=locator_key
    )


OBSERVATION_ID = UUID("44444444-4444-4444-8444-444444444444")


def observation_document() -> dict[str, object]:
    text = "bounded normalized output"
    return {
        "schema_version": 1,
        "observation_id": str(OBSERVATION_ID),
        "call_id": str(CALL_ID),
        "tool_name": "industry.web_search",
        "tool_version": "v1",
        "normalizer_version": "tool-observation-v1",
        "model_text": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "observed_at": NOW.isoformat(),
        "sources": [
            {
                "source_type": "industry_public_source",
                "source_version": "api-v2-2026-08",
                "locator": "https://example.test/source",
                "observed_at": NOW.isoformat(),
                "content_sha256": "a" * 64,
            }
        ],
    }


def test_persisted_observation_is_rebuilt_as_the_day3_contract() -> None:
    observation = parse_persisted_observation(
        observation_document(), run_id=RUN_ID, workspace_id=WORKSPACE_ID
    )

    assert observation.call_id == CALL_ID
    assert observation.sources[0].locator == "https://example.test/source"
    with pytest.raises(ValueError, match="fields"):
        parse_persisted_observation(
            {**observation_document(), "raw_provider_response": "must not enter Evidence"},
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
        )


def test_sql_locator_schema_and_terms_are_fail_closed() -> None:
    parsed = parse_sql_source_locator(
        "sql://aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
        "public.sample_company_metrics/query-runs/"
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    )
    tables: list[dict[str, object]] = [
        {
            "schema_name": "public",
            "table_name": "sample_company_metrics",
            "columns": [{"name": "industry"}, {"name": "revenue"}],
        }
    ]

    assert parsed.table == "public.sample_company_metrics"
    assert schema_columns_for_table(tables, parsed.table) == ("industry", "revenue")
    assert referenced_sql_columns(
        "SELECT industry, SUM(revenue) AS total FROM public.sample_company_metrics "
        "GROUP BY industry",
        ("industry", "revenue", "employees"),
    ) == ("industry", "revenue")
    assert license_allows_evidence("Public metadata with attribution") is True
    assert license_allows_evidence("Evidence-use-prohibited") is False
    with pytest.raises(ValueError, match="locator"):
        parse_sql_source_locator(
            "sql://aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/public.sample_company_metrics"
        )


def test_internal_sec_resource_locator_rejects_external_or_ambiguous_identity() -> None:
    fact_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    assert (
        parse_sec_resource_locator(f"sec://xbrl-facts/{fact_id}", resource="xbrl-facts") == fact_id
    )
    with pytest.raises(ValueError, match="SEC Evidence"):
        parse_sec_resource_locator(f"https://www.sec.gov/{fact_id}", resource="xbrl-facts")


def test_calculation_locator_preserves_legacy_and_formal_sec_lineage() -> None:
    digest = "a" * 64

    assert parse_calculation_source_locator(f"fixture://finance-calculations/{digest}") == digest
    assert parse_calculation_source_locator(f"sec://financial-calculations/{digest}") == digest
    with pytest.raises(ValueError, match="Calculation Evidence"):
        parse_calculation_source_locator(f"https://example.test/{digest}")
