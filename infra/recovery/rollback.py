"""Restore a scoped snapshot, exercise a previous immutable image, then return to baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from exercise import Exercise, command, write_json

ACTORS = ("api", "worker", "dispatcher", "reconciler")


def validate_images(config: dict[str, Any], current: str, previous: str) -> None:
    for image in (current, previous):
        if re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None:
            raise ValueError("Rollback requires exact local immutable image IDs")
    if current == previous:
        raise ValueError("Rollback images must be distinct")
    for actor in ACTORS:
        if config["services"][actor]["image"] != current:
            raise ValueError("Application actors do not share the recorded current image")


def image_actors(exercise: Exercise, expected: str) -> dict[str, str]:
    actors = {}
    for actor in ACTORS:
        inspected = json.loads(command("docker", "inspect", exercise.container(actor)))[0]
        if inspected["Image"] != expected or not inspected["State"]["Running"]:
            raise AssertionError(f"{actor} is not running the expected immutable image")
        actors[actor] = inspected["Image"]
    return actors


def smoke(exercise: Exercise) -> dict[str, Any]:
    """Verify retained financial results and citations, not just runtime imports."""
    exercise.wait_api()
    exercise.authenticate()
    workspace_path = f"/workspaces/{exercise.workspace}"
    filing = exercise.api(
        "GET",
        workspace_path + "/disclosures/filings",
        params={
            "cik": "0000320193",
            "forms": "10-K",
            "report_period_start": "2023-01-01",
            "report_period_end": "2023-12-31",
            "as_of": "2023-12-01T00:00:00Z",
        },
    )
    if not any(row["accession"] == "0000320193-23-000106" for row in filing["filings"]):
        raise AssertionError("Expected SEC filing is absent after rollback")
    runs = exercise.rows(
        "SELECT id FROM research_runs WHERE workspace_id=%s AND status='completed' ORDER BY id",
        (exercise.workspace,),
    )
    if not runs:
        raise AssertionError("Rollback snapshot contains no completed Research result")
    results = [
        exercise.api("GET", f"{workspace_path}/research-runs/{r['id']}/result-view") for r in runs
    ]
    evidence = exercise.rows(
        "SELECT id FROM evidence WHERE workspace_id=%s AND status='active' ORDER BY id",
        (exercise.workspace,),
    )
    if not evidence:
        raise AssertionError("Rollback snapshot contains no active Evidence")
    citations = []
    for row in evidence:
        exercise.api("GET", f"{workspace_path}/evidence/{row['id']}")
        resolution = exercise.api("GET", f"{workspace_path}/evidence/{row['id']}/resolve")
        if resolution.get("resolvable") is not True:
            raise AssertionError("Retained Evidence cannot be resolved")
        citations.append(str(row["id"]))
    monitors = exercise.api("GET", workspace_path + "/disclosures/monitors")
    cases = exercise.api("GET", workspace_path + "/disclosures/cases")
    return {
        "authenticated": True,
        "filings_sha256": hashlib.sha256(json.dumps(filing, sort_keys=True).encode()).hexdigest(),
        "research_result_sha256": hashlib.sha256(
            json.dumps(results, sort_keys=True).encode()
        ).hexdigest(),
        "resolved_evidence_ids": citations,
        "monitor_count": len(monitors["monitors"]),
        "case_count": len(cases["cases"]),
    }


def rollback(exercise: Exercise, previous: str) -> dict[str, Any]:
    from industry_platform.core.config import Settings
    from industry_platform.modules.evaluation.release_recovery_exercise import _database_digest

    compose_bytes = exercise.compose_file.read_bytes()
    config = json.loads(compose_bytes)
    current = exercise.report["application_image_id"]
    validate_images(config, current, previous)
    image_actors(exercise, current)
    schema = exercise.rows("SELECT version_num FROM alembic_version")[0]["version_num"]
    for image in (current, previous):
        heads = (
            command(
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--entrypoint",
                "python",
                image,
                "-m",
                "alembic",
                "-c",
                "apps/backend/alembic.ini",
                "heads",
            )
            .decode()
            .strip()
        )
        if heads != f"{schema} (head)":
            raise ValueError("Rollback requires both image schema heads to match the snapshot")

    env_file = exercise.directory / "runtime.env"
    original_env = env_file.read_bytes()
    source_database = exercise.env["POSTGRES_DB"]
    restored_database = f"iip_restore_{uuid4().hex[:16]}"
    postgres = exercise.container("postgres")
    postgres_exec = ("docker", "exec", "-i", postgres)
    settings = Settings(_env_file=env_file, postgres_host="127.0.0.1", postgres_port=25432)
    reference_smoke = smoke(exercise)
    started = time.monotonic()
    source_hash = ""
    restored_created = False
    try:
        for actor in ACTORS:
            exercise.fault(actor, "stop")
        source_hash = _database_digest(settings, source_database)
        dump = command(
            *postgres_exec,
            "pg_dump",
            "--username",
            "recovery",
            "--dbname",
            source_database,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        )
        # Fresh random database only: no --clean and no source-database overwrite.
        command(*postgres_exec, "createdb", "--username", "recovery", restored_database)
        restored_created = True
        command(
            *postgres_exec,
            "pg_restore",
            "--username",
            "recovery",
            "--dbname",
            restored_database,
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            input_bytes=dump,
        )
        restored_hash = _database_digest(settings, restored_database)
        if restored_hash != source_hash:
            raise AssertionError("Restored snapshot differs from the complete source database")
        for actor in ACTORS:
            config["services"][actor]["image"] = previous
        exercise.env["POSTGRES_DB"] = restored_database
        env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in exercise.env.items()), encoding="utf-8"
        )
        write_json(exercise.compose_file, config)
        exercise.compose("up", "-d", "--force-recreate", *ACTORS)
        exercise.wait_api()
        previous_actors = image_actors(exercise, previous)
        previous_smoke = smoke(exercise)
        if reference_smoke != previous_smoke:
            raise AssertionError("Rollback changed retained result/citation/read behavior")
        # Exercise the real previous Worker and live provider, not only stored reads.
        recovered = exercise.research_resume()
        result = {
            "current_image_id": current,
            "previous_image_id": previous,
            "schema_revision": schema,
            "cross_schema_rollback": False,
            "source_database": source_database,
            "restored_database": restored_database,
            "backup_bytes": len(dump),
            "backup_sha256": hashlib.sha256(dump).hexdigest(),
            "source_sha256": source_hash,
            "restored_before_smoke_sha256": restored_hash,
            "previous_actors": previous_actors,
            "retained_result_smoke": previous_smoke,
            "previous_image_research_recovery": recovered,
            "rollback_duration_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        try:
            for actor in ACTORS:
                exercise.fault(actor, "stop")
            if restored_created:
                write_json(
                    exercise.directory / "rollback-snapshot.json",
                    {
                        "database": restored_database,
                        "final_sha256": _database_digest(settings, restored_database),
                        "source_unchanged": _database_digest(settings, source_database)
                        == source_hash,
                    },
                )
        finally:
            # Always recover the original image/config; no credential-bearing backups exported.
            exercise.compose_file.write_bytes(compose_bytes)
            env_file.write_bytes(original_env)
            exercise.env["POSTGRES_DB"] = source_database
            exercise.compose("up", "-d", "--force-recreate", *ACTORS)
            exercise.wait_api()
    result["restored_current_actors"] = image_actors(exercise, current)
    result["return_to_current_smoke"] = smoke(exercise)
    snapshot = json.loads((exercise.directory / "rollback-snapshot.json").read_text())
    if not snapshot["source_unchanged"] or result["return_to_current_smoke"] != reference_smoke:
        raise AssertionError("Rollback modified the original database or baseline reads")
    result["snapshot_final_sha256"] = snapshot["final_sha256"]
    result["source_unchanged"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--previous-image", required=True)
    args = parser.parse_args()
    exercise = Exercise(args.directory)
    exercise.load()
    exercise.report = json.loads((exercise.directory / "report.json").read_text())
    try:
        exercise.scenario(
            "previous-immutable-image-rollback", lambda: rollback(exercise, args.previous_image)
        )
    finally:
        exercise.client.close()


if __name__ == "__main__":
    main()
