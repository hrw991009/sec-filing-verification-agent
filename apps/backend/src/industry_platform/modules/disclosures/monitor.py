"""Deterministic SEC disclosure Monitor analysis and watermark orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_utc
from industry_platform.modules.disclosures.diff import (
    SecFilingChangeKind,
    SecFilingDiffResult,
    SecFilingDiffService,
    SecFilingDiffStatus,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFilingCandidate,
    SecFilingContentError,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSearchHit,
    SecFilingSelectionStatus,
    SecSourceError,
    SecXbrlFact,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingImportService
from industry_platform.modules.disclosures.service import SecFilingSelectionService
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope

SEC_MONITOR_TASK_NAME = "industry_platform.disclosures.monitor.execute"
SEC_MONITOR_RULE_SET_VERSION = "sec-monitor-rules-v1"
SEC_MONITOR_DIFF_VERSION = "sec-filing-diff-v1"
SEC_MONITOR_MAX_RULES = 16


class SecMonitorRuleKind(StrEnum):
    NEW_FILING = "new_filing"
    AMENDMENT = "amendment"
    FACT_ABSOLUTE_CHANGE = "fact_absolute_change"
    SECTION_CHANGE = "section_change"


class SecMonitorStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELETED = "deleted"


class SecMonitorRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"


class SecCaseVerificationStatus(StrEnum):
    VERIFIED = "verified"


class SecCaseNotificationStatus(StrEnum):
    PENDING = "pending"


class SecMonitorError(RuntimeError):
    """Base failure carrying a stable, non-sensitive execution code."""

    def __init__(self, code: str) -> None:
        super().__init__("SEC Monitor execution failed")
        self.code = code


class SecMonitorDependencyError(SecMonitorError):
    """Retryable official-source, indexing, or persistence dependency failure."""


class SecMonitorStateError(SecMonitorError):
    """Non-retryable persisted Monitor or comparison state failure."""


@dataclass(frozen=True, slots=True)
class SecMonitorRule:
    rule_id: UUID
    kind: SecMonitorRuleKind
    rule_version: str
    section_query: str
    taxonomy: str | None = None
    concept: str | None = None
    unit: str | None = None
    threshold: str | None = None
    comparator: str | None = None

    def __post_init__(self) -> None:
        if self.rule_id.int == 0 or self.rule_version != SEC_MONITOR_RULE_SET_VERSION:
            raise ValueError("SEC Monitor rule identity is invalid")
        query = self.section_query.strip()
        if not query or len(query) > 500 or query != self.section_query:
            raise ValueError("SEC Monitor section query is invalid")
        fact_rule = self.kind is SecMonitorRuleKind.FACT_ABSOLUTE_CHANGE
        fact_values = (self.taxonomy, self.concept, self.unit, self.threshold, self.comparator)
        if fact_rule != all(value is not None for value in fact_values):
            raise ValueError("SEC Monitor fact rule is incomplete")
        if not fact_rule and any(value is not None for value in fact_values):
            raise ValueError("SEC Monitor non-fact rule has fact configuration")
        if fact_rule:
            try:
                threshold = Decimal(self.threshold or "")
            except InvalidOperation:
                raise ValueError("SEC Monitor fact threshold is invalid") from None
            if (
                not threshold.is_finite()
                or threshold < 0
                or self.threshold != format(threshold, "f")
                or self.comparator != "absolute_delta_gte"
            ):
                raise ValueError("SEC Monitor fact threshold is invalid")


@dataclass(frozen=True, slots=True)
class SecMonitorWatermark:
    watermark_id: UUID
    revision: int
    coverage_version: str
    accepted_at: datetime | None
    accession: str | None

    def __post_init__(self) -> None:
        if self.watermark_id.int == 0 or self.revision < 1 or not self.coverage_version:
            raise ValueError("SEC Monitor watermark identity is invalid")
        if (self.accepted_at is None) != (self.accession is None):
            raise ValueError("SEC Monitor watermark cursor is incomplete")
        if self.accepted_at is not None:
            require_utc(self.accepted_at, field_name="SEC Monitor watermark acceptance")


@dataclass(frozen=True, slots=True)
class SecMonitorExecutionRequest:
    run_id: UUID
    job_id: UUID
    monitor_id: UUID
    scope: WorkspaceScope
    owner_user_id: UUID
    cik: str
    allowed_forms: tuple[SecFilingForm, ...]
    knowledge_base_id: UUID
    rules: tuple[SecMonitorRule, ...]
    watermark: SecMonitorWatermark
    window_start: datetime
    window_end: datetime
    trace_id: TraceId

    def __post_init__(self) -> None:
        identifiers = (
            self.run_id,
            self.job_id,
            self.monitor_id,
            self.owner_user_id,
            self.knowledge_base_id,
        )
        if any(value.int == 0 for value in identifiers):
            raise ValueError("SEC Monitor execution identity is invalid")
        if not self.cik.isdigit() or len(self.cik) != 10 or self.cik == "0000000000":
            raise ValueError("SEC Monitor filer identity is invalid")
        forms = tuple(self.allowed_forms)
        rules = tuple(self.rules)
        if (
            not forms
            or forms != tuple(sorted(set(forms), key=lambda value: value.value))
            or not rules
            or len(rules) > SEC_MONITOR_MAX_RULES
            or len({rule.rule_id for rule in rules}) != len(rules)
        ):
            raise ValueError("SEC Monitor execution configuration is invalid")
        for value, name in ((self.window_start, "start"), (self.window_end, "end")):
            require_utc(value, field_name=f"SEC Monitor window {name}")
        if self.window_end < self.window_start:
            raise ValueError("SEC Monitor execution window is invalid")
        object.__setattr__(self, "allowed_forms", forms)
        object.__setattr__(self, "rules", rules)


@dataclass(frozen=True, slots=True)
class SecMonitorEvidencePair:
    baseline_text: SecFilingSearchHit | None = field(default=None, repr=False)
    target_text: SecFilingSearchHit | None = field(default=None, repr=False)
    baseline_fact: SecXbrlFact | None = field(default=None, repr=False)
    target_fact: SecXbrlFact | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        text_pair = self.baseline_text is not None and self.target_text is not None
        fact_pair = self.baseline_fact is not None and self.target_fact is not None
        if text_pair == fact_pair:
            raise ValueError("SEC Monitor Evidence pair must use exactly one source kind")


@dataclass(frozen=True, slots=True)
class SecMonitorFinding:
    rule: SecMonitorRule
    target: SecFilingCandidate
    diff: SecFilingDiffResult
    evidence: SecMonitorEvidencePair

    def __post_init__(self) -> None:
        if self.diff.status is not SecFilingDiffStatus.OK or self.diff.target is None:
            raise ValueError("SEC Monitor finding requires a complete diff")
        if self.diff.target.accession != self.target.accession:
            raise ValueError("SEC Monitor finding target is inconsistent")


@dataclass(frozen=True, slots=True)
class SecMonitorAnalysis:
    coverage_version: str
    accepted_at: datetime | None
    accession: str | None
    findings: tuple[SecMonitorFinding, ...]

    def __post_init__(self) -> None:
        if not self.coverage_version or (self.accepted_at is None) != (self.accession is None):
            raise ValueError("SEC Monitor analysis watermark is invalid")
        if self.accepted_at is not None:
            require_utc(self.accepted_at, field_name="SEC Monitor analysis acceptance")
        object.__setattr__(self, "findings", tuple(self.findings))


@dataclass(frozen=True, slots=True)
class SecMonitorExecutionResult:
    run_id: UUID
    monitor_id: UUID
    watermark_id: UUID
    watermark_revision: int
    case_ids: tuple[UUID, ...]


class SecMonitorRepository(Protocol):
    async def completed_result(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
    ) -> SecMonitorExecutionResult | None: ...

    async def prepare(self, *, job_id: UUID, workspace_id: UUID) -> SecMonitorExecutionRequest: ...

    async def commit(
        self,
        request: SecMonitorExecutionRequest,
        analysis: SecMonitorAnalysis,
    ) -> SecMonitorExecutionResult: ...


class SecMonitorAnalyzer(Protocol):
    async def analyze(self, request: SecMonitorExecutionRequest) -> SecMonitorAnalysis: ...


@dataclass(frozen=True, slots=True)
class SecMonitorApplicationService:
    repository: SecMonitorRepository
    analyzer: SecMonitorAnalyzer

    async def execute_job(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
    ) -> SecMonitorExecutionResult:
        completed = await self.repository.completed_result(
            job_id=job_id,
            workspace_id=workspace_id,
        )
        if completed is not None:
            return completed
        request = await self.repository.prepare(job_id=job_id, workspace_id=workspace_id)
        analysis = await self.analyzer.analyze(request)
        return await self.repository.commit(request, analysis)


@dataclass(frozen=True, slots=True)
class SecMonitorAnalysisService:
    selection: SecFilingSelectionService
    imports: SecFilingImportService
    xbrl: SecXbrlService
    diff: SecFilingDiffService

    async def analyze(self, request: SecMonitorExecutionRequest) -> SecMonitorAnalysis:
        selection_scope = FilingSelectionScope(
            cik=request.cik,
            allowed_forms=request.allowed_forms,
            report_period_start=request.window_end.date() - timedelta(days=3_660),
            report_period_end=request.window_end.date(),
            as_of=request.window_end,
            amendment_policy=SecAmendmentPolicy.AS_FILED,
        )
        try:
            selected = await self.selection.select(
                request.scope,
                selection_scope=selection_scope,
            )
        except SecSourceError as error:
            raise SecMonitorDependencyError(error.code.value) from None
        if selected.status is SecFilingSelectionStatus.INCOMPLETE:
            raise SecMonitorDependencyError(selected.error_code or "sec_coverage_incomplete")
        if selected.status is SecFilingSelectionStatus.NO_RESULT:
            return SecMonitorAnalysis(
                coverage_version=selected.coverage_version,
                accepted_at=request.watermark.accepted_at,
                accession=request.watermark.accession,
                findings=(),
            )

        candidates = tuple(
            candidate
            for candidate in sorted(
                selected.filings,
                key=lambda item: (item.accepted_at, item.accession),
            )
            if _after_watermark(candidate, request.watermark)
            and candidate.accepted_at <= request.window_end
        )
        findings: list[SecMonitorFinding] = []
        for target in candidates:
            baseline = _baseline_for(target, selected.filings)
            if baseline is None:
                raise SecMonitorStateError("monitor_baseline_missing")
            comparison = await self._compare(request, baseline=baseline, target=target)
            evidence = _evidence_pair(comparison)
            for rule in request.rules:
                if _rule_matches(rule, target=target, comparison=comparison):
                    findings.append(
                        SecMonitorFinding(
                            rule=rule,
                            target=target,
                            diff=comparison,
                            evidence=evidence,
                        )
                    )
        cursor = candidates[-1] if candidates else None
        return SecMonitorAnalysis(
            coverage_version=selected.coverage_version,
            accepted_at=(request.watermark.accepted_at if cursor is None else cursor.accepted_at),
            accession=(request.watermark.accession if cursor is None else cursor.accession),
            findings=tuple(findings),
        )

    async def _compare(
        self,
        request: SecMonitorExecutionRequest,
        *,
        baseline: SecFilingCandidate,
        target: SecFilingCandidate,
    ) -> SecFilingDiffResult:
        try:
            for candidate in (baseline, target):
                imported = await self.imports.import_filing(
                    request.scope,
                    accession=candidate.accession,
                    knowledge_base_id=request.knowledge_base_id,
                    as_of=request.window_end,
                    trace_id=request.trace_id,
                )
                if imported.status in {
                    SecFilingImportStatus.FAILED,
                    SecFilingImportStatus.CANCELLED,
                }:
                    raise SecMonitorStateError("monitor_import_state_invalid")
                await self.xbrl.sync(
                    request.scope,
                    accession=candidate.accession,
                    knowledge_base_id=request.knowledge_base_id,
                )
            taxonomy, concept = _common_fact_filter(request.rules)
            comparison = await self.diff.compare(
                request.scope,
                knowledge_base_ids=(request.knowledge_base_id,),
                financial_scope=FinancialScope(
                    cik=target.cik,
                    accession=target.accession,
                    form=FinancialForm(target.form.value),
                    report_period=target.report_date,
                    as_of=request.window_end,
                    unit="USD",
                    scale=0,
                ),
                comparison_accession=baseline.accession,
                section_query=_common_section_query(request.rules),
                taxonomy=taxonomy,
                concept=concept,
                fact_limit=20,
            )
        except SecMonitorError:
            raise
        except (SecSourceError, SecFilingContentError) as error:
            code = getattr(error, "code", "monitor_sec_dependency_failed")
            stable_code = code.value if hasattr(code, "value") else str(code)
            raise SecMonitorDependencyError(stable_code) from None
        if comparison.status in {
            SecFilingDiffStatus.NOT_READY,
            SecFilingDiffStatus.DEPENDENCY_FAILED,
        }:
            raise SecMonitorDependencyError(comparison.error_code or "monitor_diff_not_ready")
        if comparison.status is not SecFilingDiffStatus.OK:
            raise SecMonitorStateError(comparison.error_code or "monitor_diff_invalid")
        return comparison


def _after_watermark(candidate: SecFilingCandidate, watermark: SecMonitorWatermark) -> bool:
    if watermark.accepted_at is None or watermark.accession is None:
        return True
    return (candidate.accepted_at, candidate.accession) > (
        watermark.accepted_at,
        watermark.accession,
    )


def _base_form(form: SecFilingForm) -> SecFilingForm:
    if form is SecFilingForm.TEN_K_AMENDMENT:
        return SecFilingForm.TEN_K
    if form is SecFilingForm.TEN_Q_AMENDMENT:
        return SecFilingForm.TEN_Q
    return form


def _baseline_for(
    target: SecFilingCandidate,
    filings: tuple[SecFilingCandidate, ...],
) -> SecFilingCandidate | None:
    if target.amendment_relation_status is SecAmendmentRelationStatus.UNRESOLVED:
        raise SecMonitorStateError("monitor_amendment_unresolved")
    if target.base_accession is not None:
        return next(
            (candidate for candidate in filings if candidate.accession == target.base_accession),
            None,
        )
    comparable = [
        candidate
        for candidate in filings
        if candidate.accession != target.accession
        and candidate.cik == target.cik
        and candidate.amendment_relation_status is SecAmendmentRelationStatus.NOT_AMENDMENT
        and _base_form(candidate.form) is _base_form(target.form)
        and candidate.report_date < target.report_date
        and candidate.accepted_at < target.accepted_at
    ]
    return max(comparable, key=lambda item: (item.report_date, item.accepted_at), default=None)


def _common_section_query(rules: tuple[SecMonitorRule, ...]) -> str:
    values = {rule.section_query for rule in rules}
    if len(values) != 1:
        raise SecMonitorStateError("monitor_section_query_conflict")
    return next(iter(values))


def _common_fact_filter(rules: tuple[SecMonitorRule, ...]) -> tuple[str | None, str | None]:
    values = {
        (rule.taxonomy, rule.concept)
        for rule in rules
        if rule.kind is SecMonitorRuleKind.FACT_ABSOLUTE_CHANGE
    }
    if len(values) > 1:
        raise SecMonitorStateError("monitor_fact_filter_conflict")
    return next(iter(values), (None, None))


def _rule_matches(
    rule: SecMonitorRule,
    *,
    target: SecFilingCandidate,
    comparison: SecFilingDiffResult,
) -> bool:
    if rule.kind is SecMonitorRuleKind.NEW_FILING:
        return target.amendment_relation_status is SecAmendmentRelationStatus.NOT_AMENDMENT
    if rule.kind is SecMonitorRuleKind.AMENDMENT:
        return target.amendment_relation_status is SecAmendmentRelationStatus.RESOLVED
    if rule.kind is SecMonitorRuleKind.SECTION_CHANGE:
        return (
            comparison.section_change is not None
            and comparison.section_change.change_kind is SecFilingChangeKind.CHANGED
        )
    threshold = Decimal(rule.threshold or "0")
    for change in comparison.fact_changes:
        if (
            change.baseline is None
            or change.target is None
            or change.taxonomy != rule.taxonomy
            or change.concept != rule.concept
            or change.unit != rule.unit
        ):
            continue
        try:
            baseline = Decimal(change.baseline.value) * (
                Decimal(10) ** (change.baseline.scale or 0)
            )
            target_value = Decimal(change.target.value) * (
                Decimal(10) ** (change.target.scale or 0)
            )
        except InvalidOperation:
            continue
        if (
            baseline.is_finite()
            and target_value.is_finite()
            and abs(target_value - baseline) >= threshold
        ):
            return True
    return False


def _evidence_pair(comparison: SecFilingDiffResult) -> SecMonitorEvidencePair:
    if comparison.section_change is not None:
        return SecMonitorEvidencePair(
            baseline_text=comparison.section_change.baseline,
            target_text=comparison.section_change.target,
        )
    for change in comparison.fact_changes:
        if change.baseline is not None and change.target is not None:
            return SecMonitorEvidencePair(
                baseline_fact=change.baseline,
                target_fact=change.target,
            )
    raise SecMonitorStateError("monitor_evidence_pair_missing")


def monitor_job_result_payload(result: SecMonitorExecutionResult) -> Mapping[str, object]:
    return {
        "case_ids": [str(case_id) for case_id in result.case_ids],
        "monitor_id": str(result.monitor_id),
        "monitor_run_id": str(result.run_id),
        "watermark_id": str(result.watermark_id),
        "watermark_revision": result.watermark_revision,
    }
