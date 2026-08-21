"""Minimal ResearchRun shell used to give Day 4 Claims a real owner."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.identity.domain import TraceId


class ResearchRunStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CreateResearchRun:
    agent_run_id: UUID
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.agent_run_id, field_name="Research Agent Run ID")


@dataclass(frozen=True, slots=True)
class ResearchRun:
    research_run_id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    agent_run_id: UUID
    status: ResearchRunStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.research_run_id, "Research Run ID"),
            (self.workspace_id, "Research Workspace ID"),
            (self.owner_user_id, "Research owner ID"),
            (self.agent_run_id, "Research Agent Run ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Research Run revision is invalid")
        require_utc(self.created_at, field_name="Research Run creation time")
        require_utc(self.updated_at, field_name="Research Run update time")
