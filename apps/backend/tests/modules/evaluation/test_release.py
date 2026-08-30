"""Machine-check the Day 9 release registry and manifest contracts."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from industry_platform.modules.agent_runtime.domain import RunStopReason
from industry_platform.modules.evaluation.release import (
    ActionArgumentConstraint,
    ArtifactRole,
    DatasetArtifact,
    DatasetLicense,
    DatasetRecord,
    DatasetRegistry,
    DatasetStatus,
    DatasetUse,
    EvidenceLayer,
    FinalStateExpectation,
    LicenseReviewStatus,
    MilestoneOrder,
    QuestionLanguage,
    ReleaseAnswerGold,
    ReleaseBudget,
    ReleaseCaseKind,
    ReleaseCaseStatus,
    ReleaseEvalCase,
    ReleaseEvalManifest,
    ReleaseEvidenceBinding,
    ReleaseManifestStatus,
    ReleaseQuestion,
    ReleaseRuntimeConfiguration,
    ReleaseSecGold,
    ReleaseSecSource,
    ReleaseStrategy,
    ReleaseTrajectoryContract,
    canonical_sha256,
    load_dataset_registry,
    load_release_manifest,
    main,
    validate_manifest_against_registry,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
REGISTRY_PATH = REPOSITORY_ROOT / "evals" / "registry" / "sec-agent-datasets-v1.json"
MANIFEST_PATH = REPOSITORY_ROOT / "evals" / "manifests" / "sec-agent-release-v1.json"
REGISTRY_SCHEMA_PATH = REPOSITORY_ROOT / "evals" / "schemas" / "dataset-registry-v1.schema.json"
MANIFEST_SCHEMA_PATH = (
    REPOSITORY_ROOT / "evals" / "schemas" / "release-eval-manifest-v1.schema.json"
)


def _strategy() -> ReleaseStrategy:
    return ReleaseStrategy(
        strategy_id="a2",
        configuration=ReleaseRuntimeConfiguration(
            runtime_version="agent-runtime-v1",
            harness_version="harness-research-v1",
            model_provider="frozen",
            model_name="day9-contract-model",
            model_version="v1",
            prompt_version="sec-l4-prompt-v1",
            toolset_version="sec-l4-toolset-v1",
            context_version="financial-context-v1",
            retrieval_version="hybrid-v1",
            graph_version="research-l4-graph-v1",
            verifier_version=None,
            scorer_version="sec-release-scorer-v1",
        ),
        available_tools=("sec.search_filing@v1", "sec.read_filing_section@v1"),
    )


def _case(*, case_id: str = "finqa.contract.001", split: str = "dev") -> ReleaseEvalCase:
    return ReleaseEvalCase(
        case_id=case_id,
        case_version="v1",
        dataset_id="finqa",
        dataset_version="0f16e2867befa6840783e58be38c9efb9229d742",
        split=split,
        document_group_id="report-001",
        source_artifact_ids=("finqa-dev",),
        kind=ReleaseCaseKind.FIXED_CONTEXT,
        evidence_layer=EvidenceLayer.OFFLINE_CAPABILITY,
        questions=(
            ReleaseQuestion(
                language=QuestionLanguage.EN,
                text="What was the reported value?",
            ),
            ReleaseQuestion(language=QuestionLanguage.ZH, text="报告值是多少?"),
        ),
        strategy_ids=("a2",),
        budget=ReleaseBudget(
            max_steps=8,
            max_tool_calls=6,
            max_total_tokens=8_000,
            max_cost_micro_usd=50_000,
            max_latency_ms=60_000,
            max_revisions=0,
        ),
        trajectory=ReleaseTrajectoryContract(
            required_milestones=("select_context", "calculate", "finalize"),
            allowed_actions=("sec.search_filing", "sec.read_filing_section"),
            forbidden_actions=("sec.monitor.subscribe",),
            argument_constraints=(
                ActionArgumentConstraint(
                    action="sec.search_filing",
                    argument="top_k",
                    required=True,
                    allowed_values=("5",),
                ),
            ),
            partial_order=(
                MilestoneOrder(before="select_context", after="calculate"),
                MilestoneOrder(before="calculate", after="finalize"),
            ),
            final_state=(
                FinalStateExpectation(
                    path="run.status",
                    operator="eq",
                    expected_value="completed",
                ),
            ),
            expected_stop_reason=RunStopReason.FINAL,
        ),
        answer_gold=ReleaseAnswerGold(
            expected_answer_key="123",
            supporting_fact_keys=("fact-1",),
            expected_program="identity(123)",
            expected_result="123",
            tolerance="0",
            unit="USD",
            rounding_places=0,
            expected_business_status="verified",
        ),
        status=ReleaseCaseStatus.PLANNED,
    )


def _ready_registry() -> DatasetRegistry:
    registry = load_dataset_registry(REGISTRY_PATH)
    finqa = next(record for record in registry.records if record.dataset_id == "finqa")
    ready_license = DatasetLicense.model_validate(
        {
            **finqa.license.model_dump(),
            "review_status": LicenseReviewStatus.OWNER_REVIEWED,
        }
    )
    ready = DatasetRecord.model_validate(
        {
            **finqa.model_dump(),
            "license": ready_license,
            "status": DatasetStatus.RELEASE_READY,
            "release_eligible": True,
            "blockers": (),
        }
    )
    return DatasetRegistry.model_validate(
        {
            **registry.model_dump(),
            "records": (ready,),
        }
    )


def _frozen_manifest(
    registry: DatasetRegistry,
    *,
    cases: tuple[ReleaseEvalCase, ...] | None = None,
) -> ReleaseEvalManifest:
    return ReleaseEvalManifest(
        schema_version=1,
        manifest_id="sec-agent-release-v1",
        manifest_version="v1-test",
        registry_id="sec-agent-datasets-v1",
        registry_version="v1",
        registry_sha256=canonical_sha256(registry),
        status=ReleaseManifestStatus.FROZEN,
        strategies=(_strategy(),),
        cases=cases or (_case(),),
        release_ready=False,
        blockers=("runs_not_executed",),
    )


def test_registry_pins_four_sources_and_eleven_artifacts() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    records = {record.dataset_id: record for record in registry.records}
    artifacts = {
        artifact.artifact_id: artifact
        for record in registry.records
        for artifact in record.artifacts
    }

    assert set(records) == {"finqa", "tat-qa", "financebench", "finsearchcomp"}
    assert len(artifacts) == 11
    assert records["finqa"].upstream_revision == "0f16e2867befa6840783e58be38c9efb9229d742"
    assert records["tat-qa"].upstream_revision == "870accc41953dcde885aabeb963d94aabdc0fbc3"
    assert records["financebench"].upstream_revision == ("cc39aeb4afdf33909ee1412188bf89035950c2eb")
    assert records["finsearchcomp"].upstream_revision == (
        "55b6393fcf3c8f749ba5a69a70b20d4ef6f67caf"
    )
    assert artifacts["finqa-train"].byte_size == 78_216_616
    assert artifacts["finqa-train"].sha256 == (
        "49f237eb9779b569473b26b08048867d04635a7cc39ad6a7a5664c55bb428db6"
    )
    assert artifacts["finsearchcomp-full"].sha256 == (
        "6437a6dae907ec81002bd817dafc26c3e46e6b6edfde700f22645b1e2aa208c4"
    )
    assert records["finqa"].status is DatasetStatus.ADAPTER_READY
    assert records["tat-qa"].status is DatasetStatus.ADAPTER_READY
    assert records["financebench"].status is DatasetStatus.ADAPTER_READY
    assert records["finsearchcomp"].status is DatasetStatus.ADAPTER_READY
    assert not any(record.release_eligible for record in records.values())
    assert artifacts["finqa-train"].question_count == 6251
    assert artifacts["tatqa-test"].question_count == 1669
    assert artifacts["tatqa-test-gold"].question_count == 1663
    assert artifacts["financebench-open-source"].question_count == 150
    assert artifacts["financebench-document-information"].document_count == 361
    assert artifacts["financebench-document-information"].question_count is None
    assert artifacts["finsearchcomp-full"].question_count == 635
    assert artifacts["finsearchcomp-akshare"].question_count == 594


def test_registry_separates_data_code_and_restricted_rights() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    records = {record.dataset_id: record for record in registry.records}

    assert records["finqa"].license.data_license_id == "CC-BY-4.0"
    assert records["finqa"].license.code_license_id == "MIT"
    assert records["tat-qa"].license.data_license_id == "CC-BY-4.0"
    financebench = records["financebench"]
    assert financebench.license.data_license_id == "CC-BY-NC-4.0"
    assert financebench.license.commercial_use_allowed is False
    assert financebench.license.redistribution_allowed is False
    assert financebench.allowed_uses == (DatasetUse.INTERNAL_EVALUATION,)
    assert not any(artifact.redistribution_allowed for artifact in financebench.artifacts)
    assert records["finsearchcomp"].allowed_evidence_layers == (
        EvidenceLayer.OFFLINE_CAPABILITY,
        EvidenceLayer.LIVE_MODEL,
    )


def test_contract_manifest_is_blocked_and_bound_to_registry() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    manifest = load_release_manifest(MANIFEST_PATH)

    validate_manifest_against_registry(manifest, registry)
    assert manifest.status is ReleaseManifestStatus.CONTRACT_ONLY
    assert manifest.release_ready is False
    assert not manifest.cases
    assert "external_benchmark_owner_review_not_complete" in manifest.blockers
    assert "agent_security_runtime_binding_not_executed" in manifest.blockers


def test_release_schema_cli_is_deterministic_and_checked_in(tmp_path: Path) -> None:
    first_registry = tmp_path / "registry-first.json"
    first_manifest = tmp_path / "manifest-first.json"
    second_registry = tmp_path / "registry-second.json"
    second_manifest = tmp_path / "manifest-second.json"

    for registry_output, manifest_output in (
        (first_registry, first_manifest),
        (second_registry, second_manifest),
    ):
        assert (
            main(
                [
                    "--registry",
                    str(REGISTRY_PATH),
                    "--manifest",
                    str(MANIFEST_PATH),
                    "--registry-schema-output",
                    str(registry_output),
                    "--manifest-schema-output",
                    str(manifest_output),
                ]
            )
            == 0
        )

    assert first_registry.read_bytes() == second_registry.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    assert json.loads(first_registry.read_text(encoding="utf-8")) == json.loads(
        REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    assert json.loads(first_manifest.read_text(encoding="utf-8")) == json.loads(
        MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
    )


def test_json_loader_rejects_duplicate_keys_and_non_finite_values(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_dataset_registry(duplicate)

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text('{"schema_version":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        load_release_manifest(non_finite)


def test_registry_rejects_floating_revision_gold_exposure_and_rights_escalation() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    finqa = next(record for record in registry.records if record.dataset_id == "finqa")

    with pytest.raises(ValidationError, match="upstream_revision"):
        DatasetRecord.model_validate({**finqa.model_dump(), "upstream_revision": "main"})

    artifact = finqa.artifacts[0]
    with pytest.raises(ValidationError, match="containing gold"):
        DatasetArtifact.model_validate(
            {
                **artifact.model_dump(),
                "allowed_in_model_context": True,
            }
        )

    financebench = next(
        record for record in registry.records if record.dataset_id == "financebench"
    )
    with pytest.raises(ValidationError, match="redistribution rights"):
        DatasetRecord.model_validate(
            {
                **financebench.model_dump(),
                "allowed_uses": ["internal_evaluation", "redistribute_payload"],
            }
        )


def test_release_ready_dataset_requires_owner_review() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    finqa = next(record for record in registry.records if record.dataset_id == "finqa")
    with pytest.raises(ValidationError, match="owner-reviewed rights"):
        DatasetRecord.model_validate(
            {
                **finqa.model_dump(),
                "status": "release_ready",
                "release_eligible": True,
                "blockers": [],
            }
        )


def test_sec_case_rejects_future_source_and_language_mismatch() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    source = ReleaseSecSource(
        accession="0000320193-23-000106",
        form="10-K",
        available_at=as_of + timedelta(seconds=1),
        snapshot_sha256="a" * 64,
        evidence_locators=("sec://filing/item-8",),
    )
    with pytest.raises(ValidationError, match="visible after as_of"):
        ReleaseSecGold(
            cik="0000320193",
            report_period=date(2023, 9, 30),
            as_of=as_of,
            sources=(source,),
        )

    case = _case()
    with pytest.raises(ValidationError, match="languages must be unique"):
        ReleaseEvalCase.model_validate(
            {
                **case.model_dump(),
                "questions": [
                    {"language": "en", "text": "First question."},
                    {"language": "en", "text": "Duplicate language."},
                ],
            }
        )


def test_manifest_rejects_split_leakage_and_unready_datasets() -> None:
    ready_registry = _ready_registry()
    first = _case()
    leaked = _case(case_id="finqa.contract.002", split="test")
    with pytest.raises(ValidationError, match="cannot cross splits"):
        _frozen_manifest(ready_registry, cases=(first, leaked))

    registered = load_dataset_registry(REGISTRY_PATH)
    frozen = _frozen_manifest(registered)
    with pytest.raises(ValueError, match="not release eligible"):
        validate_manifest_against_registry(frozen, registered)


def test_frozen_manifest_validates_with_ready_registry_and_full_versions(
    tmp_path: Path,
) -> None:
    registry = _ready_registry()
    manifest = _frozen_manifest(registry)
    manifest_path = tmp_path / "frozen-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    validate_manifest_against_registry(manifest, registry)
    loaded = load_release_manifest(manifest_path)
    assert loaded == manifest
    configuration = loaded.strategies[0].configuration
    assert configuration.runtime_version == "agent-runtime-v1"
    assert configuration.harness_version == "harness-research-v1"
    assert configuration.model_version == "v1"
    assert configuration.prompt_version == "sec-l4-prompt-v1"
    assert configuration.context_version == "financial-context-v1"
    assert configuration.scorer_version == "sec-release-scorer-v1"


@pytest.mark.parametrize(
    ("case_updates", "expected_error"),
    [
        ({"dataset_id": "unregistered"}, "unregistered dataset"),
        ({"dataset_version": "v2"}, "dataset version does not match"),
        ({"source_artifact_ids": ("unknown",)}, "unknown artifact"),
        ({"split": "test"}, "artifact split does not match"),
        (
            {"evidence_layer": EvidenceLayer.DETERMINISTIC_CONTRACT},
            "evidence layer is not allowed",
        ),
    ],
)
def test_manifest_registry_references_fail_closed(
    case_updates: dict[str, object],
    expected_error: str,
) -> None:
    registry = _ready_registry()
    case = ReleaseEvalCase.model_validate({**_case().model_dump(), **case_updates})
    manifest = _frozen_manifest(registry, cases=(case,))

    with pytest.raises(ValueError, match=expected_error):
        validate_manifest_against_registry(manifest, registry)


def test_manifest_registry_checksum_fails_closed() -> None:
    registry = _ready_registry()
    manifest = ReleaseEvalManifest.model_validate(
        {
            **_frozen_manifest(registry).model_dump(),
            "registry_sha256": "a" * 64,
        }
    )

    with pytest.raises(ValueError, match="registry checksum does not match"):
        validate_manifest_against_registry(manifest, registry)


def test_artifact_paths_and_executed_case_binding_fail_closed() -> None:
    with pytest.raises(ValidationError, match="safe relative POSIX"):
        DatasetArtifact(
            artifact_id="bad-path",
            split="test",
            role=ArtifactRole.INPUT,
            relative_path="../gold.json",
            download_url="https://example.com/revision/gold.json",
            byte_size=1,
            sha256="a" * 64,
            contains_gold=False,
            redistribution_allowed=False,
        )

    case = _case()
    with pytest.raises(ValidationError, match="Evidence binding"):
        ReleaseEvalCase.model_validate({**case.model_dump(), "status": "executed"})

    with pytest.raises(ValidationError, match="nil UUID"):
        ReleaseEvidenceBinding(
            run_id=UUID(int=0),
            trace_id=UUID("11111111-1111-4111-8111-111111111111"),
            evidence_ids=(),
            calculation_ids=(),
        )


def test_strict_model_rejects_string_byte_size() -> None:
    registry = load_dataset_registry(REGISTRY_PATH)
    artifact = registry.records[0].artifacts[0]
    with pytest.raises(ValidationError, match="valid integer"):
        DatasetArtifact.model_validate(
            {**artifact.model_dump(), "byte_size": str(artifact.byte_size)},
            strict=True,
        )


def test_license_pair_and_contract_only_state_are_strict() -> None:
    with pytest.raises(ValidationError, match="provided together"):
        DatasetLicense(
            data_license_id="CC-BY-4.0",
            data_license_url="https://example.com/data-license",
            code_license_id="MIT",
            code_license_url=None,
            attribution_required=True,
            commercial_use_allowed=True,
            redistribution_allowed=True,
            source_documents_separately_governed=False,
            review_status=LicenseReviewStatus.METADATA_VERIFIED,
            reviewed_on=date(2026, 8, 29),
            notes=("Test-only license record.",),
        )

    manifest = load_release_manifest(MANIFEST_PATH)
    with pytest.raises(ValidationError, match="Contract-only manifest must remain blocked"):
        ReleaseEvalManifest.model_validate(
            {
                **manifest.model_dump(),
                "release_ready": True,
                "blockers": [],
            }
        )
