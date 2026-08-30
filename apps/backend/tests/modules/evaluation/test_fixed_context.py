from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest

from industry_platform.modules.evaluation.finqa import (
    FinQaAdapter,
    evaluate_finqa_program,
    finqa_programs_equal,
    score_finqa,
)
from industry_platform.modules.evaluation.fixed_context import (
    AdapterValidationReport,
    EvidenceKind,
    EvidenceLocator,
    FixedContextArtifactStore,
    FixedContextPrediction,
    stable_case_digest,
)
from industry_platform.modules.evaluation.materialize import (
    build_adapter_report,
    materialize_fixed_context_datasets,
)
from industry_platform.modules.evaluation.release import (
    DatasetArtifact,
    DatasetRecord,
    DatasetRegistry,
    load_dataset_registry,
    load_strict_json,
)
from industry_platform.modules.evaluation.tatqa import TatQaAdapter, score_tatqa

ROOT = Path(__file__).resolve().parents[5]
REGISTRY_PATH = ROOT / "evals" / "registry" / "sec-agent-datasets-v1.json"


class _AsyncBytes(httpx2.AsyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._payload


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _record(
    dataset_id: str,
    artifacts: tuple[tuple[str, bytes, int, int], ...],
) -> DatasetRecord:
    registry = load_dataset_registry(REGISTRY_PATH)
    source = next(record for record in registry.records if record.dataset_id == dataset_id)
    source_artifacts = {artifact.artifact_id: artifact for artifact in source.artifacts}
    updated = []
    for artifact_id, payload, document_count, question_count in artifacts:
        artifact = source_artifacts[artifact_id]
        updated.append(
            DatasetArtifact.model_validate(
                {
                    **artifact.model_dump(),
                    "download_url": (
                        f"https://example.com/{source.upstream_revision}/{artifact_id}.json"
                    ),
                    "byte_size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "document_count": document_count,
                    "question_count": question_count,
                }
            )
        )
    return DatasetRecord.model_validate(
        {
            **source.model_dump(),
            "artifacts": updated,
            "status": "adapter_ready",
            "release_eligible": False,
            "blockers": ["test_only"],
        }
    )


def _write_artifacts(
    root: Path,
    record: DatasetRecord,
    payloads: dict[str, bytes],
) -> None:
    store = FixedContextArtifactStore(root)
    for artifact in record.artifacts:
        path = store.path_for(record, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[artifact.artifact_id])


def _finqa_payload() -> bytes:
    return _json_bytes(
        [
            {
                "id": "fixture/page-1",
                "pre_text": ["Revenue was 10 in the prior year."],
                "post_text": ["Revenue increased in the current year."],
                "table": [["metric", "prior", "current"], ["revenue", "10", "15"]],
                "qa": {
                    "question": "What was the increase in revenue?",
                    "answer": "",
                    "program": "subtract(15, 10)",
                    "gold_inds": {
                        "text_0": "Revenue was 10 in the prior year.",
                        "table_1": "normalized supporting row",
                    },
                    "exe_ans": 5.0,
                },
            }
        ]
    )


def _tatqa_payloads() -> tuple[bytes, bytes]:
    context = {
        "table": {
            "uid": "table-1",
            "table": [["metric", "value"], ["adjustment", "17.7"]],
        },
        "paragraphs": [{"uid": "p1", "order": 1, "text": "The adjustment was 17.7%."}],
    }
    input_payload = _json_bytes(
        [
            {
                **context,
                "questions": [
                    {"uid": "q1", "order": 1, "question": "What was the adjustment?"},
                    {"uid": "q2", "order": 2, "question": "Question without released gold?"},
                ],
            }
        ]
    )
    gold_payload = _json_bytes(
        [
            {
                **context,
                "questions": [
                    {
                        "uid": "q1",
                        "order": 1,
                        "question": "What was the adjustment?",
                        "answer": 17.7,
                        "derivation": "(17.7 / 100)",
                        "answer_type": "arithmetic",
                        "answer_from": "table",
                        "rel_paragraphs": [],
                        "scale": "percent",
                        "mappings": [{"table": [1, 1]}],
                    }
                ],
            }
        ]
    )
    return input_payload, gold_payload


def test_finqa_adapter_is_deterministic_and_keeps_gold_out_of_model_input(
    tmp_path: Path,
) -> None:
    payload = _finqa_payload()
    record = _record("finqa", (("finqa-train", payload, 1, 1),))
    _write_artifacts(tmp_path, record, {"finqa-train": payload})
    adapter = FinQaAdapter(record, FixedContextArtifactStore(tmp_path))

    first = tuple(adapter.iter_split("train"))
    second = tuple(adapter.iter_split("train"))

    assert first == second
    assert stable_case_digest(first) == stable_case_digest(second)
    assert first[0].gold.evidence == (
        EvidenceLocator(kind=EvidenceKind.TEXT, index=0),
        EvidenceLocator(kind=EvidenceKind.TABLE_ROW, row=1),
    )
    assert first[0].gold.answers == ("5.0",)
    model_input = json.loads(first[0].input.model_dump_json())
    assert "gold" not in model_input
    assert "answers" not in model_input
    assert "program" not in model_input


def test_finqa_official_execution_program_and_supporting_fact_metrics(tmp_path: Path) -> None:
    payload = _finqa_payload()
    table = (("metric", "prior", "current"), ("revenue", "10", "15"))
    assert evaluate_finqa_program("subtract(15, 10)", table) == 5.0
    assert evaluate_finqa_program("divide(10, 0)", table) is None
    assert finqa_programs_equal("add(10, 15)", "add(15, 10)")
    assert not finqa_programs_equal("add(10, 15)", "add(10, 16)")

    record = _record("finqa", (("finqa-train", payload, 1, 1),))
    _write_artifacts(tmp_path, record, {"finqa-train": payload})
    case = next(FinQaAdapter(record, FixedContextArtifactStore(tmp_path)).iter_split("train"))
    correct = score_finqa(
        (case,),
        (
            FixedContextPrediction(
                case_id=case.input.case_id,
                program="subtract(15, 10)",
                evidence=case.gold.evidence,
            ),
        ),
    )
    assert correct.execution_accuracy == 1.0
    assert correct.program_accuracy == 1.0
    assert correct.supporting_fact_exact == 1.0

    wrong = score_finqa(
        (case,),
        (
            FixedContextPrediction(
                case_id=case.input.case_id,
                program="divide(10, 0)",
                evidence=(EvidenceLocator(kind=EvidenceKind.TEXT, index=0),),
            ),
        ),
    )
    assert wrong.execution_accuracy == 0.0
    assert wrong.program_accuracy == 0.0
    assert wrong.invalid_program_rate == 1.0
    assert wrong.supporting_fact_exact == 0.0


def test_finqa_unresolvable_upstream_evidence_has_no_auxiliary_denominator(
    tmp_path: Path,
) -> None:
    raw = json.loads(_finqa_payload())
    raw[0]["qa"]["gold_inds"] = {"text_-1": "upstream sentinel"}
    payload = _json_bytes(raw)
    record = _record("finqa", (("finqa-train", payload, 1, 1),))
    _write_artifacts(tmp_path, record, {"finqa-train": payload})
    case = next(FinQaAdapter(record, FixedContextArtifactStore(tmp_path)).iter_split("train"))

    score = score_finqa(
        (case,),
        (
            FixedContextPrediction(
                case_id=case.input.case_id,
                program=case.gold.program,
            ),
        ),
    )

    assert case.gold.evidence_complete is False
    assert score.execution_accuracy == 1.0
    assert score.supporting_fact_case_count == 0
    assert score.supporting_fact_exact is None


def test_tatqa_test_uses_released_gold_context_without_blending_test_input(
    tmp_path: Path,
) -> None:
    input_payload, gold_payload = _tatqa_payloads()
    record = _record(
        "tat-qa",
        (
            ("tatqa-train", gold_payload, 1, 1),
            ("tatqa-dev", gold_payload, 1, 1),
            ("tatqa-test", input_payload, 1, 2),
            ("tatqa-test-gold", gold_payload, 1, 1),
        ),
    )
    _write_artifacts(
        tmp_path,
        record,
        {
            "tatqa-train": gold_payload,
            "tatqa-dev": gold_payload,
            "tatqa-test": input_payload,
            "tatqa-test-gold": gold_payload,
        },
    )

    report = build_adapter_report(record, root=tmp_path)
    test_split = report.splits[2]

    assert test_split.input_question_count == 1
    assert test_split.scorable_case_count == 1
    assert test_split.excluded_question_count == 0
    case = next(TatQaAdapter(record, FixedContextArtifactStore(tmp_path)).iter_split("test"))
    assert case.input.source_artifact_ids == ("tatqa-test-gold",)
    assert case.gold.evidence == (EvidenceLocator(kind=EvidenceKind.TABLE_CELL, row=1, column=1),)


def test_tatqa_official_percent_metric_and_auxiliary_negative_metrics(tmp_path: Path) -> None:
    input_payload, gold_payload = _tatqa_payloads()
    record = _record(
        "tat-qa",
        (
            ("tatqa-test", input_payload, 1, 2),
            ("tatqa-test-gold", gold_payload, 1, 1),
        ),
    )
    _write_artifacts(
        tmp_path,
        record,
        {"tatqa-test": input_payload, "tatqa-test-gold": gold_payload},
    )
    case = next(TatQaAdapter(record, FixedContextArtifactStore(tmp_path)).iter_split("test"))

    score = score_tatqa(
        (case,),
        (
            FixedContextPrediction(
                case_id=case.input.case_id,
                answers=("0.177",),
                scale="",
                derivation="(17.7/100)",
                evidence=case.gold.evidence,
            ),
        ),
    )
    assert score.answer_em == 1.0
    assert score.answer_f1 == 1.0
    assert score.scale_accuracy == 0.0
    assert score.derivation_exact == 1.0
    assert score.source_exact == 1.0

    wrong = score_tatqa(
        (case,),
        (
            FixedContextPrediction(
                case_id=case.input.case_id,
                answers=("0.177",),
                scale="",
                derivation="(17.7*100)",
                evidence=(EvidenceLocator(kind=EvidenceKind.TABLE_CELL, row=0, column=0),),
            ),
        ),
    )
    assert wrong.answer_em == 1.0
    assert wrong.derivation_exact == 0.0
    assert wrong.source_exact == 0.0

    span_case = case.model_copy(
        update={
            "gold": case.gold.model_copy(
                update={
                    "answers": ("the adjustment",),
                    "answer_type": "span",
                    "scale": "",
                    "derivation": "",
                }
            )
        }
    )
    span_score = score_tatqa(
        (span_case,),
        (
            FixedContextPrediction(
                case_id=span_case.input.case_id,
                answers=("adjustment",),
            ),
        ),
    )
    assert span_score.answer_em == 1.0
    assert span_score.answer_f1 == 1.0


def test_scorers_fail_closed_on_missing_prediction(tmp_path: Path) -> None:
    input_payload, gold_payload = _tatqa_payloads()
    record = _record(
        "tat-qa",
        (
            ("tatqa-test", input_payload, 1, 2),
            ("tatqa-test-gold", gold_payload, 1, 1),
        ),
    )
    _write_artifacts(
        tmp_path,
        record,
        {"tatqa-test": input_payload, "tatqa-test-gold": gold_payload},
    )
    case = next(TatQaAdapter(record, FixedContextArtifactStore(tmp_path)).iter_split("test"))

    with pytest.raises(ValueError, match="Prediction coverage mismatch"):
        score_tatqa((case,), ())


@pytest.mark.asyncio
async def test_materializer_streams_registered_bytes_and_reuses_verified_file(
    tmp_path: Path,
) -> None:
    payload = _finqa_payload()
    record = _record("finqa", (("finqa-train", payload, 1, 1),))
    registry = _single_record_registry(record)
    requests = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(200, stream=_AsyncBytes(payload))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), trust_env=False
    ) as client:
        first = await materialize_fixed_context_datasets(
            registry,
            root=tmp_path,
            dataset_ids=("finqa",),
            client=client,
        )
        second = await materialize_fixed_context_datasets(
            registry,
            root=tmp_path,
            dataset_ids=("finqa",),
            client=client,
        )

    assert first == second
    assert requests == 1


