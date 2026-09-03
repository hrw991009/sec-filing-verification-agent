from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.models import AgentEventRecord
from industry_platform.modules.disclosures.tool_eval import (
    SecToolEvalCase,
    SecToolOutcome,
    load_sec_tool_dataset,
)
from industry_platform.modules.evaluation.release_evidence import (
    ReleaseEvidenceLayer,
    ReleaseRunObservation,
    ReleaseStrategy,
    ReleaseStrategyContract,
    _canonical_sha256,
    load_release_evidence_manifest,
)
from industry_platform.modules.evaluation.release_observation_collector import (
    CandidateKeyBinding,
    EvidenceKeyBinding,
    ProductionJudgementBuilder,
    ReleaseObservationCollection,
    ReleaseObservationCollectionError,
    ReleaseObservationCollector,
    ReleaseRunJudgement,
    write_collection_template,
)
from industry_platform.modules.evidence.domain import (
    EvidenceLocatorType,
    SecXbrlFactLocatorV1,
)
from industry_platform.modules.evidence.models import (
    EvidenceNormalizationDecisionRecord,
    EvidenceRecord,
)
from industry_platform.modules.tools.domain import (
    ToolObservation,
    ToolReference,
    ToolSource,
)
from industry_platform.modules.tools.models import ToolCallRecord

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "sec-release-evidence-v1.json"
SOURCE_PATH = ROOT / "evals" / "scenarios" / "sec-release-cases-v1.json"
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _judgements() -> tuple[ReleaseRunJudgement, ...]:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    judgements = []
    identifier = 1
    for case_id in manifest.common_case_ids:
        for strategy in ReleaseStrategy:
            judgements.append(
                ReleaseRunJudgement(
                    case_id=case_id,
                    strategy_id=strategy,
                    repetition=1,
                    run_id=UUID(int=identifier),
                    observed_outcome=SecToolOutcome.INSUFFICIENT_EVIDENCE,
                    final_state_matches=False,
                )
            )
            identifier += 1
    return tuple(judgements)


def _collection(
    judgements: tuple[ReleaseRunJudgement, ...] | None = None,
) -> ReleaseObservationCollection:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    return ReleaseObservationCollection(
        manifest_sha256=_canonical_sha256(manifest),
        evidence_layer=ReleaseEvidenceLayer.OFFLINE,
        provider="controlled-provider",
        model="controlled-model",
        model_version="v1",
        runtime_version="strategy-bound-v1",
        harness_version="strategy-bound-v1",
        prompt_version="sec-release-prompt-v1",
        toolset_version="sec-release-toolset-v1",
        judgements=_judgements() if judgements is None else judgements,
        limitations=("Unit-test collection; not production capability evidence.",),
    )


class _StubCollector(ReleaseObservationCollector):
    def __init__(self) -> None:
        pass

    async def _collect_run(
        self,
        judgement: ReleaseRunJudgement,
        *,
        case: SecToolEvalCase,
        strategy_contract: ReleaseStrategyContract,
        expected_model: str,
    ) -> ReleaseRunObservation:
        del case, expected_model
        contract = strategy_contract
        return ReleaseRunObservation(
            case_id=judgement.case_id,
            strategy_id=judgement.strategy_id,
            repetition=judgement.repetition,
            run_id=judgement.run_id,
            trace_id=f"trace-{judgement.run_id}",
            workspace_id=WORKSPACE_ID,
            result_workspace_id=WORKSPACE_ID,
            run_status=AgentRunStatus.COMPLETED,
            stop_reason=RunStopReason.FINAL,
            runtime_version=contract.runtime_version,
            harness_version=contract.harness_version,
            profile_version=contract.profile_version,
            graph_version=contract.graph_version,
            prompt_version=contract.prompt_version,
            toolset_version=contract.toolset_version,
            verifier_executed=contract.verifier_required,
            durable_monitor_enabled=contract.durable_monitor_required,
            observed_outcome=judgement.observed_outcome,
            citations_resolvable=True,
            final_state_matches=judgement.final_state_matches,
            final_state_sha256=hashlib.sha256(str(judgement.run_id).encode()).hexdigest(),
            trace_event_count=2,
            future_source_count=0,
            cross_workspace_access_count=0,
            unauthorized_write_count=0,
            duplicate_side_effect_count=0,
            injection_attempted=False,
            injection_succeeded=False,
            recovery_required=False,
            recovered=False,
            steps=1,
            total_tokens=10,
            cost_micro_usd=1,
            latency_ms=2,
        )


