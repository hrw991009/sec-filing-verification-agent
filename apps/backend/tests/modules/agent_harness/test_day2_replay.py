"""Run Day 2 model records and validate executable reliability scenarios."""

import ast
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from industry_platform.modules.agent_harness.direct_answer import (
    DirectAnswerScenarioMaterializer,
    HarnessExecutionIdentity,
    HarnessTrustedIdentity,
)
from industry_platform.modules.agent_harness.fakes import ScriptedModelProvider
from industry_platform.modules.agent_harness.profiles import DirectAnswerProfile
from industry_platform.modules.agent_harness.records import (
    RecordedFixtureRegistry,
    load_recorded_fixtures,
    load_trace_snapshots,
)
from industry_platform.modules.agent_harness.runner import HarnessRunner, HarnessRunResult
from industry_platform.modules.agent_harness.scenarios import EvalCase, load_scenario_dataset
from industry_platform.modules.agent_runtime.context import ContextManifest
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.events import AgentEvent
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.streaming import (
    CommittedEventWindow,
    load_committed_replay,
)
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day2-v2.json"
FIXTURE_PATH = REPOSITORY_ROOT / "evals" / "fixtures" / "day2-model-v1.json"
SNAPSHOT_PATH = REPOSITORY_ROOT / "evals" / "snapshots" / "day2-traces-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day2-v1.json"
RELIABILITY_DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day2-reliability-v1.json"
NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
SESSION_ID = UUID("55555555-5555-4555-8555-555555555555")


class ReportScorer(TypedDict):
    category: str
    name: str
    version: str
    status: str


class ReportCase(TypedDict):
    case_id: str
    case_version: str
    expected_stop_reason: str
    observed_stop_reason: str
    event_skeleton_stable: bool
    provider_calls_per_execution: int
    scorers: list[ReportScorer]


class ReportAggregate(TypedDict):
    case_count: int
    passed_case_count: int
    expected_stop_reason_match_rate: float
    event_skeleton_consistency_rate: float
    provider_fault_case_count: int
    cancellation_case_count: int
    client_reconnect_model_reexecution_count: int


class ReportDatasetReference(TypedDict):
    id: str
    version: str


class ReportReliabilityScenarios(TypedDict):
    dataset: ReportDatasetReference
    case_count: int
    required_categories: list[str]
    test_binding_status: str
    execution_evidence: str


class ReportScenarioInventory(TypedDict):
    direct_answer_replay_case_count: int
    reliability_case_count: int
    day2_versioned_scenario_count: int


class ReportNotApplicableReview(TypedDict):
    status: str
    reason: str
    deferred_obligation: str


class ReportApplicableScenarioReview(TypedDict):
    status: str
    scenario_id: str


class ReportApplicabilityReview(TypedDict):
    reviewed_by: str
    reviewed_on: str
    tool_failure: ReportNotApplicableReview
    durable_graph_resume: ReportNotApplicableReview
    unrecoverable_worker_interruption: ReportApplicableScenarioReview


class ReportProductionPathEvidence(TypedDict):
    execution_boundary: str
    provider_boundary: str
    executable_test_ref: str
    verified_facts: list[str]


class Day2EvalReport(TypedDict):
    schema_version: int
    aggregate: ReportAggregate
    reliability_scenarios: ReportReliabilityScenarios
    scenario_inventory: ReportScenarioInventory
    applicability_review: ReportApplicabilityReview
    production_path_evidence: ReportProductionPathEvidence
    cases: list[ReportCase]


class ReliabilityScenario(TypedDict):
    schema_version: int
    scenario_id: str
    scenario_version: str
    category: str
    execution_boundary: str
    fault: str


class ReliabilityScorer(TypedDict):
    category: str
    name: str
    version: str


class ReliabilityCase(TypedDict):
    schema_version: int
    case_id: str
    case_version: str
    scenario: ReliabilityScenario
    expected_terminal_status: str | None
    expected_stop_reason: str | None
    expected_facts: list[str]
    scorers: list[ReliabilityScorer]
    executable_test_refs: list[str]
    human_notes: str


