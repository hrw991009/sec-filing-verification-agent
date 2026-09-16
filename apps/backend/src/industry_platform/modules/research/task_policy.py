"""Bind a built-in financial research task to the fixed workflow's tool policy."""

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL2RuntimePolicy
from industry_platform.modules.disclosures.profile import create_sec_l4_profile
from industry_platform.modules.research.tasks import (
    DUPONT_MULTI_YEAR,
    RESEARCH_TASKS,
    ResearchTaskDefinition,
    is_dupont_task,
)
from industry_platform.modules.tools.domain import ToolDefinition

DUPONT_INSTRUCTIONS = """
Return one raw JSON object only, without code fences or text outside the object.
You execute sec.dupont-analysis@v1 for the locked annual 10-K scope and the immediately
preceding fiscal year. This is consolidated parent-equity three-factor DuPont, not
valuation or investment advice. First call sec.get_xbrl_facts with purpose=dupont
and no metric filters. The task binds that Tool to this sole argument: {"purpose":"dupont"}.
The server selects two annual periods and six ordered operands
per year: revenue, parent net income, opening assets, closing assets, opening parent
equity, closing parent equity. Copy each period's operands intact into finance.calculate
with operator=dupont, decimal_places=4 and rounding_mode=half_even; make one call per year.
Never compute financial values yourself or use substitute concepts/zero balances.
If inputs are missing/conflicting, or calculation rejects non-positive balances,
report the exact limitation and required source data. Do not present a partial year
as a completed two-year analysis. Do not repeat a failed call with invented inputs.
After both annual calculations, return exactly the final decision envelope:
{"decision":{"schema_version":1,"kind":"final","content_markdown":"Complete [T2S1] [T3S1]."}}
Replace the citation labels only if the actual calculation observation ordinals differ.
The decision kind is always "final", never a Research task name or analysis type.
Your content_markdown is an internal ASCII-only completion receipt, not the published
report. Do not generate Chinese prose, tables, numeric summaries, or extra JSON keys.
The host writer publishes the Chinese two-year table, input lineage, methodology,
factor directions and limitations directly from normalized calculation components.
If blocked, return a short ASCII limitation in the same final envelope when permitted
by the host schema. Missing prerequisites are limitations, not successful verification.
Do not create monitor subscriptions or invent causal business explanations.
""".strip()

DUPONT_MULTI_INSTRUCTIONS = """
Return one raw JSON object only, with no code fences or extra text.
Execute the locked 3-to-5-year consolidated parent-equity DuPont analysis.
First call sec.get_xbrl_facts with exactly {"purpose":"dupont"}.
The host returns the requested annual periods, each with six ordered operands:
revenue, parent net income, opening assets, closing assets, opening parent equity,
closing parent equity. Copy each period's operands intact to finance.calculate,
operator=dupont, decimal_places=4, rounding_mode=half_even, exactly once per period.
Never compute values yourself, invent missing operands, mix periods, or repeat a call.
After all periods are calculated, return the final envelope with an ASCII completion
receipt citing every actual calculation source, e.g. Complete [T2S1] [T3S1] [T4S1].
Use exactly this JSON envelope:
{"decision":{"schema_version":1,"kind":"final","content_markdown":"Complete [T2S1]."}}
with all actual calculation labels substituted. Never write tables, numeric summaries,
Chinese prose or extra JSON keys in this internal receipt. Do not retry rejected inputs.
The host publishes numeric tables and visualizations from calculation Evidence.
Missing/conflicting inputs or rejected balances are limitations, not completed analysis.
Do not invent business causality or create monitor subscriptions.
""".strip()


def bind_research_task_policy(
    definition: ResearchTaskDefinition,
    available: ToolL2RuntimePolicy,
    *,
    persisted_harness_version: str | None = None,
) -> ToolL2RuntimePolicy:
    registered = RESEARCH_TASKS.resolve(definition.name, definition.version)
    if definition != registered:
        raise ValueError("Research task definition does not match the Registry")
    baseline = create_sec_l4_profile(model=available.model).to_runtime_policy()
    harness_version = persisted_harness_version or definition.harness_version
    if RESEARCH_TASKS.from_harness(harness_version) != definition:
        raise ValueError("Persisted research task identity does not match its definition")
    dupont = is_dupont_task(definition)
    references = tuple(
        tool
        for tool in baseline.available_tools
        if not dupont or tool.name in {"sec.get_xbrl_facts", "finance.calculate"}
    )
    if not set(references).issubset(available.available_tools):
        raise ValueError("Research task requires unavailable Tools")
    return replace(
        baseline,
        available_tools=references,
        profile_version=harness_version,
        prompt_version="sec-dupont-analysis-prompt-v3"
        if definition == DUPONT_MULTI_YEAR
        else "sec-dupont-analysis-prompt-v2"
        if dupont
        else "sec-filing-verification-prompt-v2",
        system_instructions=DUPONT_MULTI_INSTRUCTIONS
        if definition == DUPONT_MULTI_YEAR
        else DUPONT_INSTRUCTIONS
        if dupont
        else baseline.system_instructions
        + (
            "\nReturn one raw JSON decision object, without code fences. "
            "Answer only the requested financial period. Comparative historical values in "
            "a filing do not expand the locked scope: do not add or cite other periods "
            "unless the user requested a supported comparison. For a current-period fact, "
            "select the duration end_date or balance instant matching report_period. "
            "For a calculation, retrieve each known concept in its own targeted query; "
            "do not collect an unfiltered list of unrelated metrics. "
            "Keep the final finding concise and cite the actual matching observation label."
        ),
        max_input_tokens=min(baseline.max_input_tokens, available.max_input_tokens),
        max_decision_output_tokens=min(
            baseline.max_decision_output_tokens, available.max_decision_output_tokens
        ),
        max_tool_calls=min(baseline.max_tool_calls, available.max_tool_calls),
    )


def bind_research_tool_definitions(
    harness_version: str, definitions: tuple[ToolDefinition, ...]
) -> tuple[ToolDefinition, ...]:
    """Expose only task-variable arguments; execution still uses the real Registry.

    DuPont has a fixed selection purpose, formula and precision. Keeping unrelated
    filters out of its model interface prevents accidental partial selection.
    """
    if not is_dupont_task(RESEARCH_TASKS.from_harness(harness_version)):
        return definitions
    result = []
    for definition in definitions:
        if definition.name == "sec.get_xbrl_facts":
            schema: dict[str, object] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["purpose"],
                "properties": {"purpose": {"type": "string", "const": "dupont"}},
            }
        elif definition.name == "finance.calculate":
            schema = dict(definition.input_schema)
            properties = dict(cast(Mapping[str, object], schema["properties"]))
            properties.update(
                {
                    "operator": {"type": "string", "const": "dupont"},
                    "decimal_places": {"type": "integer", "const": 4},
                }
            )
            schema["properties"] = properties
        else:
            raise ValueError("DuPont Tool surface must contain only retrieval and calculation")
        result.append(replace(definition, input_schema=schema))
    return tuple(result)
