"""Typed commands for atomically accepting one direct-answer turn."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from industry_platform.modules.agent_runtime.context import MAX_CONTEXT_QUESTION_LENGTH
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    require_non_nil_uuid,
    require_utc,
)
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L2_RUNTIME_VERSION,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import JobRequestFingerprint, PreparedJobSubmission
from industry_platform.modules.research.domain import (
    RESEARCH_RUNTIME_VERSION,
    ResearchBriefInput,
)

MAX_CONVERSATION_TITLE_LENGTH: Final = 160
MAX_USER_MESSAGE_LENGTH: Final = MAX_CONTEXT_QUESTION_LENGTH
MAX_TURN_ATTACHMENTS: Final = 4
CONVERSATION_WEB_TOOL_CALL_LIMIT: Final = 2
DIRECT_ANSWER_TASK_NAME: Final = "agent.run.direct_answer"
DIRECT_ANSWER_QUEUE_NAME: Final = "agents"
_RUN_ID_NAMESPACE: Final = UUID("d1da4f86-ae26-4a35-b444-508e6f51010a")
_TURN_FINGERPRINT_DOMAIN: Final = b"industry-platform:direct-answer-turn:v1\x00"
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")


class TurnSearchMode(StrEnum):
    """Search capability requested for one immutable turn snapshot."""

    NONE = "none"
    WEB = "web"
    LOCAL = "local"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class StartDirectAnswerTurn:
    """Trusted application request that keeps the raw user input out of Job payloads."""

    workspace_id: UUID
    user_id: UUID
    trace_id: TraceId
    budget: RunBudget
    runtime_version: str
    harness_version: str
    idempotency_key: str = field(repr=False)
    question: str = field(repr=False)
    conversation_id: UUID | None = None
    new_conversation_title: str | None = None
    search_mode: TurnSearchMode = TurnSearchMode.NONE
    industry_id: UUID | None = None
    knowledge_base_ids: tuple[UUID, ...] = ()
    attachment_ids: tuple[UUID, ...] = ()
    research_brief: ResearchBriefInput | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.workspace_id, "Turn Workspace ID"),
            (self.user_id, "Turn user ID"),
        ):
            require_non_nil_uuid(value, field_name=field_name)
        if self.conversation_id is not None:
            require_non_nil_uuid(self.conversation_id, field_name="Conversation ID")
        if self.conversation_id is not None and self.new_conversation_title is not None:
            raise ValueError("An existing conversation cannot declare a new title")
        if self.new_conversation_title is not None:
            _require_bounded_text(
                self.new_conversation_title,
                maximum=MAX_CONVERSATION_TITLE_LENGTH,
                field_name="Conversation title",
            )
            if (
                self.new_conversation_title != self.new_conversation_title.strip()
                or "\n" in self.new_conversation_title
                or "\r" in self.new_conversation_title
            ):
                raise ValueError("Conversation title is invalid")
        _require_bounded_text(
            self.question,
            maximum=MAX_USER_MESSAGE_LENGTH,
            field_name="User message",
        )
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 200:
            raise ValueError("Turn idempotency key is invalid")
        _require_version(self.runtime_version, field_name="Runtime version")
        _require_version(self.harness_version, field_name="Harness version")
        if self.search_mode not in {TurnSearchMode.NONE, TurnSearchMode.WEB}:
            raise ValueError("Only search modes 'none' and 'web' are ready")
        if self.industry_id is not None:
            require_non_nil_uuid(self.industry_id, field_name="Turn industry ID")
        if self.search_mode is TurnSearchMode.WEB and self.industry_id is None:
            raise ValueError("Web search mode requires one industry snapshot")
        knowledge_base_ids = tuple(self.knowledge_base_ids)
        if len(set(knowledge_base_ids)) != len(knowledge_base_ids):
            raise ValueError("Turn knowledge-base IDs must be unique")
        for knowledge_base_id in knowledge_base_ids:
            require_non_nil_uuid(knowledge_base_id, field_name="Turn knowledge-base ID")
        if knowledge_base_ids:
            raise ValueError("Local knowledge mode is not ready on Day 2")
        object.__setattr__(self, "knowledge_base_ids", knowledge_base_ids)
        attachment_ids = tuple(self.attachment_ids)
        if len(attachment_ids) > MAX_TURN_ATTACHMENTS:
            raise ValueError("Turn attachment limit exceeded")
        if len(set(attachment_ids)) != len(attachment_ids):
            raise ValueError("Turn attachment IDs must be unique")
        for attachment_id in attachment_ids:
            require_non_nil_uuid(attachment_id, field_name="Turn attachment ID")
        object.__setattr__(self, "attachment_ids", attachment_ids)
        if self.research_brief is not None:
            if self.search_mode is not TurnSearchMode.WEB:
                raise ValueError("Research requires the Web Tool surface")
            if self.research_brief.original_question != self.question.strip():
                raise ValueError("Research Brief cannot rewrite the original question")


@dataclass(frozen=True, slots=True)
class PreparedDirectAnswerTurn:
    """All rows needed for one atomic Conversation/Run/Job submission."""

    conversation_id: UUID
    create_conversation: bool
    conversation_title: str | None
    turn_id: UUID
    user_message_id: UUID
    run: AgentRun
    job: PreparedJobSubmission
    question: str = field(repr=False)
    search_mode: TurnSearchMode = TurnSearchMode.NONE
    industry_id: UUID | None = None
    knowledge_base_ids: tuple[UUID, ...] = ()
    attachment_ids: tuple[UUID, ...] = ()
    research_brief: ResearchBriefInput | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.conversation_id, "Prepared conversation ID"),
            (self.turn_id, "Prepared turn ID"),
            (self.user_message_id, "Prepared message ID"),
        ):
            require_non_nil_uuid(value, field_name=field_name)
        if self.create_conversation != (self.conversation_title is not None):
            raise ValueError("New conversation rows require exactly one title")
        if self.run.thread_id != self.conversation_id or self.run.turn_id != self.turn_id:
            raise ValueError("Prepared Run does not reference its Conversation and Turn")
        if self.run.job_id != self.job.job_id:
            raise ValueError("Prepared Run and Job do not reference each other")
        if self.run.workspace_id != self.job.scope.workspace_id:
            raise ValueError("Prepared Run and Job Workspace do not match")
        _require_bounded_text(
            self.question,
            maximum=MAX_USER_MESSAGE_LENGTH,
            field_name="Prepared user message",
        )
        attachment_ids = tuple(self.attachment_ids)
        if len(attachment_ids) > MAX_TURN_ATTACHMENTS:
            raise ValueError("Prepared attachment limit exceeded")
        if len(set(attachment_ids)) != len(attachment_ids):
            raise ValueError("Prepared attachment IDs must be unique")
        for attachment_id in attachment_ids:
            require_non_nil_uuid(attachment_id, field_name="Prepared attachment ID")
        object.__setattr__(self, "attachment_ids", attachment_ids)
        if (self.run.run_type is AgentRunType.RESEARCH) != (self.research_brief is not None):
            raise ValueError("Prepared Research facts do not match the Agent Run type")


@dataclass(frozen=True, slots=True)
class DirectAnswerTurnReceipt:
    """Committed IDs returned by the 202 application boundary."""

    conversation_id: UUID
    turn_id: UUID
    user_message_id: UUID
    run_id: UUID
    job_id: UUID
    outbox_event_id: UUID
    created: bool


type IdSource = Callable[[], UUID]


def deterministic_run_id(*, workspace_id: UUID, idempotency_key: str) -> UUID:
    """Keep Job payload stable when the same HTTP idempotency key is retried."""

    if not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ValueError("Turn idempotency key is invalid")
    return uuid5(_RUN_ID_NAMESPACE, f"{workspace_id}:{idempotency_key}")


def fingerprint_direct_answer_turn(
    command: StartDirectAnswerTurn, *, run_id: UUID
) -> JobRequestFingerprint:
    """Detect changed retries without putting the user's message in the Job payload."""

    document = {
        "budget": {
            "max_cost_micro_usd": command.budget.max_cost_micro_usd,
            "max_steps": command.budget.max_steps,
            "max_total_tokens": command.budget.max_total_tokens,
            "schema_version": command.budget.schema_version,
        },
        "conversation_id": (
            str(command.conversation_id) if command.conversation_id is not None else None
        ),
        "harness_version": command.harness_version,
        "industry_id": str(command.industry_id) if command.industry_id is not None else None,
        "knowledge_base_ids": sorted(str(value) for value in command.knowledge_base_ids),
        "attachment_ids": [str(value) for value in command.attachment_ids],
        "new_conversation_title": command.new_conversation_title,
        "question": command.question,
        "research_brief": (
            None
            if command.research_brief is None
            else {
                "original_question": command.research_brief.original_question,
                "confirmed_scope": list(command.research_brief.confirmed_scope),
                "exclusions": list(command.research_brief.exclusions),
                "completion_criteria": list(command.research_brief.completion_criteria),
            }
        ),
        "run_id": str(run_id),
        "runtime_version": command.runtime_version,
        "search_mode": command.search_mode.value,
        "user_id": str(command.user_id),
        "workspace_id": str(command.workspace_id),
    }
    encoded = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return JobRequestFingerprint(hashlib.sha256(_TURN_FINGERPRINT_DOMAIN + encoded).digest())


