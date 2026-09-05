from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from industry_platform.modules.evaluation import release_acceptance as acceptance_module
from industry_platform.modules.evaluation.release_acceptance import (
    AcceptancePhase,
    AcceptancePhaseStatus,
    AcceptanceReport,
    AcceptanceRunner,
    write_acceptance_report,
)
from industry_platform.modules.evaluation.release_execution import ReleaseExecutionBatch
from industry_platform.modules.evaluation.release_observation_collector import (
    ReleaseObservationCollection,
)


class _PassingRunner(AcceptanceRunner):
    def _source_commit(self) -> str:
        return "a" * 40

    def _preflight(
        self,
        *,
        require_previous_image: bool = True,
        allow_dirty_source: bool = False,
    ) -> AcceptancePhase:
        del require_previous_image, allow_dirty_source
        return _passed("preflight")

    def _browser(self) -> AcceptancePhase:
        return _passed("browser-data-preparation")

    def _release_execution(
        self, *, live_repetitions: bool
    ) -> tuple[AcceptancePhase, ReleaseExecutionBatch | None]:
        del live_repetitions
        return _passed("release-run-execution"), cast(ReleaseExecutionBatch, object())

    def _release_evidence(
        self, batch: ReleaseExecutionBatch
    ) -> tuple[AcceptancePhase, ReleaseObservationCollection | None]:
        del batch
        return _passed("release-run-evidence"), cast(ReleaseObservationCollection, object())

    def _recovery(
        self, collection: ReleaseObservationCollection, *, source_commit: str
    ) -> AcceptancePhase:
        del collection, source_commit
        return _passed("recovery-exercises")

    def _live_sec(self) -> AcceptancePhase:
        return _passed("live-sec-identity")

    def _final_readiness(
        self, phases: list[AcceptancePhase], *, source_commit: str
    ) -> AcceptancePhase:
        del phases, source_commit
        return _passed("final-readiness")


class _FixedCommitRunner(AcceptanceRunner):
    def _source_commit(self) -> str:
        return "b" * 40


def _passed(phase_id: str) -> AcceptancePhase:
    return AcceptancePhase(
        phase_id=phase_id,
        status=AcceptancePhaseStatus.PASSED,
        detail="passed",
    )


def test_full_acceptance_requires_every_engineering_phase_to_pass(tmp_path: Path) -> None:
    runner = _PassingRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")

    report = runner.run(
        run_browser=True,
        run_live_sec=True,
        live_repetitions=False,
        preflight_only=False,
    )

    assert report.engineering_evidence_complete is True
    assert tuple(item.status for item in report.phases) == (AcceptancePhaseStatus.PASSED,) * 7
    assert len(report.manual_gates) == 5


def test_core_validation_skips_recovery_without_promoting_release_readiness(
    tmp_path: Path,
) -> None:
    runner = _PassingRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")

    report = runner.run(
        run_browser=True,
        run_live_sec=True,
        live_repetitions=False,
        preflight_only=False,
        run_recovery=False,
    )

    assert report.engineering_evidence_complete is False
    assert report.phases[0].status is AcceptancePhaseStatus.PASSED
    assert report.phases[1].status is AcceptancePhaseStatus.PASSED
    assert report.phases[2].status is AcceptancePhaseStatus.PASSED
    assert report.phases[3].status is AcceptancePhaseStatus.PASSED
    assert report.phases[4].status is AcceptancePhaseStatus.SKIPPED
    assert "no rollback claim" in report.phases[4].detail
    assert report.phases[5].status is AcceptancePhaseStatus.PASSED
    assert report.phases[6].status is AcceptancePhaseStatus.SKIPPED
    assert "not evaluated" in report.phases[6].detail


def test_core_preflight_allows_dirty_source_without_weakening_core_execution(
    tmp_path: Path,
) -> None:
    calls: list[bool] = []

    class _RecordingRunner(_PassingRunner):
        def _preflight(
            self,
            *,
            require_previous_image: bool = True,
            allow_dirty_source: bool = False,
        ) -> AcceptancePhase:
            del require_previous_image
            calls.append(allow_dirty_source)
            return _passed("preflight")

    runner = _RecordingRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")

    runner.run(
        run_browser=True,
        run_live_sec=True,
        live_repetitions=False,
        preflight_only=True,
        run_recovery=False,
    )
    runner.run(
        run_browser=True,
        run_live_sec=True,
        live_repetitions=False,
        preflight_only=False,
        run_recovery=False,
    )

    assert calls == [True, False]


