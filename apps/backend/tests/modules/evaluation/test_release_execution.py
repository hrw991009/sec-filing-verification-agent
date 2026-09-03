from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from industry_platform.core.config import AgentModelRouteSettings, Settings
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.disclosures.tool_eval import load_sec_tool_dataset
from industry_platform.modules.evaluation import release_execution as execution_module
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
    ReleaseExecutionError,
    ReleaseExecutionRunner,
    load_execution_batch,
    write_execution_batch,
)
from industry_platform.modules.evaluation.release_observation_collector import (
    ProductionJudgementBuilder,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals/manifests/sec-release-evidence-v1.json"
SOURCE_PATH = ROOT / "evals/scenarios/sec-release-cases-v1.json"
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
KNOWLEDGE_BASE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")


def _settings(test_settings: Settings) -> Settings:
    return test_settings.model_copy(
        update={
            "agent_model_provider_base_url": "https://provider.example/v1",
            "agent_model_route": AgentModelRouteSettings(
                model="release-model",
                upstream_model="upstream-model",
                response_models=("upstream-model",),
                pricing_version="pricing-v1",
                input_micro_usd_per_million=1,
                cached_input_micro_usd_per_million=1,
                output_micro_usd_per_million=1,
            ),
        }
    )


def _runner(test_settings: Settings) -> ReleaseExecutionRunner:
    return ReleaseExecutionRunner(
        settings=_settings(test_settings),
        session_factory=object(),  # type: ignore[arg-type]
        provider_http_client=object(),  # type: ignore[arg-type]
        internal_http_client=object(),  # type: ignore[arg-type]
    )


class _ReleaseScopeSession:
    def __init__(
        self,
        imports: tuple[SimpleNamespace, ...],
        scalar_results: list[object] | None = None,
    ) -> None:
        self._imports = imports
        self._scalar_results = list(scalar_results or [])

    async def __aenter__(self) -> _ReleaseScopeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalars(self, statement: object) -> tuple[SimpleNamespace, ...]:
        return self._imports

    async def scalar(self, statement: object) -> object:
        return self._scalar_results.pop(0)


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


def test_execution_models_reject_invalid_identity_time_and_scope() -> None:
    bindings = _batch_bindings()
    with pytest.raises(ValueError, match="cannot be nil"):
        ReleaseExecutionBinding(
            case_id="case",
            strategy_id=ReleaseStrategy.A0,
            repetition=1,
            run_id=UUID(int=0),
            workspace_id=WORKSPACE_ID,
        )
    for update, message in (
        ({"batch_id": UUID(int=0)}, "cannot be nil"),
        (
            {"started_at": datetime(2026, 9, 3, tzinfo=UTC).replace(tzinfo=None)},
            "timezone-aware",
        ),
        ({"started_at": datetime(2026, 9, 4, tzinfo=UTC)}, "precedes"),
        ({"bindings": (*bindings[:-1], bindings[0])}, "unique"),
        (
            {
                "bindings": (
                    *bindings[:-1],
                    bindings[-1].model_copy(update={"workspace_id": USER_ID}),
                )
            },
            "cross Workspace",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            _batch(bindings).model_copy(update=update).__class__.model_validate(
                _batch(bindings).model_dump() | update
            )


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


@pytest.mark.asyncio
async def test_execute_builds_the_complete_offline_batch(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(test_settings)
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    monkeypatch.setattr(
        runner,
        "_release_scope",
        AsyncMock(return_value=(WORKSPACE_ID, USER_ID, KNOWLEDGE_BASE_ID, "owner")),
    )
    run_ids = iter(UUID(int=value) for value in range(1, 51))
    execute_one = AsyncMock(side_effect=lambda **kwargs: next(run_ids))
    monkeypatch.setattr(runner, "_execute_one", execute_one)

    batch = await runner.execute(
        manifest,
        source,
        source_commit="a" * 40,
        live_repetitions=False,
    )

    assert batch.evidence_layer is ReleaseEvidenceLayer.OFFLINE
    assert batch.provider == "provider.example"
    assert batch.model_version == "upstream-model@pricing-v1"
    assert len(batch.bindings) == 50
    assert execute_one.await_count == 50


@pytest.mark.asyncio
async def test_execute_rejects_missing_route_source_drift_and_strategy_drift(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    unconfigured = ReleaseExecutionRunner(
        settings=test_settings,
        session_factory=object(),  # type: ignore[arg-type]
        provider_http_client=object(),  # type: ignore[arg-type]
        internal_http_client=object(),  # type: ignore[arg-type]
    )
    with pytest.raises(ReleaseExecutionError, match="not configured"):
        await unconfigured.execute(manifest, source, source_commit="a" * 40, live_repetitions=False)

    runner = _runner(test_settings)
    with pytest.raises(ReleaseExecutionError, match="source cases differ"):
        await runner.execute(
            manifest,
            source.model_copy(update={"cases": tuple(reversed(source.cases))}),
            source_commit="a" * 40,
            live_repetitions=False,
        )

    monkeypatch.setattr(
        runner,
        "_release_scope",
        AsyncMock(return_value=(WORKSPACE_ID, USER_ID, KNOWLEDGE_BASE_ID, "owner")),
    )
    first = manifest.strategies[0]
    changed = first.model_copy(update={"verifier_required": not first.verifier_required})
    with pytest.raises(ReleaseExecutionError, match="feature contract changed"):
        await runner.execute(
            manifest.model_copy(update={"strategies": (changed, *manifest.strategies[1:])}),
            source,
            source_commit="a" * 40,
            live_repetitions=False,
        )


@pytest.mark.asyncio
async def test_release_scope_requires_all_filings_and_active_membership(
    test_settings: Settings,
) -> None:
    source = load_sec_tool_dataset(SOURCE_PATH)
    imports = tuple(
        SimpleNamespace(
            workspace_id=WORKSPACE_ID,
            created_by_user_id=USER_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            accession=accession,
        )
        for accession in sorted(
            {value for case in source.cases for value in case.expected_accessions}
        )
    )
    membership = SimpleNamespace(role=SimpleNamespace(value="owner"))
    runner = _runner(test_settings)
    runner._session_factory = lambda: _ReleaseScopeSession(  # type: ignore[assignment]
        imports,
        [SimpleNamespace(), membership],
    )

    assert await runner._release_scope(source) == (
        WORKSPACE_ID,
        USER_ID,
        KNOWLEDGE_BASE_ID,
        "owner",
    )

    runner._session_factory = (
        lambda: _ReleaseScopeSession(  # type: ignore[assignment]
            imports[:-1]
        )
    )
    with pytest.raises(ReleaseExecutionError, match="No active user/Workspace"):
        await runner._release_scope(source)

    runner._session_factory = lambda: _ReleaseScopeSession(  # type: ignore[assignment]
        imports,
        [None, membership],
    )
    with pytest.raises(ReleaseExecutionError, match="No active user/Workspace"):
        await runner._release_scope(source)


@pytest.mark.asyncio
async def test_execute_one_routes_oracle_and_research_and_enforces_completion(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(test_settings)
    case = load_sec_tool_dataset(SOURCE_PATH).cases[0]
    scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
    oracle_id = UUID(int=10)
    research_id = UUID(int=11)
    submit_oracle = AsyncMock(return_value=oracle_id)
    submit_research = AsyncMock(return_value=research_id)
    execute_run = AsyncMock(return_value=SimpleNamespace(status=AgentRunStatus.COMPLETED))
    monkeypatch.setattr(runner, "_submit_oracle", submit_oracle)
    monkeypatch.setattr(runner, "_submit_research", submit_research)
    monkeypatch.setattr(
        runner,
        "_runtime",
        lambda strategy: SimpleNamespace(
            model="release-model", execution_service=SimpleNamespace(execute_run=execute_run)
        ),
    )

    assert (
        await runner._execute_one(
            scope=scope,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            case=case,
            strategy_id=ReleaseStrategy.A0,
            repetition=1,
            batch_id=UUID(int=20),
            profile_version="profile-v1",
        )
        == oracle_id
    )
    assert (
        await runner._execute_one(
            scope=scope,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            case=case,
            strategy_id=ReleaseStrategy.A2,
            repetition=1,
            batch_id=UUID(int=20),
            profile_version="profile-v1",
        )
        == research_id
    )

    monkeypatch.setattr(
        runner,
        "_runtime",
        lambda strategy: SimpleNamespace(model="wrong-model", execution_service=None),
    )
    with pytest.raises(ReleaseExecutionError, match="Runtime model differs"):
        await runner._execute_one(
            scope=scope,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            case=case,
            strategy_id=ReleaseStrategy.A0,
            repetition=1,
            batch_id=UUID(int=20),
            profile_version="profile-v1",
        )

    monkeypatch.setattr(
        runner,
        "_runtime",
        lambda strategy: SimpleNamespace(
            model="release-model",
            execution_service=SimpleNamespace(
                execute_run=AsyncMock(return_value=SimpleNamespace(status=AgentRunStatus.FAILED))
            ),
        ),
    )
    with pytest.raises(ReleaseExecutionError, match="ended as failed"):
        await runner._execute_one(
            scope=scope,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            case=case,
            strategy_id=ReleaseStrategy.A0,
            repetition=1,
            batch_id=UUID(int=20),
            profile_version="profile-v1",
        )


@pytest.mark.asyncio
async def test_submission_builders_preserve_the_release_scope(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(test_settings)
    case = load_sec_tool_dataset(SOURCE_PATH).cases[0]
    scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "owner")
    oracle_submit = AsyncMock(return_value=SimpleNamespace(run_id=UUID(int=30)))
    research_start = AsyncMock(return_value=SimpleNamespace(agent_run_id=UUID(int=31)))
    monkeypatch.setattr(runner, "_oracle_context", AsyncMock(return_value="locked context"))
    monkeypatch.setattr(execution_module, "ConversationApplicationService", lambda value: value)
    monkeypatch.setattr(
        execution_module, "SqlAlchemyDirectAnswerTurnTransactionFactory", lambda value: value
    )
    monkeypatch.setattr(
        execution_module,
        "ConversationSubmissionService",
        lambda *args, **kwargs: SimpleNamespace(submit=oracle_submit),
    )
    monkeypatch.setattr(
        execution_module,
        "ResearchSubmissionService",
        lambda *args, **kwargs: SimpleNamespace(start=research_start),
    )

    oracle = await runner._submit_oracle(
        scope,
        case,
        trace_id=TraceId("trace-oracle"),
        key="oracle-key",
    )
    research = await runner._submit_research(
        scope,
        KNOWLEDGE_BASE_ID,
        case,
        trace_id=TraceId("trace-research"),
        key="research-key",
    )

    assert oracle == UUID(int=30)
    assert research == UUID(int=31)
    assert oracle_submit.await_args is not None
    assert research_start.await_args is not None
    oracle_command = oracle_submit.await_args.args[1]
    research_command = research_start.await_args.args[1]
    assert "locked context" in oracle_command.question
    assert research_command.knowledge_base_ids == (KNOWLEDGE_BASE_ID,)
    assert research_command.brief.financial_scope.accession == case.expected_accessions[-1]


def test_runtime_selects_the_declared_strategy_features(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner(test_settings)
    adapters = tuple(
        SimpleNamespace(definition=SimpleNamespace(reference=f"tool-{index}")) for index in range(5)
    )
    retrieval = SimpleNamespace(
        knowledge_search_tool=SimpleNamespace(definition=SimpleNamespace(reference="knowledge")),
        finance_calculate_tool=SimpleNamespace(definition=SimpleNamespace(reference="calculate")),
        catalog="catalog",
    )
    calls: list[dict[str, Any]] = []

    def create_runtime(*args: object, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(model="release-model")

    monkeypatch.setattr(execution_module, "create_retrieval_resources", lambda *args: retrieval)
    monkeypatch.setattr(execution_module, "create_sec_filing_tools", lambda *args: adapters)
    monkeypatch.setattr(
        execution_module,
        "create_direct_answer_runtime_resources",
        create_runtime,
    )

    runner._runtime(ReleaseStrategy.A0)
    runner._runtime(ReleaseStrategy.A1)
    runner._runtime(ReleaseStrategy.A2)
    runner._runtime(ReleaseStrategy.A3)
    runner._runtime(ReleaseStrategy.A4)

    assert calls[0]["direct_policy"].profile_version == "sec-oracle-v1"
    assert calls[1]["research_verifier_enabled"] is False
    assert calls[2]["research_verifier_enabled"] is False
    assert calls[3]["research_verifier_enabled"] is True
    assert calls[4]["research_durability_enabled"] is True
    assert len(calls[1]["tool_adapters"]) == 2
    assert len(calls[2]["tool_adapters"]) == 6
    assert len(calls[4]["tool_adapters"]) == 7


def test_execution_batch_round_trip_and_cli_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    batch = _batch(_batch_bindings())
    output = tmp_path / "batch.json"
    write_execution_batch(batch, output)
    assert load_execution_batch(output) == batch

    monkeypatch.setattr(execution_module, "Settings", lambda: object())
    monkeypatch.setattr(execution_module, "execute_release_batch", lambda **kwargs: None)
    monkeypatch.setattr(asyncio, "run", lambda coroutine: batch)
    assert execution_module.main(["--output", str(output), "--live-repetitions"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document == {"ok": True, "output": str(output), "run_count": 50}


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