def build_queued_run(
    command: StartDirectAnswerTurn,
    *,
    run_id: UUID,
    stream_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
    job_id: UUID,
    created_at: datetime,
) -> AgentRun:
    """Build the exact queued Run persisted beside the user's message."""

    require_utc(created_at, field_name="Run creation time")
    run_type = (
        AgentRunType.RESEARCH
        if command.research_brief is not None
        else AgentRunType.TOOL_LOOP
        if command.search_mode is TurnSearchMode.WEB
        else AgentRunType.DIRECT_ANSWER
    )
    expected_runtime_version = (
        RESEARCH_RUNTIME_VERSION
        if run_type is AgentRunType.RESEARCH
        else TOOL_L2_RUNTIME_VERSION
        if run_type is AgentRunType.TOOL_LOOP
        else "direct-answer-runtime-v0"
    )
    if command.runtime_version != expected_runtime_version:
        raise ValueError("Turn Runtime version does not match its search mode")
    return AgentRun(
        schema_version=1,
        run_id=run_id,
        event_stream_id=stream_id,
        workspace_id=command.workspace_id,
        user_id=command.user_id,
        run_type=run_type,
        runtime_version=command.runtime_version,
        harness_version=command.harness_version,
        budget=command.budget,
        trace_id=command.trace_id,
        status=AgentRunStatus.QUEUED,
        state_revision=0,
        created_at=created_at,
        started_at=None,
        terminal_at=None,
        stop_reason=None,
        thread_id=conversation_id,
        turn_id=turn_id,
        job_id=job_id,
    )


def _require_bounded_text(value: str, *, maximum: int, field_name: str) -> None:
    if not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def derive_conversation_title(question: str) -> str:
    """Create a stable one-line title from the first user message."""

    _require_bounded_text(
        question,
        maximum=MAX_USER_MESSAGE_LENGTH,
        field_name="User message",
    )
    normalized = " ".join(question.split())
    if len(normalized) <= MAX_CONVERSATION_TITLE_LENGTH:
        return normalized
    prefix = normalized[: MAX_CONVERSATION_TITLE_LENGTH - 3].rstrip()
    return f"{prefix}..."


def _require_version(value: str, *, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
