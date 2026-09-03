"""Strict test-only SEC source bundle for real-process browser verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from industry_platform.modules.disclosures.adapters.sec_edgar import _parse_catalog
from industry_platform.modules.disclosures.adapters.sec_submissions import _parse_current
from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecFilerCatalogSnapshot,
    SecFilingArchive,
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    SecFilingForm,
    SecSubmissionSet,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    sec_companyfacts_url,
    sec_complete_submission_url,
    sec_primary_document_url,
    sec_xbrl_source_version,
    sha256_hex,
)


class _CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_at: AwareDatetime


class _FilingSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_available_at: AwareDatetime


class _ControlledFiling(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(pattern=r"^[0-9]{10}$")
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: Literal["10-K", "10-K/A", "10-Q", "10-Q/A"]
    report_date: date
    filed_date: date
    accepted_at: AwareDatetime
    primary_document: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    submissions: _FilingSource
    primary: _FilingSource
    complete_submission: _FilingSource
    companyfacts: _FilingSource

    @field_validator("accepted_at")
    @classmethod
    def normalize_accepted_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class _ControlledManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    mode: Literal["controlled_derivative"]
    catalog: _CatalogSource
    filings: tuple[_ControlledFiling, ...] = Field(min_length=1, max_length=20)


@dataclass(frozen=True, slots=True)
class ControlledSecSourceBundle:
    dataset_version: str
    catalog: SecFilerCatalogSnapshot
    submissions: tuple[SecSubmissionSet, ...]
    archives: dict[str, SecFilingArchive]
    companyfacts: dict[str, SecXbrlSourceSnapshot]


def load_controlled_sec_source_bundle(manifest_path: Path) -> ControlledSecSourceBundle:
    """Load verified derivative bytes without making a network request."""

    manifest_file = manifest_path.resolve(strict=True)
    root = manifest_file.parent
    manifest = _ControlledManifest.model_validate(_json_object(manifest_file.read_bytes()))
    retrieved_at = manifest.catalog.retrieved_at.astimezone(UTC)
    catalog_body = _verified_source(root, manifest.catalog.path, manifest.catalog.sha256)
    catalog = _parse_catalog(catalog_body, retrieved_at=retrieved_at)

    submissions: list[SecSubmissionSet] = []
    archives: dict[str, SecFilingArchive] = {}
    companyfacts: dict[str, SecXbrlSourceSnapshot] = {}
    seen_accessions: set[str] = set()
    for entry in manifest.filings:
        if entry.accession in seen_accessions:
            raise ValueError("Controlled SEC manifest contains duplicate accessions")
        seen_accessions.add(entry.accession)
        submissions_body = _verified_source(
            root,
            entry.submissions.path,
            entry.submissions.sha256,
        )
        current = _parse_current(
            submissions_body,
            cik=entry.cik,
            retrieved_at=retrieved_at,
            source_available_at=entry.submissions.source_available_at.astimezone(UTC),
        )
        observation = next(
            (item for item in current.filings if item.accession == entry.accession),
            None,
        )
        if observation is None or (
            observation.form.value,
            observation.report_date,
            observation.filed_date,
            observation.accepted_at,
            observation.primary_document,
        ) != (
            entry.form,
            entry.report_date,
            entry.filed_date,
            entry.accepted_at.astimezone(UTC),
            entry.primary_document,
        ):
            raise ValueError("Controlled SEC filing identity does not match submissions")
        submissions.append(
            SecSubmissionSet(
                current=current,
                supplementals=(),
                required_supplemental_names=(),
            )
        )

        canonical = SecCanonicalFiling(
            id=uuid5(NAMESPACE_URL, f"controlled-sec:{entry.accession}"),
            cik=entry.cik,
            accession=entry.accession,
            form=SecFilingForm(entry.form),
            report_date=entry.report_date,
            filed_date=entry.filed_date,
            accepted_at=entry.accepted_at.astimezone(UTC),
            public_available_at=entry.accepted_at.astimezone(UTC),
            primary_document=entry.primary_document,
            source_available_at=entry.submissions.source_available_at.astimezone(UTC),
        )
        primary_body = _verified_source(root, entry.primary.path, entry.primary.sha256)
        complete_body = _verified_source(
            root,
            entry.complete_submission.path,
            entry.complete_submission.sha256,
        )
        archives[entry.accession] = SecFilingArchive(
            filing=canonical,
            documents=(
                _document(
                    canonical,
                    kind=SecFilingDocumentKind.COMPLETE_SUBMISSION,
                    filename=f"{entry.accession}.txt",
                    body=complete_body,
                    content_type="text/plain",
                    source_available_at=entry.complete_submission.source_available_at,
                    retrieved_at=retrieved_at,
                ),
                _document(
                    canonical,
                    kind=SecFilingDocumentKind.PRIMARY_DOCUMENT,
                    filename=entry.primary_document,
                    body=primary_body,
                    content_type="text/html",
                    source_available_at=entry.primary.source_available_at,
                    retrieved_at=retrieved_at,
                ),
            ),
        )
        companyfacts_body = _verified_source(
            root,
            entry.companyfacts.path,
            entry.companyfacts.sha256,
        )
        companyfacts_digest = sha256_hex(companyfacts_body)
        companyfacts[entry.accession] = SecXbrlSourceSnapshot(
            source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
            cik=entry.cik,
            source_url=sec_companyfacts_url(entry.cik),
            source_version=sec_xbrl_source_version(
                SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
                companyfacts_digest,
            ),
            content_type="application/json",
            content_sha256=companyfacts_digest,
            byte_size=len(companyfacts_body),
            retrieved_at=retrieved_at,
            source_available_at=entry.companyfacts.source_available_at.astimezone(UTC),
            body=companyfacts_body,
        )

    return ControlledSecSourceBundle(
        dataset_version=manifest.dataset_version,
        catalog=catalog,
        submissions=tuple(sorted(submissions, key=lambda item: item.current.source_available_at)),
        archives=archives,
        companyfacts=companyfacts,
    )


def _document(
    filing: SecCanonicalFiling,
    *,
    kind: SecFilingDocumentKind,
    filename: str,
    body: bytes,
    content_type: str,
    source_available_at: datetime,
    retrieved_at: datetime,
) -> SecFilingDocumentSnapshot:
    digest = sha256_hex(body)
    source_url = (
        sec_complete_submission_url(filing.cik, filing.accession)
        if kind is SecFilingDocumentKind.COMPLETE_SUBMISSION
        else sec_primary_document_url(filing.cik, filing.accession, filename)
    )
    return SecFilingDocumentSnapshot(
        kind=kind,
        cik=filing.cik,
        accession=filing.accession,
        filename=filename,
        source_url=source_url,
        source_version=f"sec-filing-{kind.value}-{digest[:24]}",
        content_type=content_type,
        content_sha256=digest,
        byte_size=len(body),
        retrieved_at=retrieved_at,
        source_available_at=source_available_at.astimezone(UTC),
        body=body,
    )


def _verified_source(root: Path, relative_path: str, expected_sha256: str) -> bytes:
    candidate = (root / relative_path).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError("Controlled SEC source must be a file inside its bundle")
    body = candidate.read_bytes()
    if not body or hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError("Controlled SEC source hash does not match its manifest")
    return body


def _json_object(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("Controlled SEC manifest is invalid") from None
    if not isinstance(value, dict):
        raise ValueError("Controlled SEC manifest must be an object")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Controlled SEC manifest contains a duplicate key")
        result[key] = value
    return result
