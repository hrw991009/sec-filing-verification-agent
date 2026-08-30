"""Strict contracts and source verification for the SEC temporal evaluation corpus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, Self, cast
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.adapters.public_egress import create_public_egress_http_client
from industry_platform.modules.evaluation.materialize import materialize_verified_download
from industry_platform.modules.evaluation.release import (
    QuestionLanguage,
    ReleaseAnswerGold,
    ReleaseBudget,
    ReleaseQuestion,
    ReleaseSecGold,
    ReleaseSecSource,
    ReleaseTrajectoryContract,
    canonical_sha256,
    load_strict_json,
)

SEC_TEMPORAL_DATASET_ID: Final = "sec-temporal-v1"
SEC_TEMPORAL_DATASET_VERSION: Final = "v1"
SEC_TEMPORAL_SCHEMA_VERSION: Final = 1
SEC_TEMPORAL_SOURCE_ADAPTER_VERSION: Final = "sec-archive-source-v1"
SEC_TEMPORAL_VALIDATOR_VERSION: Final = "sec-temporal-validator-v1"
MINIMUM_CASE_COUNT: Final = 60
MINIMUM_PAIR_COUNT: Final = 30
MINIMUM_LANGUAGE_REVIEW_PAIRS: Final = 10
_MAX_XBRL_BYTES: Final = 5 * 1024 * 1024
_MAX_XBRL_ELEMENTS: Final = 250_000
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class SecTemporalSplit(StrEnum):
    CONSTRUCTION = "construction"
    DEVELOPMENT = "development"
    RELEASE_HOLDOUT = "release_holdout"


class SecTemporalCategory(StrEnum):
    DIRECT_FACT = "direct_fact"
    TABLE_TEXT = "table_text"
    CALCULATION = "calculation"
    CROSS_PERIOD = "cross_period"
    AMENDMENT = "amendment"
    CUSTOM_FOOTNOTE_CONFLICT = "custom_footnote_conflict"
    NO_ANSWER_CUTOFF = "no_answer_cutoff"
    SECURITY_RECOVERY = "security_recovery"


class SecTemporalArtifactKind(StrEnum):
    HTML = "html"
    XBRL = "xbrl"


class SecTemporalEvidenceKind(StrEnum):
    XBRL_FACT = "xbrl_fact"
    HTML_ANCHOR = "html_anchor"
    SOURCE_SNAPSHOT = "source_snapshot"
    POLICY = "policy"


class SecTemporalScenarioKind(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    PERMISSION_DENIAL = "permission_denial"
    TRANSIENT_RECOVERY = "transient_recovery"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_unique(values: Sequence[object], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


def _safe_relative_path(value: str) -> bool:
    return not (value.startswith(("/", "\\")) or "\\" in value or ".." in value.split("/"))


class SecTemporalArtifact(_FrozenModel):
    kind: SecTemporalArtifactKind
    relative_path: str = Field(min_length=1)
    download_url: str
    byte_size: int = Field(gt=0, le=_MAX_XBRL_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _validate_artifact(self) -> Self:
        parsed = urlparse(self.download_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.sec.gov"
            or not _safe_relative_path(self.relative_path)
        ):
            raise ValueError("SEC temporal artifact location is invalid")
        expected_suffix = ".htm" if self.kind is SecTemporalArtifactKind.HTML else ".xml"
        if not self.relative_path.endswith(expected_suffix):
            raise ValueError("SEC temporal artifact extension does not match its kind")
        return self


class SecTemporalSource(_FrozenModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    split: SecTemporalSplit
    cik: str = Field(pattern=r"^[0-9]{10}$")
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: str
    report_period: date
    filed_on: date
    available_at: datetime
    primary_document: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.htm$")
    artifacts: tuple[SecTemporalArtifact, ...]

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        if self.cik == "0000000000" or self.form not in {"10-K", "10-K/A"}:
            raise ValueError("SEC temporal filing identity is invalid")
        if self.available_at.utcoffset() is None:
            raise ValueError("SEC temporal source availability is invalid")
        if tuple(artifact.kind for artifact in self.artifacts) != (
            SecTemporalArtifactKind.HTML,
            SecTemporalArtifactKind.XBRL,
        ):
            raise ValueError("SEC temporal source must pin HTML and extracted XBRL artifacts")
        accession_digits = self.accession.replace("-", "")
        archive_prefix = (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{accession_digits}/"
        )
        expected_urls = {
            SecTemporalArtifactKind.HTML: archive_prefix + self.primary_document,
            SecTemporalArtifactKind.XBRL: (
                archive_prefix + self.primary_document.removesuffix(".htm") + "_htm.xml"
            ),
        }
        if any(
            artifact.download_url != expected_urls[artifact.kind] for artifact in self.artifacts
        ):
            raise ValueError("SEC temporal artifact URL does not match filing identity")
        expected_paths = {
            SecTemporalArtifactKind.HTML: f"sources/{self.source_id}.htm",
            SecTemporalArtifactKind.XBRL: f"sources/{self.source_id}.xml",
        }
        if any(
            artifact.relative_path != expected_paths[artifact.kind] for artifact in self.artifacts
        ):
            raise ValueError("SEC temporal artifact path does not match source identity")
        return self

    def artifact(self, kind: SecTemporalArtifactKind) -> SecTemporalArtifact:
        return next(artifact for artifact in self.artifacts if artifact.kind is kind)


class SecTemporalPeriod(_FrozenModel):
    instant: date | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def _validate_period(self) -> Self:
        valid = (
            self.instant is not None and self.start_date is None and self.end_date is None
        ) or (
            self.instant is None
            and self.start_date is not None
            and self.end_date is not None
            and self.start_date <= self.end_date
        )
        if not valid:
            raise ValueError("SEC temporal fact period must be an instant or duration")
        return self

    @property
    def locator_value(self) -> str:
        if self.instant is not None:
            return self.instant.isoformat()
        return f"{self.start_date.isoformat()}/{self.end_date.isoformat()}"  # type: ignore[union-attr]


class SecTemporalEvidence(_FrozenModel):
    evidence_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    kind: SecTemporalEvidenceKind
    locator: str = Field(min_length=1)
    source_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    taxonomy: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    concept: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    period: SecTemporalPeriod | None = None
    unit: str | None = Field(default=None, min_length=1)
    expected_value: str | None = Field(default=None, min_length=1)
    anchor_text: str | None = Field(default=None, min_length=4, max_length=160)
    anchor_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _validate_evidence_shape(self) -> Self:
        fact_fields = (self.taxonomy, self.concept, self.period, self.unit, self.expected_value)
        if self.kind is SecTemporalEvidenceKind.XBRL_FACT:
            if self.source_id is None or any(value is None for value in fact_fields):
                raise ValueError("XBRL evidence requires source, concept, period, unit, and value")
            if self.anchor_text is not None or self.anchor_sha256 is not None:
                raise ValueError("XBRL evidence cannot carry an HTML anchor")
        elif self.kind is SecTemporalEvidenceKind.HTML_ANCHOR:
            if self.source_id is None or self.anchor_text is None or self.anchor_sha256 is None:
                raise ValueError("HTML evidence requires source and a pinned text anchor")
            if any(value is not None for value in fact_fields):
                raise ValueError("HTML evidence cannot carry XBRL fact fields")
            if _text_sha256(self.anchor_text) != self.anchor_sha256:
                raise ValueError("HTML evidence anchor checksum does not match")
        elif self.kind is SecTemporalEvidenceKind.SOURCE_SNAPSHOT:
            if self.source_id is None or any(
                value is not None for value in (*fact_fields, self.anchor_text, self.anchor_sha256)
            ):
                raise ValueError("Source snapshot evidence may only reference a source")
        elif self.source_id is not None or any(
            value is not None for value in (*fact_fields, self.anchor_text, self.anchor_sha256)
        ):
            raise ValueError("Policy evidence cannot reference source content")
        return self


class SecTemporalScope(_FrozenModel):
    cik: str = Field(pattern=r"^[0-9]{10}$")
    report_period: date
    as_of: datetime
    visible_source_ids: tuple[str, ...]
    forbidden_future_source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.as_of.utcoffset() is None or not self.visible_source_ids:
            raise ValueError("SEC temporal scope requires aware as_of and visible sources")
        _require_unique(self.visible_source_ids, field_name="Visible SEC source ids")
        _require_unique(self.forbidden_future_source_ids, field_name="Forbidden SEC source ids")
        if set(self.visible_source_ids) & set(self.forbidden_future_source_ids):
            raise ValueError("Visible and forbidden SEC sources must be disjoint")
        return self


class SecTemporalGold(_FrozenModel):
    gold_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    scope: SecTemporalScope
    evidence_keys: tuple[str, ...]
    answer_gold: ReleaseAnswerGold

    @model_validator(mode="after")
    def _validate_gold(self) -> Self:
        if not self.evidence_keys:
            raise ValueError("SEC temporal gold requires Evidence references")
        _require_unique(self.evidence_keys, field_name="SEC temporal gold Evidence keys")
        if not set(self.answer_gold.supporting_fact_keys) <= set(self.evidence_keys):
            raise ValueError("Supporting facts must be included in SEC temporal Evidence")
        return self


class SecTemporalScenario(_FrozenModel):
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    kind: SecTemporalScenarioKind
    untrusted_payload: str | None = Field(default=None, min_length=1)
    denied_action: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    failure_mode: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    max_attempts: int = Field(default=1, ge=1, le=2)

    @model_validator(mode="after")
    def _validate_scenario(self) -> Self:
        if self.kind is SecTemporalScenarioKind.PROMPT_INJECTION:
            if self.untrusted_payload is None or self.denied_action is None or self.failure_mode:
                raise ValueError("Prompt-injection scenario is incomplete")
        elif self.kind is SecTemporalScenarioKind.PERMISSION_DENIAL:
            if self.denied_action is None or self.untrusted_payload or self.failure_mode:
                raise ValueError("Permission-denial scenario is incomplete")
        elif self.failure_mode is None or self.denied_action or self.untrusted_payload:
            raise ValueError("Recovery scenario is incomplete")
        return self


class SecTemporalBudgetProfile(_FrozenModel):
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    budget: ReleaseBudget


class SecTemporalTrajectoryProfile(_FrozenModel):
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    trajectory: ReleaseTrajectoryContract


class SecTemporalPair(_FrozenModel):
    pair_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    split: SecTemporalSplit
    category: SecTemporalCategory
    questions: tuple[ReleaseQuestion, ...]
    gold_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    budget_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    trajectory_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    scenario_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    human_review_required: bool = True

    @model_validator(mode="after")
    def _validate_pair(self) -> Self:
        if tuple(question.language for question in self.questions) != (
            QuestionLanguage.EN,
            QuestionLanguage.ZH,
        ):
            raise ValueError("SEC temporal pair must contain ordered English and Chinese questions")
        if not any("\u4e00" <= character <= "\u9fff" for character in self.questions[1].text):
            raise ValueError("SEC temporal Chinese question must contain CJK text")
        if self.questions[0].text == self.questions[1].text:
            raise ValueError("SEC temporal paired questions must differ")
        return self


class SecTemporalCoverageRequirement(_FrozenModel):
    category: SecTemporalCategory
    minimum_cases: int = Field(ge=1)


class ExpandedSecTemporalCase(_FrozenModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pair_id: str
    dataset_id: str
    dataset_version: str
    split: SecTemporalSplit
    category: SecTemporalCategory
    question: ReleaseQuestion
    budget: ReleaseBudget
    trajectory: ReleaseTrajectoryContract
    answer_gold: ReleaseAnswerGold
    sec_gold: ReleaseSecGold
    evidence_keys: tuple[str, ...]
    scenario_id: str | None


class SecTemporalManifest(_FrozenModel):
    schema_version: int
    dataset_id: str
    dataset_version: str
    source_adapter_version: str
    validator_version: str
    status: str
    minimum_case_count: int
    minimum_pair_count: int
    coverage_requirements: tuple[SecTemporalCoverageRequirement, ...]
    sources: tuple[SecTemporalSource, ...]
    evidence: tuple[SecTemporalEvidence, ...]
    gold: tuple[SecTemporalGold, ...]
    scenarios: tuple[SecTemporalScenario, ...]
    budget_profiles: tuple[SecTemporalBudgetProfile, ...]
    trajectory_profiles: tuple[SecTemporalTrajectoryProfile, ...]
    pairs: tuple[SecTemporalPair, ...]
    language_review_sample_pair_ids: tuple[str, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_manifest(self) -> Self:
        if (
            self.schema_version != SEC_TEMPORAL_SCHEMA_VERSION
            or self.dataset_id != SEC_TEMPORAL_DATASET_ID
            or self.dataset_version != SEC_TEMPORAL_DATASET_VERSION
            or self.source_adapter_version != SEC_TEMPORAL_SOURCE_ADAPTER_VERSION
            or self.validator_version != SEC_TEMPORAL_VALIDATOR_VERSION
            or self.status != "contract_only"
            or self.minimum_case_count != MINIMUM_CASE_COUNT
            or self.minimum_pair_count != MINIMUM_PAIR_COUNT
        ):
            raise ValueError("SEC temporal manifest identity is invalid")
        if not self.blockers:
            raise ValueError("Contract-only SEC temporal manifest must retain blockers")
        self._validate_unique_collections()
        self._validate_coverage()
        self._validate_references_and_cutoffs()
        return self

    def _validate_unique_collections(self) -> None:
        for field_name, values in (
            ("source ids", tuple(item.source_id for item in self.sources)),
            ("accessions", tuple(item.accession for item in self.sources)),
            ("Evidence refs", tuple(item.evidence_ref for item in self.evidence)),
            ("gold ids", tuple(item.gold_id for item in self.gold)),
            ("scenario ids", tuple(item.scenario_id for item in self.scenarios)),
            ("budget profile ids", tuple(item.profile_id for item in self.budget_profiles)),
            ("trajectory profile ids", tuple(item.profile_id for item in self.trajectory_profiles)),
            ("pair ids", tuple(item.pair_id for item in self.pairs)),
            ("language review sample", self.language_review_sample_pair_ids),
            ("blockers", self.blockers),
        ):
            _require_unique(values, field_name=f"SEC temporal {field_name}")
        _require_unique(
            tuple(item.category for item in self.coverage_requirements),
            field_name="SEC temporal coverage categories",
        )

    def _validate_coverage(self) -> None:
        if (
            len(self.pairs) < self.minimum_pair_count
            or len(self.pairs) * 2 < self.minimum_case_count
        ):
            raise ValueError("SEC temporal manifest does not meet pair and case minimums")
        requirements = {item.category: item.minimum_cases for item in self.coverage_requirements}
        if set(requirements) != set(SecTemporalCategory):
            raise ValueError("SEC temporal coverage requirements must enumerate all categories")
        case_counts = Counter(pair.category for pair in self.pairs)
        if any(case_counts[category] * 2 < minimum for category, minimum in requirements.items()):
            raise ValueError("SEC temporal category coverage is below its frozen minimum")
        if any(not pair.human_review_required for pair in self.pairs):
            raise ValueError("Every SEC temporal bilingual pair requires human review")
        pair_ids = {pair.pair_id for pair in self.pairs}
        if (
            len(self.language_review_sample_pair_ids) < MINIMUM_LANGUAGE_REVIEW_PAIRS
            or not set(self.language_review_sample_pair_ids) <= pair_ids
        ):
            raise ValueError("SEC temporal language review sample is incomplete")

    def _validate_references_and_cutoffs(self) -> None:
        sources = {source.source_id: source for source in self.sources}
        evidence = {item.evidence_ref: item for item in self.evidence}
        gold = {item.gold_id: item for item in self.gold}
        scenarios = {item.scenario_id: item for item in self.scenarios}
        budgets = {item.profile_id for item in self.budget_profiles}
        trajectories = {item.profile_id for item in self.trajectory_profiles}
        referenced_source_ids: set[str] = set()
        referenced_evidence_keys: set[str] = set()
        referenced_gold_ids: set[str] = set()
        referenced_scenario_ids: set[str] = set()
        for item in self.evidence:
            source = sources.get(item.source_id) if item.source_id is not None else None
            if item.source_id is not None and source is None:
                raise ValueError(
                    f"SEC temporal Evidence references unknown source: {item.evidence_ref}"
                )
            if item.locator != _expected_locator(item, source):
                raise ValueError(
                    f"SEC temporal Evidence locator is not canonical: {item.evidence_ref}"
                )
        for pair in self.pairs:
            pair_gold = gold.get(pair.gold_id)
            if (
                pair_gold is None
                or pair.budget_profile_id not in budgets
                or pair.trajectory_profile_id not in trajectories
            ):
                raise ValueError(
                    f"SEC temporal pair references an unknown contract: {pair.pair_id}"
                )
            if (pair.category is SecTemporalCategory.SECURITY_RECOVERY) != (
                pair.scenario_id is not None
            ) or (pair.scenario_id is not None and pair.scenario_id not in scenarios):
                raise ValueError(f"SEC temporal scenario binding is invalid: {pair.pair_id}")
            referenced_gold_ids.add(pair.gold_id)
            if pair.scenario_id is not None:
                referenced_scenario_ids.add(pair.scenario_id)
            scope_ids = (
                *pair_gold.scope.visible_source_ids,
                *pair_gold.scope.forbidden_future_source_ids,
            )
            if any(source_id not in sources for source_id in scope_ids):
                raise ValueError(f"SEC temporal gold references an unknown source: {pair.gold_id}")
            referenced_source_ids.update(scope_ids)
            referenced_evidence_keys.update(pair_gold.evidence_keys)
            scoped_sources = tuple(sources[source_id] for source_id in scope_ids)
            if any(source.split is not pair.split for source in scoped_sources):
                raise ValueError(f"SEC temporal source crosses a split: {pair.pair_id}")
            if any(source.cik != pair_gold.scope.cik for source in scoped_sources):
                raise ValueError(f"SEC temporal source crosses a CIK scope: {pair.pair_id}")
            visible = tuple(sources[source_id] for source_id in pair_gold.scope.visible_source_ids)
            forbidden = tuple(
                sources[source_id] for source_id in pair_gold.scope.forbidden_future_source_ids
            )
            if any(source.available_at > pair_gold.scope.as_of for source in visible) or any(
                source.available_at <= pair_gold.scope.as_of for source in forbidden
            ):
                raise ValueError(f"SEC temporal point-in-time cutoff is invalid: {pair.pair_id}")
            if any(key not in evidence for key in pair_gold.evidence_keys):
                raise ValueError(f"SEC temporal gold references unknown Evidence: {pair.gold_id}")
            visible_ids = set(pair_gold.scope.visible_source_ids)
            if any(
                evidence[key].source_id is not None and evidence[key].source_id not in visible_ids
                for key in pair_gold.evidence_keys
            ):
                raise ValueError(f"SEC temporal gold leaks non-visible Evidence: {pair.gold_id}")
            if pair.category is SecTemporalCategory.NO_ANSWER_CUTOFF:
                if (
                    not forbidden
                    or pair_gold.answer_gold.expected_result is not None
                    or pair_gold.answer_gold.expected_business_status != "insufficient_evidence"
                ):
                    raise ValueError(f"SEC temporal no-answer gold is invalid: {pair.gold_id}")
            elif pair_gold.answer_gold.expected_result is None:
                raise ValueError(f"SEC temporal answer result is missing: {pair.gold_id}")
        if referenced_source_ids != set(sources):
            raise ValueError("SEC temporal manifest contains an unreferenced source")
        if referenced_evidence_keys != set(evidence):
            raise ValueError("SEC temporal manifest contains unreferenced Evidence")
        if referenced_gold_ids != set(gold):
            raise ValueError("SEC temporal manifest contains unreferenced gold")
        if referenced_scenario_ids != set(scenarios):
            raise ValueError("SEC temporal manifest contains an unreferenced scenario")

    def expand_cases(self) -> tuple[ExpandedSecTemporalCase, ...]:
        sources = {source.source_id: source for source in self.sources}
        evidence = {item.evidence_ref: item for item in self.evidence}
        gold = {item.gold_id: item for item in self.gold}
        budgets = {item.profile_id: item.budget for item in self.budget_profiles}
        trajectories = {item.profile_id: item.trajectory for item in self.trajectory_profiles}
        expanded: list[ExpandedSecTemporalCase] = []
        for pair in self.pairs:
            pair_gold = gold[pair.gold_id]
            release_sources = tuple(
                _release_source(
                    sources[source_id],
                    pair_gold,
                    evidence,
                )
                for source_id in pair_gold.scope.visible_source_ids
            )
            sec_gold = ReleaseSecGold(
                cik=pair_gold.scope.cik,
                report_period=pair_gold.scope.report_period,
                as_of=pair_gold.scope.as_of,
                sources=release_sources,
            )
            for question in pair.questions:
                expanded.append(
                    ExpandedSecTemporalCase(
                        case_id=f"{pair.pair_id}.{question.language.value}",
                        pair_id=pair.pair_id,
                        dataset_id=self.dataset_id,
                        dataset_version=self.dataset_version,
                        split=pair.split,
                        category=pair.category,
                        question=question,
                        budget=budgets[pair.budget_profile_id],
                        trajectory=trajectories[pair.trajectory_profile_id],
                        answer_gold=pair_gold.answer_gold,
                        sec_gold=sec_gold,
                        evidence_keys=pair_gold.evidence_keys,
                        scenario_id=pair.scenario_id,
                    )
                )
        return tuple(expanded)


class SecTemporalValidationReport(_FrozenModel):
    dataset_id: str
    dataset_version: str
    validator_version: str
    manifest_sha256: str
    evidence_layer: str = "deterministic_contract"
    model_executed: bool = False
    runtime_bound: bool = False
    offline_capability_scored: bool = False
    official_metric_scores: None = None
    source_count: int
    artifact_count: int
    verified_artifact_count: int
    evidence_count: int
    resolved_evidence_count: int
    pair_count: int
    expanded_case_count: int
    pair_gold_identity_rate: float
    future_leakage_violations: int
    split_case_counts: dict[str, int]
    category_case_counts: dict[str, int]
    language_review_sample_pair_ids: tuple[str, ...]
    language_review_completed: bool = False
    blockers: tuple[str, ...]


def _release_source(
    source: SecTemporalSource,
    gold: SecTemporalGold,
    evidence: dict[str, SecTemporalEvidence],
) -> ReleaseSecSource:
    locators = tuple(
        evidence[key].locator
        for key in gold.evidence_keys
        if evidence[key].source_id == source.source_id
    )
    if not locators:
        raise ValueError(f"Visible source lacks case Evidence: {source.source_id}")
    return ReleaseSecSource(
        accession=source.accession,
        form=source.form,
        available_at=source.available_at,
        snapshot_sha256=source.artifact(SecTemporalArtifactKind.HTML).sha256,
        evidence_locators=locators,
    )


def _expected_locator(
    evidence: SecTemporalEvidence,
    source: SecTemporalSource | None,
) -> str:
    if evidence.kind is SecTemporalEvidenceKind.POLICY:
        return "policy://sec-point-in-time-v1/cutoff"
    if source is None:
        raise ValueError("Source-backed Evidence requires a source")
    if evidence.kind is SecTemporalEvidenceKind.SOURCE_SNAPSHOT:
        sha256 = source.artifact(SecTemporalArtifactKind.HTML).sha256
        return f"sec-source://{source.cik}/{source.accession}/{source.primary_document}#sha256={sha256}"
    if evidence.kind is SecTemporalEvidenceKind.HTML_ANCHOR:
        return (
            f"sec-html://{source.cik}/{source.accession}/{source.primary_document}"
            f"#anchor-sha256={evidence.anchor_sha256}"
        )
    period = cast(SecTemporalPeriod, evidence.period)
    return (
        f"sec-xbrl://{source.cik}/{source.accession}/{evidence.taxonomy}/{evidence.concept}"
        f"?period={period.locator_value}&unit={evidence.unit}&dimensions=none"
    )


def load_sec_temporal_manifest(path: Path) -> SecTemporalManifest:
    load_strict_json(path)
    return SecTemporalManifest.model_validate_json(path.read_text(encoding="utf-8"), strict=True)


def write_sec_temporal_manifest(manifest: SecTemporalManifest, path: Path) -> None:
    _write_json(path, manifest.model_dump(mode="json"))


def write_sec_temporal_schema(path: Path) -> None:
    _write_json(path, SecTemporalManifest.model_json_schema(mode="validation"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def verify_sec_temporal_artifact(root: Path, artifact: SecTemporalArtifact) -> Path:
    path = root / artifact.relative_path
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ValueError(
            f"SEC temporal artifact is unavailable: {artifact.relative_path}"
        ) from error
    if len(payload) != artifact.byte_size:
        raise ValueError(f"SEC temporal artifact size mismatch: {artifact.relative_path}")
    if hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise ValueError(f"SEC temporal artifact checksum mismatch: {artifact.relative_path}")
    return path


async def materialize_sec_temporal_sources(
    manifest: SecTemporalManifest,
    *,
    root: Path,
    client: httpx2.AsyncClient | None = None,
) -> None:
    owned_client = client is None
    active_client = client or create_public_egress_http_client()
    try:
        for source in manifest.sources:
            for artifact in source.artifacts:
                path = root / artifact.relative_path
                if path.exists():
                    verify_sec_temporal_artifact(root, artifact)
                    continue
                await materialize_verified_download(
                    artifact_id=f"{source.source_id}:{artifact.kind.value}",
                    download_url=artifact.download_url,
                    byte_size=artifact.byte_size,
                    sha256=artifact.sha256,
                    target=path,
                    client=active_client,
                    accept=(
                        "text/html,application/xhtml+xml"
                        if artifact.kind is SecTemporalArtifactKind.HTML
                        else "application/xml,text/xml"
                    ),
                    error_prefix="SEC temporal artifact",
                )
    finally:
        if owned_client:
            await active_client.aclose()


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _text_sha256(value: str) -> str:
    return hashlib.sha256(_normalize_text(value).encode("utf-8")).hexdigest()


class _XbrlContext(_FrozenModel):
    instant: str | None
    start_date: str | None
    end_date: str | None
    dimension_count: int


class _XbrlDocument:
    def __init__(self, payload: bytes) -> None:
        if len(payload) > _MAX_XBRL_BYTES:
            raise ValueError("SEC temporal XBRL exceeds the parser byte budget")
        upper = payload.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("SEC temporal XBRL cannot contain DTD or entity declarations")
        try:
            root = ElementTree.fromstring(payload)  # noqa: S314 - DTD/entities rejected above.
        except ElementTree.ParseError as error:
            raise ValueError("SEC temporal XBRL is malformed") from error
        if sum(1 for _ in root.iter()) > _MAX_XBRL_ELEMENTS:
            raise ValueError("SEC temporal XBRL exceeds the element budget")
        self.root = root
        self.contexts = self._contexts()
        self.units = self._units()

    def _contexts(self) -> dict[str, _XbrlContext]:
        contexts: dict[str, _XbrlContext] = {}
        for element in self.root.iter():
            if _local_name(element.tag) != "context" or not (context_id := element.get("id")):
                continue
            values: dict[str, str] = {}
            for child in element.iter():
                name = _local_name(child.tag)
                if name in {"instant", "startDate", "endDate"}:
                    values[name] = (child.text or "").strip()
            contexts[context_id] = _XbrlContext(
                instant=values.get("instant"),
                start_date=values.get("startDate"),
                end_date=values.get("endDate"),
                dimension_count=sum(
                    1
                    for child in element.iter()
                    if _local_name(child.tag) in {"explicitMember", "typedMember"}
                ),
            )
        return contexts

    def _units(self) -> dict[str, str]:
        units: dict[str, str] = {}
        for element in self.root.iter():
            if _local_name(element.tag) != "unit" or not (unit_id := element.get("id")):
                continue
            measures = [
                (child.text or "").strip()
                for child in element.iter()
                if _local_name(child.tag) == "measure"
            ]
            if len(measures) == 1:
                units[unit_id] = "USD" if measures[0].endswith(":USD") else measures[0]
        return units

    def verify_fact(self, evidence: SecTemporalEvidence) -> None:
        period = cast(SecTemporalPeriod, evidence.period)
        matches: list[str] = []
        for element in self.root.iter():
            if (
                _local_name(element.tag) != evidence.concept
                or _taxonomy_name(element.tag) != evidence.taxonomy
                or (context := self.contexts.get(element.get("contextRef", ""))) is None
                or context.dimension_count != 0
                or self.units.get(element.get("unitRef", "")) != evidence.unit
                or not _period_matches(context, period)
            ):
                continue
            value = (element.text or "").strip()
            if value:
                matches.append(value)
        if not matches:
            raise ValueError(f"SEC temporal XBRL fact is unresolved: {evidence.evidence_ref}")
        try:
            values = {Decimal(value) for value in matches}
            expected = Decimal(cast(str, evidence.expected_value))
        except InvalidOperation as error:
            raise ValueError(
                f"SEC temporal XBRL fact is not numeric: {evidence.evidence_ref}"
            ) from error
        if len(values) != 1 or values != {expected}:
            raise ValueError(f"SEC temporal XBRL fact conflicts with gold: {evidence.evidence_ref}")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _taxonomy_name(tag: str) -> str:
    namespace = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
    if "fasb.org/us-gaap" in namespace:
        return "us-gaap"
    if "apple.com" in namespace:
        return "aapl"
    return ""


def _period_matches(context: _XbrlContext, period: SecTemporalPeriod) -> bool:
    return (
        context.instant == (period.instant.isoformat() if period.instant else None)
        and context.start_date == (period.start_date.isoformat() if period.start_date else None)
        and context.end_date == (period.end_date.isoformat() if period.end_date else None)
    )


def build_sec_temporal_report(
    manifest: SecTemporalManifest,
    *,
    root: Path,
) -> SecTemporalValidationReport:
    sources = {source.source_id: source for source in manifest.sources}
    xbrl_documents: dict[str, _XbrlDocument] = {}
    html_text: dict[str, str] = {}
    verified_artifacts = 0
    for source in manifest.sources:
        for artifact in source.artifacts:
            path = verify_sec_temporal_artifact(root, artifact)
            verified_artifacts += 1
            if artifact.kind is SecTemporalArtifactKind.XBRL:
                xbrl_documents[source.source_id] = _XbrlDocument(path.read_bytes())
            else:
                parser = _VisibleTextParser()
                parser.feed(path.read_text(encoding="utf-8", errors="strict"))
                html_text[source.source_id] = _normalize_text(" ".join(parser.parts))
    for item in manifest.evidence:
        if item.kind is SecTemporalEvidenceKind.XBRL_FACT:
            xbrl_documents[cast(str, item.source_id)].verify_fact(item)
        elif item.kind is SecTemporalEvidenceKind.HTML_ANCHOR:
            anchor = _normalize_text(cast(str, item.anchor_text))
            if anchor not in html_text[cast(str, item.source_id)]:
                raise ValueError(f"SEC temporal HTML anchor is unresolved: {item.evidence_ref}")
        elif item.kind is SecTemporalEvidenceKind.SOURCE_SNAPSHOT:
            source = sources[cast(str, item.source_id)]
            if source.artifact(SecTemporalArtifactKind.HTML).sha256 not in item.locator:
                raise ValueError(f"SEC temporal snapshot locator is invalid: {item.evidence_ref}")
    expanded = manifest.expand_cases()
    split_counts = Counter(case.split.value for case in expanded)
    category_counts = Counter(case.category.value for case in expanded)
    return SecTemporalValidationReport(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        validator_version=manifest.validator_version,
        manifest_sha256=canonical_sha256(manifest),
        source_count=len(manifest.sources),
        artifact_count=sum(len(source.artifacts) for source in manifest.sources),
        verified_artifact_count=verified_artifacts,
        evidence_count=len(manifest.evidence),
        resolved_evidence_count=len(manifest.evidence),
        pair_count=len(manifest.pairs),
        expanded_case_count=len(expanded),
        pair_gold_identity_rate=1.0,
        future_leakage_violations=0,
        split_case_counts=dict(sorted(split_counts.items())),
        category_case_counts=dict(sorted(category_counts.items())),
        language_review_sample_pair_ids=manifest.language_review_sample_pair_ids,
        blockers=manifest.blockers,
    )


def write_sec_temporal_report(
    report: SecTemporalValidationReport,
    *,
    json_output: Path,
    markdown_output: Path,
) -> None:
    _write_json(json_output, report.model_dump(mode="json"))
    lines = [
        "# sec-temporal-v1 Contract Validation",
        "",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        f"- Validator version: `{report.validator_version}`",
        "- Evidence layer: `deterministic_contract`",
        "- Model executed: `false`",
        "- Runtime bound: `false`",
        "- Offline capability scored: `false`",
        (
            f"- Sources/artifacts verified: `{report.source_count}` / "
            f"`{report.verified_artifact_count}`"
        ),
        f"- Evidence resolved: `{report.resolved_evidence_count}/{report.evidence_count}`",
        f"- Bilingual pairs/expanded cases: `{report.pair_count}` / `{report.expanded_case_count}`",
        f"- Pair gold identity: `{report.pair_gold_identity_rate:.1%}`",
        f"- Future leakage violations: `{report.future_leakage_violations}`",
        "",
        "## Category Coverage",
        "",
        "| Category | Cases |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in report.category_case_counts.items())
    lines.extend(
        [
            "",
            (
                "The language sample is still awaiting owner review. "
                "This report is not a model benchmark result."
            ),
            "",
        ]
    )
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text("\n".join(lines), encoding="utf-8", newline="\n")


async def _run(args: argparse.Namespace) -> None:
    manifest = load_sec_temporal_manifest(cast(Path, args.manifest))
    root = cast(Path, args.root)
    await materialize_sec_temporal_sources(manifest, root=root)
    report = build_sec_temporal_report(manifest, root=root)
    write_sec_temporal_schema(cast(Path, args.schema_output))
    write_sec_temporal_report(
        report,
        json_output=cast(Path, args.json_output),
        markdown_output=cast(Path, args.markdown_output),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the SEC temporal corpus")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--schema-output", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--markdown-output", required=True, type=Path)
    args = parser.parse_args(argv)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
