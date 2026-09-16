"""Deterministic current-product acceptance checks; never relabel outages as refusals."""

import re
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

# Independently transcribed consolidated USD-million anchors from MSFT annual filings.
# Ordered revenue, parent income, opening/closing assets, opening/closing parent equity.
OFFICIAL_INPUTS = {
    2021: (168088, 61271, 301311, 333779, 118304, 141988),
    2022: (198270, 72738, 333779, 364840, 141988, 166542),
    2023: (211915, 72361, 364840, 411976, 166542, 206223),
    2024: (245122, 88136, 411976, 512163, 206223, 268477),
    2025: (281724, 101832, 512163, 619003, 268477, 343479),
}
INPUT_KEYS = (
    "revenue",
    "net_income",
    "opening_assets",
    "closing_assets",
    "opening_equity",
    "closing_equity",
)


def official_values(year: int) -> dict[str, Decimal]:
    """Independent Decimal oracle, not the production finance calculator."""
    with localcontext() as context:
        context.prec = 50
        values = dict(zip(INPUT_KEYS, map(Decimal, OFFICIAL_INPUTS[year]), strict=True))
        assets = (values["opening_assets"] + values["closing_assets"]) / 2
        equity = (values["opening_equity"] + values["closing_equity"]) / 2
        for key, value in {
            "average_assets": assets,
            "average_equity": equity,
            "net_profit_margin_percent": values["net_income"] / values["revenue"] * 100,
            "asset_turnover": values["revenue"] / assets,
            "equity_multiplier": assets / equity,
            "roe_percent": values["net_income"] / equity * 100,
        }.items():
            values[key] = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        return values


def _annual_results_match(result: dict[str, Any], years: int) -> bool:
    selected = list(range(2026 - years, 2026))
    if result.get("periods") != [f"{year}-06-30" for year in selected]:
        return False
    rows = {row["key"]: row for row in result.get("rows", [])}
    if set(rows) != set(official_values(2025)):
        return False
    try:
        for key, row in rows.items():
            values = row["values"]
            if len(values) != years or not row.get("unit"):
                return False
            for index, year in enumerate(selected):
                expected = official_values(year)[key]
                cell = values[index]
                if Decimal(cell["value"]) != expected or not cell["evidence_refs"]:
                    return False
                change = cell.get("change")
                if index == 0:
                    if change is not None:
                        return False
                elif change is None or Decimal(change) != expected - official_values(year - 1)[key]:
                    return False
        return True
    except (KeyError, TypeError, InvalidOperation):
        return False


def check_case(case: dict[str, Any], record: dict[str, Any]) -> list[str]:
    errors = []
    negative = case["mode"] == "unavailable"
    if record["status"] != ("failed" if negative else "completed"):
        errors.append("run_not_completed")
    if record["model_calls"] < 1:
        errors.append("no_real_model_call")
    if record["input_tokens"] + record["output_tokens"] > record["max_total_tokens"]:
        errors.append("token_budget_exceeded")
    if (
        record["step_count"] > record["max_steps"]
        or record["cost_micro_usd"] > record["max_cost_micro_usd"]
    ):
        errors.append("execution_budget_exceeded")
    if record["terminal_at"] is None or datetime.fromisoformat(
        record["terminal_at"]
    ) > datetime.fromisoformat(record["deadline"]):
        errors.append("terminal_deadline_exceeded")
    if record["terminal_event_count"] != 1:
        errors.append("terminal_event_count")
    observations = record["observations"]
    tools = [item["tool"] for item in observations]
    if not negative and any(
        item["event_type"] in ("agent.tool.failed", "agent.step.failed")
        for item in record["errors"]
    ):
        errors.append("failed_execution_step")
    if case["mode"] == "l2":
        expected = [] if case.get("skill") is None else [case["skill"]]
        actual = [item.get("skill") for item in observations if item["tool"] == "skill.read"]
        if actual != expected or tools != ["skill.read"] * len(expected):
            errors.append("skill_selection_or_tool_surface")
        if record["harness_version"] != "conversation-l2-skills-v1":
            errors.append("wrong_l2_harness")
    else:
        if "skill.read" in tools or any(
            tool
            not in {
                "sec.get_xbrl_facts",
                "finance.calculate",
                "sec.search_filing",
                "sec.read_filing_section",
                "knowledge_search",
            }
            for tool in tools
        ):
            errors.append("research_tool_surface")
        if "sec.get_xbrl_facts" not in (record.get("tool_requests", []) if negative else tools):
            errors.append("required_xbrl_missing")
        if case["mode"] == "unavailable":
            if record.get("stop_reason") != "tool_error" or {
                item["code"] for item in record["errors"]
            } != {case["expected_error"]}:
                errors.append("unexpected_boundary_failure")
            if record.get("verification_status") == "verified" or any(
                item.get("source_count", 0) for item in observations
            ):
                errors.append("future_source_leak")
        elif record.get("verification_status") != "verified":
            errors.append("research_not_verified")
        if case["mode"] == "dupont":
            result = record.get("result_view", {})
            if (
                result.get("status") != "ready"
                or result.get("verification_status") != "verified"
                or len(result.get("periods", [])) != case["years"]
                or len(result.get("rows", [])) != 12
            ):
                errors.append("result_view_contract")
            if tools.count("finance.calculate") != case["years"]:
                errors.append("annual_calculation_count")
            if not _annual_results_match(result, case["years"]):
                errors.append("official_annual_values_or_lineage")
        if case.get("metric") == "margin" and "finance.calculate" not in tools:
            errors.append("missing_calculation")
        if case.get("metric") == "margin" and not any(
            item.get("result") == "36.1460" for item in observations
        ):
            errors.append("net_margin_gold_mismatch")
    if any(
        re.search(pattern, record["answer"], re.IGNORECASE) is None
        for pattern in case["answer_patterns"]
    ):
        errors.append("answer_contract")
    return errors
