"""Versioned deterministic Memory metrics over shared Harness EvalCases."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from industry_platform.modules.agent_harness.scenarios import ScenarioDataset

MEMORY_SCORER_VERSION: Final = "memory-scorer-v1"
MEMORY_EVAL_FIELD: Final = "memory_eval"
MEMORY_ABLATION_SCORER_VERSION: Final = "memory-ablation-scorer-v1"
MEMORY_ABLATION_EVAL_FIELD: Final = "memory_ablation_eval"


@dataclass(frozen=True, slots=True)
class MemoryMetric:
    numerator: int
    denominator: int
    value: float

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 1 or self.value < 0:
            raise ValueError("Memory metric is invalid")


@dataclass(frozen=True, slots=True)
class MemoryEvalReport:
    dataset_id: str
    dataset_version: str
    scorer_version: str
    case_count: int
    metrics: Mapping[str, MemoryMetric]


@dataclass(frozen=True, slots=True)
class MemoryAblationMode:
    case_count: int
    task_quality: MemoryMetric
    pollution: MemoryMetric
    conflict_handling: MemoryMetric
    input_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class MemoryAblationDelta:
    task_quality: float
    pollution: float
    conflict_handling: float
    input_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class MemoryAblationReport:
    dataset_id: str
    dataset_version: str
    scorer_version: str
    case_count: int
    comparison: Mapping[str, MemoryAblationMode]
    on_minus_off: MemoryAblationDelta


@dataclass(frozen=True, slots=True)
class _Observation:
    write_expected: int
    write_correct: int
    included_total: int
    included_relevant: int
    useful_included: int
    irrelevant_candidates: int
    irrelevant_included: int
    conflict_expected: int
    conflict_handled: int
    edit_expected: int
    edit_effective: int
    deletion_expected: int
    deletion_residual_refs: int
    input_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class _AblationObservation:
    mode: str
    task_quality_expected: int
    task_quality_correct: int
    pollution_opportunities: int
    pollution_events: int
    conflict_expected: int
    conflict_handled: int
    input_tokens: int
    latency_ms: int


def score_memory_dataset(dataset: ScenarioDataset) -> MemoryEvalReport:
    """Aggregate fixed-denominator Memory quality, residual, Token, and latency metrics."""

    observations = tuple(_observation(case.expected_behavior) for case in dataset.cases)
    if not observations:
        raise ValueError("Memory evaluation requires cases")
    metrics = {
        "write_accuracy": _ratio(observations, "write_correct", "write_expected"),
        "retrieval_precision": _ratio(
            observations,
            "included_relevant",
            "included_total",
        ),
        "utility": _ratio(observations, "useful_included", "included_total"),
        "pollution": _ratio(
            observations,
            "irrelevant_included",
            "irrelevant_candidates",
        ),
        "conflict_handling": _ratio(
            observations,
            "conflict_handled",
            "conflict_expected",
        ),
        "edit_effectiveness": _ratio(
            observations,
            "edit_effective",
            "edit_expected",
        ),
        "deletion_residual": _ratio(
            observations,
            "deletion_residual_refs",
            "deletion_expected",
        ),
        "average_input_tokens": _average(observations, "input_tokens"),
        "average_latency_ms": _average(observations, "latency_ms"),
    }
    return MemoryEvalReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=MEMORY_SCORER_VERSION,
        case_count=len(observations),
        metrics=metrics,
    )


def score_memory_ablation_dataset(dataset: ScenarioDataset) -> MemoryAblationReport:
    """Compare the same frozen input with Memory disabled and enabled."""

    observations = tuple(_ablation_observation(case.expected_behavior) for case in dataset.cases)
    by_mode: dict[str, _AblationObservation] = {}
    for observation in observations:
        if observation.mode in by_mode:
            raise ValueError("Memory ablation requires one case per mode")
        by_mode[observation.mode] = observation
    if set(by_mode) != {"off", "on"}:
        raise ValueError("Memory ablation requires off and on cases")
    comparison = {mode: _ablation_mode(by_mode[mode]) for mode in ("off", "on")}
    off = comparison["off"]
    on = comparison["on"]
    return MemoryAblationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=MEMORY_ABLATION_SCORER_VERSION,
        case_count=len(observations),
        comparison=comparison,
        on_minus_off=MemoryAblationDelta(
            task_quality=round(on.task_quality.value - off.task_quality.value, 6),
            pollution=round(on.pollution.value - off.pollution.value, 6),
            conflict_handling=round(
                on.conflict_handling.value - off.conflict_handling.value,
                6,
            ),
            input_tokens=on.input_tokens - off.input_tokens,
            latency_ms=on.latency_ms - off.latency_ms,
        ),
    )


def _observation(expected: Mapping[str, object]) -> _Observation:
    value = expected.get(MEMORY_EVAL_FIELD)
    if not isinstance(value, Mapping):
        raise ValueError("EvalCase has no Memory observation")
    fields = tuple(_Observation.__dataclass_fields__)
    if set(value) != set(fields):
        raise ValueError("Memory observation fields are invalid")
    integers: dict[str, int] = {}
    for field_name in fields:
        item = value[field_name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("Memory observation value is invalid")
        integers[field_name] = item
    observation = _Observation(**integers)
    if (
        observation.write_correct > observation.write_expected
        or observation.included_relevant > observation.included_total
        or observation.useful_included > observation.included_total
        or observation.irrelevant_included > observation.irrelevant_candidates
        or observation.conflict_handled > observation.conflict_expected
        or observation.edit_effective > observation.edit_expected
        or observation.deletion_residual_refs > observation.deletion_expected
    ):
        raise ValueError("Memory observation numerator exceeds its denominator")
    return observation


def _ablation_observation(expected: Mapping[str, object]) -> _AblationObservation:
    value = expected.get(MEMORY_ABLATION_EVAL_FIELD)
    if not isinstance(value, Mapping) or set(value) != set(
        _AblationObservation.__dataclass_fields__
    ):
        raise ValueError("Memory ablation observation fields are invalid")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in {"off", "on"}:
        raise ValueError("Memory ablation mode is invalid")
    integers: dict[str, int] = {}
    for field_name in _AblationObservation.__dataclass_fields__:
        if field_name == "mode":
            continue
        item = value[field_name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("Memory ablation observation value is invalid")
        integers[field_name] = item
    observation = _AblationObservation(mode=mode, **integers)
    if (
        observation.task_quality_expected < 1
        or observation.pollution_opportunities < 1
        or observation.conflict_expected < 1
        or observation.task_quality_correct > observation.task_quality_expected
        or observation.pollution_events > observation.pollution_opportunities
        or observation.conflict_handled > observation.conflict_expected
    ):
        raise ValueError("Memory ablation numerator exceeds its denominator")
    return observation


def _ablation_mode(observation: _AblationObservation) -> MemoryAblationMode:
    return MemoryAblationMode(
        case_count=1,
        task_quality=MemoryMetric(
            numerator=observation.task_quality_correct,
            denominator=observation.task_quality_expected,
            value=round(
                observation.task_quality_correct / observation.task_quality_expected,
                6,
            ),
        ),
        pollution=MemoryMetric(
            numerator=observation.pollution_events,
            denominator=observation.pollution_opportunities,
            value=round(
                observation.pollution_events / observation.pollution_opportunities,
                6,
            ),
        ),
        conflict_handling=MemoryMetric(
            numerator=observation.conflict_handled,
            denominator=observation.conflict_expected,
            value=round(observation.conflict_handled / observation.conflict_expected, 6),
        ),
        input_tokens=observation.input_tokens,
        latency_ms=observation.latency_ms,
    )


def _ratio(
    observations: tuple[_Observation, ...],
    numerator_field: str,
    denominator_field: str,
) -> MemoryMetric:
    numerator = sum(getattr(item, numerator_field) for item in observations)
    denominator = sum(getattr(item, denominator_field) for item in observations)
    if denominator < 1:
        raise ValueError(f"Memory metric {denominator_field} has no denominator")
    return MemoryMetric(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6),
    )


def _average(observations: tuple[_Observation, ...], field_name: str) -> MemoryMetric:
    total = sum(getattr(item, field_name) for item in observations)
    return MemoryMetric(
        numerator=total,
        denominator=len(observations),
        value=round(total / len(observations), 6),
    )
