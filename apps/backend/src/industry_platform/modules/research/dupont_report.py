"""Deterministic research publication from normalized DuPont observations.

The agent chooses and calls tools; it does not get to rewrite verified numbers
when the writer publishes the Research task's quantitative comparison.
"""

# Chinese typography and mathematical multiplication are intentional in published prose.
# ruff: noqa: RUF001

from decimal import Decimal
from uuid import UUID

from industry_platform.modules.agent_runtime.context import ToolObservationContextSource
from industry_platform.modules.disclosures.tool import SecGetXbrlFactsOutput
from industry_platform.modules.financial_verification.domain import FinancialOperand, FinancialScope
from industry_platform.modules.financial_verification.tool import FinanceCalculateOutput


def render_dupont_report(
    scope: FinancialScope,
    observations: tuple[ToolObservationContextSource, ...],
    citation_evidence: dict[str, UUID],
) -> str:
    periods: dict[str, tuple[FinanceCalculateOutput, str]] = {}
    issues: list[str] = []
    for observation in observations:
        label = f"T{observation.ordinal}S1"
        if observation.tool_name == "sec.get_xbrl_facts":
            output = SecGetXbrlFactsOutput.model_validate_json(observation.model_text)
            if output.purpose == "dupont":
                issues.extend(output.issues)
        elif observation.tool_name == "finance.calculate" and label in citation_evidence:
            calculation = FinanceCalculateOutput.model_validate_json(observation.model_text)
            if (
                calculation.operator.value == "dupont"
                and calculation.status.value == "ok"
                and calculation.financial_scope.to_domain() == scope
                and len(calculation.resolved_operands) == 6
                and calculation.decimal_places == 4
            ):
                end = calculation.resolved_operands[0].end_date
                if end is not None:
                    periods[end.isoformat()] = calculation, label
    if len(periods) != 2 or scope.report_period.isoformat() not in periods:
        limitations = "; ".join(dict.fromkeys(issues)) or "缺少两年已规范化、可复算的杜邦计算证据。"
        return (
            f"## 杜邦分析尚未完成\n\n{limitations}\n\n"
            "需要补齐连续两年年度收入、归母净利润及三期资产与归母权益余额，"
            "不能用零值或估算替代。"
        )
    ordered = sorted(periods.items())
    rows = [
        "## 两年三因素杜邦分析",
        "",
        f"公司 CIK：{scope.cik}；锚定申报：{scope.accession}；"
        f"截止时点：{scope.as_of.isoformat()}。",
        "",
        "### 原始输入与平均余额",
        "",
        f"金额单位：{scope.unit} × 10^{scope.scale}。"
        "期初、期末均为合并报表口径，权益为归母股东权益。",
        "",
        f"| 指标 | {ordered[0][0]} 财年 | {ordered[1][0]} 财年 |",
        "| --- | ---: | ---: |",
    ]
    inputs = ("营业收入", "归母净利润", "期初总资产", "期末总资产", "期初归母权益", "期末归母权益")
    for index, name in enumerate(inputs):
        cells = []
        for _, (calculation, label) in ordered:
            operand = calculation.resolved_operands[index].to_domain()
            value = FinancialOperand(
                operand.value, operand.evidence_ref, operand.unit, operand.scale
            ).value_in_scope(scope)
            cells.append(f"{value:f} [{label}]")
        rows.append(f"| {name} | {' | '.join(cells)} |")
    for key, name in (("average_assets", "平均总资产"), ("average_equity", "平均归母权益")):
        cells = [f"{calculation.components[key]} [{label}]" for _, (calculation, label) in ordered]
        rows.append(f"| {name} | {' | '.join(cells)} |")
    rows.extend(
        [
            "",
            "平均余额 =（期初余额 + 期末余额）/ 2。",
            "",
            "### 杜邦分解",
            "",
            "ROE = 净利率 × 总资产周转率 × 权益乘数。",
            "",
            f"| 指标 | {ordered[0][0]} 财年 | {ordered[1][0]} 财年 |",
            "| --- | ---: | ---: |",
        ]
    )
    metrics = (
        ("net_profit_margin_percent", "净利率", "%"),
        ("asset_turnover", "总资产周转率", " 倍"),
        ("equity_multiplier", "权益乘数", " 倍"),
        ("roe_percent", "ROE", "%"),
    )
    for key, name, unit in metrics:
        cells = [
            f"{calculation.components[key]}{unit} [{label}]" for _, (calculation, label) in ordered
        ]
        rows.append(f"| {name} | {' | '.join(cells)} |")
    rows.extend(["", "### 两年变化与口径限制", ""])
    prior, current = ordered[0][1], ordered[1][1]
    for key, name, _ in metrics:
        before, after = Decimal(prior[0].components[key]), Decimal(current[0].components[key])
        direction = "上升" if after > before else "下降" if after < before else "按四位小数显示不变"
        rows.append(f"- {name}{direction}。[{prior[1]}] [{current[1]}]")
    rows.extend(
        [
            "",
            "以上是数值分解和方向比较，不把会计恒等式解释为业务因果。",
            "ROE 直接以归母净利润除以平均归母权益计算；显示四位小数，计算不使用已舍入的中间因子。",
            "两点平均不能反映年内季节性变化；负权益、非正收入或资产、缺失或冲突数据不支持本版比较。",
            "本版用于标准 US-GAAP 普通企业报表分析，不是估值或投资建议。",
            "",
            "### 期间与申报追溯",
            "",
        ]
    )
    for period_end, (calculation, label) in ordered:
        source_period = calculation.resolved_operands[0]
        accessions = sorted({item.accession for item in calculation.resolved_operands})
        rows.append(
            f"- {source_period.start_date} 至 {period_end}；"
            f"输入申报：{', '.join(accessions)}；计算引用 [{label}]。"
        )
    return "\n".join(rows)
