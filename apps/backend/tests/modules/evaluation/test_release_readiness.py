from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt
from pydantic import ValidationError

from industry_platform.modules.evaluation.release_readiness import (
    ArtifactSnapshot,
    BlockerStatus,
    ExternalGateSpec,
    ExternalGateStatus,
    ReleaseDecision,
    RequirementStatus,
    build_release_readiness,
    load_release_readiness_manifest,
    load_release_readiness_report,
    render_release_readiness_markdown,
)
from industry_platform.modules.evaluation.release_readiness import (
    main as release_readiness_main,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST = ROOT / "evals" / "manifests" / "sec-release-readiness-v1.json"
REPORT = ROOT / "evals" / "reports" / "sec-release-readiness-v1.json"
MARKDOWN = ROOT / "evals" / "reports" / "sec-release-readiness-v1.md"
MANIFEST_SCHEMA = ROOT / "evals" / "schemas" / "release-readiness-manifest-v1.schema.json"
REPORT_SCHEMA = ROOT / "evals" / "schemas" / "release-readiness-report-v1.schema.json"
AUDITED_RELEASE_DOCUMENTS = (
    "README.md",
    "docs/product-scope.md",
    "docs/architecture.md",
    "docs/adr/0007-sec-disclosure-financial-fact-verification.md",
    "docs/sec-agent-evaluation.md",
    "docs/release-readiness.md",
    "docs/runbooks/day-10-release-recovery.md",
    "docs/security/third-party-notices.md",
    "docs/release-notes/v0.2.0-sec-disclosure-verifier.md",
    "docs/learning-log/day-10.md",
)


def test_release_readiness_recomputes_checked_ledger() -> None:
    report = build_release_readiness(root=ROOT, manifest_path=MANIFEST)

    assert report == load_release_readiness_report(REPORT)
    assert render_release_readiness_markdown(report) == MARKDOWN.read_text(encoding="utf-8")
    assert len(report.requirements) == 88
    assert report.status_counts == {
        RequirementStatus.COMPLETE: 46,
        RequirementStatus.IMPLEMENTED_PENDING_VERIFICATION: 34,
        RequirementStatus.THIN_SLICE: 8,
        RequirementStatus.CONTRACT_ONLY: 0,
        RequirementStatus.BLOCKED: 0,
        RequirementStatus.PLANNED: 0,
    }
    assert report.incomplete_requirement_count == 42
    assert report.release_blocker_count == 15
    assert report.pending_external_gate_count == 5
    assert report.release_decision is ReleaseDecision.NO_GO
    assert report.rc_ready is False

    gates = {gate.gate_id: gate for gate in report.external_gates}
    assert gates["day10-push-ci"].status is ExternalGateStatus.VERIFIED
    assert gates["day10-pr-ci"].status is ExternalGateStatus.VERIFIED
    assert gates["day10-main-ci"].status is ExternalGateStatus.VERIFIED
    assert gates["day10-main-ci"].source_commit == "778a1966a5fd42df6b47d4a4002cb47e67435ac4"

    blockers = {blocker.blocker_id: blocker for blocker in report.blockers}
    assert blockers["day4-core-coverage-debt"].status is BlockerStatus.CLOSED
    assert blockers["day4-core-coverage-debt"].closure_artifact_ids == (
        "ci-workflow",
        "day-10-log",
        "feature-matrix",
    )
    assert blockers["day6-source-closeout-incomplete"].status is BlockerStatus.CLOSED
    assert blockers["day6-source-closeout-incomplete"].closure_artifact_ids == (
        "sec-source-report",
        "day-6-log",
        "feature-matrix",
    )
    assert blockers["day6-verification-evidence-pending"].status is BlockerStatus.OPEN


def test_release_documentation_links_resolve_inside_repository() -> None:
    parser = MarkdownIt("commonmark")
    repository_root = ROOT.resolve()

    for relative_path in AUDITED_RELEASE_DOCUMENTS:
        document = (ROOT / relative_path).resolve()
        assert document.is_file(), f"Missing audited release document: {relative_path}"
        for destination in _markdown_destinations(
            parser.parse(document.read_text(encoding="utf-8"))
        ):
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (document.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(repository_root)
            except ValueError:
                pytest.fail(
                    f"Release document link escapes repository: {relative_path} -> {destination}"
                )
            assert target.exists(), (
                f"Broken release document link: {relative_path} -> {destination}"
            )


def test_release_candidate_draft_cannot_override_no_go_report() -> None:
    report = load_release_readiness_report(REPORT)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    candidate = (ROOT / "docs" / "release-notes" / "v0.2.0-sec-disclosure-verifier.md").read_text(
        encoding="utf-8"
    )

    assert report.release_decision is ReleaseDecision.NO_GO
    assert report.rc_ready is False
    assert "发布状态" in candidate
    assert "`NO_GO`" in candidate
    assert "文档状态" in candidate
    assert "草案" in candidate
    assert "不是发布候选" in candidate
    assert "发布判定仍为\n`NO_GO`" in readme
    assert "不得创建\n`v0.2.0-sec-disclosure-verifier` 标签" in readme


def test_release_readiness_binds_every_artifact_hash() -> None:
    report = build_release_readiness(root=ROOT, manifest_path=MANIFEST)

    for artifact in report.artifacts:
        path = ROOT / artifact.relative_path
        assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert artifact.byte_size == path.stat().st_size


def test_release_readiness_rejects_matrix_status_drift(tmp_path: Path) -> None:
    root, manifest_path = _copy_readiness_root(tmp_path)
    matrix_path = root / "docs" / "feature-matrix.md"
    original = matrix_path.read_text(encoding="utf-8")
    changed = original.replace(
        "| `thin_slice` | `complete` |",
        "| `complete` | `complete` |",
        1,
    )
    assert changed != original
    matrix_path.write_text(changed, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="scope or status changed"):
        build_release_readiness(root=root, manifest_path=manifest_path)


def test_release_readiness_rejects_missing_artifact(tmp_path: Path) -> None:
    root, manifest_path = _copy_readiness_root(tmp_path)
    (root / "docs" / "learning-log" / "day-5.md").unlink()

    with pytest.raises(ValueError, match="missing or outside repository root"):
        build_release_readiness(root=root, manifest_path=manifest_path)


def test_release_readiness_requires_exact_taxonomy_mapping(tmp_path: Path) -> None:
    root, manifest_path = _copy_readiness_root(tmp_path)
    taxonomy_path = root / "evals" / "reports" / "sec-release-failure-taxonomy-v1.json"
    value = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    removed = value["items"].pop()
    value["release_blocking_count"] -= int(removed["release_blocking"])
    value["category_counts"] = dict(Counter(item["category"] for item in value["items"]))
    taxonomy_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="do not match failure taxonomy"):
        build_release_readiness(root=root, manifest_path=manifest_path)


def test_release_readiness_rejects_closing_live_taxonomy_blocker(tmp_path: Path) -> None:
    root, manifest_path = _copy_readiness_root(tmp_path)
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    binding = next(item for item in value["blockers"] if item["source"] == "evaluation_taxonomy")
    binding["status"] = "closed"
    binding["closure_artifact_ids"] = ["day-9-log"]
    manifest_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="cannot be marked closed"):
        build_release_readiness(root=root, manifest_path=manifest_path)


