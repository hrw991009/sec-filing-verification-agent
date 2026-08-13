"""Stable command and no-tool policy consumed by the Direct Answer Runtime."""

import re
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.context import (
    CONTEXT_COMPILER_V0,
    MAX_CONTEXT_QUESTION_LENGTH,
    MAX_CONTEXT_SUMMARY_LENGTH,
)
from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    require_current_schema_version,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.final_output import (
    FINAL_MARKDOWN_CONTRACT_VERSION,
)
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state

DIRECT_ANSWER_RUNTIME_VERSION: Final = "direct-answer-runtime-v0"

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


@dataclass(frozen=True, slots=True)
class DirectAnswerRuntimePolicy:
    """Runtime-owned projection of one Harness profile; it contains no tools."""

    schema_version: int
    profile_version: str
    prompt_version: str
    context_compiler_version: str
    output_contract_version: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    system_instructions: str = field(repr=False)
    model_call_limit: int = field(default=1, init=False)
    allows_tools: bool = field(default=False, init=False)
    allows_implicit_retry: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for value, field_name in (
            (self.profile_version, "Runtime policy profile version"),
            (self.prompt_version, "Runtime policy prompt version"),
            (self.context_compiler_version, "Runtime policy Context Compiler version"),
            (self.output_contract_version, "Runtime policy output contract version"),
        ):
            if not _VERSION_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        if self.context_compiler_version != CONTEXT_COMPILER_V0:
            raise ValueError("Direct Answer requires Context Compiler v0")
        if self.output_contract_version != FINAL_MARKDOWN_CONTRACT_VERSION:
            raise ValueError("Direct Answer final output contract is unsupported")
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise ValueError("Runtime policy model is invalid")
        for token_limit, field_name in (
            (self.max_input_tokens, "Runtime policy max input tokens"),
            (self.max_output_tokens, "Runtime policy max output tokens"),
        ):
            if isinstance(token_limit, bool) or not 1 <= token_limit <= MAX_RUN_TOKENS:
                raise ValueError(f"{field_name} is invalid")
        if not self.system_instructions.strip() or len(self.system_instructions) > 20_000:
            raise ValueError("Runtime policy system instructions are invalid")


@dataclass(frozen=True, slots=True)
class DirectAnswerRunCommand:
    """All stable IDs and user data needed to execute one queued L0 Run."""

    run: AgentRun
    state: RunState
    policy: DirectAnswerRuntimePolicy
    model_step_id: UUID
    final_step_id: UUID
    manifest_id: UUID
    user_question: str = field(repr=False)
    conversation_summary: str | None = field(default=None, repr=False)
    conversation_summary_version: str | None = None

    def __post_init__(self) -> None:
        validate_run_state(self.run, self.state)
        if (
            self.run.run_type is not AgentRunType.DIRECT_ANSWER
            or self.run.status is not AgentRunStatus.QUEUED
            or self.state.status is not AgentRunStatus.QUEUED
            or self.state.revision != 0
            or self.state.step_count != 0
            or self.state.event_count != 1
        ):
            raise ValueError("Direct Answer Runtime requires a fresh queued Run")
        if self.run.runtime_version not in {
            DIRECT_ANSWER_RUNTIME_VERSION,
            "runtime-v0",
        }:
            raise ValueError("Run runtime version does not match Direct Answer Runtime")
        if self.run.budget.max_steps < 2:
            raise ValueError("Direct Answer requires one Model Step and one Final Step")
        for identifier, field_name in (
            (self.model_step_id, "Direct Answer Model Step ID"),
            (self.final_step_id, "Direct Answer Final Step ID"),
            (self.manifest_id, "Direct Answer Context manifest ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if len({self.model_step_id, self.final_step_id, self.manifest_id}) != 3:
            raise ValueError("Direct Answer execution IDs must be distinct")
        question = self.user_question.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not question or len(question) > MAX_CONTEXT_QUESTION_LENGTH:
            raise ValueError("Direct Answer user question is invalid")
        object.__setattr__(self, "user_question", question)
        if self.conversation_summary is None:
            if self.conversation_summary_version is not None:
                raise ValueError("A missing conversation summary cannot have a version")
        else:
            summary = self.conversation_summary.replace("\r\n", "\n").replace("\r", "\n").strip()
            if (
                not summary
                or len(summary) > MAX_CONTEXT_SUMMARY_LENGTH
                or self.conversation_summary_version is None
                or not _VERSION_PATTERN.fullmatch(self.conversation_summary_version)
            ):
                raise ValueError("Direct Answer conversation summary is invalid")
            object.__setattr__(self, "conversation_summary", summary)
