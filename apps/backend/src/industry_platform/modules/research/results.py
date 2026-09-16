"""Version-bound result views derived from authorized report Evidence, never Markdown."""
# ruff: noqa: RUF001 -- Chinese UI copy and explicit mathematical units.

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from industry_platform.modules.evidence.domain import (
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    SecXbrlFactLocatorV1,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.research.domain import ResearchRunView
from industry_platform.modules.research.service import ResearchQueryService
from industry_platform.modules.research.verification import (
    ResearchVerificationService,
    VerificationEvidenceState,
    VerificationReport,
    _content_hash_matches,
    _dupont_calculation_issue,
    _dupont_comparison_complete,
    _scope_identity,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResultValue(ResultModel):
    value: str
    evidence_refs: tuple[UUID, ...]
    change: str | None = None


class ResultRow(ResultModel):
    key: str
    label: str
    unit: str
    change_unit: str
    values: tuple[ResultValue, ...]


class ResearchResultView(ResultModel):
    schema_version: Literal[1] = 1
    research_run_id: UUID
    draft_id: UUID | None
    draft_revision: int | None
    verification_report_id: UUID | None = None
    verification_status: str | None = None
    status: Literal["ready", "unavailable", "not_applicable"]
    periods: tuple[date, ...] = ()
    rows: tuple[ResultRow, ...] = ()
    limitations: tuple[str, ...] = ()


INPUTS = (
    ("revenue", "营业收入"),
    ("net_income", "归母净利润"),
    ("opening_assets", "期初总资产"),
    ("closing_assets", "期末总资产"),
    ("opening_equity", "期初归母权益"),
    ("closing_equity", "期末归母权益"),
)
METRICS = (
    ("net_profit_margin_percent", "净利率", "%", "百分点"),
    ("asset_turnover", "总资产周转率", "倍", "倍"),
    ("equity_multiplier", "权益乘数", "倍", "倍"),
    ("roe_percent", "ROE", "%", "百分点"),
)


def build_result_view(
    view: ResearchRunView,
    states: tuple[VerificationEvidenceState, ...],
    report: VerificationReport | None,
) -> ResearchResultView:
    draft = view.draft
    scope = view.brief.input.financial_scope
    base = ResearchResultView(
        research_run_id=view.research_run.research_run_id,
        draft_id=None if draft is None else draft.draft_id,
        draft_revision=None if draft is None else draft.revision,
        status="not_applicable",
    )
    if draft is None or scope is None:
        return base
    by_id = {state.evidence.evidence_id: state for state in states}
    calculations: dict[date, tuple[UUID, FinancialCalculationLocatorV1]] = {}
    invalid = False
    for reference in draft.evidence_refs:
        state = by_id.get(reference)
        if state is None or not isinstance(state.evidence.locator, FinancialCalculationLocatorV1):
            continue
        locator = state.evidence.locator
        if locator.operator != "dupont":
            continue
        sources = [by_id.get(ref) for ref in locator.input_evidence_refs]
        if (
            not state.available
            or state.evidence.status is not EvidenceStatus.ACTIVE
            or not _content_hash_matches(state.evidence)
            or _dupont_calculation_issue(locator, scope, by_id) is not None
            or not _scope_identity(state.evidence, scope)[0]
            or any(
                item is None
                or not item.available
                or item.evidence.status is not EvidenceStatus.ACTIVE
                for item in sources
            )
        ):
            invalid = True
            continue
        source = sources[0]
        if source is None or not isinstance(source.evidence.locator, SecXbrlFactLocatorV1):
            invalid = True
            continue
        end = source.evidence.locator.end_date
        if end is None or date.fromisoformat(end) in calculations:
            invalid = True
            continue
        calculations[date.fromisoformat(end)] = (reference, locator)
    if not calculations and not invalid:
        return base
    if (
        invalid
        or len(calculations) != scope.annual_period_count
        or not _dupont_comparison_complete(scope, by_id)
    ):
        return base.model_copy(
            update={
                "status": "unavailable",
                "limitations": ("计算或来源不完整/不可访问，未发布可视化数值。",),
            }
        )
    periods = tuple(sorted(calculations))
    rows: list[ResultRow] = []
    money_unit = f"{scope.unit} × 10^{scope.scale}"
    specs = [
        *((key, label, money_unit, money_unit) for key, label in INPUTS),
        ("average_assets", "平均总资产", money_unit, money_unit),
        ("average_equity", "平均归母权益", money_unit, money_unit),
        *METRICS,
    ]
    try:
        for index, (key, label, unit, change_unit) in enumerate(specs):
            values: list[ResultValue] = []
            for period in periods:
                reference, calculation = calculations[period]
                value = (
                    calculation.operand_values[index]
                    if index < 6
                    else dict(calculation.components)[key]
                )
                numeric = Decimal(value)
                if not numeric.is_finite():
                    raise ValueError("Invalid result number")
                with localcontext() as context:
                    context.prec = 50
                    change = (
                        None if not values else format(numeric - Decimal(values[-1].value), "f")
                    )
                values.append(
                    ResultValue(
                        value=value,
                        change=change,
                        evidence_refs=(calculation.input_evidence_refs[index],)
                        if index < 6
                        else (reference,),
                    )
                )
            rows.append(
                ResultRow(
                    key=key, label=label, unit=unit, change_unit=change_unit, values=tuple(values)
                )
            )
    except (ValueError, InvalidOperation, KeyError, IndexError):
        return base.model_copy(
            update={"status": "unavailable", "limitations": ("结构化计算结果无效。",)}
        )
    current_report = report is not None and report.draft_id == draft.draft_id
    if current_report and report is not None:
        snapshots = {item.evidence_id: item for item in report.evidence_snapshots}
        relevant = {ref for ref, calculation in calculations.values()} | {
            ref
            for _, calculation in calculations.values()
            for ref in calculation.input_evidence_refs
        }
        current_report = all(
            ref in snapshots
            and ref in by_id
            and snapshots[ref].revision == by_id[ref].evidence.revision
            and snapshots[ref].content_sha256 == by_id[ref].evidence.content_sha256
            and snapshots[ref].available == by_id[ref].available
            and snapshots[ref].status == by_id[ref].evidence.status
            for ref in relevant
        )
    return base.model_copy(
        update={
            "status": "ready",
            "periods": periods,
            "rows": tuple(rows),
            "verification_report_id": report.report_id
            if current_report and report is not None
            else None,
            "verification_status": report.status.value
            if current_report and report is not None
            else None,
            "limitations": (
                "金额与指标来自同一报告的计算证据；较上年变化使用已发布数值之差。",
                "杜邦分解是会计恒等关系，不代表业务因果；两点平均不能反映年内季节性。",
                *(() if current_report else ("没有与当前报告和证据版本一致的核验结果。",)),
            ),
        }
    )


@dataclass(frozen=True)
class ResearchResultService:
    query: ResearchQueryService
    evidence: EvidenceUseCase
    verification: ResearchVerificationService

    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchResultView:
        view = await self.query.get(scope, research_run_id)
        if view.draft is None or view.brief.input.financial_scope is None:
            return build_result_view(view, (), None)
        states = []
        for reference in view.draft.evidence_refs:
            item, available = await self.evidence.resolve_evidence(scope, reference)
            states.append(VerificationEvidenceState(item, available))
        report = await self.verification.latest(scope, research_run_id)
        return build_result_view(view, tuple(states), report)
