"""Tests for the declarative Day 2 direct-answer profile."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace

import pytest

from industry_platform.modules.agent_harness.profiles import (
    DIRECT_ANSWER_PROFILE_SCHEMA_VERSION,
    DIRECT_ANSWER_TOOLSET_VERSION,
    DirectAnswerProfile,
    ProfileExecutionMode,
)
from industry_platform.modules.agent_runtime.domain import AgentRunType


def profile() -> DirectAnswerProfile:
    return DirectAnswerProfile(
        schema_version=DIRECT_ANSWER_PROFILE_SCHEMA_VERSION,
        profile_name="direct-answer",
        profile_version="v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/test-model",
        max_input_tokens=2_048,
        max_output_tokens=512,
        system_instructions="Answer the user's question directly and safely.",
    )


def test_direct_answer_is_explicitly_one_no_tool_baseline_call() -> None:
    configured = profile()

    assert configured.run_type is AgentRunType.DIRECT_ANSWER
    assert configured.execution_mode is ProfileExecutionMode.BASELINE_MODEL_RUN
    assert configured.model_call_limit == 1
    assert configured.allows_implicit_retry is False
    assert configured.available_tools == ()
    assert configured.toolset_version == DIRECT_ANSWER_TOOLSET_VERSION
    assert configured.final_output_format == "markdown"


def test_profile_is_immutable_and_hides_instructions() -> None:
    sensitive_instruction = "Do not disclose the private context projection."
    configured = replace(profile(), system_instructions=sensitive_instruction)

    assert sensitive_instruction not in repr(configured)
    with pytest.raises(FrozenInstanceError):
        configured.__setattr__("model_call_limit", 2)


@pytest.mark.parametrize(
    ("make_invalid", "message"),
    [
        (
            lambda configured: replace(configured, profile_name="another-profile"),
            "profile name",
        ),
        (lambda configured: replace(configured, model=" invalid model "), "model"),
        (
            lambda configured: replace(configured, max_input_tokens=0),
            "max input tokens",
        ),
        (
            lambda configured: replace(configured, max_output_tokens=0),
            "max output tokens",
        ),
        (
            lambda configured: replace(configured, system_instructions="   "),
            "system instructions",
        ),
    ],
)
def test_profile_rejects_values_that_would_change_l0_semantics(
    make_invalid: Callable[[DirectAnswerProfile], DirectAnswerProfile],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_invalid(profile())
