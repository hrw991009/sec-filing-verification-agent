from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.tool_eval import load_sec_tool_dataset
from industry_platform.modules.evaluation.release_evidence import (
    ReleaseEvidenceLayer,
    ReleaseStrategy,
    _canonical_sha256,
    load_release_evidence_manifest,
)
from industry_platform.modules.evaluation.release_execution import (
    _STRATEGY_FEATURES,
    ReleaseExecutionBatch,
    ReleaseExecutionBinding,
    ReleaseExecutionRunner,
)
from industry_platform.modules.evaluation.release_observation_collector import (
    ProductionJudgementBuilder,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals/manifests/sec-release-evidence-v1.json"
SOURCE_PATH = ROOT / "evals/scenarios/sec-release-cases-v1.json"
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
KNOWLEDGE_BASE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _batch_bindings() -> tuple[ReleaseExecutionBinding, ...]:
    source = load_sec_tool_dataset(SOURCE_PATH)
    bindings = []
    identifier = 1
    for case in source.cases:
        for strategy in ReleaseStrategy:
            bindings.append(
                ReleaseExecutionBinding(
                    case_id=case.case_id,
                    strategy_id=strategy,
                    repetition=1,
                    run_id=UUID(int=identifier),
                    workspace_id=WORKSPACE_ID,
                )
            )
            identifier += 1
    return tuple(bindings)


def _batch(bindings: tuple[ReleaseExecutionBinding, ...]) -> ReleaseExecutionBatch:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    now = datetime(2026, 9, 3, tzinfo=UTC)
    return ReleaseExecutionBatch(
        batch_id=UUID("33333333-3333-4333-8333-333333333333"),
        manifest_sha256=_canonical_sha256(manifest),
        source_manifest_sha256=hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest(),
        source_commit="a" * 40,
        evidence_layer=ReleaseEvidenceLayer.OFFLINE,
        provider="provider.example",
        model="release-model",
        model_version="upstream@pricing-v1",
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        started_at=now,
        completed_at=now,
        bindings=bindings,
    )


def test_execution_batch_requires_the_complete_fifty_run_matrix() -> None:
    bindings = _batch_bindings()

    assert len(_batch(bindings).bindings) == 50
    with pytest.raises(ValueError, match="complete A0-A4 matrix"):
        _batch(bindings[:-1])


def test_execution_features_match_the_frozen_strategy_contracts() -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)

    assert {
        item.strategy_id: (item.verifier_required, item.durable_monitor_required)
        for item in manifest.strategies
    } == _STRATEGY_FEATURES


@pytest.mark.asyncio
async def test_oracle_context_obeys_each_case_as_of_cutoff() -> None:
    source = load_sec_tool_dataset(SOURCE_PATH)
    before_2024 = next(item for item in source.cases if item.case_id == "simple-net-sales-2023")
    after_2024 = next(
        item for item in source.cases if item.case_id == "period-comparison-net-sales"
    )

    before = await ReleaseExecutionRunner._oracle_context(before_2024)
    after = await ReleaseExecutionRunner._oracle_context(after_2024)

    assert "0000320193-23-000106" in before
    assert "0000320193-24-000123" not in before
    assert "0000320193-24-000123" in after


@pytest.mark.parametrize(
    ("case_id", "answer"),
    [
        ("simple-net-sales-2023", "净销售额为 383.285 billion USD。"),
        ("calculation-net-sales-change", "净销售额同比下降 2.8%。"),
        ("period-comparison-net-income", "净利润从 2023 年到 2024 年下降。"),
    ],
)
def test_deterministic_answer_normalization_matches_semantic_gold(
    case_id: str, answer: str
) -> None:
    case = next(
        item for item in load_sec_tool_dataset(SOURCE_PATH).cases if item.case_id == case_id
    )

    assert ProductionJudgementBuilder._answer_key(answer, case) == case.expected_answer_key
