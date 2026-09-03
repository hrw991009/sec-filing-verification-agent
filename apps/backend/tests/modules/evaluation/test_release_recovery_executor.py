from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.tool_eval import SecToolOutcome
from industry_platform.modules.evaluation.release_evidence import (
    ReleaseEvidenceLayer,
    ReleaseStrategy,
)
from industry_platform.modules.evaluation.release_observation_collector import (
    ReleaseObservationCollection,
    ReleaseRunJudgement,
)
from industry_platform.modules.evaluation.release_recovery import (
    _canonical_sha256,
    build_recovery_report,
    load_recovery_manifest,
)
from industry_platform.modules.evaluation.release_recovery_executor import (
    RecoveryCommand,
    RecoveryCommandResult,
    RecoveryExecutionError,
    RecoveryExecutionPlan,
    RecoveryExecutor,
    RecoveryExercisePlan,
    _redact,
    build_automatic_plan,
    build_plan_template,
    validate_execution_plan,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "sec-release-recovery-v1.json"
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


class _StubExecutor(RecoveryExecutor):
    def _validate_source_tree(self, expected_commit: str) -> None:
        assert expected_commit == "a" * 40

    def _run(self, command: RecoveryCommand) -> RecoveryCommandResult:
        if command.argv == ("probe", "start"):
            stdout = json.dumps(
                {
                    "schema_version": 1,
                    "business_state": {"durable_count": 1, "digest": "before"},
                    "checks": {},
                    "duplicate_side_effect_count": 0,
                    "data_loss_count": 0,
                    "unauthorized_write_count": 0,
                }
            )
        elif command.argv == ("probe", "final"):
            stdout = json.dumps(
                {
                    "schema_version": 1,
                    "business_state": {"durable_count": 1, "digest": "after"},
                    "checks": {"expected_outcome": True, "dependencies_healthy": True},
                    "duplicate_side_effect_count": 0,
                    "data_loss_count": 0,
                    "unauthorized_write_count": 0,
                }
            )
        else:
            stdout = "exercise complete"
        return RecoveryCommandResult(
            argv=command.argv,
            exit_code=0,
            duration_ms=1,
            stdout=stdout,
            stderr="",
        )


def _plan() -> RecoveryExecutionPlan:
    manifest = load_recovery_manifest(MANIFEST_PATH)
    exercises = tuple(
        RecoveryExercisePlan(
            scenario_id=contract.scenario_id,
            approved_targets=(f"iip-test-{contract.fault_target}",),
            start_probe=RecoveryCommand(argv=("probe", "start")),
            commands=(RecoveryCommand(argv=("exercise", contract.scenario_id)),),
            final_probe=RecoveryCommand(argv=("probe", "final")),
            run_id=RUN_ID if contract.runtime_binding_required else None,
            workspace_id=WORKSPACE_ID if contract.runtime_binding_required else None,
        )
        for contract in manifest.scenarios
    )
    return RecoveryExecutionPlan(
        manifest_sha256=_canonical_sha256(manifest),
        source_commit="a" * 40,
        environment="disposable:release-recovery-test",
        exercises=exercises,
        limitations=("Stubbed commands; this is not production recovery evidence.",),
    )


def test_executor_materializes_all_twelve_hashed_exercise_observations(
    tmp_path: Path,
) -> None:
    manifest = load_recovery_manifest(MANIFEST_PATH)
    executor = _StubExecutor(root=tmp_path, evidence_directory=tmp_path / ".data/evidence")

    observations = executor.execute(manifest, _plan())
    report = build_recovery_report(manifest, observations, root=tmp_path)

    assert len(observations.observations) == 12
    assert report.observed_scenario_count == 12
    assert report.recovery_gate_passed is True
    assert all(item.recovery_succeeded for item in observations.observations)
    assert all((tmp_path / item.evidence_path).is_file() for item in observations.observations)


@pytest.mark.parametrize(
    "argv",
    [
        ("docker", "compose", "down", "--volumes"),
        ("tool", "--api-key=secret"),
        ("Remove-Item", "*.dump"),
        ("rm", "-rf", "named-directory"),
        ("git", "reset", "--hard"),
        ("docker", "compose", "down"),
        ("command", "&&", "other"),
    ],
)
def test_command_contract_rejects_destructive_secret_or_shell_arguments(
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="isolated execution policy"):
        RecoveryCommand(argv=argv)


def test_redaction_removes_secret_values_and_database_passwords() -> None:
    value = (
        'token=abc123 {"api_key": "json-secret"} Authorization: Bearer header-secret '
        "POSTGRESQL://user:password@example.test/db"
    )

    redacted = _redact(value)

    assert "abc123" not in redacted
    assert "json-secret" not in redacted
    assert "header-secret" not in redacted
    assert "user:password@" not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_plan_template_covers_manifest_and_preserves_locked_pytest_commands() -> None:
    manifest = load_recovery_manifest(MANIFEST_PATH)

    template = build_plan_template(
        manifest,
        source_commit="a" * 40,
        environment="staging:release-candidate",
    )

    assert tuple(item.scenario_id for item in template.exercises) == tuple(
        item.scenario_id for item in manifest.scenarios
    )
    assert template.exercises[0].commands[0].argv[:5] == (
        "uv",
        "run",
        "--locked",
        "--all-packages",
        "pytest",
    )
    assert template.exercises[1].commands[0].argv == (
        "replace-with-isolated-exercise",
        "postgres-backup-restore",
    )
    with pytest.raises(RecoveryExecutionError, match="template placeholders"):
        validate_execution_plan(manifest, template)


def test_automatic_plan_has_no_placeholders_and_derives_runtime_bindings() -> None:
    manifest = load_recovery_manifest(MANIFEST_PATH)
    judgements = tuple(
        ReleaseRunJudgement(
            case_id=f"case-{index}",
            strategy_id=ReleaseStrategy.A0,
            repetition=1,
            run_id=UUID(int=index),
            observed_outcome=SecToolOutcome.INSUFFICIENT_EVIDENCE,
            final_state_matches=False,
        )
        for index in range(1, 13)
    )
    collection = ReleaseObservationCollection(
        manifest_sha256="1" * 64,
        evidence_layer=ReleaseEvidenceLayer.OFFLINE,
        provider="provider",
        model="model",
        model_version="model-v1",
        runtime_version="runtime-v1",
        harness_version="harness-v1",
        prompt_version="prompt-v1",
        toolset_version="toolset-v1",
        judgements=judgements,
        limitations=("test binding",),
    )

    plan = build_automatic_plan(
        manifest,
        collection,
        run_workspaces={item.run_id: WORKSPACE_ID for item in judgements},
        source_commit="a" * 40,
        environment="disposable:release-recovery-test",
    )

    validate_execution_plan(manifest, plan)
    assert tuple(item.scenario_id for item in plan.exercises) == tuple(
        item.scenario_id for item in manifest.scenarios
    )
    assert all(
        "replace" not in value.lower()
        for exercise in plan.exercises
        for command in (exercise.start_probe, *exercise.commands, exercise.final_probe)
        for value in command.argv
    )
    runtime_exercises = tuple(item for item in plan.exercises if item.run_id is not None)
    assert len(runtime_exercises) == 9
    assert len({item.run_id for item in runtime_exercises}) == 9
    assert tuple(item.run_id for item in runtime_exercises) == tuple(
        item.run_id for item in judgements[:9]
    )
    assert {item.workspace_id for item in runtime_exercises} == {WORKSPACE_ID}
