"""Versioned execution profiles consumed by the unified Agent Runtime."""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    MAX_RUN_TOKENS,
    AgentRunType,
)
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL1RuntimePolicy
from industry_platform.modules.tools.domain import ToolReference, tool_references

DIRECT_ANSWER_PROFILE_SCHEMA_VERSION: Final = 1
DIRECT_ANSWER_TOOLSET_VERSION: Final = "toolset-none-v1"

_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_MAX_SYSTEM_INSTRUCTIONS_LENGTH: Final = 20_000


class ProfileExecutionMode(StrEnum):
    """Whether a profile is a baseline or a bounded Agent loop."""

    BASELINE_MODEL_RUN = "baseline_model_run"
    BOUNDED_TOOL_LOOP = "bounded_tool_loop"


@dataclass(frozen=True, slots=True)
class DirectAnswerProfile:
    """L0 policy: one no-tool model call whose final answer is Markdown."""

    schema_version: int
    profile_name: str
    profile_version: str
    prompt_version: str
    context_compiler_version: str
    output_contract_version: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    system_instructions: str = field(repr=False)
    run_type: AgentRunType = field(default=AgentRunType.DIRECT_ANSWER, init=False)
    execution_mode: ProfileExecutionMode = field(
        default=ProfileExecutionMode.BASELINE_MODEL_RUN,
        init=False,
    )
    model_call_limit: int = field(default=1, init=False)
    allows_implicit_retry: bool = field(default=False, init=False)
    toolset_version: str = field(default=DIRECT_ANSWER_TOOLSET_VERSION, init=False)
    available_tools: tuple[()] = field(default=(), init=False)
    final_output_format: str = field(default="markdown", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or (
            self.schema_version != DIRECT_ANSWER_PROFILE_SCHEMA_VERSION
            or self.schema_version != AGENT_RUNTIME_SCHEMA_VERSION
        ):
            raise ValueError(
                f"Direct-answer profile schema version must be "
                f"{DIRECT_ANSWER_PROFILE_SCHEMA_VERSION}"
            )
        if self.profile_name != "direct-answer":
            raise ValueError("Direct-answer profile name is invalid")
        for value, field_name in (
            (self.profile_version, "Direct-answer profile version"),
            (self.prompt_version, "Direct-answer prompt version"),
            (self.context_compiler_version, "Context Compiler version"),
            (self.output_contract_version, "Final output contract version"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise ValueError("Direct-answer model is invalid")
        for token_limit, field_name in (
            (self.max_input_tokens, "Direct-answer max input tokens"),
            (self.max_output_tokens, "Direct-answer max output tokens"),
        ):
            if isinstance(token_limit, bool) or not 1 <= token_limit <= MAX_RUN_TOKENS:
                raise ValueError(f"{field_name} is invalid")
        if (
            not self.system_instructions.strip()
            or len(self.system_instructions) > _MAX_SYSTEM_INSTRUCTIONS_LENGTH
        ):
            raise ValueError("Direct-answer system instructions are invalid")

    def to_runtime_policy(self) -> DirectAnswerRuntimePolicy:
        """Project Harness choices into the Runtime-owned no-tool execution policy."""

        return DirectAnswerRuntimePolicy(
            schema_version=self.schema_version,
            profile_version=self.profile_version,
            prompt_version=self.prompt_version,
            context_compiler_version=self.context_compiler_version,
            output_contract_version=self.output_contract_version,
            model=self.model,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            system_instructions=self.system_instructions,
        )


@dataclass(frozen=True, slots=True)
class ToolL1Profile:
    """L1 policy: two model calls around one exact allowlisted Tool."""

    schema_version: int
    profile_name: str
    profile_version: str
    prompt_version: str
    context_compiler_version: str
    output_contract_version: str
    toolset_version: str
    model: str
    max_input_tokens: int
    max_action_output_tokens: int
    max_final_output_tokens: int
    system_instructions: str = field(repr=False)
    available_tools: tuple[ToolReference, ...]
    run_type: AgentRunType = field(default=AgentRunType.TOOL_LOOP, init=False)
    execution_mode: ProfileExecutionMode = field(
        default=ProfileExecutionMode.BOUNDED_TOOL_LOOP,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.profile_name != "tool-l1":
            raise ValueError("Tool L1 profile name is invalid")
        # Runtime policy owns the complete validation and is also the projection.
        runtime_policy = self.to_runtime_policy()
        object.__setattr__(self, "available_tools", runtime_policy.available_tools)
        object.__setattr__(self, "system_instructions", runtime_policy.system_instructions)

    def to_runtime_policy(self) -> ToolL1RuntimePolicy:
        """Project Harness choices into the Runtime-owned L1 policy."""

        return ToolL1RuntimePolicy(
            schema_version=self.schema_version,
            profile_version=self.profile_version,
            prompt_version=self.prompt_version,
            context_compiler_version=self.context_compiler_version,
            output_contract_version=self.output_contract_version,
            toolset_version=self.toolset_version,
            model=self.model,
            max_input_tokens=self.max_input_tokens,
            max_action_output_tokens=self.max_action_output_tokens,
            max_final_output_tokens=self.max_final_output_tokens,
            system_instructions=self.system_instructions,
            available_tools=tool_references(self.available_tools),
        )
