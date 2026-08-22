"""Versioned deterministic Research metrics over shared Harness EvalCases."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from industry_platform.modules.agent_harness.scenarios import ScenarioDataset

RESEARCH_SCORER_VERSION: Final = "research-scorer-v1"
RESEARCH_EVAL_FIELD: Final = "research_eval"
_COMPARISON_TIERS: Final = ("l0", "l2", "l3")


@dataclass(frozen=True, slots=True)
class ResearchMetric:
    numerator: int
    denominator: int
    value: float

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 1 or self.value < 0:
            raise ValueError("Research metric is invalid")


@dataclass(frozen=True, slots=True)
class ResearchTierComparison:
    case_count: int
    steps: int
    total_tokens: int
    cost_micro_usd: int
    latency_ms: int
    evidence_count: int
    claim_count: int
    uncertainty_count: int


@dataclass(frozen=True, slots=True)
class ResearchEvalReport:
    dataset_id: str
    dataset_version: str
    scorer_version: str
    case_count: int
    metrics: Mapping[str, ResearchMetric]
    same_question_comparison: Mapping[str, ResearchTierComparison]


@dataclass(frozen=True, slots=True)
class _Observation:
    comparison_tier: str
    scope_expected: int
    scope_correct: int
    trajectory_expected: int
    trajectory_correct: int
    claim_support_expected: int
    claim_support_correct: int
    uncertainty_expected: int
    uncertainty_correct: int
    budget_expected: int
    budget_correct: int
    cancellation_expected: int
    cancellation_correct: int
    authorization_probes: int
    authorization_leaks: int
    steps: int
    total_tokens: int
    cost_micro_usd: int
    latency_ms: int
    evidence_count: int
    claim_count: int
    uncertainty_count: int


def score_research_dataset(dataset: ScenarioDataset) -> ResearchEvalReport:
    observations = tuple(_observation(case.expected_behavior) for case in dataset.cases)
    if not observations:
        raise ValueError("Research evaluation requires cases")
    metrics = {
        "scope_accuracy": _ratio(observations, "scope_correct", "scope_expected"),
        "trajectory_accuracy": _ratio(
            observations,
            "trajectory_correct",
            "trajectory_expected",
        ),
        "claim_support_accuracy": _ratio(
            observations,
            "claim_support_correct",
            "claim_support_expected",
        ),
        "uncertainty_accuracy": _ratio(
            observations,
            "uncertainty_correct",
            "uncertainty_expected",
        ),
        "budget_compliance": _ratio(observations, "budget_correct", "budget_expected"),
        "cancellation_accuracy": _ratio(
            observations,
            "cancellation_correct",
            "cancellation_expected",
        ),
        "authorization_leakage": _ratio(
            observations,
            "authorization_leaks",
            "authorization_probes",
        ),
        "average_steps": _average(observations, "steps"),
        "average_total_tokens": _average(observations, "total_tokens"),
        "average_cost_micro_usd": _average(observations, "cost_micro_usd"),
        "average_latency_ms": _average(observations, "latency_ms"),
    }
    comparison = {tier: _tier_comparison(observations, tier=tier) for tier in _COMPARISON_TIERS}
    return ResearchEvalReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=RESEARCH_SCORER_VERSION,
        case_count=len(observations),
        metrics=metrics,
        same_question_comparison=comparison,
    )


def _observation(expected: Mapping[str, object]) -> _Observation:
    value = expected.get(RESEARCH_EVAL_FIELD)
    if not isinstance(value, Mapping) or set(value) != set(_Observation.__dataclass_fields__):
        raise ValueError("Research observation fields are invalid")
    tier = value["comparison_tier"]
    if not isinstance(tier, str) or tier not in (*_COMPARISON_TIERS, "none"):
        raise ValueError("Research comparison tier is invalid")
    integers: dict[str, int] = {}
    for field_name in _Observation.__dataclass_fields__:
        if field_name == "comparison_tier":
            continue
        item = value[field_name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("Research observation value is invalid")
        integers[field_name] = item
    observation = _Observation(comparison_tier=tier, **integers)
    for numerator, denominator in (
        (observation.scope_correct, observation.scope_expected),
        (observation.trajectory_correct, observation.trajectory_expected),
        (observation.claim_support_correct, observation.claim_support_expected),
        (observation.uncertainty_correct, observation.uncertainty_expected),
        (observation.budget_correct, observation.budget_expected),
        (observation.cancellation_correct, observation.cancellation_expected),
        (observation.authorization_leaks, observation.authorization_probes),
    ):
        if numerator > denominator:
            raise ValueError("Research observation numerator exceeds its denominator")
    return observation


def _ratio(
    observations: tuple[_Observation, ...],
    numerator_field: str,
    denominator_field: str,
) -> ResearchMetric:
    numerator = sum(getattr(item, numerator_field) for item in observations)
    denominator = sum(getattr(item, denominator_field) for item in observations)
    if denominator < 1:
        raise ValueError(f"Research metric {denominator_field} has no denominator")
    return ResearchMetric(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6),
    )


def _average(observations: tuple[_Observation, ...], field_name: str) -> ResearchMetric:
    total = sum(getattr(item, field_name) for item in observations)
    return ResearchMetric(
        numerator=total,
        denominator=len(observations),
        value=round(total / len(observations), 6),
    )


def _tier_comparison(
    observations: tuple[_Observation, ...],
    *,
    tier: str,
) -> ResearchTierComparison:
    matches = tuple(item for item in observations if item.comparison_tier == tier)
    if len(matches) != 1:
        raise ValueError(f"Research same-question comparison requires one {tier} case")
    item = matches[0]
    return ResearchTierComparison(
        case_count=1,
        steps=item.steps,
        total_tokens=item.total_tokens,
        cost_micro_usd=item.cost_micro_usd,
        latency_ms=item.latency_ms,
        evidence_count=item.evidence_count,
        claim_count=item.claim_count,
        uncertainty_count=item.uncertainty_count,
    )
