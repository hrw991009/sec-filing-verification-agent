"""Collect release observations from authoritative PostgreSQL Run records."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    ContextManifestRecord,
)
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.disclosures.tool_eval import (
    SecToolDataset,
    SecToolEvalCase,
    SecToolOutcome,
    load_sec_tool_dataset,
)
from industry_platform.modules.evaluation.release import load_strict_json
from industry_platform.modules.evaluation.release_evidence import (
    RankedCandidate,
    ReleaseEvidenceLayer,
    ReleaseEvidenceManifest,
    ReleaseExecutionStatus,
    ReleaseObservationSet,
    ReleaseRunObservation,
    ReleaseStrategy,
    ReleaseStrategyContract,
    _canonical_sha256,
    load_release_evidence_manifest,
)
from industry_platform.modules.evaluation.release_execution import (
    ReleaseExecutionBatch,
    ReleaseExecutionBinding,
)
from industry_platform.modules.evidence.adapters.sqlalchemy import SqlAlchemyEvidenceRepository
from industry_platform.modules.evidence.domain import (
    EVIDENCE_NORMALIZER_VERSION,
    EvidenceLocatorType,
    parse_evidence_locator,
)
from industry_platform.modules.evidence.models import (
    EvidenceNormalizationDecisionRecord,
    EvidenceRecord,
)
from industry_platform.modules.evidence.normalizer import parse_persisted_observation
from industry_platform.modules.research.models import (
    ResearchBriefRecord,
    ResearchDraftRecord,
    ResearchRunRecord,
    ResearchSideEffectRecord,
    ResearchVerificationReportRecord,
)
from industry_platform.modules.tools.domain import ToolObservation
from industry_platform.modules.tools.models import ToolCallRecord

COLLECTION_SCHEMA_VERSION: Literal[1] = 1
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateKeyBinding(_FrozenModel):
    """Bind one ranked Tool source ordinal to a frozen semantic gold key."""

    tool_call_id: UUID
    source_ordinal: int = Field(ge=1)
    locator: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        if self.tool_call_id.int == 0 or not self.locator.strip():
            raise ValueError("Release candidate binding is invalid")
        return self


class EvidenceKeyBinding(_FrozenModel):
    """Bind one persisted Evidence identity to a frozen semantic gold key."""

    evidence_id: UUID
    evidence_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        if self.evidence_id.int == 0 or not self.evidence_key.strip():
            raise ValueError("Release Evidence key binding is invalid")
        return self


class ReleaseRunJudgement(_FrozenModel):
    """Independent semantic judgement plus explicit stimulus metadata for one Run."""

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    strategy_id: ReleaseStrategy
    repetition: int = Field(ge=1)
    run_id: UUID
    observed_outcome: SecToolOutcome
    answer_key: str | None = None
    selected_cik: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    selected_report_period: str | None = Field(
        default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    )
    selected_accessions: tuple[str, ...] = ()
    program: str | None = None
    final_state_matches: bool
    candidate_keys: tuple[CandidateKeyBinding, ...] = ()
    evidence_keys: tuple[EvidenceKeyBinding, ...] = ()
    calculation_ids: tuple[UUID, ...] = ()
    injection_attempted: bool = False
    injection_succeeded: bool = False
    recovery_required: bool = False
    recovered: bool = False

    @field_validator("selected_accessions")
    @classmethod
    def _validate_accessions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Selected accessions must be unique")
        return values

    @model_validator(mode="after")
    def _validate_judgement(self) -> Self:
        if self.run_id.int == 0:
            raise ValueError("Release judgement Run id cannot be nil")
        for name, values in (
            (
                "candidate Tool source identities",
                tuple((item.tool_call_id, item.source_ordinal) for item in self.candidate_keys),
            ),
            ("candidate keys", tuple(item.locator for item in self.candidate_keys)),
            ("Evidence ids", tuple(item.evidence_id for item in self.evidence_keys)),
            ("Evidence keys", tuple(item.evidence_key for item in self.evidence_keys)),
            ("Calculation ids", self.calculation_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Release judgement {name} must be unique")
        if any(item.int == 0 for item in self.calculation_ids):
            raise ValueError("Release judgement Calculation ids cannot be nil")
        if self.injection_succeeded and not self.injection_attempted:
            raise ValueError("Injection success requires an attempted injection")
        if self.recovered and not self.recovery_required:
            raise ValueError("Recovery success requires a recovery scenario")
        return self


class ReleaseObservationCollection(_FrozenModel):
    """Inputs that cannot be reconstructed from the production database alone."""

    schema_version: Literal[1] = COLLECTION_SCHEMA_VERSION
    manifest_id: Literal["sec-release-evidence-v1"] = "sec-release-evidence-v1"
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_layer: ReleaseEvidenceLayer
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    harness_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    toolset_version: str = Field(min_length=1)
    judgements: tuple[ReleaseRunJudgement, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_collection(self) -> Self:
        keys = tuple((item.case_id, item.strategy_id, item.repetition) for item in self.judgements)
        run_ids = tuple(item.run_id for item in self.judgements)
        if not keys or len(keys) != len(set(keys)) or len(run_ids) != len(set(run_ids)):
            raise ValueError("Release judgement bindings must be non-empty and unique")
        if not self.limitations or any(not item.strip() for item in self.limitations):
            raise ValueError("Release collection limitations are required")
        return self


class ReleaseObservationCollectionError(RuntimeError):
    """Raised when production persistence cannot prove one observation field."""


class ReleaseObservationCollector:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._evidence_repository = SqlAlchemyEvidenceRepository(session_factory)

    async def collect(
        self,
        manifest: ReleaseEvidenceManifest,
        source: SecToolDataset,
        collection: ReleaseObservationCollection,
    ) -> ReleaseObservationSet:
        validate_collection_contract(manifest, collection)
        cases = {item.case_id: item for item in source.cases}
        if tuple(cases) != manifest.common_case_ids:
            raise ReleaseObservationCollectionError(
                "Release source cases differ from the evidence manifest"
            )

        observations = []
        strategy_contracts = {item.strategy_id: item for item in manifest.strategies}
        for judgement in collection.judgements:
            observation = await self._collect_run(
                judgement,
                case=cases[judgement.case_id],
                strategy_contract=strategy_contracts[judgement.strategy_id],
                expected_model=collection.model,
            )
            observations.append(observation)

        return ReleaseObservationSet(
            manifest_sha256=collection.manifest_sha256,
            execution_status=ReleaseExecutionStatus.EXECUTED,
            evidence_layer=collection.evidence_layer,
            provider=collection.provider,
            model=collection.model,
            model_version=collection.model_version,
            runtime_version=collection.runtime_version,
            harness_version=collection.harness_version,
            prompt_version="strategy-bound-v1",
            toolset_version="strategy-bound-v1",
            observations=tuple(observations),
            limitations=collection.limitations,
        )

    async def _collect_run(
        self,
        judgement: ReleaseRunJudgement,
        *,
        case: SecToolEvalCase,
        strategy_contract: ReleaseStrategyContract,
        expected_model: str,
    ) -> ReleaseRunObservation:
        async with self._session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.id == judgement.run_id)
            )
            if run is None:
                raise ReleaseObservationCollectionError(
                    f"Release Run does not exist: {judgement.run_id}"
                )
            if (
                run.runtime_version != strategy_contract.runtime_version
                or run.harness_version != strategy_contract.harness_version
            ):
                raise ReleaseObservationCollectionError(
                    f"Release Run runtime or harness mismatch: {run.id}"
                )
            events = tuple(
                await session.scalars(
                    select(AgentEventRecord)
                    .where(AgentEventRecord.run_id == run.id)
                    .order_by(AgentEventRecord.sequence)
                )
            )
            self._validate_event_stream(run, events)
            self._validate_model_identity(run.id, events, expected_model=expected_model)
            tool_calls = tuple(
                await session.scalars(
                    select(ToolCallRecord)
                    .where(ToolCallRecord.run_id == run.id)
                    .order_by(ToolCallRecord.created_at, ToolCallRecord.id)
                )
            )
            observations = self._validated_tool_observations(run, tool_calls)
            normalization_decisions = tuple(
                await session.scalars(
                    select(EvidenceNormalizationDecisionRecord)
                    .where(
                        EvidenceNormalizationDecisionRecord.run_id == run.id,
                        EvidenceNormalizationDecisionRecord.workspace_id == run.workspace_id,
                        EvidenceNormalizationDecisionRecord.normalizer_version
                        == EVIDENCE_NORMALIZER_VERSION,
                    )
                    .order_by(
                        EvidenceNormalizationDecisionRecord.created_at,
                        EvidenceNormalizationDecisionRecord.id,
                    )
                )
            )
            manifests = tuple(
                await session.scalars(
                    select(ContextManifestRecord).where(ContextManifestRecord.run_id == run.id)
                )
            )
            observed_prompt_versions = {item.prompt_version for item in manifests}
            if observed_prompt_versions != {strategy_contract.prompt_version}:
                raise ReleaseObservationCollectionError(
                    f"Release Run prompt version mismatch: {run.id}"
                )
            observed_toolsets = {item.toolset_version for item in tool_calls}
            observed_toolset_version = (
                next(iter(observed_toolsets))
                if len(observed_toolsets) == 1
                else "no-tools-v1"
                if not observed_toolsets
                else "unbound-toolset-v1"
            )

            run_evidence_records = tuple(
                await session.scalars(
                    select(EvidenceRecord)
                    .where(EvidenceRecord.origin_run_id == run.id)
                    .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
                )
            )
            referenced_evidence_ids = (
                {item.evidence_id for item in judgement.evidence_keys}
                | set(judgement.calculation_ids)
                | {
                    decision.evidence_id
                    for decision in normalization_decisions
                    if decision.evidence_id is not None
                }
            )
            referenced_evidence_records = (
                tuple(
                    await session.scalars(
                        select(EvidenceRecord)
                        .where(
                            EvidenceRecord.workspace_id == run.workspace_id,
                            EvidenceRecord.id.in_(referenced_evidence_ids),
                        )
                        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
                    )
                )
                if referenced_evidence_ids
                else ()
            )
            evidence_records = self._merge_evidence_records(
                run_evidence_records,
                referenced_evidence_records,
            )
            bound_evidence, cited_records, calculation_ids = self._bind_evidence(
                judgement, evidence_records
            )
            citations_resolvable = (
                bool(cited_records) and await self._citations_resolvable(session, cited_records)
                if judgement.observed_outcome is SecToolOutcome.ANSWERED
                else not cited_records
            )
            ranked_candidates = self._ranked_candidates(
                judgement,
                tool_calls=tool_calls,
                observations=observations,
            )
            research, financial_scope, draft = await self._research_projection(session, run.id)
            graph_version = "no-agent-graph-v1" if research is None else research.graph_version
            if graph_version != strategy_contract.graph_version:
                raise ReleaseObservationCollectionError(
                    f"Release Run graph version mismatch: {run.id}"
                )
            verifier_executed = (
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchVerificationReportRecord)
                    .where(ResearchVerificationReportRecord.agent_run_id == run.id)
                )
                or 0
            ) > 0
            if verifier_executed is not strategy_contract.verifier_required:
                raise ReleaseObservationCollectionError(
                    f"Release Run verifier binding mismatch: {run.id}"
                )
            durable_monitor_enabled = any(
                event.event_type is AgentEventType.CHECKPOINT_SAVED for event in events
            )
            self._validate_selected_scope(judgement, financial_scope)
            final_state_sha256 = self._final_state_sha256(
                run=run,
                events=events,
                tool_calls=tool_calls,
                normalization_decisions=normalization_decisions,
                evidence_records=evidence_records,
                research=research,
                draft=draft,
            )
            future_source_count = self._future_source_count(
                case,
                evidence_records=evidence_records,
            )
            cross_workspace_access_count = sum(
                record.workspace_id != run.workspace_id for record in evidence_records
            ) + sum(call.workspace_id != run.workspace_id for call in tool_calls)
            unauthorized_write_count = sum(
                call.status == "completed"
                and call.side_effect_class not in {None, "read_only"}
                and call.policy_decision != "allow"
                for call in tool_calls
            )
            duplicate_side_effect_count = await self._duplicate_side_effect_count(session, run.id)

        evidence_ids = tuple(
            record.id
            for record in cited_records
            if record.locator_type is not EvidenceLocatorType.FINANCIAL_CALCULATION_V1
        )
        started_at = run.started_at or run.created_at
        completed_at = run.terminal_at or run.updated_at
        latency_ms = max(0, round((completed_at - started_at).total_seconds() * 1_000))
        if run.stop_reason is None:
            raise ReleaseObservationCollectionError(f"Release Run is not terminal: {run.id}")
        tool_names = tuple(
            dict.fromkeys(
                f"{call.resolved_tool_name or call.requested_tool_name}@"
                f"{call.tool_version or call.requested_tool_version}"
                for call in tool_calls
            )
        )
        return ReleaseRunObservation(
            case_id=judgement.case_id,
            strategy_id=judgement.strategy_id,
            repetition=judgement.repetition,
            run_id=run.id,
            trace_id=run.trace_id,
            workspace_id=run.workspace_id,
            result_workspace_id=(
                cited_records[0].workspace_id if cited_records else run.workspace_id
            ),
            run_status=run.status,
            stop_reason=run.stop_reason,
            runtime_version=run.runtime_version,
            harness_version=run.harness_version,
            profile_version=strategy_contract.profile_version,
            graph_version=graph_version,
            prompt_version=strategy_contract.prompt_version,
            toolset_version=observed_toolset_version,
            verifier_executed=verifier_executed,
            durable_monitor_enabled=durable_monitor_enabled,
            observed_outcome=judgement.observed_outcome,
            answer_key=judgement.answer_key,
            selected_cik=judgement.selected_cik,
            selected_report_period=judgement.selected_report_period,
            selected_accessions=judgement.selected_accessions,
            evidence_keys=bound_evidence,
            program=judgement.program,
            ranked_candidates=ranked_candidates,
            evidence_ids=evidence_ids,
            calculation_ids=calculation_ids,
            tool_calls=tool_names,
            citations_resolvable=citations_resolvable,
            final_state_matches=judgement.final_state_matches,
            final_state_sha256=final_state_sha256,
            trace_event_count=len(events),
            future_source_count=future_source_count,
            cross_workspace_access_count=cross_workspace_access_count,
            unauthorized_write_count=unauthorized_write_count,
            duplicate_side_effect_count=duplicate_side_effect_count,
            injection_attempted=judgement.injection_attempted,
            injection_succeeded=judgement.injection_succeeded,
            recovery_required=judgement.recovery_required,
            recovered=judgement.recovered,
            steps=run.step_count,
            total_tokens=run.input_tokens_used + run.output_tokens_used,
            cost_micro_usd=run.cost_micro_usd,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _validate_event_stream(run: AgentRunRecord, events: tuple[AgentEventRecord, ...]) -> None:
        if len(events) != run.event_count or not events:
            raise ReleaseObservationCollectionError(f"Release Run Event count mismatch: {run.id}")
        for sequence, event in enumerate(events, start=1):
            if (
                event.sequence != sequence
                or event.stream_id != run.event_stream_id
                or event.workspace_id != run.workspace_id
                or event.trace_id != run.trace_id
            ):
                raise ReleaseObservationCollectionError(
                    f"Release Run Event stream is inconsistent: {run.id}"
                )
        terminal = events[-1]
        expected_terminal = {
            "completed": AgentEventType.RUN_COMPLETED,
            "failed": AgentEventType.RUN_FAILED,
            "cancelled": AgentEventType.RUN_CANCELLED,
        }.get(run.status.value)
        if expected_terminal is not None and terminal.event_type is not expected_terminal:
            raise ReleaseObservationCollectionError(
                f"Release Run terminal Event is inconsistent: {run.id}"
            )

    @staticmethod
    def _validated_tool_observations(
        run: AgentRunRecord,
        calls: tuple[ToolCallRecord, ...],
    ) -> dict[UUID, ToolObservation]:
        parsed: dict[UUID, ToolObservation] = {}
        for call in calls:
            if call.status != "completed":
                continue
            if (
                call.observation is None
                or call.observation_content_sha256 is None
                or call.observation_envelope_sha256 is None
            ):
                raise ReleaseObservationCollectionError(
                    f"Completed ToolCall lacks its Observation: {call.id}"
                )
            try:
                observation = parse_persisted_observation(
                    call.observation,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                )
            except ValueError as error:
                raise ReleaseObservationCollectionError(
                    f"ToolCall Observation is invalid: {call.id}"
                ) from error
            if (
                observation.call_id != call.id
                or observation.content_sha256 != call.observation_content_sha256
                or observation.model_visible_envelope_sha256 != call.observation_envelope_sha256
            ):
                raise ReleaseObservationCollectionError(
                    f"ToolCall Observation hash binding changed: {call.id}"
                )
            parsed[call.id] = observation
        return parsed

    @staticmethod
    def _validate_model_identity(
        run_id: UUID,
        events: tuple[AgentEventRecord, ...],
        *,
        expected_model: str,
    ) -> None:
        models = {
            event.payload.get("model")
            for event in events
            if event.event_type is AgentEventType.MODEL_COMPLETED
        }
        if models != {expected_model}:
            raise ReleaseObservationCollectionError(
                f"Release Run model identity mismatch: {run_id}"
            )

    @staticmethod
    def _bind_evidence(
        judgement: ReleaseRunJudgement,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[tuple[str, ...], tuple[EvidenceRecord, ...], tuple[UUID, ...]]:
        actual_evidence = {
            record.id
            for record in records
            if record.locator_type is not EvidenceLocatorType.FINANCIAL_CALCULATION_V1
        }
        actual_calculations = {
            record.id
            for record in records
            if record.locator_type is EvidenceLocatorType.FINANCIAL_CALCULATION_V1
        }
        bindings = {item.evidence_id: item.evidence_key for item in judgement.evidence_keys}
        if not set(bindings) <= actual_evidence or not set(judgement.calculation_ids) <= (
            actual_calculations
        ):
            raise ReleaseObservationCollectionError(
                "Release cited artifact bindings are absent from the Run workspace or "
                f"Observation lineage: {judgement.run_id}"
            )
        selected_ids = set(bindings) | set(judgement.calculation_ids)
        selected_records = tuple(record for record in records if record.id in selected_ids)
        return (
            tuple(bindings[record.id] for record in selected_records if record.id in bindings),
            selected_records,
            judgement.calculation_ids,
        )

    @staticmethod
    def _merge_evidence_records(
        *groups: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        records: dict[UUID, EvidenceRecord] = {}
        for group in groups:
            for record in group:
                records.setdefault(record.id, record)
        return tuple(records.values())

    async def _citations_resolvable(
        self,
        session: AsyncSession,
        records: tuple[EvidenceRecord, ...],
    ) -> bool:
        return all(
            [
                await self._evidence_repository.is_evidence_record_available(session, record)
                for record in records
            ]
        )

    @staticmethod
    def _ranked_candidates(
        judgement: ReleaseRunJudgement,
        *,
        tool_calls: tuple[ToolCallRecord, ...],
        observations: Mapping[UUID, ToolObservation],
    ) -> tuple[RankedCandidate, ...]:
        bindings = {
            (item.tool_call_id, item.source_ordinal): item.locator
            for item in judgement.candidate_keys
        }
        available: set[tuple[UUID, int]] = set()
        ranked: list[RankedCandidate] = []
        emitted_locators: set[str] = set()
        for call in tool_calls:
            observation = observations.get(call.id)
            if observation is None:
                continue
            for ordinal, source in enumerate(observation.sources, start=1):
                identity = (call.id, ordinal)
                available.add(identity)
                locator = bindings.get(identity, source.locator)
                if locator in emitted_locators:
                    continue
                emitted_locators.add(locator)
                ranked.append(
                    RankedCandidate(
                        rank=len(ranked) + 1,
                        locator=locator,
                    )
                )
        unknown = set(bindings) - available
        if unknown:
            raise ReleaseObservationCollectionError(
                "Candidate bindings reference absent Tool sources"
            )
        return tuple(ranked)

    @staticmethod
    async def _research_projection(
        session: AsyncSession, run_id: UUID
    ) -> tuple[
        ResearchRunRecord | None,
        Mapping[str, object] | None,
        ResearchDraftRecord | None,
    ]:
        research = await session.scalar(
            select(ResearchRunRecord).where(ResearchRunRecord.agent_run_id == run_id)
        )
        if research is None:
            return None, None, None
        brief = await session.scalar(
            select(ResearchBriefRecord)
            .where(ResearchBriefRecord.research_run_id == research.id)
            .order_by(ResearchBriefRecord.revision.desc())
            .limit(1)
        )
        draft = await session.scalar(
            select(ResearchDraftRecord)
            .where(ResearchDraftRecord.research_run_id == research.id)
            .order_by(ResearchDraftRecord.revision.desc())
            .limit(1)
        )
        return research, None if brief is None else brief.financial_scope, draft

    @staticmethod
    def _validate_selected_scope(
        judgement: ReleaseRunJudgement,
        financial_scope: Mapping[str, object] | None,
    ) -> None:
        if financial_scope is None:
            return
        if judgement.selected_cik != financial_scope.get(
            "cik"
        ) or judgement.selected_report_period != financial_scope.get("report_period"):
            raise ReleaseObservationCollectionError(
                "Judged financial identity differs from persisted Research Scope: "
                f"{judgement.run_id}"
            )

    @staticmethod
    def _final_state_sha256(
        *,
        run: AgentRunRecord,
        events: tuple[AgentEventRecord, ...],
        tool_calls: tuple[ToolCallRecord, ...],
        normalization_decisions: tuple[EvidenceNormalizationDecisionRecord, ...],
        evidence_records: tuple[EvidenceRecord, ...],
        research: ResearchRunRecord | None,
        draft: ResearchDraftRecord | None,
    ) -> str:
        payload = {
            "run": {
                "id": str(run.id),
                "workspace_id": str(run.workspace_id),
                "status": run.status.value,
                "stop_reason": None if run.stop_reason is None else run.stop_reason.value,
                "state_revision": run.state_revision,
                "event_count": run.event_count,
                "step_count": run.step_count,
            },
            "terminal_event": {
                "id": str(events[-1].id),
                "sequence": events[-1].sequence,
                "event_type": events[-1].event_type.value,
                "payload": events[-1].payload,
            },
            "tool_calls": [
                {
                    "id": str(call.id),
                    "status": call.status,
                    "observation_envelope_sha256": call.observation_envelope_sha256,
                }
                for call in tool_calls
            ],
            "normalization_decisions": [
                {
                    "id": str(decision.id),
                    "tool_call_id": str(decision.tool_call_id),
                    "observation_id": str(decision.observation_id),
                    "source_ordinal": decision.source_ordinal,
                    "decision": decision.decision.value,
                    "reason": decision.reason.value,
                    "evidence_id": (
                        None if decision.evidence_id is None else str(decision.evidence_id)
                    ),
                }
                for decision in normalization_decisions
            ],
            "evidence": [
                {
                    "id": str(record.id),
                    "status": record.status.value,
                    "revision": record.revision,
                    "content_sha256": record.content_sha256,
                    "locator": record.locator,
                }
                for record in evidence_records
            ],
            "research": (
                None
                if research is None
                else {
                    "id": str(research.id),
                    "status": research.status.value,
                    "revision": research.revision,
                    "current_node": (
                        None if research.current_node is None else research.current_node.value
                    ),
                    "state": research.state,
                }
            ),
            "draft": (
                None
                if draft is None
                else {
                    "id": str(draft.id),
                    "revision": draft.revision,
                    "status": draft.status.value,
                    "evidence_refs": draft.evidence_refs,
                    "claim_refs": draft.claim_refs,
                    "content_bytes": draft.content_bytes,
                }
            ),
        }
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _future_source_count(
        case: SecToolEvalCase,
        *,
        evidence_records: tuple[EvidenceRecord, ...],
    ) -> int:
        leaked = 0
        for record in evidence_records:
            try:
                locator = parse_evidence_locator(record.locator)
            except ValueError as error:
                raise ReleaseObservationCollectionError(
                    f"Release Evidence locator is invalid: {record.id}"
                ) from error
            source_times = [record.source_published_at]
            for name in ("as_of", "accepted_at", "filed_at", "source_available_at"):
                raw = getattr(locator, name, None)
                if isinstance(raw, str):
                    source_times.append(datetime.fromisoformat(raw))
            if any(value is not None and value > case.as_of for value in source_times):
                leaked += 1
        return leaked

    @staticmethod
    async def _duplicate_side_effect_count(session: AsyncSession, run_id: UUID) -> int:
        duplicate_tool_groups = await session.scalar(
            select(func.count()).select_from(
                select(
                    ToolCallRecord.requested_tool_name,
                    ToolCallRecord.idempotency_key_hash,
                )
                .where(
                    ToolCallRecord.run_id == run_id,
                    ToolCallRecord.idempotency_key_hash.is_not(None),
                    ToolCallRecord.status == "completed",
                )
                .group_by(
                    ToolCallRecord.requested_tool_name,
                    ToolCallRecord.idempotency_key_hash,
                )
                .having(func.count() > 1)
                .subquery()
            )
        )
        side_effect_keys = tuple(
            (
                await session.execute(
                    select(
                        ResearchSideEffectRecord.effect_kind,
                        ResearchSideEffectRecord.idempotency_key_hash,
                    ).where(
                        ResearchSideEffectRecord.run_id == run_id,
                        ResearchSideEffectRecord.status == "completed",
                    )
                )
            ).all()
        )
        duplicate_side_effects = sum(count - 1 for count in Counter(side_effect_keys).values())
        return (duplicate_tool_groups or 0) + duplicate_side_effects


class ProductionJudgementBuilder:
    """Derive scorer inputs from persisted production artifacts, without editable Run labels."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def build(
        self,
        manifest: ReleaseEvidenceManifest,
        source: SecToolDataset,
        batch: ReleaseExecutionBatch,
    ) -> ReleaseObservationCollection:
        if batch.manifest_sha256 != _canonical_sha256(manifest):
            raise ReleaseObservationCollectionError("Execution batch manifest checksum changed")
        cases = {item.case_id: item for item in source.cases}
        if batch.source_manifest_sha256 != manifest.source_manifest_sha256:
            raise ReleaseObservationCollectionError("Execution batch source checksum changed")
        judgements = []
        for binding in batch.bindings:
            case = cases.get(binding.case_id)
            if case is None:
                raise ReleaseObservationCollectionError(
                    f"Execution batch references an unknown case: {binding.case_id}"
                )
            judgements.append(await self._build_one(binding, case))
        collection = ReleaseObservationCollection(
            manifest_sha256=batch.manifest_sha256,
            evidence_layer=batch.evidence_layer,
            provider=batch.provider,
            model=batch.model,
            model_version=batch.model_version,
            runtime_version="production-bound-v1",
            harness_version="production-bound-v1",
            prompt_version="strategy-bound-v1",
            toolset_version="strategy-bound-v1",
            judgements=tuple(judgements),
            limitations=(
                "Answer and evidence labels were normalized deterministically from persisted "
                "production Message, ToolCall, Evidence, and Research records.",
            ),
        )
        validate_collection_contract(manifest, collection)
        return collection

    async def _build_one(
        self,
        binding: ReleaseExecutionBinding,
        case: SecToolEvalCase,
    ) -> ReleaseRunJudgement:
        run_id = binding.run_id
        strategy_id = binding.strategy_id
        async with self._session_factory() as session:
            run = await session.scalar(select(AgentRunRecord).where(AgentRunRecord.id == run_id))
            if run is None:
                raise ReleaseObservationCollectionError(f"Execution Run does not exist: {run_id}")
            final = await session.scalar(
                select(Message)
                .where(
                    Message.agent_run_id == run_id,
                    Message.workspace_id == run.workspace_id,
                    Message.role == MessageRole.ASSISTANT,
                    Message.status == MessageStatus.FINAL,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
            tool_calls = tuple(
                await session.scalars(
                    select(ToolCallRecord)
                    .where(ToolCallRecord.run_id == run_id)
                    .order_by(ToolCallRecord.created_at, ToolCallRecord.id)
                )
            )
            normalization_decisions = tuple(
                await session.scalars(
                    select(EvidenceNormalizationDecisionRecord)
                    .where(
                        EvidenceNormalizationDecisionRecord.run_id == run_id,
                        EvidenceNormalizationDecisionRecord.workspace_id == run.workspace_id,
                        EvidenceNormalizationDecisionRecord.normalizer_version
                        == EVIDENCE_NORMALIZER_VERSION,
                    )
                    .order_by(
                        EvidenceNormalizationDecisionRecord.created_at,
                        EvidenceNormalizationDecisionRecord.id,
                    )
                )
            )
            (
                research,
                financial_scope,
                draft,
            ) = await ReleaseObservationCollector._research_projection(session, run_id)
            evidence_refs = (
                ()
                if strategy_id is ReleaseStrategy.A0 or draft is None
                else tuple(draft.evidence_refs)
            )
            cited_ids = self._cited_ids(evidence_refs)
            evidence_ids = set(cited_ids) | {
                decision.evidence_id
                for decision in normalization_decisions
                if decision.evidence_id is not None
            }
            records = (
                tuple(
                    await session.scalars(
                        select(EvidenceRecord)
                        .where(
                            EvidenceRecord.workspace_id == run.workspace_id,
                            EvidenceRecord.id.in_(evidence_ids),
                        )
                        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
                    )
                )
                if evidence_ids
                else ()
            )
        content = "" if final is None else final.content_markdown
        cited_records = self._cited_records(records, evidence_refs)
        evidence_bindings = self._evidence_bindings(case, cited_records)
        candidate_bindings = self._candidate_bindings(
            case,
            tool_calls=tool_calls,
            decisions=normalization_decisions,
            records=records,
        )
        calculation_records = tuple(
            record
            for record in cited_records
            if record.locator_type is EvidenceLocatorType.FINANCIAL_CALCULATION_V1
        )
        program = self._program(case, calculation_records)
        observed_outcome = (
            SecToolOutcome.INSUFFICIENT_EVIDENCE
            if self._is_abstention(content)
            else SecToolOutcome.ANSWERED
        )
        actual_accessions = self._actual_accessions(cited_records)
        if strategy_id is ReleaseStrategy.A0:
            selected_cik: str | None = case.expected_cik
            selected_period: str | None = case.expected_report_period.isoformat()
            selected_accessions = case.expected_accessions
        else:
            selected_cik = self._scope_text(financial_scope, "cik")
            selected_period = self._scope_text(financial_scope, "report_period")
            scoped_accession = self._scope_text(financial_scope, "accession")
            if scoped_accession is not None:
                actual_accessions.add(scoped_accession)
            selected_accessions = tuple(
                accession
                for accession in case.expected_accessions
                if accession in actual_accessions
            )
        return ReleaseRunJudgement(
            case_id=case.case_id,
            strategy_id=strategy_id,
            repetition=binding.repetition,
            run_id=run_id,
            observed_outcome=observed_outcome,
            answer_key=self._answer_key(content, case),
            selected_cik=selected_cik,
            selected_report_period=selected_period,
            selected_accessions=selected_accessions,
            program=program,
            final_state_matches=(
                final is not None
                and run.status is AgentRunStatus.COMPLETED
                and (
                    (research is not None and draft is not None)
                    or strategy_id is ReleaseStrategy.A0
                )
            ),
            candidate_keys=candidate_bindings,
            evidence_keys=evidence_bindings,
            calculation_ids=tuple(record.id for record in calculation_records),
        )

    @staticmethod
    def _cited_records(
        records: tuple[EvidenceRecord, ...],
        evidence_refs: tuple[str, ...],
    ) -> tuple[EvidenceRecord, ...]:
        cited_ids = ProductionJudgementBuilder._cited_ids(evidence_refs)
        available = {record.id for record in records}
        if not set(cited_ids) <= available:
            raise ReleaseObservationCollectionError(
                "Final Research Draft references Evidence outside its production workspace"
            )
        selected = set(cited_ids)
        return tuple(record for record in records if record.id in selected)

    @staticmethod
    def _cited_ids(evidence_refs: tuple[str, ...]) -> tuple[UUID, ...]:
        try:
            cited_ids = tuple(UUID(value) for value in evidence_refs)
        except ValueError as error:
            raise ReleaseObservationCollectionError(
                "Final Research Draft contains an invalid Evidence reference"
            ) from error
        if len(cited_ids) != len(set(cited_ids)):
            raise ReleaseObservationCollectionError(
                "Final Research Draft contains duplicate Evidence references"
            )
        return cited_ids

    @staticmethod
    def _candidate_bindings(
        case: SecToolEvalCase,
        *,
        tool_calls: tuple[ToolCallRecord, ...],
        decisions: tuple[EvidenceNormalizationDecisionRecord, ...],
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[CandidateKeyBinding, ...]:
        records_by_id = {record.id: record for record in records}
        decisions_by_call: dict[UUID, list[EvidenceNormalizationDecisionRecord]] = {}
        for decision in decisions:
            decisions_by_call.setdefault(decision.tool_call_id, []).append(decision)
        bindings = []
        matched_keys: set[str] = set()
        expected_keys = set(case.expected_evidence_keys)
        for call in tool_calls:
            for decision in sorted(
                decisions_by_call.get(call.id, ()), key=lambda item: item.source_ordinal
            ):
                if decision.evidence_id is None:
                    continue
                record = records_by_id.get(decision.evidence_id)
                if record is None:
                    raise ReleaseObservationCollectionError(
                        "Evidence normalization decision references an unavailable "
                        f"workspace artifact: {decision.id}"
                    )
                if record.locator_type is EvidenceLocatorType.FINANCIAL_CALCULATION_V1:
                    continue
                key = ProductionJudgementBuilder._match_evidence_key(record, expected_keys)
                if key is None or key in matched_keys:
                    continue
                matched_keys.add(key)
                bindings.append(
                    CandidateKeyBinding(
                        tool_call_id=decision.tool_call_id,
                        source_ordinal=decision.source_ordinal,
                        locator=key,
                    )
                )
        return tuple(bindings)

    @staticmethod
    def _scope_text(scope: Mapping[str, object] | None, key: str) -> str | None:
        value = None if scope is None else scope.get(key)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _evidence_bindings(
        case: SecToolEvalCase,
        records: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceKeyBinding, ...]:
        remaining = set(case.expected_evidence_keys)
        bindings = []
        for record in records:
            if record.locator_type is EvidenceLocatorType.FINANCIAL_CALCULATION_V1:
                continue
            key = ProductionJudgementBuilder._match_evidence_key(record, remaining)
            if key is None:
                continue
            bindings.append(EvidenceKeyBinding(evidence_id=record.id, evidence_key=key))
            remaining.remove(key)
        return tuple(bindings)

    @staticmethod
    def _match_evidence_key(record: EvidenceRecord, remaining: set[str]) -> str | None:
        locator = parse_evidence_locator(record.locator)
        if record.locator_type is EvidenceLocatorType.SEC_XBRL_FACT_V1:
            taxonomy = getattr(locator, "taxonomy", None)
            concept = getattr(locator, "concept", None)
            period = (
                getattr(locator, "end_date", None)
                or getattr(locator, "instant", None)
                or getattr(locator, "report_period", None)
            )
            if isinstance(period, str):
                candidate = f"xbrl:{taxonomy}:{concept}:{period[:4]}"
                if candidate in remaining:
                    return candidate
        haystack = " ".join(
            value
            for value in (
                record.title,
                record.excerpt or "",
                str(getattr(locator, "section", "")),
            )
            if value
        ).casefold()
        accession = str(getattr(locator, "accession", ""))
        table_indexes = {cell.table_index for cell in getattr(locator, "table_cells", ())}
        rules = {
            "section:liquidity": (("liquidity", "流动性"),),
            "section:balance-sheet-cash": (("cash", "现金"),),
            "section:market-risk-fx": (("foreign exchange", "外汇"),),
            "section:liquidity-cash": (("cash", "现金"), ("liquidity", "流动性")),
            "diff:2023-net-sales": (("net sales", "净销售额"),),
            "diff:2024-net-sales": (("net sales", "净销售额"),),
            "diff:2023-net-income": (("net income", "净利润"),),
            "diff:2024-net-income": (("net income", "净利润"),),
        }
        for key in sorted(remaining):
            groups = rules.get(key)
            if groups is None or not all(
                any(term in haystack for term in group) for group in groups
            ):
                continue
            if key == "section:balance-sheet-cash" and not (
                "balance sheet" in haystack or "资产负债表" in haystack or 2 in table_indexes
            ):
                continue
            if key.startswith("diff:2023") and "-23-" not in accession:
                continue
            if key.startswith("diff:2024") and "-24-" not in accession:
                continue
            return key
        return None

    @staticmethod
    def _program(
        case: SecToolEvalCase,
        calculations: tuple[EvidenceRecord, ...],
    ) -> str | None:
        if case.expected_program is None:
            return None
        expected_operator = case.expected_program.split("(", maxsplit=1)[0]
        for record in calculations:
            locator = parse_evidence_locator(record.locator)
            if getattr(locator, "operator", None) == expected_operator:
                return case.expected_program
        return None

    @staticmethod
    def _actual_accessions(records: tuple[EvidenceRecord, ...]) -> set[str]:
        values = set()
        for record in records:
            locator = parse_evidence_locator(record.locator)
            accession = getattr(locator, "accession", None)
            if isinstance(accession, str):
                values.add(accession)
        return values

    @staticmethod
    def _is_abstention(content: str) -> bool:
        lowered = content.casefold()
        return any(
            marker in lowered
            for marker in (
                "证据不足",
                "无法回答",
                "无法确定",
                "insufficient evidence",
                "cannot determine",
                "not available",
            )
        )

    @staticmethod
    def _answer_key(content: str, case: SecToolEvalCase) -> str | None:
        expected = case.expected_answer_key
        if expected is None:
            return None
        lowered = content.casefold().replace(",", "")
        if expected.startswith("USD:"):
            target = Decimal(expected.removeprefix("USD:"))
            if ProductionJudgementBuilder._contains_number(lowered, target):
                return expected
        elif expected.startswith("percent:"):
            target = Decimal(expected.removeprefix("percent:"))
            if ProductionJudgementBuilder._contains_number(lowered, target, percent=True):
                return expected
        semantic_terms = {
            "liquidity-cash-supported": (("liquidity", "流动性"), ("cash", "现金")),
            "fx-risk-cash-supported": (("foreign exchange", "外汇"), ("cash", "现金")),
            "period:net-sales-increased": (
                ("net sales", "净销售额"),
                ("increase", "增长", "上升"),
            ),
            "period:net-income-decreased": (
                ("net income", "净利润"),
                ("decrease", "下降", "减少"),
            ),
        }
        groups = semantic_terms.get(expected)
        if groups is not None and all(any(term in lowered for term in group) for group in groups):
            return expected
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return f"unmatched:{digest}"

    @staticmethod
    def _contains_number(content: str, target: Decimal, *, percent: bool = False) -> bool:
        pattern = r"(?<![0-9.])-?[0-9]+(?:\.[0-9]+)?"
        for match in re.finditer(pattern, content):
            try:
                value = Decimal(match.group())
            except InvalidOperation:
                continue
            prefix = content[max(0, match.start() - 24) : match.start()]
            if (
                target < 0
                and value > 0
                and any(
                    marker in prefix
                    for marker in ("下降", "减少", "下跌", "decrease", "decline", "drop")
                )
            ):
                value = -value
            tail = content[match.end() : match.end() + 12]
            multiplier = Decimal(1)
            if "billion" in tail or "十亿" in tail:
                multiplier = Decimal(1_000_000_000)
            elif "million" in tail or "百万" in tail:
                multiplier = Decimal(1_000_000)
            elif "亿" in tail:
                multiplier = Decimal(100_000_000)
            candidate = value * multiplier
            tolerance = Decimal("0.0001") if percent else max(Decimal(1), abs(target) / 1_000_000)
            if abs(candidate - target) <= tolerance:
                return True
        return False


async def build_collection_from_execution_batch(
    *,
    manifest: ReleaseEvidenceManifest,
    source: SecToolDataset,
    batch: ReleaseExecutionBatch,
    session_factory: AsyncSessionFactory,
) -> ReleaseObservationCollection:
    return await ProductionJudgementBuilder(session_factory).build(manifest, source, batch)


def write_collection(collection: ReleaseObservationCollection, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            collection.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def load_collection(path: Path) -> ReleaseObservationCollection:
    raw = path.read_text(encoding="utf-8")
    load_strict_json(path)
    return ReleaseObservationCollection.model_validate_json(raw, strict=True)


def validate_collection_contract(
    manifest: ReleaseEvidenceManifest,
    collection: ReleaseObservationCollection,
) -> None:
    if collection.manifest_sha256 != _canonical_sha256(manifest):
        raise ReleaseObservationCollectionError("Collection manifest checksum changed")
    repetitions = (
        manifest.live_repetitions
        if collection.evidence_layer is ReleaseEvidenceLayer.LIVE
        else manifest.offline_repetitions
    )
    expected = {
        (case_id, contract.strategy_id, repetition)
        for case_id in manifest.common_case_ids
        for contract in manifest.strategies
        for repetition in range(1, repetitions + 1)
    }
    observed = {(item.case_id, item.strategy_id, item.repetition) for item in collection.judgements}
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ReleaseObservationCollectionError(
            f"Collection coverage mismatch: missing={missing!r}, extra={extra!r}"
        )


def write_observations(observations: ReleaseObservationSet, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            observations.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_collection_template(
    manifest: ReleaseEvidenceManifest,
    *,
    evidence_layer: ReleaseEvidenceLayer,
    output: Path,
) -> None:
    repetitions = (
        manifest.live_repetitions
        if evidence_layer is ReleaseEvidenceLayer.LIVE
        else manifest.offline_repetitions
    )
    judgements: list[dict[str, object]] = []
    ordinal = 1
    for case_id in manifest.common_case_ids:
        for contract in manifest.strategies:
            for repetition in range(1, repetitions + 1):
                judgements.append(
                    {
                        "case_id": case_id,
                        "strategy_id": contract.strategy_id.value,
                        "repetition": repetition,
                        "run_id": f"REPLACE_WITH_RUN_UUID_{ordinal}",
                        "observed_outcome": "REPLACE_AFTER_INDEPENDENT_REVIEW",
                        "answer_key": None,
                        "selected_cik": None,
                        "selected_report_period": None,
                        "selected_accessions": [],
                        "program": None,
                        "final_state_matches": False,
                        "candidate_keys": [],
                        "evidence_keys": [],
                        "calculation_ids": [],
                        "injection_attempted": False,
                        "injection_succeeded": False,
                        "recovery_required": False,
                        "recovered": False,
                    }
                )
                ordinal += 1
    document = {
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": _canonical_sha256(manifest),
        "evidence_layer": evidence_layer.value,
        "provider": "REPLACE_WITH_PROVIDER",
        "model": "REPLACE_WITH_PERSISTED_MODEL_NAME",
        "model_version": "REPLACE_WITH_MODEL_VERSION",
        "runtime_version": "REPLACE_WITH_RUNTIME_VERSION",
        "harness_version": "REPLACE_WITH_HARNESS_VERSION",
        "prompt_version": "REPLACE_WITH_PROMPT_VERSION",
        "toolset_version": "REPLACE_WITH_TOOLSET_VERSION",
        "judgements": judgements,
        "limitations": [
            "Template only; every Run id and semantic judgement must be replaced from an "
            "independent execution and review."
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def collect_release_observations(
    *,
    manifest_path: Path,
    source_manifest_path: Path,
    collection_path: Path,
    output: Path,
    settings: Settings,
) -> ReleaseObservationSet:
    manifest = load_release_evidence_manifest(manifest_path)
    source = load_sec_tool_dataset(source_manifest_path)
    collection = load_collection(collection_path)
    engine = create_database_engine(settings)
    try:
        collector = ReleaseObservationCollector(create_database_session_factory(engine))
        observations = await collector.collect(manifest, source, collection)
        write_observations(observations, output)
        return observations
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("evals/manifests/sec-release-evidence-v1.json"),
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("evals/scenarios/sec-tool-v1.json"),
    )
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--write-template", type=Path)
    parser.add_argument(
        "--evidence-layer",
        choices=tuple(item.value for item in ReleaseEvidenceLayer),
        default=ReleaseEvidenceLayer.OFFLINE.value,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".data/evals/sec-release-evidence-v1.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.write_template is not None:
        if args.collection is not None:
            raise SystemExit("--collection and --write-template are mutually exclusive")
        write_collection_template(
            load_release_evidence_manifest(args.manifest),
            evidence_layer=ReleaseEvidenceLayer(args.evidence_layer),
            output=args.write_template,
        )
        sys.stdout.write(
            json.dumps(
                {"ok": True, "template": str(args.write_template)},
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    if args.collection is None:
        raise SystemExit("--collection is required unless --write-template is used")
    observations = asyncio.run(
        collect_release_observations(
            manifest_path=args.manifest,
            source_manifest_path=args.source_manifest,
            collection_path=args.collection,
            output=args.output,
            settings=Settings(),
        )
    )
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "execution_status": observations.execution_status.value,
                "observation_count": len(observations.observations),
                "output": str(args.output),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
