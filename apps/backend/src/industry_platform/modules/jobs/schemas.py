"""HTTP contracts for durable recovery job acceptance."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.jobs.domain import JobStatus


class ReplayJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_dispatch_generation: int = Field(ge=1)
    additional_attempts: int = Field(default=1, ge=1, le=3)


class JobSubmissionResponse(BaseModel):
    job_id: UUID
    outbox_event_id: UUID
    dispatch_generation: int
    status: JobStatus
    created: bool