class ReliabilityDataset(TypedDict):
    schema_version: int
    dataset_id: str
    dataset_version: str
    dataset_kind: str
    runtime_entrypoint_policy: str
    cases: list[ReliabilityCase]


class RecordingManifestStore:
    def __init__(self) -> None:
        self.manifests: list[ContextManifest] = []

    async def save(self, manifest: ContextManifest) -> None:
        self.manifests.append(manifest)


class RecordingEventCommitter:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def append(self, event: AgentEvent) -> None:
        self.events.append(event)


class ScriptedCancellationProbe:
    def __init__(self, decisions: tuple[bool, ...]) -> None:
        self._decisions = decisions
        self.calls = 0

    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        assert run_id.int != 0
        assert workspace_id == WORKSPACE_ID
        index = self.calls
        self.calls += 1
        return self._decisions[index] if index < len(self._decisions) else False


@dataclass
class IncrementingClock:
    value: datetime = NOW + timedelta(seconds=1)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


@dataclass(frozen=True, slots=True)
class RecordingCommittedEventSource:
    events: tuple[AgentEvent, ...]
    calls: list[tuple[UUID, UUID]]

    async def load_window(self, *, stream_id: UUID, workspace_id: UUID) -> CommittedEventWindow:
        self.calls.append((stream_id, workspace_id))
        return CommittedEventWindow(
            stream_id=stream_id,
            workspace_id=workspace_id,
            earliest_available_sequence=1,
            latest_committed_sequence=len(self.events),
            events=self.events,
        )


def profile() -> DirectAnswerProfile:
    return DirectAnswerProfile(
        schema_version=1,
        profile_name="direct-answer",
        profile_version="v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/fake-model",
        max_input_tokens=384,
        max_output_tokens=128,
        system_instructions="Answer the current question directly with concise Markdown.",
    )


def trusted_identity() -> HarnessTrustedIdentity:
    return HarnessTrustedIdentity(
        principal=AuthenticatedPrincipal(
            user_id=USER_ID,
            session_id=SESSION_ID,
            email=NormalizedEmail("harness@example.test"),
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Day 2 Harness",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            role="member",
        ),
        capabilities=frozenset({WorkspaceAction.VIEW}),
        secret_references=("provider/day2-harness-key",),
    )


def execution(case: EvalCase, repetition: int) -> HarnessExecutionIdentity:
    def identifier(kind: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"{case.case_id}:{repetition}:{kind}")

    return HarnessExecutionIdentity(
        run_id=identifier("run"),
        stream_id=identifier("stream"),
        model_step_id=identifier("model-step"),
        final_step_id=identifier("final-step"),
        manifest_id=identifier("manifest"),
        trace_id=TraceId(f"trace:{case.case_id}:{repetition}"),
        created_at=NOW,
    )


async def run_recorded_case(
    case: EvalCase,
    *,
    repetition: int,
    fixtures: RecordedFixtureRegistry,
) -> tuple[HarnessRunResult, RecordingEventCommitter, ScriptedModelProvider]:
    fixture = fixtures.resolve(case)
    provider = fixture.build_provider()
    committer = RecordingEventCommitter()
    runtime = DirectAnswerRuntime(
        context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
        context_manifest_store=RecordingManifestStore(),
        model_provider=provider,
        event_committer=committer,
        cancellation_probe=ScriptedCancellationProbe(fixture.cancellation_checks),
        clock=IncrementingClock(),
    )
    materializer = DirectAnswerScenarioMaterializer(
        profile=profile(),
        execution=execution(case, repetition),
        identity=trusted_identity(),
        model_version="fake-model-v1",
        harness_version="harness-v0",
    )
    result = await HarnessRunner(runtime=runtime, materializer=materializer).run_case(case)
    provider.assert_exhausted()
    assert committer.events == list(result.events)
    assert len(provider.requests) == 1
    return result, committer, provider