def test_release_readiness_rejects_unsupported_evidence_claims() -> None:
    with pytest.raises(ValidationError, match="Verified external gate requires evidence"):
        ExternalGateSpec(
            gate_id="unproven-gate",
            status=ExternalGateStatus.VERIFIED,
            owner="owner",
            detail="No evidence was supplied.",
        )
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ArtifactSnapshot(
            artifact_id="artifact",
            relative_path="artifact.json",
            sha256="missing",
            byte_size=1,
        )


def test_release_readiness_cli_writes_report_and_schemas(tmp_path: Path) -> None:
    json_output = tmp_path / "reports" / "readiness.json"
    markdown_output = tmp_path / "reports" / "readiness.md"
    manifest_schema_output = tmp_path / "schemas" / "manifest.json"
    report_schema_output = tmp_path / "schemas" / "report.json"

    assert (
        release_readiness_main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                str(MANIFEST),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
                "--manifest-schema-output",
                str(manifest_schema_output),
                "--report-schema-output",
                str(report_schema_output),
            ]
        )
        == 0
    )
    assert load_release_readiness_report(json_output) == load_release_readiness_report(REPORT)
    assert markdown_output.read_text(encoding="utf-8") == MARKDOWN.read_text(encoding="utf-8")
    assert json.loads(manifest_schema_output.read_text(encoding="utf-8")) == json.loads(
        MANIFEST_SCHEMA.read_text(encoding="utf-8")
    )
    assert json.loads(report_schema_output.read_text(encoding="utf-8")) == json.loads(
        REPORT_SCHEMA.read_text(encoding="utf-8")
    )


def _copy_readiness_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    manifest = load_release_readiness_manifest(MANIFEST)
    for artifact in manifest.artifacts:
        source = ROOT / artifact.relative_path
        target = root / artifact.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest_path = root / "evals" / "manifests" / MANIFEST.name
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MANIFEST, manifest_path)
    return root, manifest_path


def _markdown_destinations(tokens: Sequence[object]) -> tuple[str, ...]:
    destinations: list[str] = []
    for token in tokens:
        children = getattr(token, "children", None) or ()
        for child in children:
            attribute = (
                "href" if child.type == "link_open" else "src" if child.type == "image" else None
            )
            if attribute is None:
                continue
            value = child.attrGet(attribute)
            if value is not None:
                destinations.append(value)
    return tuple(destinations)
