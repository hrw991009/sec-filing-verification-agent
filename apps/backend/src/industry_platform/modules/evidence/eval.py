"""Versioned deterministic Evidence metrics over shared Harness EvalCases."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from industry_platform.modules.agent_harness.scenarios import ScenarioDataset

EVIDENCE_SCORER_VERSION: Final = "evidence-scorer-v1"
EVIDENCE_EVAL_FIELD: Final = "evidence_eval"


@dataclass(frozen=True, slots=True)
class EvidenceMetric:
    numerator: int
    denominator: int
    value: float

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 1 or self.value < 0:
            raise ValueError("Evidence metric is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceEvalReport:
    dataset_id: str
    dataset_version: str
    scorer_version: str
    case_count: int
    metrics: Mapping[str, EvidenceMetric]


@dataclass(frozen=True, slots=True)
class _Observation:
    validity_expected: int
    validity_correct: int
    attribution_expected: int
    attribution_correct: int
    claim_support_expected: int
    claim_support_correct: int
    coverage_expected: int
    coverage_correct: int
    conflict_expected: int
    conflict_correct: int
    resolvability_expected: int
    resolvability_correct: int
    authorization_probes: int
    authorization_leaks: int
    normalization_latency_ms: int


def score_evidence_dataset(dataset: ScenarioDataset) -> EvidenceEvalReport:
    observations = tuple(_observation(case.expected_behavior) for case in dataset.cases)
    if not observations:
        raise ValueError("Evidence evaluation requires cases")
    metrics = {
        "validity_accuracy": _ratio(observations, "validity_correct", "validity_expected"),
        "attribution_accuracy": _ratio(
            observations,
            "attribution_correct",
            "attribution_expected",
        ),
        "claim_support_accuracy": _ratio(
            observations,
            "claim_support_correct",
            "claim_support_expected",
        ),
        "coverage_accuracy": _ratio(
            observations,
            "coverage_correct",
            "coverage_expected",
        ),
        "conflict_accuracy": _ratio(
            observations,
            "conflict_correct",
            "conflict_expected",
        ),
        "citation_resolvability": _ratio(
            observations,
            "resolvability_correct",
            "resolvability_expected",
        ),
        "authorization_leakage": _ratio(
            observations,
            "authorization_leaks",
            "authorization_probes",
        ),
        "average_normalization_latency_ms": _average(
            observations,
            "normalization_latency_ms",
        ),
    }
    return EvidenceEvalReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=EVIDENCE_SCORER_VERSION,
        case_count=len(observations),
        metrics=metrics,
    )


def _observation(expected: Mapping[str, object]) -> _Observation:
    value = expected.get(EVIDENCE_EVAL_FIELD)
    if not isinstance(value, Mapping) or set(value) != set(_Observation.__dataclass_fields__):
        raise ValueError("Evidence observation fields are invalid")
    integers: dict[str, int] = {}
    for field_name in _Observation.__dataclass_fields__:
        item = value[field_name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("Evidence observation value is invalid")
        integers[field_name] = item
    observation = _Observation(**integers)
    for numerator, denominator in (
        (observation.validity_correct, observation.validity_expected),
        (observation.attribution_correct, observation.attribution_expected),
        (observation.claim_support_correct, observation.claim_support_expected),
        (observation.coverage_correct, observation.coverage_expected),
        (observation.conflict_correct, observation.conflict_expected),
        (observation.resolvability_correct, observation.resolvability_expected),
        (observation.authorization_leaks, observation.authorization_probes),
    ):
        if numerator > denominator:
            raise ValueError("Evidence observation numerator exceeds its denominator")
    return observation


def _ratio(
    observations: tuple[_Observation, ...],
    numerator_field: str,
    denominator_field: str,
) -> EvidenceMetric:
    numerator = sum(getattr(item, numerator_field) for item in observations)
    denominator = sum(getattr(item, denominator_field) for item in observations)
    if denominator < 1:
        raise ValueError(f"Evidence metric {denominator_field} has no denominator")
    return EvidenceMetric(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6),
    )


def _average(observations: tuple[_Observation, ...], field_name: str) -> EvidenceMetric:
    total = sum(getattr(item, field_name) for item in observations)
    return EvidenceMetric(
        numerator=total,
        denominator=len(observations),
        value=round(total / len(observations), 6),
    )
