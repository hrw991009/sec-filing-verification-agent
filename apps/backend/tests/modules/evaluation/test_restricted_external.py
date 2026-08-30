from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from industry_platform.modules.evaluation import restricted_external
from industry_platform.modules.evaluation.fixed_context import FixedContextArtifactStore
from industry_platform.modules.evaluation.release import (
    DatasetArtifact,
    DatasetRecord,
    load_dataset_registry,
)
from industry_platform.modules.evaluation.restricted_external import (
    FinanceBenchAdapter,
    FinSearchCompAdapter,
    FinSearchDependency,
    build_financebench_report,
    build_finsearchcomp_reports,
)

ROOT = Path(__file__).resolve().parents[5]
REGISTRY_PATH = ROOT / "evals" / "registry" / "sec-agent-datasets-v1.json"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _jsonl_bytes(values: list[object]) -> bytes:
    return b"\n".join(_json_bytes(value) for value in values) + b"\n"


def _record(dataset_id: str, payloads: dict[str, bytes]) -> DatasetRecord:
    registry = load_dataset_registry(REGISTRY_PATH)
    source = next(record for record in registry.records if record.dataset_id == dataset_id)
    artifacts = []
    for artifact in source.artifacts:
        payload = payloads[artifact.artifact_id]
        document_count, question_count = _fixture_counts(artifact.artifact_id)
        artifacts.append(
            DatasetArtifact.model_validate(
                {
                    **artifact.model_dump(),
                    "download_url": (
                        f"https://example.com/{source.upstream_revision}/{artifact.artifact_id}"
                    ),
                    "byte_size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "document_count": document_count,
                    "question_count": question_count,
                }
            )
        )
    return DatasetRecord.model_validate({**source.model_dump(), "artifacts": artifacts})


def _fixture_counts(artifact_id: str) -> tuple[int, int | None]:
    if artifact_id == "financebench-document-information":
        return 3, None
    if artifact_id == "financebench-open-source":
        return 1, 1
    if artifact_id == "finsearchcomp-full":
        return 4, 4
    return 3, 3


def _write_payloads(root: Path, record: DatasetRecord, payloads: dict[str, bytes]) -> None:
    store = FixedContextArtifactStore(root)
    for artifact in record.artifacts:
        path = store.path_for(record, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[artifact.artifact_id])


def _financebench_payloads() -> dict[str, bytes]:
    question = {
        "financebench_id": "financebench_id_00001",
        "company": "Example Corp",
        "doc_name": "EXAMPLE_2024_10K",
        "question_type": "metrics-generated",
        "question_reasoning": "Information extraction",
        "domain_question_num": None,
        "question": "What was revenue?",
        "answer": "$10.00",
        "justification": "Reported revenue.",
        "dataset_subset_label": "OPEN_SOURCE",
        "evidence": [
            {
                "evidence_text": "Revenue was $10.",
                "doc_name": "EXAMPLE_2024_10K",
                "evidence_page_num": 4,
                "evidence_text_full_page": "Financial statements. Revenue was $10.",
            }
        ],
    }
    referenced = {
        "doc_name": "EXAMPLE_2024_10K",
        "doc_type": "10K",
        "doc_period": 2024,
        "doc_link": "https://example.com/example-2024.pdf",
        "company": "Example Corp",
        "gics_sector": "Industrials",
    }
    conflicting = {
        "doc_name": "UNUSED_2023_10K",
        "doc_type": "10K",
        "doc_period": 2023,
        "doc_link": "https://example.com/unused.pdf",
        "company": "Unused Corp",
        "gics_sector": "Financials",
    }
    return {
        "financebench-open-source": _jsonl_bytes([question]),
        "financebench-document-information": _jsonl_bytes(
            [referenced, conflicting, {**conflicting, "doc_period": 2022}]
        ),
    }


def _finsearch_item(task: str, number: int, *, dynamic: bool) -> dict[str, object]:
    item: dict[str, object] = {
        "prompt_id": f"({task})Fixture_{number:03d}",
        "prompt": f"Question {task}-{number}?",
        "response_reference": f"Answer {task}-{number}",
        "judge_prompt_template": "{prompt} {response_reference} {response}",
        "judge_system_prompt": "Judge the response.",
    }
    if dynamic:
        item.update(
            {
                "ground_truth": f"ground-{number}",
                "time": "2025/8/14",
                "akshare_ticker": f"ticker-{number}",
                "wind_ticker": f"wind-{number}",
            }
        )
    return item


def _finsearch_payloads() -> dict[str, bytes]:
    shared_dynamic = _finsearch_item("T1", 1, dynamic=True)
    professional_dynamic = _finsearch_item("T1", 2, dynamic=True)
    historical = _finsearch_item("T2", 1, dynamic=False)
    investigation = _finsearch_item("T3", 1, dynamic=False)
    akshare_dynamic = {**shared_dynamic, "time": "2025-08-14 00:00:00"}
    return {
        "finsearchcomp-full": _json_bytes(
            [shared_dynamic, professional_dynamic, historical, investigation]
        ),
        "finsearchcomp-akshare": _json_bytes([akshare_dynamic, historical, investigation]),
    }


