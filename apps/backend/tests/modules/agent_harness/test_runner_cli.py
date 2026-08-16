"""Tests proving the Harness delegates to Runtime and its CLI stays metadata-only."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.agent_harness.cli import run_cli
from industry_platform.modules.agent_harness.runner import (
    HarnessExecutionError,
    HarnessRunner,
    MaterializedScenario,
)
from industry_platform.modules.agent_harness.scenarios import Scenario, load_scenario_dataset
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.identity.domain import TraceId

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day2-v1.json"
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
TRACE_ID = TraceId("trace-day2-harness")
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: AgentEventType,
    *,
    payload: dict[str, object] | None = None,
) -> AgentEvent:
    return AgentEvent(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=sequence,
        occurred_at=NOW + timedelta(seconds=sequence - 1),
        trace_id=TRACE_ID,
        event_type=event_type,
        payload={} if payload is None else payload,
    )


class RecordingMaterializer:
    def __init__(self) -> None:
        self.scenarios: list[Scenario] = []

    def materialize(self, scenario: Scenario) -> MaterializedScenario[str, UUID]:
        self.scenarios.append(scenario)
        return MaterializedScenario(command="answer", runtime_context=WORKSPACE_ID)


class RecordingRuntime:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self.events = events
        self.calls: list[tuple[str, UUID]] = []

    async def run(self, command: str, runtime_context: UUID) -> AsyncIterator[AgentEvent]:
        self.calls.append((command, runtime_context))
        for item in self.events:
            yield item


@pytest.mark.asyncio
async def test_runner_calls_the_injected_runtime_once_and_preserves_events() -> None:
    eval_case = load_scenario_dataset(DATASET_PATH).cases[0]
    events = (
        event(1, AgentEventType.RUN_QUEUED),
        event(
            2,
            AgentEventType.RUN_COMPLETED,
            payload={"stop_reason": "final"},
        ),
    )
    runtime = RecordingRuntime(events)
    materializer = RecordingMaterializer()
    runner = HarnessRunner(runtime=runtime, materializer=materializer)

    result = await runner.run_case(eval_case)

    assert result.events == events
    assert runtime.calls == [("answer", WORKSPACE_ID)]
    assert materializer.scenarios == [eval_case.scenario]


@pytest.mark.asyncio
async def test_runner_does_not_turn_an_empty_runtime_stream_into_success() -> None:
    eval_case = load_scenario_dataset(DATASET_PATH).cases[0]
    runtime = RecordingRuntime(())
    runner = HarnessRunner(runtime=runtime, materializer=RecordingMaterializer())

    with pytest.raises(HarnessExecutionError, match="no Events"):
        await runner.run_case(eval_case)


@pytest.mark.asyncio
async def test_runner_rejects_missing_terminal_and_wrong_expected_stop_reason() -> None:
    eval_case = load_scenario_dataset(DATASET_PATH).cases[0]
    missing_terminal = HarnessRunner(
        runtime=RecordingRuntime((event(1, AgentEventType.RUN_QUEUED),)),
        materializer=RecordingMaterializer(),
    )
    wrong_terminal = HarnessRunner(
        runtime=RecordingRuntime(
            (
                event(1, AgentEventType.RUN_QUEUED),
                event(
                    2,
                    AgentEventType.RUN_FAILED,
                    payload={"stop_reason": "provider_timeout"},
                ),
            )
        ),
        materializer=RecordingMaterializer(),
    )

    with pytest.raises(HarnessExecutionError, match="exactly one"):
        await missing_terminal.run_case(eval_case)
    with pytest.raises(HarnessExecutionError, match="contradicts"):
        await wrong_terminal.run_case(eval_case)


def test_cli_validates_and_lists_only_non_sensitive_metadata() -> None:
    stdout = StringIO()
    stderr = StringIO()
    status = run_cli(
        ("validate", "--dataset", str(DATASET_PATH)),
        stdout=stdout,
        stderr=stderr,
    )
    validation = json.loads(stdout.getvalue())

    assert status == 0
    assert validation["status"] == "valid"
    assert validation["case_count"] == 1
    assert stderr.getvalue() == ""

    stdout = StringIO()
    status = run_cli(
        ("list", "--dataset", str(DATASET_PATH)),
        stdout=stdout,
        stderr=stderr,
    )
    listed = stdout.getvalue()

    assert status == 0
    assert "day2-direct-answer-basic" in listed
    assert "What is the purpose of an Agent Runtime?" not in listed
    assert "The Runtime advances" not in listed


def test_cli_returns_a_stable_error_without_echoing_invalid_content(tmp_path: Path) -> None:
    sensitive_value = "do-not-echo-this-input"
    invalid_dataset = tmp_path / "invalid.json"
    invalid_dataset.write_text(
        f'{{"schema_version":1,"schema_version":1,"input":"{sensitive_value}"}}',
        encoding="utf-8",
    )
    stdout = StringIO()
    stderr = StringIO()

    status = run_cli(
        ("validate", "--dataset", str(invalid_dataset)),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 2
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "INVALID_SCENARIO_DATASET"
    assert sensitive_value not in stderr.getvalue()


def test_cli_argument_errors_use_the_injected_machine_readable_stream() -> None:
    stdout = StringIO()
    stderr = StringIO()

    status = run_cli(("validate",), stdout=stdout, stderr=stderr)

    assert status == 2
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "INVALID_HARNESS_ARGUMENTS"