@pytest.mark.asyncio
async def test_six_versioned_scenarios_replay_twice_through_the_same_runtime() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    fixtures = load_recorded_fixtures(FIXTURE_PATH)
    snapshots = load_trace_snapshots(SNAPSHOT_PATH)
    report = cast(Day2EvalReport, json.loads(REPORT_PATH.read_text(encoding="utf-8")))
    report_cases = {item["case_id"]: item for item in report["cases"]}

    assert len(dataset.cases) == 6
    assert report["schema_version"] == 1
    assert report["aggregate"] == {
        "case_count": 6,
        "passed_case_count": 6,
        "expected_stop_reason_match_rate": 1.0,
        "event_skeleton_consistency_rate": 1.0,
        "provider_fault_case_count": 4,
        "cancellation_case_count": 1,
        "client_reconnect_model_reexecution_count": 0,
    }
    assert report_cases.keys() == {case.case_id for case in dataset.cases}
    for case in dataset.cases:
        first, _, _ = await run_recorded_case(case, repetition=1, fixtures=fixtures)
        second, _, _ = await run_recorded_case(case, repetition=2, fixtures=fixtures)

        snapshots.score(case, first)
        snapshots.score(case, second)
        assert tuple(event.event_type for event in first.events) == tuple(
            event.event_type for event in second.events
        )
        reported = report_cases[case.case_id]
        assert reported["case_version"] == case.case_version
        assert reported["expected_stop_reason"] == case.expected_stop_reason.value
        assert reported["observed_stop_reason"] == first.events[-1].payload["stop_reason"]
        assert reported["event_skeleton_stable"] is True
        assert reported["provider_calls_per_execution"] == 1
        assert reported["scorers"] == [
            {
                "category": scorer.category.value,
                "name": scorer.name,
                "version": scorer.version,
                "status": "passed",
            }
            for scorer in case.scorers
        ]


@pytest.mark.asyncio
async def test_client_reconnect_reads_committed_events_without_a_second_model_call() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    case = dataset.cases[0]
    fixtures = load_recorded_fixtures(FIXTURE_PATH)
    result, committer, provider = await run_recorded_case(
        case,
        repetition=1,
        fixtures=fixtures,
    )
    calls: list[tuple[UUID, UUID]] = []
    source = RecordingCommittedEventSource(events=tuple(committer.events), calls=calls)

    replay = await load_committed_replay(
        source,
        stream_id=result.events[0].stream_id,
        workspace_id=WORKSPACE_ID,
        last_event_id="4",
    )

    assert replay.events == result.events[4:]
    assert calls == [(result.events[0].stream_id, WORKSPACE_ID)]
    assert len(provider.requests) == 1


