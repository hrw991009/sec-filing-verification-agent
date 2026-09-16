"""The local check summary must never manufacture a release pass."""

import subprocess
from pathlib import Path

import pytest

from industry_platform.modules.evaluation import recovery_capabilities as checks


@pytest.mark.parametrize(
    ("xml", "exit_code", "passed"),
    [
        ("<testsuites><testsuite><testcase name='ok'/></testsuite></testsuites>", 0, True),
        (
            "<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>",
            0,
            False,
        ),
        (
            "<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>",
            0,
            False,
        ),
        ("<testsuites><testsuite><testcase/></testsuite></testsuites>", 1, False),
        ("<testsuites/>", 0, False),
        ("invalid", 0, False),
        ("<!DOCTYPE testsuites><testsuites/>", 0, False),
    ],
)
def test_junit_result_requires_real_executed_success(
    tmp_path: Path,
    xml: str,
    exit_code: int,
    passed: bool,
) -> None:
    path = tmp_path / "report.xml"
    path.write_text(xml, encoding="utf-8")
    assert checks.junit_outcome(path, exit_code)["passed"] is passed
    assert checks.junit_outcome(tmp_path / "missing.xml", 0)["passed"] is False


def test_check_run_records_dirty_identity_but_keeps_release_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checks, "RECOVERY_CAPABILITY_CHECKS", {"controlled": ("fixed-test",)})
    monkeypatch.setattr(
        checks,
        "source_identity",
        lambda root: {
            "commit": "a" * 40,
            "dirty": True,
            "source_sha256": "b" * 64,
        },
    )

    def run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        path = Path(argv[-1].split("=", 1)[1])
        path.write_text(
            "<testsuites><testsuite><testcase/></testsuite></testsuites>", encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr(subprocess, "run", run)
    output = tmp_path / ".data" / "evals" / "checks"
    result = checks.run_checks(tmp_path, output)
    assert result["checks_passed"] is True
    assert result["release_accepted"] is False
    assert result["deferred"] == ["previous-image-rollback"]
    with pytest.raises(FileExistsError):
        checks.run_checks(tmp_path, output)
    with pytest.raises(ValueError, match="inside"):
        checks.run_checks(tmp_path, tmp_path / "outside")
