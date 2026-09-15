"""Built-in research tasks pin versions, capabilities and the fixed graph."""

from dataclasses import replace

import pytest

from industry_platform.modules.disclosures.profile import create_sec_l5_profile
from industry_platform.modules.disclosures.tool import sec_get_xbrl_facts_definition
from industry_platform.modules.financial_verification.tool import finance_calculate_definition
from industry_platform.modules.research.domain import ResearchNode
from industry_platform.modules.research.task_policy import (
    bind_research_task_policy,
    bind_research_tool_definitions,
)
from industry_platform.modules.research.tasks import (
    DUPONT_ANALYSIS,
    FILING_VERIFICATION,
    RESEARCH_TASKS,
    ResearchTaskDefinition,
)


def test_skill_roles_cover_each_existing_graph_node_once() -> None:
    nodes = [node for role in FILING_VERIFICATION.roles for node in role.nodes]
    assert set(nodes) == set(ResearchNode)
    assert len(nodes) == len(set(nodes))


@pytest.mark.parametrize("version", ["latest", "v2", "", "V1"])
def test_unknown_versions_fail_closed(version: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        RESEARCH_TASKS.resolve(FILING_VERIFICATION.name, version)
    with pytest.raises(ValueError, match="unsupported"):
        RESEARCH_TASKS.from_harness(f"skill:{FILING_VERIFICATION.name}:{version}")


def test_persisted_identity_resolves_and_legacy_research_is_unaffected() -> None:
    assert RESEARCH_TASKS.from_harness(FILING_VERIFICATION.harness_version) == FILING_VERIFICATION
    assert RESEARCH_TASKS.from_harness("harness-research-v1") is None


@pytest.mark.parametrize("definition", [FILING_VERIFICATION, DUPONT_ANALYSIS])
def test_historical_task_identity_is_preserved_on_rebind(
    definition: ResearchTaskDefinition,
) -> None:
    legacy = f"skill:{definition.name}:{definition.version}"
    assert RESEARCH_TASKS.from_harness(legacy) == definition
    available = create_sec_l5_profile(model="test-model").to_runtime_policy()
    policy = bind_research_task_policy(definition, available, persisted_harness_version=legacy)
    assert policy.profile_version == legacy
    assert bind_research_task_policy(definition, policy, persisted_harness_version=legacy) == policy
    with pytest.raises(ValueError, match="identity"):
        bind_research_task_policy(
            definition, available, persisted_harness_version="harness-research-v1"
        )


def test_profile_removes_write_tools_preserves_model_and_tighter_limits() -> None:
    available = replace(
        create_sec_l5_profile(model="test-model").to_runtime_policy(),
        max_input_tokens=4_096,
        max_decision_output_tokens=512,
        max_tool_calls=3,
    )
    policy = bind_research_task_policy(FILING_VERIFICATION, available)
    assert {tool.name for tool in policy.available_tools} == {
        "knowledge_search",
        "finance.calculate",
        "sec.search_filing",
        "sec.read_filing_section",
        "sec.get_xbrl_facts",
        "sec.diff_filings",
    }
    assert policy.model == available.model
    assert policy.max_input_tokens == 4_096
    assert policy.max_decision_output_tokens == 512
    assert policy.max_tool_calls == 3
    assert policy == bind_research_task_policy(FILING_VERIFICATION, policy)


def test_binding_cannot_grant_a_tool_missing_from_current_surface() -> None:
    available = create_sec_l5_profile(model="test-model").to_runtime_policy()
    available = replace(available, available_tools=available.available_tools[1:])
    with pytest.raises(ValueError, match="unavailable Tools"):
        bind_research_task_policy(FILING_VERIFICATION, available)


def test_dupont_rebind_preserves_identity_graph_and_read_only_capabilities() -> None:
    assert RESEARCH_TASKS.from_harness(DUPONT_ANALYSIS.harness_version) == DUPONT_ANALYSIS
    assert DUPONT_ANALYSIS.roles == FILING_VERIFICATION.roles
    available = create_sec_l5_profile(model="test-model").to_runtime_policy()
    policy = bind_research_task_policy(DUPONT_ANALYSIS, available)
    assert policy == bind_research_task_policy(DUPONT_ANALYSIS, policy)
    assert policy.profile_version == "research-task:sec.dupont-analysis:v1"
    assert {tool.name for tool in policy.available_tools} == {
        "sec.get_xbrl_facts",
        "finance.calculate",
    }
    assert "purpose=dupont" in policy.system_instructions
    assert "opening assets" in policy.system_instructions


def test_dupont_model_interface_pins_purpose_precision_and_operator() -> None:
    original = (sec_get_xbrl_facts_definition(), finance_calculate_definition())
    bound = bind_research_tool_definitions(DUPONT_ANALYSIS.harness_version, original)
    assert set(bound[0].input_schema["properties"]) == {"purpose"}  # type: ignore[call-overload]
    assert bound[1].input_schema["properties"]["operator"]["const"] == "dupont"  # type: ignore[index]
    assert original[0].input_schema != bound[0].input_schema
    assert bind_research_tool_definitions(FILING_VERIFICATION.harness_version, original) == original
