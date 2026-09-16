"""Run eleven isolated recovery checks without claiming twelve-scenario release approval."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from industry_platform.modules.evaluation.release_recovery_executor import _redact
from industry_platform.modules.evaluation.release_recovery_exercise import (
    RECOVERY_CAPABILITY_CHECKS,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed Git inspection arguments
        ("git", *args),  # noqa: S607 - installed Git executable
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    ).stdout


def source_identity(root: Path) -> dict[str, object]:
    paths = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    digests = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in sorted(set(paths))
        if name
        and (root / name).is_file()
        and (
            name.startswith(("apps/backend/", ".github/workflows/"))
            or name in {"pyproject.toml", "uv.lock"}
        )
    }
    return {
        "commit": _git(root, "rev-parse", "HEAD").strip(),
        "dirty": bool(_git(root, "status", "--porcelain")),
        "source_sha256": hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest(),
        "source_file_count": len(digests),
    }


def junit_outcome(path: Path, exit_code: int) -> dict[str, object]:
    result: dict[str, object] = {"passed": False, "tests": 0, "failures": 0, "skipped": 0}
    if not path.is_file():
        return result
    raw = path.read_bytes()
    if len(raw) > 10_000_000 or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return result
    try:
        document = ET.fromstring(raw)  # noqa: S314 - bounded local pytest output, no DTD/entities
    except ET.ParseError:
        return result
    cases = document.findall(".//testcase")
    failures = sum(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    return {
        "passed": exit_code == 0 and bool(cases) and failures == skipped == 0,
        "tests": len(cases),
        "failures": failures,
        "skipped": skipped,
        "junit_sha256": hashlib.sha256(raw).hexdigest(),
    }


def run_checks(root: Path, output: Path) -> dict[str, object]:
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / ".data" / "evals"):
        raise ValueError("Recovery capability evidence must stay inside .data/evals")
    output.mkdir(parents=True, exist_ok=False)
    identity = source_identity(root)
    environment = dict(os.environ)
    for name in ("POSTGRES", "REDIS", "MINIO", "VECTOR", "ELASTICSEARCH"):
        environment[f"{name}_TESTS_REQUIRED"] = "1"
    environment.setdefault("MILVUS_ENDPOINT", "http://127.0.0.1:19530")
    environment.setdefault("ELASTICSEARCH_ENDPOINT", "http://127.0.0.1:19200")
    started = datetime.now(UTC).isoformat()
    results = []
    for scenario, references in RECOVERY_CAPABILITY_CHECKS.items():
        xml_path = output / f"{scenario}.xml"
        argv = (
            "uv",
            "run",
            "--locked",
            "--all-packages",
            "pytest",
            "-q",
            "--tb=short",
            "--show-capture=no",
            *references,
            f"--junitxml={xml_path}",
        )
        begin = monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed check registry; no shell
                argv,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=600,
            )
            exit_code = completed.returncode
            log = _redact(completed.stdout + completed.stderr)
        except (OSError, subprocess.TimeoutExpired) as error:
            exit_code, log = 124, type(error).__name__
        log_path = output / f"{scenario}.log"
        log_path.write_text(log, encoding="utf-8")
        outcome = junit_outcome(xml_path, exit_code)
        results.append(
            {
                "scenario_id": scenario,
                "verification_refs": references,
                "exit_code": exit_code,
                "duration_ms": round((monotonic() - begin) * 1000),
                "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
                **outcome,
            }
        )
        sys.stdout.write(f"{scenario}: {'PASS' if outcome['passed'] else 'FAIL'}\n")
        sys.stdout.flush()
    source_unchanged = source_identity(root)["source_sha256"] == identity["source_sha256"]
    passed = sum(result["passed"] is True for result in results)
    report: dict[str, object] = {
        "schema_version": 1,
        "report_id": "recovery-capabilities-v1",
        "evidence_level": "isolated_controlled_fault_checks",
        "source": identity,
        "source_unchanged": source_unchanged,
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "scenarios": results,
        "passed": passed,
        "total": len(results),
        "checks_passed": passed == len(results) and source_unchanged,
        "release_accepted": False,
        "deferred": ["previous-image-rollback"],
        "limitations": [
            "Connection refusal is not a shared-container outage or restart exercise.",
            "Index loss is scoped to disposable-version entries, not a shared collection drop.",
            "The notification provider and worker business result are controlled fixtures, "
            "not live model or notification integrations.",
            "Disposable Run identities do not validate unrelated production release Run bindings.",
            "A dirty tree is identified by its source digest; "
            "this is not clean-commit release evidence.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    report = run_checks(Path.cwd(), args.output_directory)
    sys.stdout.write(
        f"Recovery capabilities: {report['passed']}/{report['total']}; "
        "release acceptance remains pending.\n"
    )
    return 0 if report["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
