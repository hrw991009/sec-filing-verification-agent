"""Skill selection pins versions, capabilities and the existing executable graph."""

from dataclasses import replace

import pytest

from industry_platform.modules.disclosures.profile import create_sec_l5_profile
from industry_platform.modules.research.domain import ResearchNode
from industry_platform.modules.skills.policy import bind_skill_policy
from industry_platform.modules.skills.registry import FILING_VERIFICATION, SKILL_REGISTRY


def test_skill_roles_cover_each_existing_graph_node_once() -> None:
    nodes = [node for role in FILING_VERIFICATION.roles for node in role.nodes]
    assert set(nodes) == set(ResearchNode)
    assert len(nodes) == len(set(nodes))


@pytest.mark.parametrize("version", ["latest", "v2", "", "V1"])
def test_unknown_versions_fail_closed(version: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        SKILL_REGISTRY.resolve(FILING_VERIFICATION.name, version)
    with pytest.raises(ValueError, match="unsupported"):
        SKILL_REGISTRY.from_harness(f"skill:{FILING_VERIFICATION.name}:{version}")


def test_persisted_identity_resolves_and_legacy_research_is_unaffected() -> None:
    assert SKILL_REGISTRY.from_harness(FILING_VERIFICATION.harness_version) == FILING_VERIFICATION
    assert SKILL_REGISTRY.from_harness("harness-research-v1") is None


def test_profile_removes_write_tools_preserves_model_and_tighter_limits() -> None:
    available = replace(
        create_sec_l5_profile(model="test-model").to_runtime_policy(),
        max_input_tokens=4_096,
        max_decision_output_tokens=512,
        max_tool_calls=3,
    )
    policy = bind_skill_policy(FILING_VERIFICATION, available)
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
    assert policy == bind_skill_policy(FILING_VERIFICATION, policy)


def test_binding_cannot_grant_a_tool_missing_from_current_surface() -> None:
    available = create_sec_l5_profile(model="test-model").to_runtime_policy()
    available = replace(available, available_tools=available.available_tools[1:])
    with pytest.raises(ValueError, match="unavailable Tools"):
        bind_skill_policy(FILING_VERIFICATION, available)