@pytest.mark.asyncio
async def test_materializer_does_not_publish_checksum_mismatch(tmp_path: Path) -> None:
    expected = _finqa_payload()
    record = _record("finqa", (("finqa-train", expected, 1, 1),))
    registry = _single_record_registry(record)

    def handler(_: httpx2.Request) -> httpx2.Response:
        replacement = b"x" * len(expected)
        return httpx2.Response(200, stream=_AsyncBytes(replacement))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), trust_env=False
    ) as client:
        with pytest.raises(ValueError, match="checksum mismatch"):
            await materialize_fixed_context_datasets(
                registry,
                root=tmp_path,
                dataset_ids=("finqa",),
                client=client,
            )

    target = FixedContextArtifactStore(tmp_path).path_for(record, record.artifacts[0])
    assert not target.exists()
    assert not tuple(target.parent.glob("*.partial"))


def _single_record_registry(record: DatasetRecord) -> DatasetRegistry:
    source = load_dataset_registry(REGISTRY_PATH)
    return DatasetRegistry.model_validate({**source.model_dump(), "records": [record]})


@pytest.mark.parametrize("stem", ["finqa-adapter-v1", "tatqa-adapter-v1"])
def test_checked_in_adapter_report_is_contract_only_and_portable(stem: str) -> None:
    report = AdapterValidationReport.model_validate(
        load_strict_json(ROOT / "evals" / "reports" / f"{stem}.json")
    )

    assert report.model_executed is False
    assert report.official_metric_scores is None
    assert all(not Path(artifact.path).is_absolute() for artifact in report.artifacts)
