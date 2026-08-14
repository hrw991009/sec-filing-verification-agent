"""Run every Day 2 record through the same DirectAnswerRuntime twice."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
SESSION_ID = UUID("55555555-5555-4555-8555-555555555555")


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

    assert len(dataset.cases) == 6
    for case in dataset.cases:
        first, _, _ = await run_recorded_case(case, repetition=1, fixtures=fixtures)
        second, _, _ = await run_recorded_case(case, repetition=2, fixtures=fixtures)

        snapshots.score(case, first)
        snapshots.score(case, second)
        assert tuple(event.event_type for event in first.events) == tuple(
            event.event_type for event in second.events
        )


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


def test_trace_snapshots_never_record_prompt_answer_or_secret_text() -> None:
    snapshot_text = SNAPSHOT_PATH.read_text(encoding="utf-8")

    assert "What is the purpose" not in snapshot_text
    assert "The Runtime advances" not in snapshot_text
    assert "provider/day2-harness-key" not in snapshot_text
