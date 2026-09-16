"""Run one fixed, isolated release-recovery exercise and emit strict probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast
from uuid import UUID

import psycopg
from psycopg import sql

from industry_platform.core.config import Settings
from industry_platform.modules.evaluation.release_recovery_executor import RecoveryStateProbe

_SCENARIO_IDS: Final = (
    "fresh-migration",
    "postgres-backup-restore",
    "filing-index-rebuild",
    "worker-interruption-resume",
    "redis-outage-recovery",
    "minio-outage-recovery",
    "elasticsearch-outage-rebuild",
    "milvus-outage-rebuild",
    "sec-429-backoff",
    "dead-letter-replay",
    "notification-unknown-idempotency",
    "previous-image-rollback",
)
_ENVIRONMENT_PATTERN: Final = re.compile(
    r"^(?:disposable|staging):[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
)
_IMAGE_DIGEST_PATTERN: Final = re.compile(r"^[^\s:@]+(?:/[^\s:@]+)*@sha256:[a-f0-9]{64}$")
_STATE_FILE = "start-state.json"
_RESULT_FILE = "exercise-result.json"
_COMPOSE_FILE = "infra/compose/compose.yaml"
RECOVERY_CAPABILITY_CHECKS: Final[Mapping[str, tuple[str, ...]]] = {
    "fresh-migration": (
        "apps/backend/tests/integration/test_migration_smoke.py::"
        "test_complete_migration_history_round_trip",
    ),
    "postgres-backup-restore": (
        "apps/backend/tests/integration/test_release_backup_restore_postgres.py::"
        "test_backup_restore_preserves_populated_jobs_and_all_public_tables",
    ),
    "filing-index-rebuild": (
        "apps/backend/tests/integration/test_knowledge_ingestion_worker_postgres_minio.py::"
        "test_worker_persists_dual_indexes_and_deduplicates_delivery[None]",
    ),
    "worker-interruption-resume": (
        "apps/backend/tests/integration/test_worker_process_recovery_postgres.py::"
        "test_hard_killed_process_resumes_same_job_run_and_checkpoint_once",
    ),
    "redis-outage-recovery": (
        "apps/backend/tests/integration/test_outbox_dispatcher_postgres_redis.py::"
        "test_connection_outage_retains_outbox_then_publishes_original_identity_once",
    ),
    "minio-outage-recovery": (
        "apps/backend/tests/integration/test_knowledge_ingestion_worker_postgres_minio.py::"
        "test_worker_persists_dual_indexes_and_deduplicates_delivery[minio]",
    ),
    "elasticsearch-outage-rebuild": (
        "apps/backend/tests/integration/test_knowledge_ingestion_worker_postgres_minio.py::"
        "test_worker_persists_dual_indexes_and_deduplicates_delivery[elasticsearch]",
    ),
    "milvus-outage-rebuild": (
        "apps/backend/tests/integration/test_knowledge_ingestion_worker_postgres_minio.py::"
        "test_worker_persists_dual_indexes_and_deduplicates_delivery[milvus]",
    ),
    "sec-429-backoff": (
        "apps/backend/tests/modules/disclosures/test_sec_edgar_adapter.py::"
        "test_429_retries_with_bounded_delay_and_never_becomes_no_result",
        "apps/backend/tests/integration/test_sec_request_budget_redis.py",
    ),
    "dead-letter-replay": (
        "apps/backend/tests/integration/test_job_recovery_postgres.py::"
        "test_authorized_replay_keeps_identity_and_history_and_rejects_stale_owner",
    ),
    "notification-unknown-idempotency": (
        "apps/backend/tests/integration/test_write_recovery_postgres.py::"
        "test_unknown_delivery_is_reconciled_before_original_key_retry",
    ),
}


@dataclass(frozen=True, slots=True)
class ExerciseBinding:
    scenario_id: str
    environment: str
    state_directory: Path
    run_id: UUID | None
    workspace_id: UUID | None

    def __post_init__(self) -> None:
        if self.scenario_id not in _SCENARIO_IDS:
            raise ValueError("Recovery scenario is not registered")
        if _ENVIRONMENT_PATTERN.fullmatch(self.environment) is None:
            raise ValueError("Recovery environment is invalid")
        if (self.run_id is None) != (self.workspace_id is None):
            raise ValueError("Recovery Run and Workspace bindings must be paired")
        workspace_id = self.workspace_id
        if (
            self.run_id is not None
            and workspace_id is not None
            and (self.run_id.int == 0 or workspace_id.int == 0)
        ):
            raise ValueError("Recovery binding cannot contain nil UUIDs")


class RecoveryExerciseFailure(RuntimeError):
    """Raised when an isolated recovery exercise cannot prove its outcome."""


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RecoveryExerciseFailure(f"Recovery state is invalid: {path.name}")
    return value


def _run(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int = 1_800,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(  # noqa: S603 - all callers use fixed argv registries
        tuple(argv),
        cwd=Path.cwd(),
        env=dict(os.environ if environment is None else environment),
        input=input_bytes,
        capture_output=True,
        check=False,
        shell=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RecoveryExerciseFailure(
            f"Recovery command failed with exit code {completed.returncode}: {Path(argv[0]).name}"
        )
    return completed


def _settings() -> Settings:
    return Settings(_env_file=Path(".env"))


def _connect(
    settings: Settings, database: str | None = None
) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=database or settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
    )


def _database_state(settings: Settings, *, database: str | None = None) -> dict[str, object]:
    state: dict[str, object] = {}
    row_hashes: dict[str, str] = {}
    with _connect(settings, database) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute(
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        )
        available = {str(row[0]) for row in cursor.fetchall()}
        # Include every public business table, including newly added financial,
        # ingestion and identity records. A fixed shortlist silently missed data loss.
        for table in sorted(available - {"alembic_version"}):
            identifier = sql.Identifier("public", table)
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(identifier))
            count_row = cursor.fetchone()
            if count_row is None:
                raise RecoveryExerciseFailure(f"Business table count disappeared: {table}")
            state[f"{table}_count"] = int(cast(int, count_row[0]))
            cursor.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} AS t").format(identifier))
            rows = sorted(str(row[0]) for row in cursor.fetchall())
            row_hashes[table] = _json_sha256(rows)
        if "alembic_version" in available:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()
            state["alembic_revision"] = "none" if revision is None else str(revision[0])
            row_hashes["alembic_version"] = _json_sha256(state["alembic_revision"])
    state["database_sha256"] = _json_sha256(row_hashes)
    return state


def _run_state(settings: Settings, binding: ExerciseBinding) -> dict[str, object]:
    if binding.run_id is None or binding.workspace_id is None:
        return {"runtime_binding_required": False}
    with _connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, event_count, step_count, workspace_id::text "
            "FROM agent_runs WHERE id = %s",
            (binding.run_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return {
            "runtime_binding_required": True,
            "run_exists": False,
            "run_id": str(binding.run_id),
            "workspace_id": str(binding.workspace_id),
        }
    return {
        "runtime_binding_required": True,
        "run_exists": True,
        "run_id": str(binding.run_id),
        "workspace_id": str(row[3]),
        "run_status": str(row[0]),
        "run_event_count": cast(int, row[1]),
        "run_step_count": cast(int, row[2]),
    }


def _business_state(settings: Settings, binding: ExerciseBinding) -> dict[str, int | str | bool]:
    combined: dict[str, object] = {
        **_database_state(settings),
        **_run_state(settings, binding),
        "scenario_id": binding.scenario_id,
    }
    return {
        key: value
        for key, value in combined.items()
        if isinstance(value, (bool, int, str)) and not isinstance(value, float)
    }


def _integrity_counts(settings: Settings, binding: ExerciseBinding) -> tuple[int, int]:
    run_id = binding.run_id
    with _connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(SUM(group_count - 1), 0) FROM ("
            "SELECT requested_tool_name, idempotency_key_hash, COUNT(*) AS group_count "
            "FROM tool_calls WHERE status = 'completed' "
            "AND idempotency_key_hash IS NOT NULL "
            "AND (%s::uuid IS NULL OR run_id = %s::uuid) "
            "GROUP BY requested_tool_name, idempotency_key_hash HAVING COUNT(*) > 1"
            ") AS duplicate_tool_calls",
            (run_id, run_id),
        )
        duplicate_tool_row = cursor.fetchone()
        cursor.execute(
            "SELECT COALESCE(SUM(group_count - 1), 0) FROM ("
            "SELECT effect_kind, idempotency_key_hash, COUNT(*) AS group_count "
            "FROM research_side_effects WHERE status = 'completed' "
            "AND (%s::uuid IS NULL OR run_id = %s::uuid) "
            "GROUP BY effect_kind, idempotency_key_hash HAVING COUNT(*) > 1"
            ") AS duplicate_research_effects",
            (run_id, run_id),
        )
        duplicate_effect_row = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE status = 'completed' "
            "AND side_effect_class IN ('idempotent_write', 'non_idempotent_write') "
            "AND policy_decision IS DISTINCT FROM 'allow' "
            "AND (%s::uuid IS NULL OR run_id = %s::uuid)",
            (run_id, run_id),
        )
        unauthorized_row = cursor.fetchone()
    if duplicate_tool_row is None or duplicate_effect_row is None or unauthorized_row is None:
        raise RecoveryExerciseFailure("Recovery integrity query returned no result")
    duplicate_count = int(cast(int, duplicate_tool_row[0])) + int(
        cast(int, duplicate_effect_row[0])
    )
    return duplicate_count, int(cast(int, unauthorized_row[0]))


def _pytest(scenario_id: str) -> None:
    refs = RECOVERY_CAPABILITY_CHECKS.get(scenario_id)
    if not refs:
        raise RecoveryExerciseFailure(
            f"Recovery scenario has no fixed pytest reference: {scenario_id}"
        )
    environment = dict(os.environ)
    environment.update(
        {
            "POSTGRES_TESTS_REQUIRED": "1",
            "REDIS_TESTS_REQUIRED": "1",
            "MINIO_TESTS_REQUIRED": "1",
            "VECTOR_TESTS_REQUIRED": "1",
            "ELASTICSEARCH_TESTS_REQUIRED": "1",
            "MILVUS_ENDPOINT": environment.get("MILVUS_ENDPOINT", "http://127.0.0.1:19530"),
            "ELASTICSEARCH_ENDPOINT": environment.get(
                "ELASTICSEARCH_ENDPOINT", "http://127.0.0.1:19200"
            ),
        }
    )
    completed = _run(
        ("uv", "run", "--locked", "--all-packages", "pytest", "-q", "-rA", *refs),
        environment=environment,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace").lower()
    if re.search(r"\bskipped\b", output):
        raise RecoveryExerciseFailure(f"Recovery pytest skipped required evidence: {scenario_id}")


def _database_digest(settings: Settings, database: str) -> str:
    return str(_database_state(settings, database=database)["database_sha256"])


def _backup_restore(settings: Settings) -> dict[str, object]:
    suffix = hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:16]
    restore_database = f"iip_restore_{suffix}"
    if re.fullmatch(r"iip_restore_[a-f0-9]{16}", restore_database) is None:
        raise RecoveryExerciseFailure("Disposable restore database name is invalid")
    postgres_exec = _postgres_exec()
    dump = _run(
        (
            *postgres_exec,
            "pg_dump",
            "--username",
            settings.postgres_user,
            "--dbname",
            settings.postgres_db,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ),
        timeout=1_800,
    ).stdout
    created = False
    try:
        with _connect(settings, "postgres") as connection:
            connection.autocommit = True
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restore_database))
            )
            created = True
        _run(
            (
                *postgres_exec,
                "pg_restore",
                "--username",
                settings.postgres_user,
                "--dbname",
                restore_database,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ),
            input_bytes=dump,
            timeout=1_800,
        )
        source_sha256 = _database_digest(settings, settings.postgres_db)
        restored_sha256 = _database_digest(settings, restore_database)
        if source_sha256 != restored_sha256:
            raise RecoveryExerciseFailure("Restored business data digest differs from the source")
        return {
            "backup_bytes": len(dump),
            "source_sha256": source_sha256,
            "restored_sha256": restored_sha256,
        }
    finally:
        if created:
            _drop_owned_restore_database(settings, restore_database)


def _postgres_exec() -> tuple[str, ...]:
    container = os.getenv("RECOVERY_POSTGRES_CONTAINER")
    if container is not None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container) is None:
            raise RecoveryExerciseFailure("Recovery PostgreSQL container must be an exact identity")
        return ("docker", "exec", "-i", container)
    return (
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        _COMPOSE_FILE,
        "exec",
        "-T",
        "postgres",
    )


def _drop_owned_restore_database(settings: Settings, database: str) -> None:
    if re.fullmatch(r"iip_restore_[a-f0-9]{16}", database) is None:
        raise RecoveryExerciseFailure("Refusing to remove an unscoped restore database")
    with _connect(settings, "postgres") as connection:
        connection.autocommit = True
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
        )


def _previous_image() -> dict[str, object]:
    digest = os.getenv("PREVIOUS_IMAGE_DIGEST", "")
    if _IMAGE_DIGEST_PATTERN.fullmatch(digest) is None:
        raise RecoveryExerciseFailure("PREVIOUS_IMAGE_DIGEST is missing or is not immutable")
    inspected = _run(("docker", "image", "inspect", digest), timeout=120)
    document = json.loads(inspected.stdout.decode("utf-8"))
    if not isinstance(document, list) or len(document) != 1:
        raise RecoveryExerciseFailure("Previous image inspection returned an invalid identity")
    smoke = _run(
        (
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            digest,
            "-c",
            "import industry_platform; print('previous-image-runtime-ok')",
        ),
        timeout=300,
    )
    if smoke.stdout.strip() != b"previous-image-runtime-ok":
        raise RecoveryExerciseFailure("Previous image runtime smoke did not pass")
    return {"image_digest": digest, "runtime_import": True}


def execute(binding: ExerciseBinding) -> dict[str, object]:
    settings = _settings()
    details: dict[str, object]
    if binding.scenario_id == "postgres-backup-restore":
        details = _backup_restore(settings)
    elif binding.scenario_id == "previous-image-rollback":
        details = _previous_image()
    else:
        _pytest(binding.scenario_id)
        details = {"verification_passed": True}
    result = {
        "schema_version": 1,
        "scenario_id": binding.scenario_id,
        "environment": binding.environment,
        "run_id": None if binding.run_id is None else str(binding.run_id),
        "workspace_id": None if binding.workspace_id is None else str(binding.workspace_id),
        "ok": True,
        "verification_level": (
            "full_scenario"
            if binding.scenario_id in {"fresh-migration", "postgres-backup-restore"}
            else "capability_checks"
        ),
        "details": details,
    }
    _write_json(binding.state_directory / _RESULT_FILE, result)
    return result


def probe(binding: ExerciseBinding, *, phase: Literal["start", "final"]) -> RecoveryStateProbe:
    settings = _settings()
    current = _business_state(settings, binding)
    duplicate_side_effect_count, unauthorized_write_count = _integrity_counts(settings, binding)
    start_path = binding.state_directory / _STATE_FILE
    if phase == "start":
        if start_path.exists():
            raise RecoveryExerciseFailure("Recovery start state already exists")
        _write_json(start_path, current)
        return RecoveryStateProbe(
            business_state=current,
            checks={},
            duplicate_side_effect_count=duplicate_side_effect_count,
            data_loss_count=0,
            unauthorized_write_count=unauthorized_write_count,
        )

    start = _read_json(start_path)
    result = _read_json(binding.state_directory / _RESULT_FILE)
    data_loss_count = sum(
        max(0, value - current_value)
        for key, value in start.items()
        if key.endswith("_count")
        and isinstance(value, int)
        and not isinstance(value, bool)
        and isinstance((current_value := current.get(key)), int)
        and not isinstance(current_value, bool)
    )
    if start.get("database_sha256") != current.get("database_sha256"):
        data_loss_count += 1
    binding_visible = not bool(current.get("runtime_binding_required")) or (
        current.get("run_exists") is True
        and current.get("workspace_id") == str(binding.workspace_id)
    )
    checks = {
        "exercise_completed": result.get("ok") is True,
        # A production Run merely visible before/after unrelated disposable tests
        # has NOT been subjected to the fault. Nor is an image import a rollback.
        "scenario_verified": result.get("verification_level") == "full_scenario",
        "durable_state_preserved": data_loss_count == 0,
        "runtime_binding_visible": binding_visible,
    }
    return RecoveryStateProbe(
        business_state=current,
        checks=checks,
        duplicate_side_effect_count=duplicate_side_effect_count,
        data_loss_count=data_loss_count,
        unauthorized_write_count=unauthorized_write_count,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=_SCENARIO_IDS, required=True)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--workspace-id", type=UUID)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--phase", choices=("start", "final"), required=True)
    subparsers.add_parser("execute")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path.cwd().resolve()
    state_directory = (
        args.state_directory.resolve()
        if args.state_directory.is_absolute()
        else (root / args.state_directory).resolve()
    )
    allowed_root = (root / ".data" / "evals").resolve()
    if not state_directory.is_relative_to(allowed_root):
        raise SystemExit("Recovery state directory must be inside .data/evals")
    binding = ExerciseBinding(
        scenario_id=args.scenario,
        environment=args.environment,
        state_directory=state_directory,
        run_id=args.run_id,
        workspace_id=args.workspace_id,
    )
    try:
        value = execute(binding) if args.command == "execute" else probe(binding, phase=args.phase)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.stderr.write(
            json.dumps(
                {"ok": False, "error": type(error).__name__, "scenario": binding.scenario_id},
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n"
        )
        return 1
    document = value.model_dump(mode="json") if isinstance(value, RecoveryStateProbe) else value
    sys.stdout.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