def test_core_cli_succeeds_without_claiming_complete_release_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(acceptance_module, "AcceptanceRunner", _PassingRunner)

    exit_code = acceptance_module.main(
        [
            "--live-sec",
            "--skip-recovery",
            "--output-directory",
            ".data/evals/sec-core-validation-v1",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["scope"] == "core"
    assert output["engineering_evidence_complete"] is False
    report = json.loads((tmp_path / output["output"]).read_text(encoding="utf-8"))
    assert report["engineering_evidence_complete"] is False
    assert report["phases"][4]["status"] == "skipped"
    assert report["phases"][6]["status"] == "skipped"


def test_missing_inputs_block_execution_without_promoting_skipped_phases(
    tmp_path: Path,
) -> None:
    runner = _FixedCommitRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")

    report = runner.run(
        run_browser=True,
        run_live_sec=False,
        live_repetitions=False,
        preflight_only=False,
    )

    assert report.engineering_evidence_complete is False
    assert report.phases[0].status is AcceptancePhaseStatus.BLOCKED
    assert report.phases[1].status is AcceptancePhaseStatus.BLOCKED
    assert report.phases[-2].status is AcceptancePhaseStatus.SKIPPED
    assert report.phases[-1].status is AcceptancePhaseStatus.BLOCKED


def test_acceptance_report_is_machine_readable_and_keeps_manual_gates(
    tmp_path: Path,
) -> None:
    runner = _PassingRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")
    report = runner.run(
        run_browser=True,
        run_live_sec=True,
        live_repetitions=False,
        preflight_only=False,
    )
    output = tmp_path / ".data/evals/acceptance-report.json"

    write_acceptance_report(report, output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["report_id"] == "sec-release-acceptance-v1"
    assert document["engineering_evidence_complete"] is True
    assert len(document["manual_gates"]) == 5


def test_report_contract_rejects_a_false_complete_claim() -> None:
    phases = tuple(
        _passed(item)
        for item in (
            "preflight",
            "browser-data-preparation",
            "release-run-execution",
            "release-run-evidence",
            "recovery-exercises",
            "live-sec-identity",
            "final-readiness",
        )
    )

    with pytest.raises(ValueError, match="engineering status is inconsistent"):
        AcceptanceReport(
            source_commit="a" * 40,
            generated_at=datetime(2026, 9, 2, tzinfo=UTC),
            phases=phases,
            engineering_evidence_complete=False,
            manual_gates=("owner review",),
        )


def test_report_contract_requires_the_complete_phase_set() -> None:
    with pytest.raises(ValueError, match="phase set or order"):
        AcceptanceReport(
            source_commit="a" * 40,
            generated_at=datetime(2026, 9, 2, tzinfo=UTC),
            phases=(_passed("preflight"),),
            engineering_evidence_complete=True,
            manual_gates=("owner review",),
        )


def test_browser_phase_starts_all_five_dependencies_before_the_real_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_manifest = tmp_path / "test-results/sec-real-runtime/runtime-manifest.json"
    runtime_manifest.parent.mkdir(parents=True)
    runtime_manifest.write_text(
        json.dumps(
            {
                "api_interception": False,
                "dependencies": [
                    "postgresql",
                    "redis",
                    "minio",
                    "milvus",
                    "elasticsearch",
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **kwargs: object) -> object:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(shutil, "which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr(subprocess, "run", run)
    runner = AcceptanceRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")

    phase = runner._browser()

    assert phase.status is AcceptancePhaseStatus.PASSED
    assert calls[0][1:3] == ("compose", "--env-file")
    assert {"postgres", "redis", "minio", "milvus", "elasticsearch"} <= set(calls[0])
    assert calls[1][1:] == ("apps/backend/tests/sec_browser_e2e_runner.py",)


def test_preflight_reads_each_named_acceptance_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "env": tmp_path / ".env",
        "evidence": tmp_path / "evals/manifests/sec-release-evidence-v1.json",
        "cases": tmp_path / "evals/scenarios/sec-release-cases-v1.json",
        "recovery": tmp_path / "evals/manifests/sec-release-recovery-v1.json",
        "browser": tmp_path / "evals/fixtures/sec/sec-browser-v1/manifest.json",
        "runner": tmp_path / "apps/backend/tests/sec_browser_e2e_runner.py",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cases" if path == paths["cases"] else "{}", encoding="utf-8")
    seen: dict[str, Path] = {}

    def load_manifest(path: Path) -> SimpleNamespace:
        seen["evidence"] = path
        return SimpleNamespace(
            source_manifest_sha256=hashlib.sha256(paths["cases"].read_bytes()).hexdigest(),
            common_case_ids=("case",),
        )

    def load_cases(path: Path) -> SimpleNamespace:
        seen["cases"] = path
        return SimpleNamespace(cases=(SimpleNamespace(case_id="case"),))

    def load_recovery(path: Path) -> SimpleNamespace:
        seen["recovery"] = path
        return SimpleNamespace()

    monkeypatch.setattr(acceptance_module, "load_release_evidence_manifest", load_manifest)
    monkeypatch.setattr(acceptance_module, "load_sec_tool_dataset", load_cases)
    monkeypatch.setattr(acceptance_module, "load_recovery_manifest", load_recovery)
    monkeypatch.setattr(
        acceptance_module,
        "Settings",
        lambda **kwargs: SimpleNamespace(
            agent_model_route="agent",
            agent_model_provider_base_url="https://provider.example/v1",
            agent_model_provider_api_key="configured",
            sec_source_configured=True,
            env_file=kwargs.get("_env_file"),
        ),
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"{name}.exe")
    monkeypatch.setenv(
        "PREVIOUS_IMAGE_DIGEST",
        f"registry.example/industry-platform@sha256:{'a' * 64}",
    )
    runner = AcceptanceRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")
    monkeypatch.setattr(runner, "_working_tree_clean", lambda: True)

    phase = runner._preflight()

    assert phase.status is AcceptancePhaseStatus.PASSED
    assert seen == {
        "evidence": paths["evidence"],
        "cases": paths["cases"],
        "recovery": paths["recovery"],
    }


def test_core_preflight_does_not_require_recovery_manifest_or_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "env": tmp_path / ".env",
        "evidence": tmp_path / "evals/manifests/sec-release-evidence-v1.json",
        "cases": tmp_path / "evals/scenarios/sec-release-cases-v1.json",
        "browser": tmp_path / "evals/fixtures/sec/sec-browser-v1/manifest.json",
        "runner": tmp_path / "apps/backend/tests/sec_browser_e2e_runner.py",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cases" if path == paths["cases"] else "{}", encoding="utf-8")

    monkeypatch.setattr(
        acceptance_module,
        "load_release_evidence_manifest",
        lambda path: SimpleNamespace(
            source_manifest_sha256=hashlib.sha256(paths["cases"].read_bytes()).hexdigest(),
            common_case_ids=("case",),
        ),
    )
    monkeypatch.setattr(
        acceptance_module,
        "load_sec_tool_dataset",
        lambda path: SimpleNamespace(cases=(SimpleNamespace(case_id="case"),)),
    )
    monkeypatch.setattr(
        acceptance_module,
        "load_recovery_manifest",
        lambda path: pytest.fail(f"Recovery manifest unexpectedly loaded: {path}"),
    )
    monkeypatch.setattr(
        acceptance_module,
        "Settings",
        lambda **kwargs: SimpleNamespace(
            agent_model_route="agent",
            agent_model_provider_base_url="https://provider.example/v1",
            agent_model_provider_api_key="configured",
            sec_source_configured=True,
            env_file=kwargs.get("_env_file"),
        ),
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"{name}.exe")
    monkeypatch.delenv("PREVIOUS_IMAGE_DIGEST", raising=False)
    runner = AcceptanceRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")
    monkeypatch.setattr(runner, "_working_tree_clean", lambda: True)

    phase = runner._preflight(require_previous_image=False)

    assert phase.status is AcceptancePhaseStatus.PASSED
    assert "core validation" in phase.detail


def test_core_preflight_can_check_external_prerequisites_from_dirty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "env": tmp_path / ".env",
        "evidence": tmp_path / "evals/manifests/sec-release-evidence-v1.json",
        "cases": tmp_path / "evals/scenarios/sec-release-cases-v1.json",
        "browser": tmp_path / "evals/fixtures/sec/sec-browser-v1/manifest.json",
        "runner": tmp_path / "apps/backend/tests/sec_browser_e2e_runner.py",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cases" if path == paths["cases"] else "{}", encoding="utf-8")

    monkeypatch.setattr(
        acceptance_module,
        "load_release_evidence_manifest",
        lambda path: SimpleNamespace(
            source_manifest_sha256=hashlib.sha256(paths["cases"].read_bytes()).hexdigest(),
            common_case_ids=("case",),
        ),
    )
    monkeypatch.setattr(
        acceptance_module,
        "load_sec_tool_dataset",
        lambda path: SimpleNamespace(cases=(SimpleNamespace(case_id="case"),)),
    )
    monkeypatch.setattr(
        acceptance_module,
        "Settings",
        lambda **kwargs: SimpleNamespace(
            agent_model_route="agent",
            agent_model_provider_base_url="https://provider.example/v1",
            agent_model_provider_api_key="configured",
            sec_source_configured=True,
            env_file=kwargs.get("_env_file"),
        ),
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"{name}.exe")
    runner = AcceptanceRunner(root=tmp_path, output_directory=tmp_path / ".data/evals")
    monkeypatch.setattr(runner, "_working_tree_clean", lambda: False)

    execution_phase = runner._preflight(require_previous_image=False)
    preflight_phase = runner._preflight(
        require_previous_image=False,
        allow_dirty_source=True,
    )

    assert execution_phase.status is AcceptancePhaseStatus.BLOCKED
    assert "requires a clean source tree" in execution_phase.detail
    assert preflight_phase.status is AcceptancePhaseStatus.PASSED
    assert "working tree is dirty; commit it before execution" in preflight_phase.detail
