"""Deterministic SEC Claim verification contracts and application service."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID, uuid5

from industry_platform.modules.agent_runtime.domain import (
    RunStopReason,
    require_non_nil_uuid,
    require_utc,
)
from industry_platform.modules.evidence.domain import (
    ClaimEvidenceRelation,
    Evidence,
    EvidenceNotFoundError,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    RelationStatus,
    ResearchClaim,
    SecFilingChunkLocatorV1,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialOperand,
    FinancialOperator,
    FinancialRoundingMode,
    FinancialScope,
    calculate_financial_result,
)
from industry_platform.modules.research.ports import ResearchQueryRepository
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

VERIFICATION_REPORT_SCHEMA_VERSION: Final = 1
VERIFICATION_CHECKER_VERSION: Final = "sec-claim-verifier-v1"
MAX_VERIFICATION_CLAIMS: Final = 100
MAX_VERIFICATION_REFS: Final = 128


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class VerificationClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class VerificationIssueCode(StrEnum):
    CLAIM_NOT_FOUND = "claim_not_found"
    RELATION_INVALIDATED = "relation_invalidated"
    EVIDENCE_INACTIVE = "evidence_inactive"
    AUTHORIZATION_MISMATCH = "authorization_mismatch"
    CITATION_UNRESOLVABLE = "citation_unresolvable"
    SCOPE_IDENTITY_MISMATCH = "scope_identity_mismatch"
    FUTURE_SOURCE = "future_source"
    SOURCE_HASH_MISMATCH = "source_hash_mismatch"
    CALCULATION_INPUT_MISSING = "calculation_input_missing"
    CALCULATION_MISMATCH = "calculation_mismatch"
    CLAIM_CONFLICT = "claim_conflict"
    CLAIM_REFUTED = "claim_refuted"
    MISSING_EVIDENCE = "missing_evidence"
    COVERAGE_INCOMPLETE = "coverage_incomplete"


class VerificationIssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class VerificationRepairability(StrEnum):
    REPAIRABLE = "repairable"
    TERMINAL = "terminal"


class VerificationAllowedAction(StrEnum):
    TARGETED_RETRIEVE = "targeted_retrieve"
    RECALCULATE = "recalculate"


@dataclass(frozen=True, slots=True)
class VerificationEvidenceState:
    evidence: Evidence
    available: bool


@dataclass(frozen=True, slots=True)
class VerificationEvidenceSnapshot:
    evidence_id: UUID
    revision: int
    status: EvidenceStatus
    content_sha256: str
    available: bool

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.evidence_id, field_name="Verification Evidence ID")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Verification Evidence revision is invalid")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("Verification Evidence hash is invalid")


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    issue_id: UUID
    code: VerificationIssueCode
    severity: VerificationIssueSeverity
    claim_id: UUID | None
    expected_refs: tuple[str, ...]
    observed_refs: tuple[str, ...]
    repairability: VerificationRepairability
    allowed_action: VerificationAllowedAction | None
    details_digest: str

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.issue_id, field_name="Verification issue ID")
        if self.claim_id is not None:
            require_non_nil_uuid(self.claim_id, field_name="Verification issue Claim ID")
        object.__setattr__(self, "expected_refs", _bounded_refs(self.expected_refs))
        object.__setattr__(self, "observed_refs", _bounded_refs(self.observed_refs))
        if (self.repairability is VerificationRepairability.REPAIRABLE) != (
            self.allowed_action is not None
        ):
            raise ValueError("Verification issue repair action is inconsistent")
        if len(self.details_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.details_digest
        ):
            raise ValueError("Verification issue digest is invalid")


@dataclass(frozen=True, slots=True)
class VerificationClaimResult:
    claim_id: UUID
    claim_revision: int | None
    required: bool
    verdict: VerificationClaimVerdict
    coverage: float
    evidence_refs: tuple[UUID, ...]
    citation_refs: tuple[UUID, ...]
    calculation_refs: tuple[UUID, ...]
    issues: tuple[VerificationIssue, ...]

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.claim_id, field_name="Verification Claim ID")
        if self.claim_revision is not None and (
            isinstance(self.claim_revision, bool) or self.claim_revision < 1
        ):
            raise ValueError("Verification Claim revision is invalid")
        if isinstance(self.coverage, bool) or not 0 <= self.coverage <= 1:
            raise ValueError("Verification Claim coverage is invalid")
        for name in ("evidence_refs", "citation_refs", "calculation_refs"):
            object.__setattr__(self, name, _unique_ids(getattr(self, name), name, required=False))
        if not set(self.citation_refs).issubset(self.evidence_refs) or not set(
            self.calculation_refs
        ).issubset(self.citation_refs):
            raise ValueError("Verification Claim refs are inconsistent")
        decisive = self.verdict is not VerificationClaimVerdict.INSUFFICIENT
        if decisive != (self.coverage == 1) or (decisive and not self.citation_refs):
            raise ValueError("Verification Claim verdict is unsupported")
        issues = tuple(self.issues)
        if any(issue.claim_id != self.claim_id for issue in issues):
            raise ValueError("Verification Claim issue belongs to another Claim")
        object.__setattr__(self, "issues", issues)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    report_id: UUID
    research_run_id: UUID
    agent_run_id: UUID
    workspace_id: UUID
    draft_id: UUID
    revision: int
    graph_version: str
    financial_scope: FinancialScope
    status: VerificationStatus
    coverage: float
    required_claim_ids: tuple[UUID, ...]
    claims: tuple[VerificationClaimResult, ...]
    evidence_snapshots: tuple[VerificationEvidenceSnapshot, ...]
    issues: tuple[VerificationIssue, ...]
    runtime_stop_reason: RunStopReason | None
    created_at: datetime
    checker_version: str = VERIFICATION_CHECKER_VERSION
    schema_version: int = VERIFICATION_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for identifier, name in (
            (self.report_id, "Verification report ID"),
            (self.research_run_id, "Verification Research Run ID"),
            (self.agent_run_id, "Verification Agent Run ID"),
            (self.workspace_id, "Verification Workspace ID"),
            (self.draft_id, "Verification Draft ID"),
        ):
            require_non_nil_uuid(identifier, field_name=name)
        if self.schema_version != VERIFICATION_REPORT_SCHEMA_VERSION:
            raise ValueError("Verification report schema version is unsupported")
        if self.checker_version != VERIFICATION_CHECKER_VERSION:
            raise ValueError("Verification checker version is unsupported")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Verification report revision is invalid")
        if not self.graph_version.strip() or len(self.graph_version) > 128:
            raise ValueError("Verification graph version is invalid")
        required_claim_ids = _unique_ids(self.required_claim_ids, "required_claim_ids")
        claims = tuple(self.claims)
        if not required_claim_ids or len(claims) != len(required_claim_ids):
            raise ValueError("Verification report required Claims are invalid")
        if tuple(item.claim_id for item in claims) != required_claim_ids:
            raise ValueError("Verification report Claim order is invalid")
        evidence_snapshots = tuple(self.evidence_snapshots)
        if len({item.evidence_id for item in evidence_snapshots}) != len(evidence_snapshots):
            raise ValueError("Verification report Evidence snapshots are duplicated")
        referenced_evidence = {
            evidence_id for claim in claims for evidence_id in claim.evidence_refs
        }
        if not referenced_evidence.issubset(item.evidence_id for item in evidence_snapshots):
            raise ValueError("Verification report Evidence snapshot coverage is incomplete")
        issues = tuple(self.issues)
        claim_issues = tuple(issue for claim in claims for issue in claim.issues)
        if issues != claim_issues + tuple(issue for issue in issues if issue.claim_id is None):
            raise ValueError("Verification report issue order is invalid")
        expected_coverage = round(
            sum(item.verdict is not VerificationClaimVerdict.INSUFFICIENT for item in claims)
            / len(required_claim_ids),
            6,
        )
        if self.coverage != expected_coverage:
            raise ValueError("Verification report coverage is inconsistent")
        if self.status is not _aggregate_status(claims):
            raise ValueError("Verification report status is inconsistent")
        require_utc(self.created_at, field_name="Verification report creation time")
        object.__setattr__(self, "required_claim_ids", required_claim_ids)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "evidence_snapshots", evidence_snapshots)
        object.__setattr__(self, "issues", issues)


@dataclass(frozen=True, slots=True)
class VerificationSnapshot:
    report_id: UUID
    research_run_id: UUID
    agent_run_id: UUID
    workspace_id: UUID
    draft_id: UUID
    revision: int
    graph_version: str
    financial_scope: FinancialScope
    required_claim_ids: tuple[UUID, ...]
    claims: tuple[ResearchClaim, ...]
    evidence_states: tuple[VerificationEvidenceState, ...]
    runtime_stop_reason: RunStopReason | None
    created_at: datetime


class VerificationReportRepository(Protocol):
    async def next_revision(self, scope: WorkspaceScope, research_run_id: UUID) -> int: ...

    async def save(
        self, scope: WorkspaceScope, report: VerificationReport
    ) -> VerificationReport: ...

    async def latest(
        self, scope: WorkspaceScope, research_run_id: UUID
    ) -> VerificationReport | None: ...


class VerificationInputError(ValueError):
    pass


class VerificationConflictError(RuntimeError):
    pass


class VerificationPersistenceError(RuntimeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Verification persistence is unavailable")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class ResearchVerificationService:
    research_repository: ResearchQueryRepository
    evidence_service: EvidenceUseCase
    report_repository: VerificationReportRepository
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)

    async def verify(self, scope: WorkspaceScope, research_run_id: UUID) -> VerificationReport:
        if not scope_allows(scope, WorkspaceAction.RUN_RESEARCH):
            raise WorkspaceAccessDeniedError
        view = await self.research_repository.get(scope, research_run_id)
        draft = view.draft
        financial_scope = view.brief.input.financial_scope
        if draft is None or financial_scope is None or not draft.claim_refs:
            raise VerificationInputError("SEC Verification requires a scoped Research Draft")
        if len(draft.claim_refs) > MAX_VERIFICATION_CLAIMS:
            raise VerificationInputError("SEC Verification Claim limit exceeded")
        claims = await self.evidence_service.list_claims(
            scope, research_run_id, limit=MAX_VERIFICATION_CLAIMS
        )
        claims_by_id = {claim.claim_id: claim for claim in claims}
        selected_claims = tuple(
            claim
            for claim_id in draft.claim_refs
            if (claim := claims_by_id.get(claim_id)) is not None
        )
        evidence_by_id = {
            link.evidence.evidence_id: link.evidence
            for claim in selected_claims
            for link in claim.relations
        }
        calculation_inputs = {
            evidence_id
            for evidence in tuple(evidence_by_id.values())
            if isinstance(evidence.locator, FinancialCalculationLocatorV1)
            for evidence_id in evidence.locator.input_evidence_refs
        }
        for evidence_id in sorted(calculation_inputs - set(evidence_by_id), key=str):
            try:
                evidence_by_id[evidence_id] = await self.evidence_service.get_evidence(
                    scope, evidence_id
                )
            except EvidenceNotFoundError:
                continue
        evidence_states: list[VerificationEvidenceState] = []
        for evidence in sorted(evidence_by_id.values(), key=lambda item: str(item.evidence_id)):
            evidence_states.append(
                VerificationEvidenceState(
                    evidence=evidence,
                    available=await self.evidence_service.is_evidence_available(
                        scope, evidence.evidence_id
                    ),
                )
            )
        revision = await self.report_repository.next_revision(scope, research_run_id)
        report_id = uuid5(
            research_run_id,
            f"verification:{VERIFICATION_CHECKER_VERSION}:{revision}:{draft.draft_id}",
        )
        report = evaluate_verification_snapshot(
            VerificationSnapshot(
                report_id=report_id,
                research_run_id=research_run_id,
                agent_run_id=view.research_run.agent_run_id,
                workspace_id=scope.workspace_id,
                draft_id=draft.draft_id,
                revision=revision,
                graph_version=view.research_run.graph_version,
                financial_scope=financial_scope,
                required_claim_ids=draft.claim_refs,
                claims=selected_claims,
                evidence_states=tuple(evidence_states),
                runtime_stop_reason=view.stop_reason,
                created_at=self.clock(),
            )
        )
        return await self.report_repository.save(scope, report)

    async def latest(
        self, scope: WorkspaceScope, research_run_id: UUID
    ) -> VerificationReport | None:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError
        return await self.report_repository.latest(scope, research_run_id)


def evaluate_verification_snapshot(snapshot: VerificationSnapshot) -> VerificationReport:
    evidence_by_id = {item.evidence.evidence_id: item for item in snapshot.evidence_states}
    claims_by_id = {claim.claim_id: claim for claim in snapshot.claims}
    report_issues: list[VerificationIssue] = []
    claim_results: list[VerificationClaimResult] = []
    issue_ordinal = 0

    def issue(
        *,
        code: VerificationIssueCode,
        claim_id: UUID | None,
        expected_refs: tuple[str, ...] = (),
        observed_refs: tuple[str, ...] = (),
        repairability: VerificationRepairability = VerificationRepairability.TERMINAL,
        allowed_action: VerificationAllowedAction | None = None,
    ) -> VerificationIssue:
        nonlocal issue_ordinal
        issue_ordinal += 1
        digest_source = "|".join(
            (
                code.value,
                "" if claim_id is None else str(claim_id),
                *expected_refs,
                *observed_refs,
            )
        )
        return VerificationIssue(
            issue_id=uuid5(snapshot.report_id, f"issue:{issue_ordinal}:{code.value}"),
            code=code,
            severity=VerificationIssueSeverity.ERROR,
            claim_id=claim_id,
            expected_refs=expected_refs,
            observed_refs=observed_refs,
            repairability=repairability,
            allowed_action=allowed_action,
            details_digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
        )

    for claim_id in snapshot.required_claim_ids:
        claim = claims_by_id.get(claim_id)
        claim_issues: list[VerificationIssue] = []
        if claim is None:
            claim_issues.append(
                issue(
                    code=VerificationIssueCode.CLAIM_NOT_FOUND,
                    claim_id=claim_id,
                    expected_refs=(str(claim_id),),
                    repairability=VerificationRepairability.REPAIRABLE,
                    allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
                )
            )
            result = VerificationClaimResult(
                claim_id=claim_id,
                claim_revision=None,
                required=True,
                verdict=VerificationClaimVerdict.INSUFFICIENT,
                coverage=0,
                evidence_refs=(),
                citation_refs=(),
                calculation_refs=(),
                issues=tuple(claim_issues),
            )
            claim_results.append(result)
            report_issues.extend(claim_issues)
            continue

        supports: list[UUID] = []
        refutes: list[UUID] = []
        citations: list[UUID] = []
        calculations: list[UUID] = []
        observed = tuple(link.evidence.evidence_id for link in claim.relations)
        for link in claim.relations:
            evidence = link.evidence
            evidence_id = evidence.evidence_id
            state = evidence_by_id.get(evidence_id)
            if link.status is not RelationStatus.ACTIVE:
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.RELATION_INVALIDATED,
                        claim_id=claim_id,
                        observed_refs=(str(evidence_id),),
                        repairability=VerificationRepairability.REPAIRABLE,
                        allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
                    )
                )
                continue
            if evidence.status is not EvidenceStatus.ACTIVE:
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.EVIDENCE_INACTIVE,
                        claim_id=claim_id,
                        observed_refs=(str(evidence_id), evidence.status.value),
                        repairability=VerificationRepairability.REPAIRABLE,
                        allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
                    )
                )
                continue
            authorization = evidence.authorization_snapshot
            if (
                authorization.workspace_id != snapshot.workspace_id
                or authorization.action != "evidence.normalize"
            ):
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.AUTHORIZATION_MISMATCH,
                        claim_id=claim_id,
                        expected_refs=(str(snapshot.workspace_id),),
                        observed_refs=(str(authorization.workspace_id),),
                    )
                )
                continue
            if state is None or not state.available:
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.CITATION_UNRESOLVABLE,
                        claim_id=claim_id,
                        observed_refs=(str(evidence_id),),
                        repairability=VerificationRepairability.REPAIRABLE,
                        allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
                    )
                )
                continue
            identity_matches, future_source = _scope_identity(evidence, snapshot.financial_scope)
            if not identity_matches:
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.SCOPE_IDENTITY_MISMATCH,
                        claim_id=claim_id,
                        expected_refs=_scope_refs(snapshot.financial_scope),
                        observed_refs=(str(evidence_id),),
                    )
                )
                continue
            if future_source:
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.FUTURE_SOURCE,
                        claim_id=claim_id,
                        expected_refs=(snapshot.financial_scope.as_of.isoformat(),),
                        observed_refs=(str(evidence_id),),
                    )
                )
                continue
            if not _content_hash_matches(evidence):
                claim_issues.append(
                    issue(
                        code=VerificationIssueCode.SOURCE_HASH_MISMATCH,
                        claim_id=claim_id,
                        expected_refs=(evidence.content_sha256,),
                        observed_refs=(str(evidence_id),),
                    )
                )
                continue
            if isinstance(evidence.locator, FinancialCalculationLocatorV1):
                calculation_issue = _calculation_issue(
                    evidence.locator, snapshot.financial_scope, evidence_by_id
                )
                if calculation_issue is not None:
                    code, missing_refs = calculation_issue
                    claim_issues.append(
                        issue(
                            code=code,
                            claim_id=claim_id,
                            observed_refs=(str(evidence_id), *missing_refs),
                            repairability=VerificationRepairability.REPAIRABLE,
                            allowed_action=VerificationAllowedAction.RECALCULATE,
                        )
                    )
                    continue
                calculations.append(evidence_id)
            citations.append(evidence_id)
            if link.relation is ClaimEvidenceRelation.SUPPORTS:
                supports.append(evidence_id)
            elif link.relation is ClaimEvidenceRelation.REFUTES:
                refutes.append(evidence_id)

        if supports and refutes:
            verdict = VerificationClaimVerdict.CONFLICTING
            claim_issues.append(
                issue(
                    code=VerificationIssueCode.CLAIM_CONFLICT,
                    claim_id=claim_id,
                    observed_refs=tuple(str(item) for item in (*supports, *refutes)),
                )
            )
        elif supports:
            verdict = VerificationClaimVerdict.SUPPORTED
        elif refutes:
            verdict = VerificationClaimVerdict.REFUTED
            claim_issues.append(
                issue(
                    code=VerificationIssueCode.CLAIM_REFUTED,
                    claim_id=claim_id,
                    observed_refs=tuple(str(item) for item in refutes),
                )
            )
        else:
            verdict = VerificationClaimVerdict.INSUFFICIENT
            claim_issues.append(
                issue(
                    code=VerificationIssueCode.MISSING_EVIDENCE,
                    claim_id=claim_id,
                    expected_refs=(str(claim_id),),
                    observed_refs=tuple(str(item) for item in observed),
                    repairability=VerificationRepairability.REPAIRABLE,
                    allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
                )
            )
        result = VerificationClaimResult(
            claim_id=claim_id,
            claim_revision=claim.revision,
            required=True,
            verdict=verdict,
            coverage=0 if verdict is VerificationClaimVerdict.INSUFFICIENT else 1,
            evidence_refs=observed,
            citation_refs=tuple(citations),
            calculation_refs=tuple(calculations),
            issues=tuple(claim_issues),
        )
        claim_results.append(result)
        report_issues.extend(claim_issues)

    if any(item.verdict is VerificationClaimVerdict.INSUFFICIENT for item in claim_results):
        report_issues.append(
            issue(
                code=VerificationIssueCode.COVERAGE_INCOMPLETE,
                claim_id=None,
                expected_refs=tuple(str(item) for item in snapshot.required_claim_ids),
                observed_refs=tuple(
                    str(item.claim_id)
                    for item in claim_results
                    if item.verdict is not VerificationClaimVerdict.INSUFFICIENT
                ),
                repairability=VerificationRepairability.REPAIRABLE,
                allowed_action=VerificationAllowedAction.TARGETED_RETRIEVE,
            )
        )
    evidence_snapshots = tuple(
        VerificationEvidenceSnapshot(
            evidence_id=state.evidence.evidence_id,
            revision=state.evidence.revision,
            status=state.evidence.status,
            content_sha256=state.evidence.content_sha256,
            available=state.available,
        )
        for state in snapshot.evidence_states
    )
    coverage = round(
        sum(item.verdict is not VerificationClaimVerdict.INSUFFICIENT for item in claim_results)
        / len(snapshot.required_claim_ids),
        6,
    )
    return VerificationReport(
        report_id=snapshot.report_id,
        research_run_id=snapshot.research_run_id,
        agent_run_id=snapshot.agent_run_id,
        workspace_id=snapshot.workspace_id,
        draft_id=snapshot.draft_id,
        revision=snapshot.revision,
        graph_version=snapshot.graph_version,
        financial_scope=snapshot.financial_scope,
        status=_aggregate_status(tuple(claim_results)),
        coverage=coverage,
        required_claim_ids=snapshot.required_claim_ids,
        claims=tuple(claim_results),
        evidence_snapshots=evidence_snapshots,
        issues=tuple(report_issues),
        runtime_stop_reason=snapshot.runtime_stop_reason,
        created_at=snapshot.created_at,
    )


def _aggregate_status(claims: tuple[VerificationClaimResult, ...]) -> VerificationStatus:
    verdicts = {item.verdict for item in claims if item.required}
    if VerificationClaimVerdict.CONFLICTING in verdicts:
        return VerificationStatus.CONFLICT
    if verdicts == {VerificationClaimVerdict.SUPPORTED}:
        return VerificationStatus.VERIFIED
    if VerificationClaimVerdict.SUPPORTED in verdicts:
        return VerificationStatus.PARTIAL
    return VerificationStatus.INSUFFICIENT_EVIDENCE


def _scope_identity(evidence: Evidence, scope: FinancialScope) -> tuple[bool, bool]:
    locator = evidence.locator
    if isinstance(locator, SecFilingChunkLocatorV1):
        accepted_at = datetime.fromisoformat(locator.accepted_at)
        return (
            locator.cik == scope.cik
            and locator.accession == scope.accession
            and locator.form == scope.form.value
            and locator.report_period == scope.report_period.isoformat(),
            accepted_at > scope.as_of,
        )
    if isinstance(locator, SecFilingTextLocatorV1):
        accepted_at = datetime.fromisoformat(locator.accepted_at)
        return (
            locator.cik == scope.cik
            and locator.accession == scope.accession
            and locator.form == scope.form.value
            and locator.report_period == scope.report_period.isoformat()
            and datetime.fromisoformat(locator.as_of) == scope.as_of,
            accepted_at > scope.as_of,
        )
    if isinstance(locator, SecXbrlFactLocatorV1):
        source_available_at = datetime.fromisoformat(locator.source_available_at)
        return (
            locator.cik == scope.cik
            and locator.accession == scope.accession
            and locator.form == scope.form.value
            and locator.report_period == scope.report_period.isoformat()
            and datetime.fromisoformat(locator.as_of) == scope.as_of
            and locator.unit == scope.unit
            and _xbrl_period_matches(locator, scope),
            source_available_at > scope.as_of,
        )
    if isinstance(locator, FinancialCalculationLocatorV1):
        return FinancialScope.from_mapping(dict(locator.financial_scope)) == scope, False
    return False, False


def _xbrl_period_matches(locator: SecXbrlFactLocatorV1, scope: FinancialScope) -> bool:
    report_period = scope.report_period.isoformat()
    if locator.period_kind == "instant":
        return locator.instant == report_period
    if locator.period_kind == "duration":
        return locator.end_date == report_period
    return False


def _content_hash_matches(evidence: Evidence) -> bool:
    locator = evidence.locator
    if isinstance(locator, (SecFilingChunkLocatorV1, SecFilingTextLocatorV1, SecXbrlFactLocatorV1)):
        return locator.content_sha256 == evidence.content_sha256
    if isinstance(locator, FinancialCalculationLocatorV1):
        return locator.observation_sha256 == evidence.content_sha256
    return False


def _calculation_issue(
    locator: FinancialCalculationLocatorV1,
    scope: FinancialScope,
    evidence_by_id: dict[UUID, VerificationEvidenceState],
) -> tuple[VerificationIssueCode, tuple[str, ...]] | None:
    missing = tuple(
        str(evidence_id)
        for evidence_id in locator.input_evidence_refs
        if (state := evidence_by_id.get(evidence_id)) is None
        or state.evidence.status is not EvidenceStatus.ACTIVE
        or not state.available
        or not _scope_identity(state.evidence, scope)[0]
        or not _content_hash_matches(state.evidence)
    )
    if missing:
        return VerificationIssueCode.CALCULATION_INPUT_MISSING, missing
    if (
        locator.reconciliation_status != "consistent"
        or locator.reconciliation_version != "financial-reconciliation-v1"
    ):
        return VerificationIssueCode.CALCULATION_MISMATCH, ()
    try:
        calculation = FinancialCalculation(
            operator=FinancialOperator(locator.operator),
            operands=tuple(
                FinancialOperand(value=value, evidence_ref=evidence_id)
                for value, evidence_id in zip(
                    locator.operand_values, locator.input_evidence_refs, strict=True
                )
            ),
            decimal_places=locator.decimal_places,
            rounding_mode=FinancialRoundingMode(locator.rounding_mode),
        )
        result = calculate_financial_result(scope, calculation)
    except ValueError:
        return VerificationIssueCode.CALCULATION_MISMATCH, ()
    if (
        result.value != locator.result
        or result.formula != locator.formula
        or result.unit != locator.unit
        or result.scale != locator.scale
        or result.evidence_refs != locator.input_evidence_refs
    ):
        return VerificationIssueCode.CALCULATION_MISMATCH, ()
    return None


def _scope_refs(scope: FinancialScope) -> tuple[str, ...]:
    return (
        scope.cik,
        scope.accession,
        scope.form.value,
        scope.report_period.isoformat(),
        scope.as_of.isoformat(),
    )


def _bounded_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    refs = tuple(dict.fromkeys(values))
    if len(refs) > MAX_VERIFICATION_REFS or any(
        not value.strip() or len(value) > 200 or "\x00" in value for value in refs
    ):
        raise ValueError("Verification issue refs are invalid")
    return refs


def _unique_ids(
    values: tuple[UUID, ...], field_name: str, *, required: bool = True
) -> tuple[UUID, ...]:
    selected = tuple(values)
    if (
        (required and not selected)
        or len(selected) > MAX_VERIFICATION_REFS
        or len(set(selected)) != len(selected)
        or any(item.int == 0 for item in selected)
    ):
        raise ValueError(f"Verification {field_name} are invalid")
    return selected
