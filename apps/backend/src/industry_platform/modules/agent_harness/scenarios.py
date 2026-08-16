"""Versioned Scenario/EvalCase contracts and their strict JSON dataset loader."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    MAX_RUN_COST_MICRO_USD,
    MAX_RUN_STEPS,
    MAX_RUN_TOKENS,
    AgentRunType,
    RunBudget,
    RunStopReason,
    require_utc,
    snapshot_json_mapping,
)

SCENARIO_SCHEMA_VERSION: Final = 1
SCENARIO_DATASET_SCHEMA_VERSION: Final = 1
MAX_SCENARIO_TIMEOUT_SECONDS: Final = 86_400
MAX_SCENARIO_DATASET_BYTES: Final = 1_000_000

_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_MAX_HUMAN_NOTES_LENGTH: Final = 4_000
_SCENARIO_INPUT_FIELDS: Final = frozenset({"question", "conversation_summary"})


class ScorerCategory(StrEnum):
    """The four evaluation layers accumulated from Day 2 through Day 7."""

    TRAJECTORY = "trajectory"
    RESULT = "result"
    EVIDENCE = "evidence"
    RUNTIME_RECOVERY = "runtime_recovery"


def _require_schema_version(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value != SCENARIO_SCHEMA_VERSION:
        raise ValueError(f"{field_name} must be {SCENARIO_SCHEMA_VERSION}")


def _require_reference(value: str, *, field_name: str) -> None:
    path_segments = value.split("/")
    if not _REFERENCE_PATTERN.fullmatch(value) or any(
        segment in {"", ".", ".."} for segment in path_segments
    ):
        raise ValueError(f"{field_name} is invalid")


def _require_positive_bounded_integer(
    value: int,
    *,
    maximum: int,
    field_name: str,
) -> None:
    if isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")


def _snapshot_unique_references(
    values: Sequence[VersionedReference],
    *,
    field_name: str,
) -> tuple[VersionedReference, ...]:
    snapshot = tuple(values)
    if len(snapshot) != len(set(snapshot)):
        raise ValueError(f"{field_name} must not contain duplicate references")
    return snapshot


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        thawed: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Harness JSON object keys must be strings")
            thawed[key] = _thaw_json_value(item)
        return thawed
    if isinstance(value, list | tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _freeze_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def snapshot_harness_json_mapping(
    value: Mapping[str, object],
    *,
    error_message: str,
) -> Mapping[str, object]:
    try:
        thawed = _thaw_json_value(value)
        if not isinstance(thawed, Mapping):
            raise ValueError(error_message)
        canonical = snapshot_json_mapping(thawed, error_message=error_message)
        return cast(Mapping[str, object], _freeze_json_value(canonical))
    except (RecursionError, ValueError):
        raise ValueError(error_message) from None


@dataclass(frozen=True, slots=True)
class VersionedReference:
    """A stable logical reference that does not embed an external payload."""

    name: str
    version: str

    def __post_init__(self) -> None:
        _require_reference(self.name, field_name="Reference name")
        _require_reference(self.version, field_name="Reference version")


@dataclass(frozen=True, slots=True)
class ScenarioBudget:
    """Relative Scenario ceilings materialized into a trusted absolute RunBudget."""

    max_steps: int
    max_total_tokens: int
    max_cost_micro_usd: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        for value, maximum, field_name in (
            (self.max_steps, MAX_RUN_STEPS, "Scenario max steps"),
            (self.max_total_tokens, MAX_RUN_TOKENS, "Scenario max total tokens"),
            (self.max_cost_micro_usd, MAX_RUN_COST_MICRO_USD, "Scenario max cost"),
            (
                self.timeout_seconds,
                MAX_SCENARIO_TIMEOUT_SECONDS,
                "Scenario timeout seconds",
            ),
        ):
            _require_positive_bounded_integer(
                value,
                maximum=maximum,
                field_name=field_name,
            )

    def materialize(self, *, started_at: datetime) -> RunBudget:
        """Create a RunBudget without storing an absolute deadline in the dataset."""

        require_utc(started_at, field_name="Scenario start time")
        return RunBudget(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            max_steps=self.max_steps,
            max_total_tokens=self.max_total_tokens,
            max_cost_micro_usd=self.max_cost_micro_usd,
            deadline=started_at + timedelta(seconds=self.timeout_seconds),
        )


@dataclass(frozen=True, slots=True)
class ScorerSpec:
    """One versioned scorer declaration, not an executable dynamic import."""

    category: ScorerCategory
    name: str
    version: str
    parameters: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _require_reference(self.name, field_name="Scorer name")
        _require_reference(self.version, field_name="Scorer version")
        object.__setattr__(
            self,
            "parameters",
            snapshot_harness_json_mapping(
                self.parameters,
                error_message="Scorer parameters must be canonical JSON data",
            ),
        )


@dataclass(frozen=True, slots=True)
class Scenario:
    """One reproducible input and execution configuration for the Harness."""

    schema_version: int
    scenario_id: str
    scenario_version: str
    run_type: AgentRunType
    profile: VersionedReference
    input: Mapping[str, object] = field(repr=False)
    runtime_version: str
    harness_version: str
    model_version: str
    prompt_version: str
    context_version: str
    budget: ScenarioBudget
    deterministic_fixture_refs: tuple[VersionedReference, ...]
    toolset_version: str
    available_tools: tuple[VersionedReference, ...] = ()

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, field_name="Scenario schema version")
        for value, field_name in (
            (self.scenario_id, "Scenario ID"),
            (self.scenario_version, "Scenario version"),
            (self.runtime_version, "Runtime version"),
            (self.harness_version, "Harness version"),
            (self.model_version, "Model version"),
            (self.prompt_version, "Prompt version"),
            (self.context_version, "Context version"),
            (self.toolset_version, "Toolset version"),
        ):
            _require_reference(value, field_name=field_name)

        scenario_input = snapshot_harness_json_mapping(
            self.input,
            error_message="Scenario input must be canonical JSON data",
        )
        if not scenario_input:
            raise ValueError("Scenario input must not be empty")
        if set(scenario_input) - _SCENARIO_INPUT_FIELDS:
            raise ValueError("Scenario input contains unsupported fields")
        question = scenario_input.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("A Scenario requires a non-blank question")
        conversation_summary = scenario_input.get("conversation_summary")
        if conversation_summary is not None and (
            not isinstance(conversation_summary, str) or not conversation_summary.strip()
        ):
            raise ValueError("Scenario conversation summary must be a non-blank string")
        object.__setattr__(self, "input", scenario_input)

        tools = _snapshot_unique_references(
            self.available_tools,
            field_name="Available tools",
        )
        fixtures = _snapshot_unique_references(
            self.deterministic_fixture_refs,
            field_name="Deterministic fixtures",
        )
        if self.run_type is AgentRunType.DIRECT_ANSWER and tools:
            raise ValueError("A Day 2 direct-answer Scenario cannot expose tools")
        if not fixtures:
            raise ValueError("A deterministic Scenario requires at least one fixture reference")
        object.__setattr__(self, "available_tools", tools)
        object.__setattr__(self, "deterministic_fixture_refs", fixtures)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """Expected behavior and scorer declarations around one Scenario."""

    schema_version: int
    case_id: str
    case_version: str
    scenario: Scenario
    expected_stop_reason: RunStopReason
    expected_behavior: Mapping[str, object] = field(repr=False)
    scorers: tuple[ScorerSpec, ...]
    trace_refs: tuple[VersionedReference, ...] = ()
    artifact_refs: tuple[VersionedReference, ...] = ()
    human_notes: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version, field_name="EvalCase schema version")
        _require_reference(self.case_id, field_name="EvalCase ID")
        _require_reference(self.case_version, field_name="EvalCase version")
        expected_behavior = snapshot_harness_json_mapping(
            self.expected_behavior,
            error_message="Expected behavior must be canonical JSON data",
        )
        if self.expected_stop_reason is RunStopReason.FINAL:
            final_output = expected_behavior.get("final_output")
            if final_output is None or (isinstance(final_output, str) and not final_output.strip()):
                raise ValueError("A final EvalCase requires an expected final output")
        elif "final_output" in expected_behavior:
            raise ValueError("A non-final EvalCase cannot claim a successful final output")
        if not self.scorers:
            raise ValueError("An EvalCase requires at least one Scorer")
        scorer_keys = {(item.category, item.name, item.version) for item in self.scorers}
        if len(scorer_keys) != len(self.scorers):
            raise ValueError("EvalCase Scorers must not contain duplicate references")
        object.__setattr__(self, "scorers", tuple(self.scorers))
        object.__setattr__(self, "expected_behavior", expected_behavior)
        object.__setattr__(
            self,
            "trace_refs",
            _snapshot_unique_references(self.trace_refs, field_name="Trace snapshots"),
        )
        object.__setattr__(
            self,
            "artifact_refs",
            _snapshot_unique_references(self.artifact_refs, field_name="Artifact references"),
        )
        if self.human_notes is not None and (
            not self.human_notes.strip() or len(self.human_notes) > _MAX_HUMAN_NOTES_LENGTH
        ):
            raise ValueError("EvalCase human notes are invalid")


@dataclass(frozen=True, slots=True)
class ScenarioDataset:
    """One immutable, versioned collection of EvalCases."""

    schema_version: int
    dataset_id: str
    dataset_version: str
    cases: tuple[EvalCase, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or (
            self.schema_version != SCENARIO_DATASET_SCHEMA_VERSION
        ):
            raise ValueError(
                f"Scenario dataset schema version must be {SCENARIO_DATASET_SCHEMA_VERSION}"
            )
        _require_reference(self.dataset_id, field_name="Dataset ID")
        _require_reference(self.dataset_version, field_name="Dataset version")
        cases = tuple(self.cases)
        if not cases:
            raise ValueError("A Scenario dataset requires at least one EvalCase")
        case_keys = {(item.case_id, item.case_version) for item in cases}
        if len(case_keys) != len(cases):
            raise ValueError("Scenario dataset contains duplicate EvalCases")
        object.__setattr__(self, "cases", cases)


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Scenario dataset JSON contains duplicate object keys")
        document[key] = value
    return document


def _reject_non_finite_number(value: str) -> object:
    raise ValueError(f"Scenario dataset JSON contains a non-finite number: {value}")


def _as_mapping(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(dict[str, object], value)


def _as_list(value: object, *, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return value


def _as_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _as_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _require_fields(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    field_name: str,
) -> None:
    actual = set(value)
    missing = required - actual
    unknown = actual - required
    if missing:
        raise ValueError(f"{field_name} is missing required fields")
    if unknown:
        raise ValueError(f"{field_name} contains unknown fields")


def _parse_reference(value: object, *, field_name: str) -> VersionedReference:
    document = _as_mapping(value, field_name=field_name)
    _require_fields(
        document,
        required=frozenset({"name", "version"}),
        field_name=field_name,
    )
    return VersionedReference(
        name=_as_string(document["name"], field_name=f"{field_name} name"),
        version=_as_string(document["version"], field_name=f"{field_name} version"),
    )


def _parse_reference_list(value: object, *, field_name: str) -> tuple[VersionedReference, ...]:
    return tuple(
        _parse_reference(item, field_name=field_name)
        for item in _as_list(value, field_name=field_name)
    )


def _parse_budget(value: object) -> ScenarioBudget:
    document = _as_mapping(value, field_name="Scenario budget")
    fields = frozenset({"max_steps", "max_total_tokens", "max_cost_micro_usd", "timeout_seconds"})
    _require_fields(document, required=fields, field_name="Scenario budget")
    return ScenarioBudget(
        max_steps=_as_integer(document["max_steps"], field_name="Scenario max steps"),
        max_total_tokens=_as_integer(
            document["max_total_tokens"],
            field_name="Scenario max total tokens",
        ),
        max_cost_micro_usd=_as_integer(
            document["max_cost_micro_usd"],
            field_name="Scenario max cost",
        ),
        timeout_seconds=_as_integer(
            document["timeout_seconds"],
            field_name="Scenario timeout seconds",
        ),
    )


def _parse_scorer(value: object) -> ScorerSpec:
    document = _as_mapping(value, field_name="Scorer")
    _require_fields(
        document,
        required=frozenset({"category", "name", "version", "parameters"}),
        field_name="Scorer",
    )
    try:
        category = ScorerCategory(_as_string(document["category"], field_name="Scorer category"))
    except ValueError:
        raise ValueError("Scorer category is unsupported") from None
    return ScorerSpec(
        category=category,
        name=_as_string(document["name"], field_name="Scorer name"),
        version=_as_string(document["version"], field_name="Scorer version"),
        parameters=_as_mapping(document["parameters"], field_name="Scorer parameters"),
    )


def _parse_scenario(value: object) -> Scenario:
    document = _as_mapping(value, field_name="Scenario")
    _require_fields(
        document,
        required=frozenset(
            {
                "schema_version",
                "scenario_id",
                "scenario_version",
                "run_type",
                "profile",
                "input",
                "runtime_version",
                "harness_version",
                "model_version",
                "prompt_version",
                "context_version",
                "toolset_version",
                "available_tools",
                "budget",
                "deterministic_fixture_refs",
            }
        ),
        field_name="Scenario",
    )
    try:
        run_type = AgentRunType(_as_string(document["run_type"], field_name="Scenario run type"))
    except ValueError:
        raise ValueError("Scenario run type is unsupported") from None
    return Scenario(
        schema_version=_as_integer(
            document["schema_version"],
            field_name="Scenario schema version",
        ),
        scenario_id=_as_string(document["scenario_id"], field_name="Scenario ID"),
        scenario_version=_as_string(
            document["scenario_version"],
            field_name="Scenario version",
        ),
        run_type=run_type,
        profile=_parse_reference(document["profile"], field_name="Harness profile"),
        input=_as_mapping(document["input"], field_name="Scenario input"),
        runtime_version=_as_string(
            document["runtime_version"],
            field_name="Runtime version",
        ),
        harness_version=_as_string(
            document["harness_version"],
            field_name="Harness version",
        ),
        model_version=_as_string(document["model_version"], field_name="Model version"),
        prompt_version=_as_string(
            document["prompt_version"],
            field_name="Prompt version",
        ),
        context_version=_as_string(
            document["context_version"],
            field_name="Context version",
        ),
        toolset_version=_as_string(
            document["toolset_version"],
            field_name="Toolset version",
        ),
        available_tools=_parse_reference_list(
            document["available_tools"],
            field_name="Available tool",
        ),
        budget=_parse_budget(document["budget"]),
        deterministic_fixture_refs=_parse_reference_list(
            document["deterministic_fixture_refs"],
            field_name="Deterministic fixture",
        ),
    )


def _parse_eval_case(value: object) -> EvalCase:
    document = _as_mapping(value, field_name="EvalCase")
    _require_fields(
        document,
        required=frozenset(
            {
                "schema_version",
                "case_id",
                "case_version",
                "scenario",
                "expected_stop_reason",
                "expected_behavior",
                "scorers",
                "trace_refs",
                "artifact_refs",
                "human_notes",
            }
        ),
        field_name="EvalCase",
    )
    try:
        stop_reason = RunStopReason(
            _as_string(
                document["expected_stop_reason"],
                field_name="Expected stop reason",
            )
        )
    except ValueError:
        raise ValueError("Expected stop reason is unsupported") from None
    raw_notes = document["human_notes"]
    if raw_notes is not None and not isinstance(raw_notes, str):
        raise ValueError("EvalCase human notes must be a string or null")
    return EvalCase(
        schema_version=_as_integer(
            document["schema_version"],
            field_name="EvalCase schema version",
        ),
        case_id=_as_string(document["case_id"], field_name="EvalCase ID"),
        case_version=_as_string(document["case_version"], field_name="EvalCase version"),
        scenario=_parse_scenario(document["scenario"]),
        expected_stop_reason=stop_reason,
        expected_behavior=_as_mapping(
            document["expected_behavior"],
            field_name="Expected behavior",
        ),
        scorers=tuple(
            _parse_scorer(item) for item in _as_list(document["scorers"], field_name="Scorers")
        ),
        trace_refs=_parse_reference_list(document["trace_refs"], field_name="Trace snapshot"),
        artifact_refs=_parse_reference_list(
            document["artifact_refs"],
            field_name="Artifact reference",
        ),
        human_notes=raw_notes,
    )


def parse_scenario_dataset(serialized: str) -> ScenarioDataset:
    """Parse one fail-closed JSON document into the v1 typed contract."""

    if len(serialized.encode("utf-8")) > MAX_SCENARIO_DATASET_BYTES:
        raise ValueError("Scenario dataset exceeds the size limit")
    try:
        loaded = cast(
            object,
            json.loads(
                serialized,
                object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=_reject_non_finite_number,
            ),
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Scenario dataset is not valid JSON") from error
    document = _as_mapping(loaded, field_name="Scenario dataset")
    _require_fields(
        document,
        required=frozenset({"schema_version", "dataset_id", "dataset_version", "cases"}),
        field_name="Scenario dataset",
    )
    return ScenarioDataset(
        schema_version=_as_integer(
            document["schema_version"],
            field_name="Scenario dataset schema version",
        ),
        dataset_id=_as_string(document["dataset_id"], field_name="Dataset ID"),
        dataset_version=_as_string(
            document["dataset_version"],
            field_name="Dataset version",
        ),
        cases=tuple(
            _parse_eval_case(item) for item in _as_list(document["cases"], field_name="EvalCases")
        ),
    )


def load_scenario_dataset(path: Path) -> ScenarioDataset:
    """Read and parse a bounded UTF-8 Scenario dataset file."""

    with path.open("rb") as dataset_file:
        encoded = dataset_file.read(MAX_SCENARIO_DATASET_BYTES + 1)
    if len(encoded) > MAX_SCENARIO_DATASET_BYTES:
        raise ValueError("Scenario dataset exceeds the size limit")
    try:
        serialized = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("Scenario dataset must use valid UTF-8") from None
    return parse_scenario_dataset(serialized)