@pytest.mark.asyncio
async def test_collector_requires_complete_common_case_strategy_coverage() -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    collector = _StubCollector()

    observations = await collector.collect(manifest, source, _collection())

    assert len(observations.observations) == 50
    assert observations.runtime_version == "strategy-bound-v1"
    assert observations.harness_version == "strategy-bound-v1"

    with pytest.raises(ReleaseObservationCollectionError, match="coverage mismatch"):
        await collector.collect(manifest, source, _collection(_judgements()[:-1]))


def test_judgement_rejects_duplicate_semantic_candidate_bindings() -> None:
    with pytest.raises(ValueError, match="candidate keys must be unique"):
        ReleaseRunJudgement(
            case_id="simple-net-sales-2023",
            strategy_id=ReleaseStrategy.A1,
            repetition=1,
            run_id=UUID(int=1),
            observed_outcome=SecToolOutcome.ANSWERED,
            final_state_matches=True,
            candidate_keys=(
                CandidateKeyBinding(tool_call_id=UUID(int=2), source_ordinal=1, locator="gold:key"),
                CandidateKeyBinding(tool_call_id=UUID(int=2), source_ordinal=2, locator="gold:key"),
            ),
        )


def test_ranked_candidates_use_observation_source_order_and_explicit_gold_mapping() -> None:
    call_id = UUID(int=20)
    text = "{}"
    observation = ToolObservation(
        schema_version=1,
        observation_id=UUID(int=21),
        call_id=call_id,
        run_id=UUID(int=1),
        workspace_id=WORKSPACE_ID,
        tool=ToolReference("sec.get_xbrl_facts", "v1"),
        normalizer_version="tool-observation-v1",
        model_text=text,
        sources=(
            ToolSource(
                source_type="sec_xbrl_fact",
                source_version="v1",
                locator="sec://xbrl-facts/first",
                observed_at=NOW,
                content_sha256="1" * 64,
            ),
            ToolSource(
                source_type="sec_xbrl_fact",
                source_version="v1",
                locator="sec://xbrl-facts/second",
                observed_at=NOW,
                content_sha256="2" * 64,
            ),
        ),
        observed_at=NOW,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    call = ToolCallRecord(id=call_id)
    judgement = ReleaseRunJudgement(
        case_id="simple-net-sales-2023",
        strategy_id=ReleaseStrategy.A2,
        repetition=1,
        run_id=UUID(int=1),
        observed_outcome=SecToolOutcome.ANSWERED,
        final_state_matches=True,
        candidate_keys=(
            CandidateKeyBinding(tool_call_id=call_id, source_ordinal=2, locator="gold:second"),
        ),
    )

    candidates = ReleaseObservationCollector._ranked_candidates(
        judgement,
        tool_calls=(call,),
        observations={call_id: observation},
    )

    assert tuple((item.rank, item.locator) for item in candidates) == (
        (1, "sec://xbrl-facts/first"),
        (2, "gold:second"),
    )


def test_ranked_candidates_preserve_sources_across_multiple_tool_calls() -> None:
    first_call_id = UUID(int=40)
    second_call_id = UUID(int=41)

    def observation(call_id: UUID, locator: str, digest: str) -> ToolObservation:
        text = f'{{"locator":"{locator}"}}'
        return ToolObservation(
            schema_version=1,
            observation_id=UUID(int=call_id.int + 10),
            call_id=call_id,
            run_id=UUID(int=1),
            workspace_id=WORKSPACE_ID,
            tool=ToolReference("sec.search_filing", "v1"),
            normalizer_version="tool-observation-v1",
            model_text=text,
            sources=(
                ToolSource(
                    source_type="sec_filing_chunk",
                    source_version="v1",
                    locator=locator,
                    observed_at=NOW,
                    content_sha256=digest * 64,
                ),
            ),
            observed_at=NOW,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    judgement = ReleaseRunJudgement(
        case_id="simple-net-sales-2023",
        strategy_id=ReleaseStrategy.A2,
        repetition=1,
        run_id=UUID(int=1),
        observed_outcome=SecToolOutcome.ANSWERED,
        final_state_matches=True,
        candidate_keys=(
            CandidateKeyBinding(
                tool_call_id=second_call_id,
                source_ordinal=1,
                locator="gold:second-call",
            ),
        ),
    )

    candidates = ReleaseObservationCollector._ranked_candidates(
        judgement,
        tool_calls=(ToolCallRecord(id=first_call_id), ToolCallRecord(id=second_call_id)),
        observations={
            first_call_id: observation(first_call_id, "sec://filing/first-call", "1"),
            second_call_id: observation(second_call_id, "sec://filing/second-call", "2"),
        },
    )

    assert tuple((item.rank, item.locator) for item in candidates) == (
        (1, "sec://filing/first-call"),
        (2, "gold:second-call"),
    )


def test_ranked_candidates_deduplicate_repeated_source_locators() -> None:
    first_call_id = UUID(int=45)
    second_call_id = UUID(int=46)

    def observation(call_id: UUID, observation_id: UUID) -> ToolObservation:
        text = "{}"
        return ToolObservation(
            schema_version=1,
            observation_id=observation_id,
            call_id=call_id,
            run_id=UUID(int=1),
            workspace_id=WORKSPACE_ID,
            tool=ToolReference("sec.search_filing", "v1"),
            normalizer_version="tool-observation-v1",
            model_text=text,
            sources=(
                ToolSource(
                    source_type="sec_filing_chunk",
                    source_version="v1",
                    locator="sec://filing/repeated",
                    observed_at=NOW,
                    content_sha256="4" * 64,
                ),
            ),
            observed_at=NOW,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    judgement = ReleaseRunJudgement(
        case_id="simple-net-sales-2023",
        strategy_id=ReleaseStrategy.A2,
        repetition=1,
        run_id=UUID(int=1),
        observed_outcome=SecToolOutcome.ANSWERED,
        final_state_matches=True,
    )

    candidates = ReleaseObservationCollector._ranked_candidates(
        judgement,
        tool_calls=(ToolCallRecord(id=first_call_id), ToolCallRecord(id=second_call_id)),
        observations={
            first_call_id: observation(first_call_id, UUID(int=47)),
            second_call_id: observation(second_call_id, UUID(int=48)),
        },
    )

    assert tuple((item.rank, item.locator) for item in candidates) == (
        (1, "sec://filing/repeated"),
    )


def test_evidence_binding_only_exports_finally_selected_artifacts() -> None:
    selected_id = UUID(int=30)
    intermediate_id = UUID(int=31)
    calculation_id = UUID(int=32)
    records = (
        EvidenceRecord(
            id=intermediate_id,
            locator_type=EvidenceLocatorType.SEC_FILING_CHUNK_V1,
        ),
        EvidenceRecord(
            id=selected_id,
            locator_type=EvidenceLocatorType.SEC_XBRL_FACT_V1,
        ),
        EvidenceRecord(
            id=calculation_id,
            locator_type=EvidenceLocatorType.FINANCIAL_CALCULATION_V1,
        ),
    )
    judgement = ReleaseRunJudgement(
        case_id="simple-net-sales-2023",
        strategy_id=ReleaseStrategy.A2,
        repetition=1,
        run_id=UUID(int=1),
        observed_outcome=SecToolOutcome.ANSWERED,
        final_state_matches=True,
        evidence_keys=(EvidenceKeyBinding(evidence_id=selected_id, evidence_key="gold:net-sales"),),
        calculation_ids=(calculation_id,),
    )

    evidence_keys, cited_records, calculation_ids = ReleaseObservationCollector._bind_evidence(
        judgement, records
    )

    assert evidence_keys == ("gold:net-sales",)
    assert tuple(record.id for record in cited_records) == (selected_id, calculation_id)
    assert intermediate_id not in {record.id for record in cited_records}
    assert calculation_ids == (calculation_id,)


def test_final_draft_references_select_only_loaded_workspace_evidence() -> None:
    selected_id = UUID(int=33)
    intermediate_id = UUID(int=34)
    records = (EvidenceRecord(id=intermediate_id), EvidenceRecord(id=selected_id))

    cited = ProductionJudgementBuilder._cited_records(records, (str(selected_id),))

    assert tuple(record.id for record in cited) == (selected_id,)
    with pytest.raises(ReleaseObservationCollectionError, match="production workspace"):
        ProductionJudgementBuilder._cited_records(records, (str(UUID(int=99)),))


def test_candidate_binding_uses_current_normalization_lineage_for_reused_evidence() -> None:
    case = load_sec_tool_dataset(SOURCE_PATH).cases[0]
    current_call_id = UUID(int=60)
    reused_evidence_id = UUID(int=61)
    locator = SecXbrlFactLocatorV1(
        cik="0000320193",
        accession="0000320193-23-000106",
        form="10-K",
        report_period="2023-09-30",
        as_of="2023-11-03T12:00:00+00:00",
        fact_id=UUID(int=62),
        filing_id=UUID(int=63),
        source_id=UUID(int=64),
        source_snapshot_id=None,
        source_kind="companyfacts_aggregate",
        taxonomy="us-gaap",
        concept="Revenue",
        unit="USD",
        period_kind="duration",
        instant=None,
        start_date="2022-09-25",
        end_date="2023-09-30",
        context_id=None,
        dimensions={},
        decimals=None,
        scale=None,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        source_version="sec-companyfacts-v1",
        source_content_sha256="d" * 64,
        content_sha256="e" * 64,
        source_available_at="2023-11-03T06:01:00+00:00",
        retrieved_at="2026-09-03T00:00:00+00:00",
    )
    reused = EvidenceRecord(
        id=reused_evidence_id,
        origin_run_id=UUID(int=59),
        locator_type=EvidenceLocatorType.SEC_XBRL_FACT_V1,
        locator=locator.to_mapping(),
    )
    decision = EvidenceNormalizationDecisionRecord(
        id=UUID(int=65),
        tool_call_id=current_call_id,
        source_ordinal=1,
        evidence_id=reused_evidence_id,
    )

    bindings = ProductionJudgementBuilder._candidate_bindings(
        case,
        tool_calls=(ToolCallRecord(id=current_call_id),),
        decisions=(decision,),
        records=(reused,),
    )

    assert bindings == (
        CandidateKeyBinding(
            tool_call_id=current_call_id,
            source_ordinal=1,
            locator="xbrl:us-gaap:Revenue:2023",
        ),
    )


def test_model_identity_requires_a_matching_completed_model_event() -> None:
    run_id = UUID(int=1)
    matching = AgentEventRecord(
        event_type=AgentEventType.MODEL_COMPLETED,
        payload={"model": "controlled-model"},
    )

    ReleaseObservationCollector._validate_model_identity(
        run_id, (matching,), expected_model="controlled-model"
    )

    with pytest.raises(ReleaseObservationCollectionError, match="model identity mismatch"):
        ReleaseObservationCollector._validate_model_identity(
            run_id, (), expected_model="controlled-model"
        )


def test_collector_constructor_accepts_the_application_session_contract() -> None:
    factory = cast(AsyncSessionFactory, object())
    collector = ReleaseObservationCollector(factory)

    assert collector is not None


def test_collection_template_has_full_coverage_but_cannot_be_scored_as_execution(
    tmp_path: Path,
) -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    output = tmp_path / "collection.json"

    write_collection_template(
        manifest,
        evidence_layer=ReleaseEvidenceLayer.OFFLINE,
        output=output,
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert len(document["judgements"]) == 50
    assert document["judgements"][0]["run_id"] == "REPLACE_WITH_RUN_UUID_1"
    assert document["runtime_version"] == "REPLACE_WITH_RUNTIME_VERSION"
    assert document["harness_version"] == "REPLACE_WITH_HARNESS_VERSION"
    with pytest.raises(ValueError, match="UUID"):
        ReleaseObservationCollection.model_validate_json(
            output.read_text(encoding="utf-8"), strict=True
        )
