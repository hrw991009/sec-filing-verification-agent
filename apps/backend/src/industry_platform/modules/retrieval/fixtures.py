"""Strict loader for the version-controlled SEC fixture manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.retrieval.domain import (
    SecFilingFixture,
    SecFixtureFact,
)


@dataclass(frozen=True, slots=True)
class SecFixtureCatalog:
    dataset_version: str
    filings: tuple[SecFilingFixture, ...]

    def __post_init__(self) -> None:
        filings = tuple(self.filings)
        identities = {(item.cik, item.accession, item.form) for item in filings}
        if not filings or len(identities) != len(filings):
            raise ValueError("SEC fixture catalog identities are invalid")
        object.__setattr__(self, "filings", filings)

    def select(self, scope: FinancialScope) -> SecFilingFixture | None:
        matches = tuple(item for item in self.filings if item.matches(scope))
        return matches[0] if len(matches) == 1 else None


def load_sec_fixture_catalog(manifest_path: Path, *, repository_root: Path) -> SecFixtureCatalog:
    """Load exact fields and verify every declared fixture hash before use."""

    root = repository_root.resolve(strict=True)
    manifest = manifest_path.resolve(strict=True)
    if not manifest.is_relative_to(root):
        raise ValueError("SEC fixture manifest must be inside the repository root")
    try:
        raw: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("SEC fixture manifest cannot be loaded") from None
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "dataset_version", "filings"}:
        raise ValueError("SEC fixture manifest fields are invalid")
    if raw["schema_version"] != 1 or not isinstance(raw["dataset_version"], str):
        raise ValueError("SEC fixture manifest version is invalid")
    filings_raw = raw["filings"]
    if not isinstance(filings_raw, list) or not filings_raw:
        raise ValueError("SEC fixture manifest filings are invalid")
    filings = tuple(
        _parse_filing(item, dataset_version=raw["dataset_version"], root=root)
        for item in filings_raw
    )
    return SecFixtureCatalog(dataset_version=raw["dataset_version"], filings=filings)


def _parse_filing(
    value: object,
    *,
    dataset_version: str,
    root: Path,
) -> SecFilingFixture:
    expected = {
        "cik",
        "accession",
        "form",
        "report_period",
        "filed_at",
        "accepted_at",
        "primary_document",
        "canonical_url",
        "fixture_path",
        "content_sha256",
        "license_or_terms",
        "facts",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("SEC fixture filing fields are invalid")
    facts_raw = value["facts"]
    if not isinstance(facts_raw, list) or not facts_raw:
        raise ValueError("SEC fixture facts are invalid")
    fixture_path = value["fixture_path"]
    if not isinstance(fixture_path, str):
        raise ValueError("SEC fixture path is invalid")
    fixture_file = (root / fixture_path).resolve(strict=True)
    if not fixture_file.is_relative_to(root) or not fixture_file.is_file():
        raise ValueError("SEC fixture file is invalid")
    digest = hashlib.sha256(fixture_file.read_bytes()).hexdigest()
    if digest != value["content_sha256"]:
        raise ValueError("SEC fixture content hash does not match its manifest")
    try:
        return SecFilingFixture(
            dataset_version=dataset_version,
            cik=str(value["cik"]),
            accession=str(value["accession"]),
            form=str(value["form"]),
            report_period=date.fromisoformat(str(value["report_period"])),
            filed_at=datetime.fromisoformat(str(value["filed_at"])),
            accepted_at=datetime.fromisoformat(str(value["accepted_at"])),
            primary_document=str(value["primary_document"]),
            canonical_url=str(value["canonical_url"]),
            fixture_path=fixture_path,
            content_sha256=str(value["content_sha256"]),
            license_or_terms=str(value["license_or_terms"]),
            facts=tuple(_parse_fact(item) for item in facts_raw),
        )
    except (TypeError, ValueError):
        raise ValueError("SEC fixture filing is invalid") from None


def _parse_fact(value: object) -> SecFixtureFact:
    expected = {
        "key",
        "value",
        "unit",
        "scale",
        "period_start",
        "period_end",
        "section",
        "source_page",
        "anchor",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("SEC fixture fact fields are invalid")
    scale = value["scale"]
    source_page = value["source_page"]
    if (
        isinstance(scale, bool)
        or not isinstance(scale, int)
        or isinstance(source_page, bool)
        or not isinstance(source_page, int)
        or source_page < 1
    ):
        raise ValueError("SEC fixture fact numeric fields are invalid")
    return SecFixtureFact(
        key=str(value["key"]),
        value=str(value["value"]),
        unit=str(value["unit"]),
        scale=scale,
        period_start=date.fromisoformat(str(value["period_start"])),
        period_end=date.fromisoformat(str(value["period_end"])),
        section=str(value["section"]),
        source_page=source_page,
        anchor=str(value["anchor"]),
    )
