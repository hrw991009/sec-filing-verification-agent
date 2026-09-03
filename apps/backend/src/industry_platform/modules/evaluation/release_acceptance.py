"""Run the complete SEC release acceptance chain from preparation to readiness evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.disclosures.live_sec_smoke import run_live_sec_identity_smoke
from industry_platform.modules.disclosures.tool_eval import load_sec_tool_dataset
from industry_platform.modules.evaluation.release import load_strict_json
from industry_platform.modules.evaluation.release_evidence import (
    build_release_evidence_report,
    load_release_evidence_manifest,
    write_release_evidence,
)
from industry_platform.modules.evaluation.release_execution import (
    ReleaseExecutionBatch,
    execute_release_batch,
)
from industry_platform.modules.evaluation.release_observation_collector import (
    ReleaseObservationCollection,
    ReleaseObservationCollector,
    build_collection_from_execution_batch,
    write_collection,
    write_observations,
)
from industry_platform.modules.evaluation.release_recovery import (
    build_recovery_report,
    load_recovery_manifest,
    write_recovery_report,
)
from industry_platform.modules.evaluation.release_recovery_executor import (
    RecoveryExecutor,
    build_automatic_plan,
    load_run_workspaces,
    write_execution_plan,
    write_recovery_observations,
)

ACCEPTANCE_SCHEMA_VERSION: Literal[1] = 1
ACCEPTANCE_REPORT_ID: Literal["sec-release-acceptance-v1"] = "sec-release-acceptance-v1"
_IMAGE_DIGEST_PATTERN = re.compile(r"^[^\s:@]+(?:/[^\s:@]+)*@sha256:[a-f0-9]{64}$")
_EXPECTED_BROWSER_DEPENDENCIES = {
    "postgresql",
    "redis",
    "minio",
    "milvus",
    "elasticsearch",
}
_COMPOSE_SERVICES = (
    "postgres",
    "redis",
    "minio",
    "minio-init",
    "milvus",
    "elasticsearch",
)
_EXPECTED_PHASE_IDS = (
    "preflight",
    "browser-data-preparation",
    "release-run-execution",
    "release-run-evidence",
    "recovery-exercises",
    "live-sec-identity",
    "final-readiness",
)


class AcceptancePhaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AcceptancePhase(_FrozenModel):
    phase_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    status: AcceptancePhaseStatus
    detail: str = Field(min_length=1)
    evidence_path: str | None = None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _validate_phase(self) -> Self:
        if (self.evidence_path is None) != (self.evidence_sha256 is None):
            raise ValueError("Acceptance phase evidence path and hash must be paired")
        return self


class AcceptanceReport(_FrozenModel):
    schema_version: Literal[1] = ACCEPTANCE_SCHEMA_VERSION
    report_id: Literal["sec-release-acceptance-v1"] = ACCEPTANCE_REPORT_ID
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    generated_at: datetime
    phases: tuple[AcceptancePhase, ...]
    engineering_evidence_complete: bool
    manual_gates: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if tuple(item.phase_id for item in self.phases) != _EXPECTED_PHASE_IDS:
            raise ValueError("Acceptance phase set or order is incomplete")
        complete = all(item.status is AcceptancePhaseStatus.PASSED for item in self.phases)
        if self.engineering_evidence_complete != complete:
            raise ValueError("Acceptance engineering status is inconsistent")
        if not self.manual_gates or len(self.manual_gates) != len(set(self.manual_gates)):
            raise ValueError("Acceptance manual gates must be non-empty and unique")
        return self


class AcceptanceRunner:
    def __init__(self, *, root: Path, output_directory: Path) -> None:
        self._root = root.resolve()
        self._output_directory = output_directory.resolve()
        if not self._output_directory.is_relative_to(self._root):
            raise ValueError("Acceptance output directory must be inside the repository")
        self._output_directory.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        *,
        run_browser: bool,
        run_live_sec: bool,
        live_repetitions: bool,
        preflight_only: bool,
    ) -> AcceptanceReport:
        source_commit = self._source_commit()
        phases = [self._preflight()]
        if preflight_only or phases[0].status is not AcceptancePhaseStatus.PASSED:
            phases.extend(
                self._remaining_phases(
                    preflight_only=preflight_only,
                    run_browser=run_browser,
                    run_live_sec=run_live_sec,
                )
            )
            return self._report(source_commit, phases)

        browser = self._browser() if run_browser else self._skipped("browser-data-preparation")
        phases.append(browser)
        if browser.status not in {AcceptancePhaseStatus.PASSED, AcceptancePhaseStatus.SKIPPED}:
            phases.extend(self._blocked_after("release-run-execution", run_live_sec=run_live_sec))
            return self._report(source_commit, phases)

        execution_phase, batch = self._release_execution(live_repetitions=live_repetitions)
        phases.append(execution_phase)
        if batch is None:
            phases.extend(self._blocked_after("release-run-evidence", run_live_sec=run_live_sec))
            return self._report(source_commit, phases)

        evidence_phase, collection = self._release_evidence(batch)
        phases.append(evidence_phase)
        if collection is None:
            phases.extend(self._blocked_after("recovery-exercises", run_live_sec=run_live_sec))
            return self._report(source_commit, phases)

        phases.append(self._recovery(collection, source_commit=source_commit))
        phases.append(self._live_sec() if run_live_sec else self._skipped("live-sec-identity"))
        phases.append(self._final_readiness(phases, source_commit=source_commit))
        return self._report(source_commit, phases)

    def _preflight(self) -> AcceptancePhase:
        paths = {
            "env": self._root / ".env",
            "evidence_manifest": (self._root / "evals/manifests/sec-release-evidence-v1.json"),
            "release_cases": self._root / "evals/scenarios/sec-release-cases-v1.json",
            "recovery_manifest": (self._root / "evals/manifests/sec-release-recovery-v1.json"),
            "browser_fixture_manifest": (
                self._root / "evals/fixtures/sec/sec-browser-v1/manifest.json"
            ),
            "browser_runner": self._root / "apps/backend/tests/sec_browser_e2e_runner.py",
        }
        missing = [
            path.relative_to(self._root).as_posix() for path in paths.values() if not path.is_file()
        ]
        missing_tools = [name for name in ("docker", "git", "pnpm") if shutil.which(name) is None]
        if missing or missing_tools:
            detail = [
                *(f"file:{item}" for item in missing),
                *(f"tool:{item}" for item in missing_tools),
            ]
            return AcceptancePhase(
                phase_id="preflight",
                status=AcceptancePhaseStatus.BLOCKED,
                detail=f"Missing acceptance prerequisites: {', '.join(detail)}",
            )
        try:
            settings = Settings(_env_file=paths["env"])
            manifest = load_release_evidence_manifest(paths["evidence_manifest"])
            source = load_sec_tool_dataset(paths["release_cases"])
            if (
                hashlib.sha256(paths["release_cases"].read_bytes()).hexdigest()
                != manifest.source_manifest_sha256
            ):
                raise ValueError("Release source manifest checksum changed")
            if tuple(item.case_id for item in source.cases) != manifest.common_case_ids:
                raise ValueError("Release source cases differ from the evidence manifest")
            load_recovery_manifest(paths["recovery_manifest"])
            if not self._working_tree_clean():
                raise ValueError("Acceptance execution requires a clean source tree")
            if (
                settings.agent_model_route is None
                or settings.agent_model_provider_base_url is None
                or settings.agent_model_provider_api_key is None
            ):
                raise ValueError("Agent model Provider variables are not configured")
            if not settings.sec_source_configured:
                raise ValueError("SEC identity variables are not configured")
            if _IMAGE_DIGEST_PATTERN.fullmatch(os.getenv("PREVIOUS_IMAGE_DIGEST", "")) is None:
                raise ValueError("PREVIOUS_IMAGE_DIGEST is missing or is not immutable")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return self._failure("preflight", error, blocked=True)
        return AcceptancePhase(
            phase_id="preflight",
            status=AcceptancePhaseStatus.PASSED,
            detail=(
                "Manifests, source hashes, clean commit, tools, Provider, SEC identity, and "
                "immutable rollback image are ready."
            ),
        )

    def _browser(self) -> AcceptancePhase:
        try:
            docker = shutil.which("docker")
            if docker is None:
                raise RuntimeError("docker is unavailable")
            compose = subprocess.run(  # noqa: S603 - fixed repository Compose command
                (
                    docker,
                    "compose",
                    "--env-file",
                    ".env",
                    "-f",
                    "infra/compose/compose.yaml",
                    "--profile",
                    "vector",
                    "--profile",
                    "search",
                    "up",
                    "-d",
                    "--wait",
                    *_COMPOSE_SERVICES,
                ),
                cwd=self._root,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
            if compose.returncode != 0:
                raise RuntimeError(f"dependency compose exited {compose.returncode}")
            completed = subprocess.run(
                (sys.executable, "apps/backend/tests/sec_browser_e2e_runner.py"),
                cwd=self._root,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1_800,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"browser runner exited {completed.returncode}")
            manifest_path = self._root / "test-results/sec-real-runtime/runtime-manifest.json"
            document = load_strict_json(manifest_path)
            if (
                not isinstance(document, dict)
                or document.get("api_interception") is not False
                or set(document.get("dependencies", ())) != _EXPECTED_BROWSER_DEPENDENCIES
            ):
                raise RuntimeError("Browser runtime manifest is incomplete")
            return self._evidenced_phase(
                "browser-data-preparation",
                manifest_path,
                "Migration, controlled SEC import, and Chinese browser journey passed "
                "without interception.",
            )
        except (OSError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            return self._failure("browser-data-preparation", error)

    def _release_execution(
        self, *, live_repetitions: bool
    ) -> tuple[AcceptancePhase, ReleaseExecutionBatch | None]:
        output = self._output_directory / "sec-release-execution-v1.json"
        try:
            batch = asyncio.run(
                execute_release_batch(
                    manifest_path=self._root / "evals/manifests/sec-release-evidence-v1.json",
                    source_manifest_path=self._root / "evals/scenarios/sec-release-cases-v1.json",
                    output=output,
                    settings=Settings(),
                    live_repetitions=live_repetitions,
                )
            )
            expected = 150 if live_repetitions else 50
            if len(batch.bindings) != expected:
                raise RuntimeError(f"Expected {expected} production Runs")
            return (
                self._evidenced_phase(
                    "release-run-execution",
                    output,
                    f"Executed {len(batch.bindings)} production Runs across A0-A4.",
                ),
                batch,
            )
        except (OSError, ValueError, RuntimeError, SQLAlchemyError) as error:
            return self._failure("release-run-execution", error), None

    def _release_evidence(
        self, batch: ReleaseExecutionBatch
    ) -> tuple[AcceptancePhase, ReleaseObservationCollection | None]:
        manifest_path = self._root / "evals/manifests/sec-release-evidence-v1.json"
        source_path = self._root / "evals/scenarios/sec-release-cases-v1.json"
        collection_path = self._output_directory / "sec-release-collection-v1.json"
        observations_path = self._output_directory / "sec-release-evidence-v1.json"
        report_path = self._output_directory / "sec-release-evidence-report-v1.json"

        async def collect() -> ReleaseObservationCollection:
            settings = Settings()
            engine = create_database_engine(settings)
            try:
                session_factory = create_database_session_factory(engine)
                manifest = load_release_evidence_manifest(manifest_path)
                source = load_sec_tool_dataset(source_path)
                collection = await build_collection_from_execution_batch(
                    manifest=manifest,
                    source=source,
                    batch=batch,
                    session_factory=session_factory,
                )
                write_collection(collection, collection_path)
                observations = await ReleaseObservationCollector(session_factory).collect(
                    manifest, source, collection
                )
                write_observations(observations, observations_path)
                report = build_release_evidence_report(
                    manifest,
                    source,
                    observations,
                    source_manifest_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                )
                write_release_evidence(
                    report,
                    json_output=report_path,
                    markdown_output=self._output_directory / "sec-release-evidence-report-v1.md",
                    schema_output=self._output_directory / "release-evidence-v1.schema.json",
                )
                if not (
                    report.global_a0_a4_comparable
                    and report.capability_gate_passed
                    and report.observability_gate_passed
                    and report.security_gate_passed
                ):
                    raise RuntimeError("Release Run evidence gates did not pass")
                return collection
            finally:
                await engine.dispose()

        try:
            collection = asyncio.run(collect())
            return (
                self._evidenced_phase(
                    "release-run-evidence",
                    report_path,
                    f"Automatic production evidence passed for {len(batch.bindings)} Runs.",
                ),
                collection,
            )
        except (OSError, ValueError, RuntimeError, SQLAlchemyError) as error:
            return self._failure("release-run-evidence", error), None

    def _recovery(
        self,
        collection: ReleaseObservationCollection,
        *,
        source_commit: str,
    ) -> AcceptancePhase:
        manifest_path = self._root / "evals/manifests/sec-release-recovery-v1.json"
        plan_path = self._output_directory / "sec-release-recovery-plan-v1.json"
        observations_path = self._output_directory / "sec-release-recovery-v1.json"
        report_path = self._output_directory / "sec-release-recovery-report-v1.json"
        try:
            manifest = load_recovery_manifest(manifest_path)
            run_workspaces = asyncio.run(load_run_workspaces(Settings(), collection))
            plan = build_automatic_plan(
                manifest,
                collection,
                run_workspaces=run_workspaces,
                source_commit=source_commit,
                environment="disposable:sec-release-recovery",
                state_directory=self._output_directory / "recovery-state" / uuid4().hex,
            )
            write_execution_plan(plan, plan_path)
            observations = RecoveryExecutor(
                root=self._root,
                evidence_directory=self._output_directory / "recovery-evidence",
            ).execute(manifest, plan)
            write_recovery_observations(observations, observations_path)
            report = build_recovery_report(manifest, observations, root=self._root)
            write_recovery_report(
                report,
                json_output=report_path,
                markdown_output=self._output_directory / "sec-release-recovery-report-v1.md",
                schema_output=self._output_directory / "release-recovery-v1.schema.json",
            )
            if not report.recovery_gate_passed:
                raise RuntimeError("Release recovery gates did not pass")
            return self._evidenced_phase(
                "recovery-exercises",
                report_path,
                f"Recovery gates passed for {report.observed_scenario_count} isolated exercises.",
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return self._failure("recovery-exercises", error)

    def _live_sec(self) -> AcceptancePhase:
        evidence_path = self._output_directory / "sec-live-identity-v1.json"
        try:
            asyncio.run(run_live_sec_identity_smoke(settings=Settings(), output=evidence_path))
            return self._evidenced_phase(
                "live-sec-identity",
                evidence_path,
                "Live SEC request passed with the configured contact identity.",
            )
        except (OSError, ValueError, RuntimeError) as error:
            return self._failure("live-sec-identity", error)

    def _final_readiness(
        self, phases: list[AcceptancePhase], *, source_commit: str
    ) -> AcceptancePhase:
        output = self._output_directory / "final-readiness-report.json"
        passed = all(item.status is AcceptancePhaseStatus.PASSED for item in phases)
        document = {
            "schema_version": 1,
            "source_commit": source_commit,
            "generated_at": datetime.now(UTC).isoformat(),
            "engineering_evidence_complete": passed,
            "phase_evidence": [item.model_dump(mode="json") for item in phases],
        }
        output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not passed:
            return AcceptancePhase(
                phase_id="final-readiness",
                status=AcceptancePhaseStatus.FAILED,
                detail="At least one required engineering phase did not pass.",
                evidence_path=output.relative_to(self._root).as_posix(),
                evidence_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            )
        return self._evidenced_phase(
            "final-readiness",
            output,
            "All engineering evidence is complete; only owner review and external "
            "governance remain.",
        )

    def _evidenced_phase(self, phase_id: str, path: Path, detail: str) -> AcceptancePhase:
        return AcceptancePhase(
            phase_id=phase_id,
            status=AcceptancePhaseStatus.PASSED,
            detail=detail,
            evidence_path=path.relative_to(self._root).as_posix(),
            evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    @staticmethod
    def _failure(
        phase_id: str,
        error: Exception,
        *,
        blocked: bool = False,
    ) -> AcceptancePhase:
        detail = " ".join(str(error).split())[:500] or type(error).__name__
        return AcceptancePhase(
            phase_id=phase_id,
            status=AcceptancePhaseStatus.BLOCKED if blocked else AcceptancePhaseStatus.FAILED,
            detail=f"{type(error).__name__}: {detail}",
        )

    @staticmethod
    def _skipped(phase_id: str) -> AcceptancePhase:
        return AcceptancePhase(
            phase_id=phase_id,
            status=AcceptancePhaseStatus.SKIPPED,
            detail="Phase was explicitly skipped; no verification claim was made.",
        )

    def _remaining_phases(
        self,
        *,
        preflight_only: bool,
        run_browser: bool,
        run_live_sec: bool,
    ) -> list[AcceptancePhase]:
        status = AcceptancePhaseStatus.SKIPPED if preflight_only else AcceptancePhaseStatus.BLOCKED
        detail = (
            "Preflight-only mode; phase was not executed."
            if preflight_only
            else "Blocked by preflight."
        )
        requested = {
            "browser-data-preparation": run_browser,
            "release-run-execution": True,
            "release-run-evidence": True,
            "recovery-exercises": True,
            "live-sec-identity": run_live_sec,
            "final-readiness": True,
        }
        return [
            AcceptancePhase(phase_id=phase_id, status=status, detail=detail)
            if enabled
            else self._skipped(phase_id)
            for phase_id, enabled in requested.items()
        ]

    def _blocked_after(self, phase_id: str, *, run_live_sec: bool) -> list[AcceptancePhase]:
        start = _EXPECTED_PHASE_IDS.index(phase_id)
        return [
            self._skipped(item)
            if item == "live-sec-identity" and not run_live_sec
            else AcceptancePhase(
                phase_id=item,
                status=AcceptancePhaseStatus.BLOCKED,
                detail="Blocked by a failed prerequisite phase.",
            )
            for item in _EXPECTED_PHASE_IDS[start:]
        ]

    def _source_commit(self) -> str:
        return subprocess.run(
            ("git", "rev-parse", "HEAD"),  # noqa: S607 - fixed Git inspection command
            cwd=self._root,
            capture_output=True,
            check=True,
            text=True,
            encoding="ascii",
        ).stdout.strip()

    def _working_tree_clean(self) -> bool:
        return not subprocess.run(
            ("git", "status", "--porcelain"),  # noqa: S607 - fixed Git inspection command
            cwd=self._root,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        ).stdout

    @staticmethod
    def _report(source_commit: str, phases: list[AcceptancePhase]) -> AcceptanceReport:
        return AcceptanceReport(
            source_commit=source_commit,
            generated_at=datetime.now(UTC),
            phases=tuple(phases),
            engineering_evidence_complete=all(
                item.status is AcceptancePhaseStatus.PASSED for item in phases
            ),
            manual_gates=(
                "Open the generated desktop and mobile artifacts and confirm visual correctness.",
                "Complete Chinese financial-domain owner sampling and sign-off.",
                "Review external dataset and redistribution rights before public benchmark claims.",
                "Attach branch, pull-request, and merge-to-main CI URLs for this source commit.",
                "Approve the retained evidence bundle and release decision as owner.",
            ),
        )


def write_acceptance_report(report: AcceptanceReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(".data/evals/sec-release-acceptance-v1"),
    )
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--live-sec", action="store_true")
    parser.add_argument("--live-repetitions", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = AcceptanceRunner(root=Path.cwd(), output_directory=args.output_directory)
    report = runner.run(
        run_browser=not args.skip_browser,
        run_live_sec=args.live_sec,
        live_repetitions=args.live_repetitions,
        preflight_only=args.preflight_only,
    )
    output = args.output_directory / "acceptance-report.json"
    write_acceptance_report(report, output)
    command_succeeded = report.engineering_evidence_complete or (
        args.preflight_only and report.phases[0].status is AcceptancePhaseStatus.PASSED
    )
    sys.stdout.write(
        json.dumps(
            {
                "ok": command_succeeded,
                "output": str(output),
                "phases": {item.phase_id: item.status.value for item in report.phases},
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if command_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