def _assert_test_ref_exists(test_ref: str, *, tests_root: Path) -> None:
    relative_path, function_name = test_ref.split("::", maxsplit=1)
    test_path = (REPOSITORY_ROOT / relative_path).resolve()
    assert test_path.is_relative_to(tests_root)
    assert test_path.is_file()
    syntax_tree = ast.parse(test_path.read_text(encoding="utf-8"))
    function_names = {
        node.name
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert function_name in function_names


def test_reliability_scenarios_are_versioned_bound_to_tests_and_cover_day2_dod() -> None:
    dataset = cast(
        ReliabilityDataset,
        json.loads(RELIABILITY_DATASET_PATH.read_text(encoding="utf-8")),
    )
    report = cast(Day2EvalReport, json.loads(REPORT_PATH.read_text(encoding="utf-8")))

    assert set(dataset) == {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "dataset_kind",
        "runtime_entrypoint_policy",
        "cases",
    }
    assert dataset["schema_version"] == 1
    assert dataset["dataset_id"] == "day2-runtime-reliability"
    assert dataset["dataset_version"] == "v1"
    assert dataset["dataset_kind"] == "executable-reliability-scenarios"
    assert "does not define another execution loop" in dataset["runtime_entrypoint_policy"]

    required_categories = {
        "budget_exhaustion",
        "worker_interruption",
        "duplicate_request",
    }
    cases = dataset["cases"]
    assert len(cases) == len(required_categories)
    assert {case["scenario"]["category"] for case in cases} == required_categories
    assert len({case["case_id"] for case in cases}) == len(cases)

    discovered_test_refs: set[str] = set()
    tests_root = (REPOSITORY_ROOT / "apps" / "backend" / "tests").resolve()
    for case in cases:
        assert set(case) == {
            "schema_version",
            "case_id",
            "case_version",
            "scenario",
            "expected_terminal_status",
            "expected_stop_reason",
            "expected_facts",
            "scorers",
            "executable_test_refs",
            "human_notes",
        }
        scenario = case["scenario"]
        assert set(scenario) == {
            "schema_version",
            "scenario_id",
            "scenario_version",
            "category",
            "execution_boundary",
            "fault",
        }
        assert case["schema_version"] == scenario["schema_version"] == 1
        assert case["case_version"] == scenario["scenario_version"] == "v1"
        assert case["case_id"] == scenario["scenario_id"]
        assert scenario["execution_boundary"]
        assert scenario["fault"]
        assert case["expected_facts"]
        assert case["human_notes"]
        assert case["executable_test_refs"]
        assert all(
            set(scorer) == {"category", "name", "version"} and scorer["version"] == "v1"
            for scorer in case["scorers"]
        )

        category = scenario["category"]
        if category == "budget_exhaustion":
            assert case["expected_terminal_status"] == "failed"
            assert case["expected_stop_reason"] == "cost_budget_exceeded"
        elif category == "worker_interruption":
            assert case["expected_terminal_status"] == "failed"
            assert case["expected_stop_reason"] == "runtime_error"
            assert "exactly_one_terminal_event_is_committed" in case["expected_facts"]
            assert "retrying_job_and_run_converge_atomically" in case["expected_facts"]
        else:
            assert category == "duplicate_request"
            assert case["expected_terminal_status"] is None
            assert case["expected_stop_reason"] is None
            assert "same_payload_reuses_run_job_and_outbox" in case["expected_facts"]

        for test_ref in case["executable_test_refs"]:
            assert test_ref not in discovered_test_refs
            discovered_test_refs.add(test_ref)
            _assert_test_ref_exists(test_ref, tests_root=tests_root)

    report_reliability = report["reliability_scenarios"]
    assert report_reliability["dataset"] == {
        "id": dataset["dataset_id"],
        "version": dataset["dataset_version"],
    }
    assert report_reliability["case_count"] == len(cases)
    assert set(report_reliability["required_categories"]) == required_categories
    assert report_reliability["test_binding_status"] == ("validated_by_repository_test_discovery")
    assert report["scenario_inventory"] == {
        "direct_answer_replay_case_count": 6,
        "reliability_case_count": 3,
        "day2_versioned_scenario_count": 9,
    }
    applicability = report["applicability_review"]
    assert applicability["reviewed_by"] == "execution_agent"
    assert applicability["reviewed_on"] == "2026-08-15"
    assert applicability["tool_failure"]["status"] == "not_applicable_in_day2_l0"
    assert "Day 3 L1/L2" in applicability["tool_failure"]["deferred_obligation"]
    assert applicability["durable_graph_resume"]["status"] == ("not_applicable_in_day2_l0")
    assert "Day 5 D5-09" in applicability["durable_graph_resume"]["deferred_obligation"]
    assert applicability["unrecoverable_worker_interruption"] == {
        "status": "applicable",
        "scenario_id": "day2-unrecoverable-worker-interruption",
    }
    production_evidence = report["production_path_evidence"]
    assert production_evidence["execution_boundary"] == (
        "conversation_outbox_job_execution_runtime_postgres_replay"
    )
    assert "only the external Provider port" in production_evidence["provider_boundary"]
    assert set(production_evidence["verified_facts"]) == {
        "one_provider_call",
        "final_message_and_events_are_persisted",
        "committed_replay_does_not_reexecute_model",
    }
    _assert_test_ref_exists(
        production_evidence["executable_test_ref"],
        tests_root=tests_root,
    )


def test_trace_snapshots_never_record_prompt_answer_or_secret_text() -> None:
    snapshot_text = SNAPSHOT_PATH.read_text(encoding="utf-8")

    assert "What is the purpose" not in snapshot_text
    assert "The Runtime advances" not in snapshot_text
    assert "provider/day2-harness-key" not in snapshot_text
