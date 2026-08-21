"""Strict HTTP contracts for Memory candidates, decisions, and revisions."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from industry_platform.modules.memory.domain import (
    MAX_MEMORY_CONTENT_LENGTH,
    MAX_MEMORY_SOURCE_MESSAGES,
    MemoryCandidateStatus,
    MemoryKind,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryRevisionValidity,
    MemoryScope,
    MemoryStatus,
    MemoryWriteAction,
)

MemoryContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH),
]


class StrictMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateMemoryCandidateRequest(StrictMemoryModel):
    conversation_id: UUID
    message_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_MEMORY_SOURCE_MESSAGES,
    )
    scope: MemoryScope = MemoryScope.USER

    @model_validator(mode="after")
    def source_messages_are_unique(self) -> Self:
        if len(set(self.message_ids)) != len(self.message_ids):
            raise ValueError("Memory source messages must be unique")
        return self


class ResolveMemoryCandidateRequest(StrictMemoryModel):
    action: MemoryWriteAction = MemoryWriteAction.CREATE
    content: MemoryContent
    scope: MemoryScope = MemoryScope.USER
    kind: MemoryKind = MemoryKind.NOTE
    expires_at: datetime | None = None
    target_memory_id: UUID | None = None
    target_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def target_matches_action(self) -> Self:
        requires_target = self.action in {MemoryWriteAction.UPDATE, MemoryWriteAction.MERGE}
        if requires_target != (self.target_memory_id is not None):
            raise ValueError("Memory target does not match action")
        if requires_target != (self.target_revision is not None):
            raise ValueError("Memory target revision does not match action")
        return self


class MemoryCandidateResponse(StrictMemoryModel):
    id: UUID
    conversation_id: UUID
    source_message_ids: list[UUID]
    suggested_content: str | None
    suggested_scope: MemoryScope
    suggested_expires_at: datetime | None
    confidence: float
    write_reason: str
    policy_decision: MemoryPolicyDecision
    policy_reason: MemoryPolicyReason
    status: MemoryCandidateStatus
    revision: int
    resolved_memory_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MemoryCandidateCreatedResponse(MemoryCandidateResponse):
    created: bool


class MemoryCandidateCollectionResponse(StrictMemoryModel):
    candidates: list[MemoryCandidateResponse]


class MemoryRevisionResponse(StrictMemoryModel):
    id: UUID
    version: int
    content: str
    scope: MemoryScope
    kind: MemoryKind
    write_action: MemoryWriteAction
    write_reason: str
    policy_decision: MemoryPolicyDecision
    editor_user_id: UUID
    source_message_ids: list[UUID]
    validity: MemoryRevisionValidity
    created_at: datetime


class MemoryResponse(StrictMemoryModel):
    id: UUID
    owner_user_id: UUID
    source_conversation_id: UUID
    scope: MemoryScope
    kind: MemoryKind
    confidence: float
    status: MemoryStatus
    current_revision_id: UUID
    current_version: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryCollectionResponse(StrictMemoryModel):
    memories: list[MemoryResponse]


class MemoryDetailResponse(StrictMemoryModel):
    memory: MemoryResponse
    current_revision: MemoryRevisionResponse
    revisions: list[MemoryRevisionResponse]


class MemoryResolutionResponse(StrictMemoryModel):
    memory: MemoryDetailResponse
    action: MemoryWriteAction
    created: bool
