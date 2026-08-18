"""Strict HTTP contracts for industry context and collection visibility."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.industry.domain import (
    CollectionRunStatus,
    ProviderCode,
    ProviderErrorCode,
    ProviderReadiness,
    SourceKind,
)
from industry_platform.modules.jobs.domain import ScheduleMisfirePolicy


class StrictIndustryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndustryResponse(StrictIndustryModel):
    id: UUID
    code: str
    name: str
    default_query: str
    default_symbol: str


class IndustryCollectionResponse(StrictIndustryModel):
    industries: list[IndustryResponse]


class IndustryPreferenceResponse(StrictIndustryModel):
    workspace_id: UUID
    user_id: UUID
    industry: IndustryResponse
    updated_at: datetime


class SetIndustryPreferenceRequest(StrictIndustryModel):
    industry_id: UUID


class ProviderStatusResponse(StrictIndustryModel):
    provider: ProviderCode
    kind: SourceKind
    readiness: ProviderReadiness
    reason_code: ProviderErrorCode | None


class ProviderStatusCollectionResponse(StrictIndustryModel):
    providers: list[ProviderStatusResponse]


class SourceItemResponse(StrictIndustryModel):
    id: UUID
    industry_id: UUID
    kind: SourceKind
    provider: ProviderCode
    external_id: str
    title: str
    summary: str
    locator: str
    published_at: datetime
    collected_at: datetime
    content_sha256: str
    metadata: dict[str, object]


class SourceItemCollectionResponse(StrictIndustryModel):
    items: list[SourceItemResponse]
    limit: int
    offset: int


class CollectionRunResponse(StrictIndustryModel):
    id: UUID
    industry_id: UUID
    kind: SourceKind
    provider: ProviderCode
    status: CollectionRunStatus
    scheduled_for: datetime | None
    started_at: datetime | None
    terminal_at: datetime | None
    last_error_code: str | None
    fetched_count: int
    inserted_count: int
    duplicate_count: int


class CollectionRunCollectionResponse(StrictIndustryModel):
    runs: list[CollectionRunResponse]


class CreateCollectionScheduleRequest(StrictIndustryModel):
    industry_id: UUID
    kind: SourceKind
    cron_expression: str = Field(min_length=9, max_length=120)
    timezone_name: str = Field(min_length=1, max_length=100)
    misfire_policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.COALESCE_LATEST
    catch_up_window_seconds: int = Field(ge=1, le=604_800, default=86_400)
    max_catch_up: int = Field(ge=1, le=1_000, default=100)


class CollectionScheduleResponse(StrictIndustryModel):
    id: UUID
    industry_id: UUID
    kind: SourceKind
    cron_expression: str
    timezone_name: str
    next_due_at: datetime | None
    last_fired_at: datetime | None
    enabled: bool
    misfire_policy: ScheduleMisfirePolicy
    misfire_error_code: str | None


class CollectionScheduleCreatedResponse(StrictIndustryModel):
    id: UUID
    created: bool


class CollectionScheduleCollectionResponse(StrictIndustryModel):
    schedules: list[CollectionScheduleResponse]


class TriggerCollectionRequest(StrictIndustryModel):
    trigger_id: UUID


class TriggerCollectionResponse(StrictIndustryModel):
    occurrence_id: UUID
    job_id: UUID
    created: bool
