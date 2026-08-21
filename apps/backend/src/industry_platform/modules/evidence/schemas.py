"""Strict HTTP contracts for Evidence normalization and Claim inspection."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.evidence.domain import (
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceStatus,
    GraphNodeType,
    RelationStatus,
)
from industry_platform.modules.research.domain import ResearchRunStatus


class StrictLedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NormalizeObservationRequest(StrictLedgerModel):
    tool_call_id: UUID
    observation_id: UUID


class InvalidateEvidenceRequest(StrictLedgerModel):
    status: EvidenceStatus
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def status_is_terminal(self) -> Self:
        if self.status is EvidenceStatus.ACTIVE:
            raise ValueError("Evidence invalidation status must not be active")
        return self

    @field_validator("reason")
    @classmethod
    def reason_is_normalized(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("Evidence invalidation reason is invalid")
        return value


class IndustrySourceLocatorResponse(StrictLedgerModel):
    schema_version: Literal[1]
    locator_type: Literal["industry_source_v1"]
    source_item_id: UUID
    source_kind: str
    provider: str
    source_version: str
    content_sha256: str


class SqlResultLocatorResponse(StrictLedgerModel):
    schema_version: Literal[1]
    locator_type: Literal["sql_result_v1"]
    query_run_id: UUID
    connection_id: UUID
    schema_snapshot_id: UUID
    schema_snapshot_sha256: str
    tables: list[str]
    columns: list[str]
    row_start: int
    row_end: int


EvidenceLocatorResponse = Annotated[
    IndustrySourceLocatorResponse | SqlResultLocatorResponse,
    Field(discriminator="locator_type"),
]


class AuthorizationSnapshotResponse(StrictLedgerModel):
    workspace_id: UUID
    actor_user_id: UUID
    role: str
    action: str
    captured_at: datetime


class EvidenceResponse(StrictLedgerModel):
    id: UUID
    workspace_id: UUID
    kind: EvidenceKind
    title: str
    canonical_url: str | None
    locator: EvidenceLocatorResponse
    excerpt: str | None
    content_sha256: str
    source_published_at: datetime | None
    retrieved_at: datetime
    license_or_terms: str
    status: EvidenceStatus
    revision: int
    invalidated_at: datetime | None
    invalidation_reason: str | None
    origin_run_id: UUID
    origin_step_id: UUID
    origin_tool_call_id: UUID
    origin_observation_id: UUID
    origin_source_ordinal: int
    normalizer_version: str
    authorization_snapshot: AuthorizationSnapshotResponse
    source_resource_version: str
    created_at: datetime
    updated_at: datetime


class EvidenceCollectionResponse(StrictLedgerModel):
    evidence: list[EvidenceResponse]


class EvidenceNormalizationItemResponse(StrictLedgerModel):
    source_ordinal: int
    decision: EvidenceDecision
    reason: EvidenceDecisionReason
    evidence: EvidenceResponse | None


class EvidenceNormalizationResponse(StrictLedgerModel):
    observation_id: UUID
    tool_call_id: UUID
    normalizer_version: str
    items: list[EvidenceNormalizationItemResponse]


class CreateResearchRunRequest(StrictLedgerModel):
    agent_run_id: UUID


class ResearchRunResponse(StrictLedgerModel):
    id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    agent_run_id: UUID
    status: ResearchRunStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class ResearchRunCollectionResponse(StrictLedgerModel):
    research_runs: list[ResearchRunResponse]


class ClaimEvidenceInputRequest(StrictLedgerModel):
    evidence_id: UUID
    relation: ClaimEvidenceRelation


class CreateClaimRequest(StrictLedgerModel):
    statement: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)
    relations: list[ClaimEvidenceInputRequest] = Field(default_factory=list, max_length=32)
    origin_run_id: UUID
    origin_step_id: UUID

    @field_validator("statement")
    @classmethod
    def statement_is_normalized(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("Claim statement is invalid")
        return value

    @model_validator(mode="after")
    def relations_are_unique(self) -> Self:
        if len({item.evidence_id for item in self.relations}) != len(self.relations):
            raise ValueError("Claim Evidence relations must be unique")
        return self


class ClaimEvidenceResponse(StrictLedgerModel):
    evidence: EvidenceResponse
    relation: ClaimEvidenceRelation
    relation_version: int
    status: RelationStatus
    ordinal: int
    origin_run_id: UUID
    origin_step_id: UUID


class ResearchClaimResponse(StrictLedgerModel):
    id: UUID
    workspace_id: UUID
    research_run_id: UUID
    statement: str
    confidence: float
    verification_status: ClaimVerificationStatus
    coverage: float
    conflict: bool
    revision: int
    relations: list[ClaimEvidenceResponse]
    created_at: datetime
    updated_at: datetime


class ResearchClaimCollectionResponse(StrictLedgerModel):
    claims: list[ResearchClaimResponse]


class GraphNodeResponse(StrictLedgerModel):
    id: UUID
    node_type: GraphNodeType
    resource_id: UUID
    label: str
    status: RelationStatus


class GraphEdgeResponse(StrictLedgerModel):
    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation: ClaimEvidenceRelation
    status: RelationStatus


class ClaimGraphResponse(StrictLedgerModel):
    research_run_id: UUID
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
