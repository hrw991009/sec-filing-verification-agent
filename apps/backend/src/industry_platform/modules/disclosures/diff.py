"""Fail-closed contracts and application service for comparable SEC filing diffs."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from industry_platform.modules.disclosures.domain import (
    SecAmendmentRelationStatus,
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingForm,
    SecFilingRetrievalTrace,
    SecFilingSearchHit,
    SecSourceErrorCode,
    SecXbrlFact,
    SecXbrlFactQuery,
    SecXbrlSourceKind,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingContentService
from industry_platform.modules.disclosures.ports import SecFilingContentRepository
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.workspaces.domain import WorkspaceScope

SEC_FILING_DIFF_VERSION: Final = "sec-filing-diff-v1"
SEC_MAX_DIFF_FACT_CHANGES: Final = 20
SEC_MAX_DIFF_TOOL_FACT_CHANGES: Final = 6

_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class SecFilingDiffStatus(StrEnum):
    OK = "ok"
    NO_RESULT = "no_result"
    NOT_READY = "not_ready"
    NOT_COMPARABLE = "not_comparable"
    PERMISSION_DENIED = "permission_denied"
    DEPENDENCY_FAILED = "dependency_failed"


class SecFilingDiffRelationship(StrEnum):
    BASE_AMENDMENT = "base_amendment"
    ADJACENT_PERIOD = "adjacent_period"


class SecFilingChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class SecFilingComparisonIdentity:
    import_id: UUID
    knowledge_base_id: UUID
    cik: str
    accession: str
    form: SecFilingForm
    report_date: date
    filed_date: date
    public_available_at: datetime
    amendment_relation_status: SecAmendmentRelationStatus
    base_accession: str | None

    def __post_init__(self) -> None:
        if self.import_id.int == 0 or self.knowledge_base_id.int == 0:
            raise ValueError("SEC filing comparison import identity is invalid")
        if not self.cik.isdigit() or len(self.cik) != 10:
            raise ValueError("SEC filing comparison CIK is invalid")
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing comparison accession is invalid")
        if self.public_available_at.tzinfo is None or self.public_available_at.utcoffset() is None:
            raise ValueError("SEC filing comparison availability must be timezone-aware")
        resolved = self.amendment_relation_status is SecAmendmentRelationStatus.RESOLVED
        if resolved != (self.base_accession is not None):
            raise ValueError("SEC filing comparison amendment identity is inconsistent")
        if self.base_accession is not None and not _ACCESSION_PATTERN.fullmatch(
            self.base_accession
        ):
            raise ValueError("SEC filing comparison base accession is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingComparisonPreparation:
    status: SecFilingDiffStatus
    accession: str
    identity: SecFilingComparisonIdentity | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing comparison preparation accession is invalid")
        if (self.status is SecFilingDiffStatus.OK) != (self.identity is not None):
            raise ValueError("SEC filing comparison preparation is inconsistent")
        if self.identity is not None and self.identity.accession != self.accession:
            raise ValueError("SEC filing comparison preparation identity is inconsistent")
        if (self.status is SecFilingDiffStatus.DEPENDENCY_FAILED) != (self.error_code is not None):
            raise ValueError("SEC filing comparison preparation error state is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingFactChange:
    taxonomy: str
    concept: str
    unit: str | None
    period_kind: str
    period_bucket: str
    dimensions: tuple[tuple[str, str], ...]
    is_custom: bool
    change_kind: SecFilingChangeKind
    baseline: SecXbrlFact | None
    target: SecXbrlFact | None

    def __post_init__(self) -> None:
        if not self.taxonomy or not self.concept or not self.period_kind or not self.period_bucket:
            raise ValueError("SEC filing fact change identity is invalid")
        dimensions = tuple(sorted(self.dimensions))
        if len({name for name, _value in dimensions}) != len(dimensions):
            raise ValueError("SEC filing fact change dimensions are invalid")
        if self.change_kind is SecFilingChangeKind.ADDED:
            valid = self.baseline is None and self.target is not None
        elif self.change_kind is SecFilingChangeKind.REMOVED:
            valid = self.baseline is not None and self.target is None
        else:
            valid = self.baseline is not None and self.target is not None
        if not valid:
            raise ValueError("SEC filing fact change sides are inconsistent")
        object.__setattr__(self, "dimensions", dimensions)


@dataclass(frozen=True, slots=True)
class SecFilingSectionChange:
    section: str
    change_kind: SecFilingChangeKind
    baseline: SecFilingSearchHit
    target: SecFilingSearchHit

    def __post_init__(self) -> None:
        if not self.section.strip() or self.change_kind not in {
            SecFilingChangeKind.CHANGED,
            SecFilingChangeKind.UNCHANGED,
        }:
            raise ValueError("SEC filing section change is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingDiffResult:
    status: SecFilingDiffStatus
    requested_accession: str
    comparison_accession: str
    relationship: SecFilingDiffRelationship | None = None
    baseline: SecFilingComparisonIdentity | None = None
    target: SecFilingComparisonIdentity | None = None
    fact_changes: tuple[SecFilingFactChange, ...] = ()
    section_change: SecFilingSectionChange | None = None
    unchanged_fact_count: int = 0
    baseline_retrieval_trace: SecFilingRetrievalTrace | None = None
    target_retrieval_trace: SecFilingRetrievalTrace | None = None
    error_code: str | None = None
    version: str = SEC_FILING_DIFF_VERSION

    def __post_init__(self) -> None:
        if self.version != SEC_FILING_DIFF_VERSION:
            raise ValueError("SEC filing diff version is unsupported")
        if not _ACCESSION_PATTERN.fullmatch(
            self.requested_accession
        ) or not _ACCESSION_PATTERN.fullmatch(self.comparison_accession):
            raise ValueError("SEC filing diff accession is invalid")
        changes = tuple(self.fact_changes)
        if len(changes) > SEC_MAX_DIFF_FACT_CHANGES or self.unchanged_fact_count < 0:
            raise ValueError("SEC filing diff fact counts are invalid")
        complete = self.status is SecFilingDiffStatus.OK
        if complete != all(
            value is not None for value in (self.relationship, self.baseline, self.target)
        ):
            raise ValueError("SEC filing diff comparison state is inconsistent")
        if complete == (self.error_code is not None):
            raise ValueError("SEC filing diff error state is inconsistent")
        if complete:
            if self.baseline is None or self.target is None:
                raise AssertionError("Validated SEC filing diff lost its identities")
            accessions = {self.baseline.accession, self.target.accession}
            if accessions != {self.requested_accession, self.comparison_accession}:
                raise ValueError("SEC filing diff identities do not match the request")
        elif changes or self.section_change is not None or self.unchanged_fact_count:
            raise ValueError("Failed SEC filing diff cannot expose partial comparison output")
        object.__setattr__(self, "fact_changes", changes)


@dataclass(frozen=True, slots=True)
class SecFilingDiffService:
    repository: SecFilingContentRepository
    content_service: SecFilingContentService
    xbrl_service: SecXbrlService
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def compare(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        comparison_accession: str,
        section_query: str,
        taxonomy: str | None = None,
        concept: str | None = None,
        fact_limit: int = SEC_MAX_DIFF_TOOL_FACT_CHANGES,
    ) -> SecFilingDiffResult:
        if not _ACCESSION_PATTERN.fullmatch(comparison_accession):
            raise ValueError("SEC filing comparison accession is invalid")
        if comparison_accession == financial_scope.accession:
            return self._failed(
                financial_scope.accession,
                comparison_accession,
                SecFilingDiffStatus.NOT_COMPARABLE,
                "same_accession",
            )
        if not section_query.strip() or len(section_query) > 500:
            raise ValueError("SEC filing diff section query is invalid")
        if not 1 <= fact_limit <= SEC_MAX_DIFF_FACT_CHANGES:
            raise ValueError("SEC filing diff fact limit is invalid")
        if financial_scope.as_of > self.clock():
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)

        requested_preparation, comparison_preparation = await asyncio.gather(
            self.repository.prepare_comparison_identity(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=financial_scope.accession,
                as_of=financial_scope.as_of,
            ),
            self.repository.prepare_comparison_identity(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=comparison_accession,
                as_of=financial_scope.as_of,
            ),
        )
        failed = self._preparation_failure(
            financial_scope.accession,
            comparison_accession,
            requested_preparation,
            comparison_preparation,
        )
        if failed is not None:
            return failed
        requested = requested_preparation.identity
        comparison = comparison_preparation.identity
        if requested is None or comparison is None:
            raise AssertionError("Ready SEC filing comparison lost its identity")
        if (
            requested.cik != financial_scope.cik
            or requested.form.value != financial_scope.form.value
            or requested.report_date != financial_scope.report_period
        ):
            return self._failed(
                financial_scope.accession,
                comparison_accession,
                SecFilingDiffStatus.PERMISSION_DENIED,
                "financial_scope_mismatch",
            )
        ordered = resolve_filing_diff_relationship(requested, comparison)
        if ordered is None:
            return self._failed(
                financial_scope.accession,
                comparison_accession,
                SecFilingDiffStatus.NOT_COMPARABLE,
                "filing_scope_not_comparable",
            )
        relationship, baseline, target = ordered
        fact_query = SecXbrlFactQuery(
            taxonomy=taxonomy,
            concept=concept,
            source_kinds=tuple(SecXbrlSourceKind),
            limit=100,
        )
        baseline_facts, target_facts, baseline_sections, target_sections = await asyncio.gather(
            self.xbrl_service.get_imported_facts(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=baseline.accession,
                as_of=financial_scope.as_of,
                query=fact_query,
            ),
            self.xbrl_service.get_imported_facts(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=target.accession,
                as_of=financial_scope.as_of,
                query=fact_query,
            ),
            self.content_service.search_imported(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=baseline.accession,
                as_of=financial_scope.as_of,
                query=section_query.strip(),
            ),
            self.content_service.search_imported(
                scope,
                knowledge_base_ids=knowledge_base_ids,
                accession=target.accession,
                as_of=financial_scope.as_of,
                query=section_query.strip(),
            ),
        )
        for result in (baseline_facts, target_facts, baseline_sections, target_sections):
            mapped = _map_content_status(result.status)
            if mapped is not None:
                return self._failed(
                    financial_scope.accession,
                    comparison_accession,
                    mapped,
                    result.error_code or f"diff_input_{result.status.value}",
                )
        try:
            fact_changes, unchanged_count = _compare_facts(
                baseline_facts.facts,
                target_facts.facts,
                baseline_report_date=baseline.report_date,
                target_report_date=target.report_date,
                limit=fact_limit,
            )
        except ValueError:
            return self._failed(
                financial_scope.accession,
                comparison_accession,
                SecFilingDiffStatus.NOT_COMPARABLE,
                "ambiguous_fact_identity",
            )
        section_change = _compare_section_hits(baseline_sections.hits, target_sections.hits)
        if section_change is None:
            return self._failed(
                financial_scope.accession,
                comparison_accession,
                SecFilingDiffStatus.NOT_COMPARABLE,
                "section_identity_not_shared",
            )
        return SecFilingDiffResult(
            status=SecFilingDiffStatus.OK,
            requested_accession=financial_scope.accession,
            comparison_accession=comparison_accession,
            relationship=relationship,
            baseline=baseline,
            target=target,
            fact_changes=fact_changes,
            section_change=section_change,
            unchanged_fact_count=unchanged_count,
            baseline_retrieval_trace=baseline_sections.retrieval_trace,
            target_retrieval_trace=target_sections.retrieval_trace,
            error_code=None,
        )

    def _preparation_failure(
        self,
        requested_accession: str,
        comparison_accession: str,
        requested: SecFilingComparisonPreparation,
        comparison: SecFilingComparisonPreparation,
    ) -> SecFilingDiffResult | None:
        for preparation in (requested, comparison):
            if preparation.status is not SecFilingDiffStatus.OK:
                return self._failed(
                    requested_accession,
                    comparison_accession,
                    preparation.status,
                    preparation.error_code or f"comparison_{preparation.status.value}",
                )
        return None

    @staticmethod
    def _failed(
        requested_accession: str,
        comparison_accession: str,
        status: SecFilingDiffStatus,
        error_code: str,
    ) -> SecFilingDiffResult:
        return SecFilingDiffResult(
            status=status,
            requested_accession=requested_accession,
            comparison_accession=comparison_accession,
            error_code=error_code,
        )


def _base_form(form: SecFilingForm) -> SecFilingForm:
    if form is SecFilingForm.TEN_K_AMENDMENT:
        return SecFilingForm.TEN_K
    if form is SecFilingForm.TEN_Q_AMENDMENT:
        return SecFilingForm.TEN_Q
    return form


def resolve_filing_diff_relationship(
    left: SecFilingComparisonIdentity,
    right: SecFilingComparisonIdentity,
) -> (
    tuple[
        SecFilingDiffRelationship,
        SecFilingComparisonIdentity,
        SecFilingComparisonIdentity,
    ]
    | None
):
    """Return a deterministic base/target order only for supported filing relationships."""
    if left.cik != right.cik or _base_form(left.form) is not _base_form(right.form):
        return None
    for amendment, base in ((left, right), (right, left)):
        if (
            amendment.amendment_relation_status is SecAmendmentRelationStatus.RESOLVED
            and amendment.base_accession == base.accession
            and amendment.report_date == base.report_date
            and amendment.form
            in {
                SecFilingForm.TEN_K_AMENDMENT,
                SecFilingForm.TEN_Q_AMENDMENT,
            }
            and base.form is _base_form(amendment.form)
        ):
            return SecFilingDiffRelationship.BASE_AMENDMENT, base, amendment
    if any(
        item.amendment_relation_status is not SecAmendmentRelationStatus.NOT_AMENDMENT
        or item.form in {SecFilingForm.TEN_K_AMENDMENT, SecFilingForm.TEN_Q_AMENDMENT}
        for item in (left, right)
    ):
        return None
    baseline, target = sorted((left, right), key=lambda item: (item.report_date, item.accession))
    day_gap = (target.report_date - baseline.report_date).days
    comparable_gap = (
        300 <= day_gap <= 430
        if _base_form(baseline.form) is SecFilingForm.TEN_K
        else 70 <= day_gap <= 110
    )
    if not comparable_gap:
        return None
    return SecFilingDiffRelationship.ADJACENT_PERIOD, baseline, target


def _map_content_status(status: SecFilingContentStatus) -> SecFilingDiffStatus | None:
    return {
        SecFilingContentStatus.OK: None,
        SecFilingContentStatus.NO_RESULT: SecFilingDiffStatus.NO_RESULT,
        SecFilingContentStatus.NOT_READY: SecFilingDiffStatus.NOT_READY,
        SecFilingContentStatus.PERMISSION_DENIED: SecFilingDiffStatus.PERMISSION_DENIED,
        SecFilingContentStatus.DEPENDENCY_FAILED: SecFilingDiffStatus.DEPENDENCY_FAILED,
    }[status]


def _period_bucket(fact: SecXbrlFact) -> str:
    if fact.period.instant is not None:
        return "instant"
    if fact.period.start_date is None or fact.period.end_date is None:
        return fact.period.kind.value
    days = (fact.period.end_date - fact.period.start_date).days
    if 70 <= days <= 110:
        return "quarter"
    if 160 <= days <= 300:
        return "year_to_date"
    if 300 <= days <= 400:
        return "annual"
    return f"duration_{days}"


def _fact_key(fact: SecXbrlFact) -> tuple[object, ...]:
    return (
        fact.taxonomy,
        fact.concept,
        fact.unit,
        fact.period.kind.value,
        _period_bucket(fact),
        fact.dimensions,
        fact.is_custom,
    )


def _fact_at_report_date(fact: SecXbrlFact, report_date: date) -> bool:
    anchor = fact.period.instant if fact.period.instant is not None else fact.period.end_date
    return anchor == report_date


def _canonical_facts(
    facts: tuple[SecXbrlFact, ...],
    *,
    report_date: date,
) -> dict[tuple[object, ...], SecXbrlFact]:
    priority = {
        SecXbrlSourceKind.RAW_INSTANCE: 0,
        SecXbrlSourceKind.RAW_INLINE: 1,
        SecXbrlSourceKind.COMPANYFACTS_AGGREGATE: 2,
    }
    grouped: dict[tuple[object, ...], list[SecXbrlFact]] = {}
    for fact in facts:
        if _fact_at_report_date(fact, report_date):
            grouped.setdefault(_fact_key(fact), []).append(fact)
    selected: dict[tuple[object, ...], SecXbrlFact] = {}
    for key, candidates in grouped.items():
        best_priority = min(priority[item.source_kind] for item in candidates)
        preferred = tuple(
            item for item in candidates if priority[item.source_kind] == best_priority
        )
        payloads = {(item.value, item.decimals, item.scale) for item in preferred}
        if len(payloads) != 1:
            raise ValueError("SEC filing fact identity resolves to conflicting values")
        selected[key] = min(preferred, key=lambda item: (item.ordinal, str(item.id)))
    return selected


def _compare_facts(
    baseline_facts: tuple[SecXbrlFact, ...],
    target_facts: tuple[SecXbrlFact, ...],
    *,
    baseline_report_date: date,
    target_report_date: date,
    limit: int,
) -> tuple[tuple[SecFilingFactChange, ...], int]:
    baseline = _canonical_facts(baseline_facts, report_date=baseline_report_date)
    target = _canonical_facts(target_facts, report_date=target_report_date)
    changes: list[SecFilingFactChange] = []
    unchanged = 0
    for key in sorted(set(baseline) | set(target), key=lambda item: repr(item)):
        before = baseline.get(key)
        after = target.get(key)
        if before is None:
            kind = SecFilingChangeKind.ADDED
        elif after is None:
            kind = SecFilingChangeKind.REMOVED
        elif (before.value, before.decimals, before.scale) == (
            after.value,
            after.decimals,
            after.scale,
        ):
            unchanged += 1
            continue
        else:
            kind = SecFilingChangeKind.CHANGED
        representative = before if before is not None else after
        if representative is None:
            raise AssertionError("SEC filing fact diff lost both sides")
        changes.append(
            SecFilingFactChange(
                taxonomy=representative.taxonomy,
                concept=representative.concept,
                unit=representative.unit,
                period_kind=representative.period.kind.value,
                period_bucket=_period_bucket(representative),
                dimensions=representative.dimensions,
                is_custom=representative.is_custom,
                change_kind=kind,
                baseline=before,
                target=after,
            )
        )
    return tuple(changes[:limit]), unchanged


def _normalized_section(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _compare_section_hits(
    baseline_hits: tuple[SecFilingSearchHit, ...],
    target_hits: tuple[SecFilingSearchHit, ...],
) -> SecFilingSectionChange | None:
    baseline = _best_section_hits(baseline_hits)
    target = _best_section_hits(target_hits)
    shared = sorted(set(baseline) & set(target))
    if not shared:
        return None
    section_key = max(
        shared,
        key=lambda key: (min(baseline[key].score, target[key].score), key),
    )
    before = baseline[section_key]
    after = target[section_key]
    return SecFilingSectionChange(
        section=after.section,
        change_kind=(
            SecFilingChangeKind.UNCHANGED
            if before.content_sha256 == after.content_sha256
            else SecFilingChangeKind.CHANGED
        ),
        baseline=before,
        target=after,
    )


def _best_section_hits(
    hits: tuple[SecFilingSearchHit, ...],
) -> dict[str, SecFilingSearchHit]:
    grouped: dict[str, list[SecFilingSearchHit]] = {}
    for hit in hits:
        grouped.setdefault(_normalized_section(hit.section), []).append(hit)
    return {
        section: min(
            candidates,
            key=lambda item: (-item.score, str(item.chunk_id)),
        )
        for section, candidates in grouped.items()
    }
