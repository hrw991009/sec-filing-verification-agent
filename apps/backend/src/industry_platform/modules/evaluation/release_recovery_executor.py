"""Execute isolated recovery plans and materialize auditable observations."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.evaluation.release import load_strict_json
from industry_platform.modules.evaluation.release_observation_collector import (
    ReleaseObservationCollection,
    load_collection,
)
from industry_platform.modules.evaluation.release_recovery import (
    RecoveryAutomation,
    RecoveryExecutionStatus,
    RecoveryManifest,
    RecoveryObservation,
    RecoveryObservationSet,
    RecoveryScenarioContract,
    _canonical_sha256,
    load_recovery_manifest,
)

RECOVERY_PLAN_SCHEMA_VERSION: Literal[1] = 1
RECOVERY_PROBE_SCHEMA_VERSION: Literal[1] = 1
RECOVERY_EVIDENCE_SCHEMA_VERSION: Literal[1] = 1
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_COMMIT_PATTERN = r"^[a-f0-9]{40}$"
_ENVIRONMENT_PATTERN = r"^(?:disposable|staging):[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
_SENSITIVE_ARGUMENT = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[-_]?key|authorization|bearer)"
)
_SENSITIVE_OUTPUT = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|secret|token|api[-_]?key|authorization)\b"
    r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|(?:bearer\s+)?[^\s,;}]+)"
)
_DATABASE_URL = re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:)[^@\s]+(@)")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_FORBIDDEN_SEQUENCE = re.compile(
    r"(?i)(?:down\s+--volumes|volume\s+rm|system\s+prune|builder\s+prune|"
    r"network\s+prune|image\s+prune|container\s+prune|git\s+(?:clean|reset)|"
    r"docker(?:\.exe)?\s+(?:rm|rmi)|docker(?:\.exe)?\s+compose\s+down|"
    r"\b(?:drop|truncate)\s+(?:database|schema|table|index|collection)\b)"
)
_SHELL_META = frozenset({"|", ";", "&&", "||", ">", ">>", "<"})
_DESTRUCTIVE_EXECUTABLES = frozenset(
    {
        "del",
        "del.exe",
        "diskpart",
        "diskpart.exe",
        "erase",
        "format",
        "format.com",
        "rd",
        "remove-item",
        "rm",
        "rmdir",
    }
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecoveryCommand(_FrozenModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=600, ge=1, le=7_200)

    @model_validator(mode="after")
    def _validate_command(self) -> Self:
        if any(not value.strip() or "\x00" in value for value in self.argv):
            raise ValueError("Recovery command arguments must be non-empty")
        command_text = " ".join(self.argv)
        executable = Path(self.argv[0]).name.lower()
        if (
            executable in _DESTRUCTIVE_EXECUTABLES
            or any(value in _SHELL_META for value in self.argv)
            or _FORBIDDEN_SEQUENCE.search(command_text)
            or any(_SENSITIVE_ARGUMENT.search(value) for value in self.argv)
            or any("*" in value or "?" in value for value in self.argv)
        ):
            raise ValueError("Recovery command violates the isolated execution policy")
        return self


class RecoveryStateProbe(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_PROBE_SCHEMA_VERSION
    business_state: Mapping[str, int | str | bool]
    checks: Mapping[str, bool] = Field(default_factory=dict)
    duplicate_side_effect_count: int = Field(ge=0)
    data_loss_count: int = Field(ge=0)
    unauthorized_write_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_probe(self) -> Self:
        if (
            not self.business_state
            or any(not key.strip() for key in self.business_state)
            or any(not key.strip() for key in self.checks)
        ):
            raise ValueError("Recovery probe state and check names are required")
        return self

    @property
    def state_sha256(self) -> str:
        return _json_sha256(self.business_state)


class RecoveryExercisePlan(_FrozenModel):
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    approved_targets: tuple[str, ...] = Field(min_length=1, max_length=32)
    start_probe: RecoveryCommand
    commands: tuple[RecoveryCommand, ...] = Field(min_length=1, max_length=32)
    final_probe: RecoveryCommand
    run_id: UUID | None = None
    case_id: UUID | None = None
    workspace_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_exercise(self) -> Self:
        if len(self.approved_targets) != len(set(self.approved_targets)) or any(
            not item.strip() for item in self.approved_targets
        ):
            raise ValueError("Recovery approved targets must be unique and non-empty")
        for identifier in (self.run_id, self.case_id, self.workspace_id):
            if identifier is not None and identifier.int == 0:
                raise ValueError("Recovery plan binding cannot contain nil UUIDs")
        return self


class RecoveryExecutionPlan(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_PLAN_SCHEMA_VERSION
    manifest_id: Literal["sec-release-recovery-v1"] = "sec-release-recovery-v1"
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str = Field(pattern=_COMMIT_PATTERN)
    environment: str = Field(pattern=_ENVIRONMENT_PATTERN)
    exercises: tuple[RecoveryExercisePlan, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_plan(self) -> Self:
        scenario_ids = tuple(item.scenario_id for item in self.exercises)
        if not scenario_ids or len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Recovery plan scenario ids must be non-empty and unique")
        if not self.limitations or any(not item.strip() for item in self.limitations):
            raise ValueError("Recovery plan limitations are required")
        return self


class RecoveryCommandResult(_FrozenModel):
    argv: tuple[str, ...]
    exit_code: int
    duration_ms: int = Field(ge=0)
    stdout: str
    stderr: str


class RecoveryExerciseEvidence(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_EVIDENCE_SCHEMA_VERSION
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str = Field(pattern=_COMMIT_PATTERN)
    environment: str
    scenario_id: str
    fault_target: str
    expected_outcome: str
    required_services: tuple[str, ...]
    approved_targets: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    start_probe_result: RecoveryCommandResult
    command_results: tuple[RecoveryCommandResult, ...]
    final_probe_result: RecoveryCommandResult
    start_state: RecoveryStateProbe
    final_state: RecoveryStateProbe
    recovery_succeeded: bool


class RecoveryExecutionError(RuntimeError):
    """Raised when an exercise cannot produce trustworthy recovery evidence."""


class RecoveryExecutor:
    def __init__(self, *, root: Path, evidence_directory: Path) -> None:
        self._root = root.resolve()
        self._evidence_directory = evidence_directory.resolve()
        if not self._evidence_directory.is_relative_to(self._root):
            raise RecoveryExecutionError(
                "Recovery evidence directory must be inside the repository"
            )

    def execute(
        self,
        manifest: RecoveryManifest,
        plan: RecoveryExecutionPlan,
    ) -> RecoveryObservationSet:
        validate_execution_plan(manifest, plan)
        exercises = {item.scenario_id: item for item in plan.exercises}
        self._validate_source_tree(plan.source_commit)
        self._evidence_directory.mkdir(parents=True, exist_ok=True)

        observations = []
        for contract in manifest.scenarios:
            exercise = exercises[contract.scenario_id]
            observations.append(
                self._execute_exercise(
                    manifest_sha256=plan.manifest_sha256,
                    source_commit=plan.source_commit,
                    environment=plan.environment,
                    contract=contract,
                    exercise=exercise,
                )
            )
        return RecoveryObservationSet(
            manifest_sha256=plan.manifest_sha256,
            execution_status=RecoveryExecutionStatus.EXECUTED,
            source_commit=plan.source_commit,
            environment=plan.environment,
            observations=tuple(observations),
            limitations=plan.limitations,
        )

    def _execute_exercise(
        self,
        *,
        manifest_sha256: str,
        source_commit: str,
        environment: str,
        contract: RecoveryScenarioContract,
        exercise: RecoveryExercisePlan,
    ) -> RecoveryObservation:
        started_at = datetime.now(UTC)
        start_result = self._run(exercise.start_probe)
        start_state = self._parse_probe(start_result, phase="start")
        command_results = tuple(self._run(command) for command in exercise.commands)
        final_result = self._run(exercise.final_probe)
        final_state = self._parse_probe(final_result, phase="final")
        completed_at = datetime.now(UTC)
        recovery_succeeded = (
            all(item.exit_code == 0 for item in command_results)
            and bool(final_state.checks)
            and all(final_state.checks.values())
            and final_state.duplicate_side_effect_count == 0
            and final_state.data_loss_count == 0
            and final_state.unauthorized_write_count == 0
        )
        evidence = RecoveryExerciseEvidence(
            manifest_sha256=manifest_sha256,
            source_commit=source_commit,
            environment=environment,
            scenario_id=contract.scenario_id,
            fault_target=contract.fault_target,
            expected_outcome=contract.expected_outcome,
            required_services=contract.required_services,
            approved_targets=exercise.approved_targets,
            started_at=started_at,
            completed_at=completed_at,
            start_probe_result=start_result,
            command_results=command_results,
            final_probe_result=final_result,
            start_state=start_state,
            final_state=final_state,
            recovery_succeeded=recovery_succeeded,
        )
        evidence_path = self._evidence_directory / f"{contract.scenario_id}.json"
        _write_json(evidence.model_dump(mode="json"), evidence_path)
        relative_evidence_path = PurePosixPath(evidence_path.relative_to(self._root).as_posix())
        recovery_command_sha256 = _json_sha256(
            [command.model_dump(mode="json") for command in exercise.commands]
        )
        return RecoveryObservation(
            scenario_id=contract.scenario_id,
            exercise_id=uuid4(),
            run_id=exercise.run_id,
            case_id=exercise.case_id,
            workspace_id=exercise.workspace_id,
            started_at=started_at,
            completed_at=completed_at,
            start_state_sha256=start_state.state_sha256,
            final_state_sha256=final_state.state_sha256,
            recovery_command_sha256=recovery_command_sha256,
            evidence_path=relative_evidence_path.as_posix(),
            evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            recovery_succeeded=recovery_succeeded,
            duplicate_side_effect_count=final_state.duplicate_side_effect_count,
            data_loss_count=final_state.data_loss_count,
            unauthorized_write_count=final_state.unauthorized_write_count,
            duration_ms=round((completed_at - started_at).total_seconds() * 1_000),
        )

    def _run(self, command: RecoveryCommand) -> RecoveryCommandResult:
        started = monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv is validated and shell is disabled
                command.argv,
                cwd=self._root,
                env=os.environ.copy(),
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=command.timeout_seconds,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            exit_code = 124 if isinstance(error, subprocess.TimeoutExpired) else 127
            stdout = ""
            stderr = type(error).__name__
        return RecoveryCommandResult(
            argv=command.argv,
            exit_code=exit_code,
            duration_ms=max(0, round((monotonic() - started) * 1_000)),
            stdout=_redact(stdout),
            stderr=_redact(stderr),
        )

    @staticmethod
    def _parse_probe(result: RecoveryCommandResult, *, phase: str) -> RecoveryStateProbe:
        if result.exit_code != 0:
            raise RecoveryExecutionError(f"Recovery {phase} probe failed")
        try:
            return RecoveryStateProbe.model_validate_json(result.stdout, strict=True)
        except ValueError as error:
            raise RecoveryExecutionError(
                f"Recovery {phase} probe did not emit the strict JSON contract"
            ) from error

    def _validate_source_tree(self, expected_commit: str) -> None:
        commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),  # noqa: S607 - fixed Git inspection command
            cwd=self._root,
            capture_output=True,
            check=True,
            text=True,
            encoding="ascii",
        ).stdout.strip()
        if commit != expected_commit:
            raise RecoveryExecutionError("Recovery source commit differs from repository HEAD")
        status = subprocess.run(
            ("git", "status", "--porcelain"),  # noqa: S607 - fixed Git inspection command
            cwd=self._root,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        ).stdout
        if status:
            raise RecoveryExecutionError("Recovery evidence requires a clean source tree")


def load_execution_plan(path: Path) -> RecoveryExecutionPlan:
    raw = path.read_text(encoding="utf-8")
    load_strict_json(path)
    return RecoveryExecutionPlan.model_validate_json(raw, strict=True)


def validate_execution_plan(
    manifest: RecoveryManifest,
    plan: RecoveryExecutionPlan,
) -> None:
    if plan.manifest_sha256 != _canonical_sha256(manifest):
        raise RecoveryExecutionError("Recovery plan manifest checksum changed")
    contracts = {item.scenario_id: item for item in manifest.scenarios}
    exercises = {item.scenario_id: item for item in plan.exercises}
    if set(exercises) != set(contracts):
        raise RecoveryExecutionError("Recovery plan must cover all frozen scenarios")
    for contract in manifest.scenarios:
        exercise = exercises[contract.scenario_id]
        if contract.runtime_binding_required and (
            exercise.run_id is None or exercise.workspace_id is None
        ):
            raise RecoveryExecutionError(
                f"Recovery scenario requires Run and Workspace binding: {contract.scenario_id}"
            )
        values = [*exercise.approved_targets]
        for command in (exercise.start_probe, *exercise.commands, exercise.final_probe):
            values.extend(command.argv)
        if any("replace" in value.lower() for value in values):
            raise RecoveryExecutionError(
                f"Recovery scenario still contains template placeholders: {contract.scenario_id}"
            )


def write_recovery_observations(observations: RecoveryObservationSet, output: Path) -> None:
    _write_json(observations.model_dump(mode="json"), output)


def build_plan_template(
    manifest: RecoveryManifest,
    *,
    source_commit: str,
    environment: str,
) -> RecoveryExecutionPlan:
    exercises = []
    for contract in manifest.scenarios:
        commands = (
            RecoveryCommand(argv=tuple(shlex.split(contract.verification_ref)))
            if contract.automation is RecoveryAutomation.PYTEST
            else RecoveryCommand(argv=("replace-with-isolated-exercise", contract.scenario_id))
        )
        exercises.append(
            RecoveryExercisePlan(
                scenario_id=contract.scenario_id,
                approved_targets=("REPLACE_WITH_VERIFIED_TARGET",),
                start_probe=RecoveryCommand(
                    argv=("replace-with-state-probe", contract.scenario_id, "start")
                ),
                commands=(commands,),
                final_probe=RecoveryCommand(
                    argv=("replace-with-state-probe", contract.scenario_id, "final")
                ),
            )
        )
    return RecoveryExecutionPlan(
        manifest_sha256=_canonical_sha256(manifest),
        source_commit=source_commit,
        environment=environment,
        exercises=tuple(exercises),
        limitations=(
            "Template only; replace targets, probes, manual exercise commands, and required "
            "runtime bindings before execution.",
        ),
    )


_RECOVERY_MODULE = "industry_platform.modules.evaluation.release_recovery_exercise"
_AUTOMATED_TARGETS: Mapping[str, tuple[str, ...]] = {
    "fresh-migration": ("postgres:disposable-migration",),
    "postgres-backup-restore": ("postgres:disposable-restore",),
    "filing-index-rebuild": (
        "postgres:release-evidence",
        "minio:release-evidence",
        "elasticsearch:release-recovery",
        "milvus:release-recovery",
    ),
    "worker-interruption-resume": ("worker:release-recovery",),
    "redis-outage-recovery": ("compose:redis",),
    "minio-outage-recovery": ("compose:minio",),
    "elasticsearch-outage-rebuild": ("compose:elasticsearch",),
    "milvus-outage-rebuild": ("compose:milvus",),
    "sec-429-backoff": ("sec:controlled-429",),
    "dead-letter-replay": ("outbox:release-recovery",),
    "notification-unknown-idempotency": ("tool-delivery:release-recovery",),
    "previous-image-rollback": ("release-image:previous-immutable",),
}


def build_automatic_plan(
    manifest: RecoveryManifest,
    collection: ReleaseObservationCollection,
    *,
    run_workspaces: Mapping[UUID, UUID],
    source_commit: str,
    environment: str,
    state_directory: Path = Path(".data/evals/sec-release-recovery-state-v1"),
) -> RecoveryExecutionPlan:
    """Build the fixed 12-scenario plan from production Run bindings."""

    if not collection.judgements:
        raise RecoveryExecutionError("Recovery automation requires production Run bindings")
    collection_run_ids = tuple(item.run_id for item in collection.judgements)
    runtime_contracts = tuple(item for item in manifest.scenarios if item.runtime_binding_required)
    if len(collection_run_ids) < len(runtime_contracts):
        raise RecoveryExecutionError("Recovery automation has too few production Run bindings")
    missing_workspaces = set(collection_run_ids) - set(run_workspaces)
    workspace_ids = {run_workspaces[item] for item in collection_run_ids if item in run_workspaces}
    if missing_workspaces or len(workspace_ids) != 1:
        raise RecoveryExecutionError(
            "Recovery automation requires one complete production Workspace binding"
        )
    workspace_id = next(iter(workspace_ids))
    if set(_AUTOMATED_TARGETS) != {item.scenario_id for item in manifest.scenarios}:
        raise RecoveryExecutionError("Recovery automation targets differ from the manifest")

    python = sys.executable
    exercises: list[RecoveryExercisePlan] = []
    runtime_bindings = iter(collection_run_ids)
    for contract in manifest.scenarios:
        binding: UUID | None = None
        if contract.runtime_binding_required:
            binding = next(runtime_bindings)
        scenario_state = state_directory / contract.scenario_id
        base = (
            python,
            "-m",
            _RECOVERY_MODULE,
            "--scenario",
            contract.scenario_id,
            "--state-directory",
            scenario_state.as_posix(),
            "--environment",
            environment,
        )
        binding_args: tuple[str, ...] = ()
        if binding is not None:
            binding_args = (
                "--run-id",
                str(binding),
                "--workspace-id",
                str(workspace_id),
            )
        exercises.append(
            RecoveryExercisePlan(
                scenario_id=contract.scenario_id,
                approved_targets=_AUTOMATED_TARGETS[contract.scenario_id],
                start_probe=RecoveryCommand(
                    argv=(*base, *binding_args, "probe", "--phase", "start"),
                    timeout_seconds=120,
                ),
                commands=(
                    RecoveryCommand(
                        argv=(*base, *binding_args, "execute"),
                        timeout_seconds=7_200,
                    ),
                ),
                final_probe=RecoveryCommand(
                    argv=(*base, *binding_args, "probe", "--phase", "final"),
                    timeout_seconds=120,
                ),
                run_id=binding,
                workspace_id=None if binding is None else workspace_id,
            )
        )
    return RecoveryExecutionPlan(
        manifest_sha256=_canonical_sha256(manifest),
        source_commit=source_commit,
        environment=environment,
        exercises=tuple(exercises),
        limitations=(
            "Runs and Workspace identities are bound from the production release collection; "
            "the previous immutable image digest remains an operator-supplied environment value.",
        ),
    )


async def load_run_workspaces(
    settings: Settings,
    collection: ReleaseObservationCollection,
) -> dict[UUID, UUID]:
    """Resolve collection Run ownership from PostgreSQL without trusting local input."""

    run_ids = tuple(item.run_id for item in collection.judgements)
    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        async with session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(AgentRunRecord.id, AgentRunRecord.workspace_id).where(
                            AgentRunRecord.id.in_(run_ids)
                        )
                    )
                )
                .tuples()
                .all()
            )
    finally:
        await engine.dispose()
    result = {run_id: workspace_id for run_id, workspace_id in rows}
    if set(result) != set(run_ids):
        raise RecoveryExecutionError("Recovery automation cannot resolve every production Run")
    return result


def _redact(value: str) -> str:
    redacted = _SENSITIVE_OUTPUT.sub(r"\1[REDACTED]", value)
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)
    return _DATABASE_URL.sub(r"\1[REDACTED]\2", redacted)


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(value: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_execution_plan(plan: RecoveryExecutionPlan, output: Path) -> None:
    """Persist one strict automatic plan for review and execution."""

    _write_json(plan.model_dump(mode="json"), output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("evals/manifests/sec-release-recovery-v1.json"),
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--write-template", type=Path)
    parser.add_argument("--write-automatic-plan", type=Path)
    parser.add_argument("--collection", type=Path)
    parser.add_argument(
        "--environment",
        default="disposable:sec-release-recovery",
    )
    parser.add_argument("--source-commit")
    parser.add_argument(
        "--evidence-directory",
        type=Path,
        default=Path(".data/evals/sec-release-recovery-v1"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".data/evals/sec-release-recovery-v1.json"),
    )
    parser.add_argument(
        "--schema-output",
        type=Path,
        default=Path(".data/evals/recovery-execution-plan-v1.schema.json"),
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        default=Path(".data/evals/sec-release-recovery-state-v1"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_recovery_manifest(args.manifest)
    _write_json(RecoveryExecutionPlan.model_json_schema(mode="validation"), args.schema_output)
    write_modes = sum(
        value is not None for value in (args.write_template, args.write_automatic_plan)
    )
    if write_modes:
        if args.plan is not None or write_modes != 1:
            raise SystemExit("Recovery plan input and write modes are mutually exclusive")
        source_commit = (
            args.source_commit
            or subprocess.run(
                ("git", "rev-parse", "HEAD"),  # noqa: S607 - fixed Git inspection command
                capture_output=True,
                check=True,
                text=True,
                encoding="ascii",
            ).stdout.strip()
        )
        if args.write_template is not None:
            generated = build_plan_template(
                manifest,
                source_commit=source_commit,
                environment=args.environment,
            )
            output = args.write_template
        else:
            if args.collection is None:
                raise SystemExit("--collection is required for an automatic recovery plan")
            collection = load_collection(args.collection)
            run_workspaces = asyncio.run(load_run_workspaces(Settings(), collection))
            generated = build_automatic_plan(
                manifest,
                collection,
                run_workspaces=run_workspaces,
                source_commit=source_commit,
                environment=args.environment,
                state_directory=args.state_directory,
            )
            output = args.write_automatic_plan
            if output is None:
                raise AssertionError("Automatic recovery plan output disappeared")
        _write_json(generated.model_dump(mode="json"), output)
        sys.stdout.write(
            json.dumps(
                {"ok": True, "plan": str(output)},
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    if args.plan is None:
        raise SystemExit("--plan is required unless --write-template is used")
    plan = load_execution_plan(args.plan)
    observations = RecoveryExecutor(
        root=Path.cwd(), evidence_directory=args.evidence_directory
    ).execute(manifest, plan)
    write_recovery_observations(observations, args.output)
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "execution_status": observations.execution_status.value,
                "observation_count": len(observations.observations),
                "output": str(args.output),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
