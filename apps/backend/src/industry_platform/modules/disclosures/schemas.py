"""HTTP schemas for SEC filer discovery."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.disclosures.domain import (
    SecFilerCandidate,
    SecFilerMatchKind,
    SecFilerResolution,
    SecFilerResolutionStatus,
)


class SecFilerCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(pattern=r"^[0-9]{10}$")
    canonical_name: str
    tickers: list[str]
    matched_by: SecFilerMatchKind
    matched_value: str
    confidence: float = Field(ge=0, le=1)
    source_version: str
    source_url: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_observed_at: datetime
    alias_valid_from: datetime | None
    alias_valid_to: datetime | None

    @classmethod
    def from_domain(cls, value: SecFilerCandidate) -> SecFilerCandidateResponse:
        return cls(
            cik=value.cik,
            canonical_name=value.canonical_name,
            tickers=list(value.tickers),
            matched_by=value.matched_by,
            matched_value=value.matched_value,
            confidence=value.confidence,
            source_version=value.source_version,
            source_url=value.source_url,
            content_sha256=value.content_sha256,
            source_observed_at=value.source_observed_at,
            alias_valid_from=value.alias_valid_from,
            alias_valid_to=value.alias_valid_to,
        )


class SecFilerResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilerResolutionStatus
    query: str
    normalized_query: str
    candidates: list[SecFilerCandidateResponse]
    catalog_source_version: str
    catalog_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_retrieved_at: datetime

    @classmethod
    def from_domain(cls, value: SecFilerResolution) -> SecFilerResolutionResponse:
        return cls(
            status=value.status,
            query=value.query,
            normalized_query=value.normalized_query,
            candidates=[SecFilerCandidateResponse.from_domain(item) for item in value.candidates],
            catalog_source_version=value.catalog_source_version,
            catalog_content_sha256=value.catalog_content_sha256,
            catalog_retrieved_at=value.catalog_retrieved_at,
        )