def test_financebench_adapter_sanitizes_gold_and_reports_upstream_conflict(
    tmp_path: Path,
) -> None:
    payloads = _financebench_payloads()
    record = _record("financebench", payloads)
    _write_payloads(tmp_path, record, payloads)

    case = FinanceBenchAdapter(record, FixedContextArtifactStore(tmp_path)).cases()[0]
    model_input = json.loads(case.input.model_dump_json())
    report = build_financebench_report(record, root=tmp_path)

    assert case.gold.answer == "$10.00"
    assert "answer" not in model_input
    assert "evidence" not in model_input
    assert report.question_count == 1
    assert report.evidence_count == 1
    assert report.conflicting_unreferenced_document_ids == ("UNUSED_2023_10K",)
    assert report.official_metric_scores is None


def test_financebench_rejects_ambiguous_referenced_metadata(tmp_path: Path) -> None:
    payloads = _financebench_payloads()
    metadata = [
        json.loads(line)
        for line in payloads["financebench-document-information"].decode().splitlines()
    ]
    metadata.append({**metadata[0], "doc_period": 2023})
    payloads["financebench-document-information"] = _jsonl_bytes(metadata)
    record = _record("financebench", payloads)
    _write_payloads(tmp_path, record, payloads)

    with pytest.raises(ValueError, match="referenced document metadata is ambiguous"):
        FinanceBenchAdapter(record, FixedContextArtifactStore(tmp_path)).cases()


def test_finsearchcomp_separates_historical_and_dynamic_dependency_contracts(
    tmp_path: Path,
) -> None:
    payloads = _finsearch_payloads()
    record = _record("finsearchcomp", payloads)
    _write_payloads(tmp_path, record, payloads)

    full_cases, akshare_cases = FinSearchCompAdapter(
        record, FixedContextArtifactStore(tmp_path)
    ).cases()
    historical, live = build_finsearchcomp_reports(record, root=tmp_path)

    assert len(full_cases) == 4
    assert len(akshare_cases) == 3
    assert historical.historical_case_count == 2
    assert historical.akshare_overlap_count == 2
    assert live.dynamic_case_count == 2
    assert live.akshare_compatible_count == 1
    assert live.professional_dependency_count == 1
    assert live.akshare_timestamp_drift_count == 1
    assert live.pass_k is None
    assert {case.input.dependency for case in full_cases if case.input.task.value == "T1"} == {
        FinSearchDependency.DYNAMIC_AKSHARE,
        FinSearchDependency.DYNAMIC_PROFESSIONAL,
    }
    sanitized = json.loads(full_cases[0].input.model_dump_json())
    assert "reference_answer" not in sanitized
    assert "judge_system_prompt" not in sanitized
    assert "frozen_ground_truth" not in sanitized


def test_finsearchcomp_rejects_akshare_gold_drift(tmp_path: Path) -> None:
    payloads = _finsearch_payloads()
    akshare = json.loads(payloads["finsearchcomp-akshare"])
    akshare[0]["response_reference"] = "changed gold"
    payloads["finsearchcomp-akshare"] = _json_bytes(akshare)
    record = _record("finsearchcomp", payloads)
    _write_payloads(tmp_path, record, payloads)

    with pytest.raises(ValueError, match="differs from the full release"):
        FinSearchCompAdapter(record, FixedContextArtifactStore(tmp_path)).cases()


def test_restricted_external_cli_writes_separate_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finance_payloads = _financebench_payloads()
    finsearch_payloads = _finsearch_payloads()
    finance_record = _record("financebench", finance_payloads)
    finsearch_record = _record("finsearchcomp", finsearch_payloads)
    _write_payloads(tmp_path, finance_record, finance_payloads)
    _write_payloads(tmp_path, finsearch_record, finsearch_payloads)
    registry = load_dataset_registry(REGISTRY_PATH).model_copy(
        update={"records": (finance_record, finsearch_record)}
    )

    async def materialized_locally(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(restricted_external, "load_dataset_registry", lambda path: registry)
    monkeypatch.setattr(
        restricted_external,
        "materialize_restricted_external",
        materialized_locally,
    )
    report_directory = tmp_path / "reports"
    finance_schema = tmp_path / "schemas" / "financebench.json"
    finsearch_schema = tmp_path / "schemas" / "finsearchcomp.json"

    assert (
        restricted_external.main(
            [
                "--registry",
                str(tmp_path / "registry.json"),
                "--root",
                str(tmp_path),
                "--report-directory",
                str(report_directory),
                "--financebench-schema-output",
                str(finance_schema),
                "--finsearchcomp-schema-output",
                str(finsearch_schema),
            ]
        )
        == 0
    )

    finance_report = json.loads(
        (report_directory / "financebench-adapter-v1.json").read_text(encoding="utf-8")
    )
    historical_report = json.loads(
        (report_directory / "finsearchcomp-historical-v1.json").read_text(encoding="utf-8")
    )
    live_report = json.loads(
        (report_directory / "finsearchcomp-live-v1.json").read_text(encoding="utf-8")
    )
    assert finance_report["question_count"] == 1
    assert historical_report["historical_case_count"] == 2
    assert live_report["dynamic_case_count"] == 2
    assert live_report["live_dependencies_executed"] is False
    assert finance_schema.is_file()
    assert finsearch_schema.is_file()
    assert "Historical conversion is reported separately" in (
        report_directory / "finsearchcomp-historical-v1.md"
    ).read_text(encoding="utf-8")
