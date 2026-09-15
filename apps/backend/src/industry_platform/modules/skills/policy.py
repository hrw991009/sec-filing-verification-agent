"""Bind registered Skill metadata to the existing executable SEC profile."""

from dataclasses import replace

from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL2RuntimePolicy
from industry_platform.modules.disclosures.profile import create_sec_l4_profile
from industry_platform.modules.skills.registry import SKILL_REGISTRY, SkillDefinition


def bind_skill_policy(
    definition: SkillDefinition, available: ToolL2RuntimePolicy
) -> ToolL2RuntimePolicy:
    registered = SKILL_REGISTRY.resolve(definition.name, definition.version)
    if definition != registered:
        raise ValueError("Skill definition does not match the Registry")
    baseline = create_sec_l4_profile(model=available.model).to_runtime_policy()
    if not set(baseline.available_tools).issubset(available.available_tools):
        raise ValueError("Skill requires unavailable Tools")
    return replace(
        baseline,
        profile_version=definition.harness_version,
        prompt_version="sec-filing-verification-prompt-v1",
        max_input_tokens=min(baseline.max_input_tokens, available.max_input_tokens),
        max_decision_output_tokens=min(
            baseline.max_decision_output_tokens, available.max_decision_output_tokens
        ),
        max_tool_calls=min(baseline.max_tool_calls, available.max_tool_calls),
    )
