"""Transactional PostgreSQL adapter for the Evidence and Claim ledger."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.domain import AgentRunType, AgentStepStatus
from industry_platform.modules.agent_runtime.models import AgentRunRecord, AgentStepRecord
from industry_platform.modules.data_explorer.domain import (
    DataConnectionStatus,
    QueryRunStatus,
)
from industry_platform.modules.data_explorer.models import (
    DataConnectionRecord,
    QueryResultRecord,
    QueryRunRecord,
    SchemaSnapshotRecord,
)
from industry_platform.modules.disclosures.models import (
    SecFilingDocumentRecord,
    SecFilingRecord,
    SecSourceSnapshotRecord,
    SecXbrlFactRecord,
    SecXbrlSourceRecord,
    WorkspaceSecImportRecord,
)
from industry_platform.modules.disclosures.schemas import SecXbrlFactResponse
from industry_platform.modules.disclosures.tool import (
    SEC_READ_FILING_SECTION_TOOL_NAME,
    SEC_SEARCH_FILING_TOOL_NAME,
    SecGetXbrlFactsOutput,
    SecReadFilingSectionOutput,
    SecSearchFilingOutput,
)
from industry_platform.modules.evidence.domain import (
    EVIDENCE_NORMALIZER_VERSION,
    AuthorizationSnapshot,
    ClaimEvidenceInput,
    ClaimEvidenceLink,
    ClaimGraph,
    ClaimNotFoundError,
    ClaimVerificationStatus,
    CreateClaim,
    Evidence,
    EvidenceConflictError,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceNormalizationItem,
    EvidenceNormalizationResult,
    EvidenceNotFoundError,
    EvidencePersistenceError,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    IndustrySourceLocatorV1,
    InvalidateEvidence,
    NormalizeObservation,
    RelationStatus,
    ResearchClaim,
    ResearchRunNotFoundError,
    SecFilingChunkLocatorV1,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
    SqlResultLocatorV1,
    canonical_fingerprint,
    claim_coverage,
    claim_verification_status,
    parse_evidence_locator,
)
from industry_platform.modules.evidence.models import (
    ClaimEvidenceRecord,
    EvidenceNormalizationDecisionRecord,
    EvidenceRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    ResearchClaimRecord,
)
from industry_platform.modules.evidence.normalizer import (
    FINANCE_CALCULATION_SOURCE_TYPE,
    FINANCE_CALCULATION_SOURCE_VERSION,
    INDUSTRY_SOURCE_TYPE,
    KNOWLEDGE_SEC_SOURCE_TYPE,
    SEC_FILING_TEXT_SOURCE_TYPE,
    SEC_XBRL_FACT_SOURCE_TYPE,
    SQL_SOURCE_TYPE,
    SQL_SOURCE_VERSION,
    license_allows_evidence,
    parse_calculation_source_locator,
    parse_knowledge_source_locator,
    parse_persisted_observation,
    parse_sec_resource_locator,
    parse_sql_source_locator,
    referenced_sql_columns,
    schema_columns_for_table,
)
from industry_platform.modules.files.domain import FileObjectStatus
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialOperand,
    calculate_financial_result,
)
from industry_platform.modules.financial_verification.tool import FinanceCalculateOutput
from industry_platform.modules.identity.models import AuditLog, AuditOutcome
from industry_platform.modules.industry.domain import SourceKind
from industry_platform.modules.industry.models import DataSourceRecord, SourceItemRecord
from industry_platform.modules.knowledge.domain import (
    DocumentIndexKind,
    DocumentIndexStatus,
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeBaseStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentRecord,
    DocumentVersionRecord,
    KnowledgeBaseRecord,
)
from industry_platform.modules.research.domain import (
    CreateResearchRun,
    ResearchRun,
    ResearchRunStatus,
)
from industry_platform.modules.research.models import ResearchRunRecord
from industry_platform.modules.retrieval.domain import (
    KnowledgeSearchStatus,
    SecFilingFixture,
    knowledge_evidence_ref,
)
from industry_platform.modules.retrieval.fixtures import SecFixtureCatalog
from industry_platform.modules.tools.domain import ToolObservation, ToolSource
from industry_platform.modules.tools.models import ToolCallRecord
from industry_platform.modules.workspaces.domain import WorkspaceScope


def _sec_xbrl_fact_matches_records(
    fact: SecXbrlFactResponse,
    fact_record: SecXbrlFactRecord,
    source_record: SecXbrlSourceRecord,
    filing: SecFilingRecord,
) -> bool:
    return (
        fact.id == fact_record.id
        and fact.filing_id == fact_record.filing_id
        and fact.source_id == fact_record.source_id
        and fact.source_snapshot_id == source_record.filing_snapshot_id
        and fact.source_kind.value == source_record.source_kind
        and fact.cik == filing.cik
        and fact.accession == fact_record.accession
        and fact.taxonomy == fact_record.taxonomy
        and fact.concept == fact_record.concept
        and fact.value == fact_record.value
        and fact.unit == fact_record.unit
        and fact.period.kind.value == fact_record.period_kind
        and fact.period.instant == fact_record.instant
        and fact.period.start_date == fact_record.start_date
        and fact.period.end_date == fact_record.end_date
        and fact.filed_date == fact_record.filed_date
        and fact.form.value == fact_record.form
        and fact.context_id == fact_record.raw_context_id
        and fact.dimensions == fact_record.dimensions
        and fact.decimals == fact_record.decimals
        and fact.scale == fact_record.scale
        and fact.format == fact_record.format
        and fact.is_custom == fact_record.is_custom
        and fact.source_url == source_record.source_url
        and fact.source_version == source_record.source_version
        and fact.source_content_sha256 == source_record.content_sha256.hex()
        and fact.source_available_at == source_record.source_available_at
        and fact.retrieved_at == source_record.retrieved_at
    )


class SqlAlchemyEvidenceRepository:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        fixture_catalog: SecFixtureCatalog | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fixture_catalog = fixture_catalog

    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
        *,
        authorization: AuthorizationSnapshot,
    ) -> EvidenceNormalizationResult:
        try:
            async with self._session_factory() as session, session.begin():
                call = await session.scalar(
                    select(ToolCallRecord)
                    .where(
                        ToolCallRecord.id == command.tool_call_id,
                        ToolCallRecord.workspace_id == scope.workspace_id,
                        ToolCallRecord.status == "completed",
                    )
                    .with_for_update()
                )
                if call is None or call.observation is None or call.execution_step_id is None:
                    raise EvidenceNotFoundError
                try:
                    observation = parse_persisted_observation(
                        call.observation,
                        run_id=call.run_id,
                        workspace_id=call.workspace_id,
                    )
                except ValueError as error:
                    raise EvidencePersistenceError from error
                if (
                    observation.call_id != call.id
                    or observation.observation_id != command.observation_id
                    or observation.schema_version != call.observation_schema_version
                    or observation.content_sha256 != call.observation_content_sha256
                    or observation.model_visible_envelope_sha256 != call.observation_envelope_sha256
                    or observation.tool.name != call.resolved_tool_name
                    or observation.tool.version != call.tool_version
                ):
                    raise EvidencePersistenceError

                existing = (
                    (
                        await session.execute(
                            select(EvidenceNormalizationDecisionRecord)
                            .where(
                                EvidenceNormalizationDecisionRecord.workspace_id
                                == scope.workspace_id,
                                EvidenceNormalizationDecisionRecord.tool_call_id == call.id,
                                EvidenceNormalizationDecisionRecord.observation_id
                                == observation.observation_id,
                                EvidenceNormalizationDecisionRecord.normalizer_version
                                == EVIDENCE_NORMALIZER_VERSION,
                            )
                            .order_by(EvidenceNormalizationDecisionRecord.source_ordinal)
                        )
                    )
                    .scalars()
                    .all()
                )
                if existing:
                    if len(existing) != len(observation.sources):
                        raise EvidencePersistenceError
                    return await self._decision_result(session, observation, existing)

                decisions: list[EvidenceNormalizationDecisionRecord] = []
                for ordinal, source in enumerate(observation.sources, start=1):
                    evidence, reason = await self._normalize_source(
                        session,
                        scope,
                        call,
                        observation,
                        source,
                        ordinal=ordinal,
                        authorization=authorization,
                    )
                    decision = EvidenceNormalizationDecisionRecord(
                        id=uuid4(),
                        workspace_id=scope.workspace_id,
                        run_id=call.run_id,
                        tool_call_id=call.id,
                        observation_id=observation.observation_id,
                        source_ordinal=ordinal,
                        normalizer_version=EVIDENCE_NORMALIZER_VERSION,
                        decision=(
                            EvidenceDecision.ACCEPTED
                            if evidence is not None
                            else EvidenceDecision.REJECTED
                        ),
                        reason=reason,
                        evidence_id=None if evidence is None else evidence.id,
                        created_at=authorization.captured_at,
                    )
                    session.add(decision)
                    decisions.append(decision)
                await session.flush()
                self._audit(
                    session,
                    scope,
                    action="evidence.normalize",
                    resource_id=observation.observation_id,
                    trace_id=str(command.trace_id),
                    now=authorization.captured_at,
                    metadata={
                        "accepted_count": sum(
                            item.decision is EvidenceDecision.ACCEPTED for item in decisions
                        ),
                        "normalizer_version": EVIDENCE_NORMALIZER_VERSION,
                        "source_count": len(decisions),
                        "tool_call_id": str(call.id),
                    },
                )
                return await self._decision_result(session, observation, decisions)
        except (EvidenceNotFoundError, EvidencePersistenceError):
            raise
        except IntegrityError as error:
            raise EvidenceConflictError from error
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def list_evidence(
        self,
        scope: WorkspaceScope,
        *,
        status: EvidenceStatus | None,
        kind: EvidenceKind | None,
        origin_run_id: UUID | None,
        limit: int,
    ) -> tuple[Evidence, ...]:
        try:
            async with self._session_factory() as session:
                statement = select(EvidenceRecord).where(
                    EvidenceRecord.workspace_id == scope.workspace_id
                )
                if status is not None:
                    statement = statement.where(EvidenceRecord.status == status)
                if kind is not None:
                    statement = statement.where(EvidenceRecord.kind == kind)
                if origin_run_id is not None:
                    statement = statement.where(EvidenceRecord.origin_run_id == origin_run_id)
                records = (
                    (
                        await session.execute(
                            statement.order_by(EvidenceRecord.created_at.desc()).limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                visible: list[Evidence] = []
                for record in records:
                    if record.status is not EvidenceStatus.ACTIVE or await self._is_available(
                        session, record
                    ):
                        visible.append(self._evidence_snapshot(record))
                return tuple(visible)
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def get_evidence(self, scope: WorkspaceScope, evidence_id: UUID) -> Evidence:
        try:
            async with self._session_factory() as session:
                record = await self._evidence_record(session, scope, evidence_id)
                if record.status is EvidenceStatus.ACTIVE and not await self._is_available(
                    session, record
                ):
                    raise EvidenceNotFoundError
                return self._evidence_snapshot(record)
        except EvidenceNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def invalidate_evidence(
        self,
        scope: WorkspaceScope,
        command: InvalidateEvidence,
        *,
        invalidated_at: datetime,
    ) -> Evidence:
        try:
            async with self._session_factory() as session, session.begin():
                record = await self._evidence_record(
                    session,
                    scope,
                    command.evidence_id,
                    lock=True,
                )
                if record.revision != command.expected_revision:
                    raise EvidenceConflictError
                if record.status is not EvidenceStatus.ACTIVE:
                    raise EvidenceConflictError
                record.status = command.status
                record.excerpt = None
                record.invalidated_at = invalidated_at
                record.invalidation_reason = command.reason
                record.revision += 1
                record.updated_at = invalidated_at
                await self._invalidate_claim_relations(
                    session,
                    scope,
                    record,
                    invalidated_at=invalidated_at,
                )
                self._audit(
                    session,
                    scope,
                    action="evidence.invalidate",
                    resource_id=record.id,
                    trace_id=str(command.trace_id),
                    now=invalidated_at,
                    metadata={
                        "revision": record.revision,
                        "status": record.status.value,
                    },
                )
                await session.flush()
                return self._evidence_snapshot(record)
        except (EvidenceConflictError, EvidenceNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def create_research_run(
        self,
        scope: WorkspaceScope,
        command: CreateResearchRun,
        *,
        created_at: datetime,
    ) -> ResearchRun:
        try:
            async with self._session_factory() as session, session.begin():
                agent_run = await session.scalar(
                    select(AgentRunRecord).where(
                        AgentRunRecord.id == command.agent_run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                        AgentRunRecord.user_id == scope.user_id,
                        AgentRunRecord.run_type == AgentRunType.RESEARCH,
                    )
                )
                if agent_run is None:
                    raise ResearchRunNotFoundError
                existing = await session.scalar(
                    select(ResearchRunRecord).where(ResearchRunRecord.agent_run_id == agent_run.id)
                )
                if existing is not None:
                    return self._research_run_snapshot(existing)
                record = ResearchRunRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    owner_user_id=scope.user_id,
                    agent_run_id=agent_run.id,
                    status=ResearchRunStatus.DRAFT,
                    revision=1,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(record)
                self._audit(
                    session,
                    scope,
                    action="research_run.create",
                    resource_id=record.id,
                    trace_id=str(command.trace_id),
                    now=created_at,
                    metadata={"agent_run_id": str(agent_run.id)},
                )
                await session.flush()
                return self._research_run_snapshot(record)
        except ResearchRunNotFoundError:
            raise
        except IntegrityError as error:
            raise EvidenceConflictError from error
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def list_research_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[ResearchRun, ...]:
        try:
            async with self._session_factory() as session:
                records = (
                    (
                        await session.execute(
                            select(ResearchRunRecord)
                            .where(ResearchRunRecord.workspace_id == scope.workspace_id)
                            .order_by(ResearchRunRecord.created_at.desc())
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                return tuple(self._research_run_snapshot(record) for record in records)
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime,
    ) -> ResearchClaim:
        try:
            async with self._session_factory() as session, session.begin():
                research_run = await session.scalar(
                    select(ResearchRunRecord)
                    .where(
                        ResearchRunRecord.id == command.research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                        ResearchRunRecord.agent_run_id == command.origin_run_id,
                    )
                    .with_for_update()
                )
                if research_run is None:
                    raise ResearchRunNotFoundError
                origin_step = await session.scalar(
                    select(AgentStepRecord).where(
                        AgentStepRecord.id == command.origin_step_id,
                        AgentStepRecord.run_id == command.origin_run_id,
                        AgentStepRecord.workspace_id == scope.workspace_id,
                        AgentStepRecord.status == AgentStepStatus.COMPLETED,
                    )
                )
                if origin_step is None:
                    raise ResearchRunNotFoundError

                evidence_by_id: dict[UUID, EvidenceRecord] = {}
                if command.relations:
                    records = (
                        (
                            await session.execute(
                                select(EvidenceRecord).where(
                                    EvidenceRecord.workspace_id == scope.workspace_id,
                                    EvidenceRecord.id.in_(
                                        item.evidence_id for item in command.relations
                                    ),
                                    EvidenceRecord.status == EvidenceStatus.ACTIVE,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    evidence_by_id = {record.id: record for record in records}
                    if len(evidence_by_id) != len(command.relations):
                        raise EvidenceNotFoundError
                    for record in records:
                        if not await self._is_available(session, record):
                            raise EvidenceNotFoundError

                verification = claim_verification_status(command.relations)
                claim = ResearchClaimRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    research_run_id=research_run.id,
                    statement=command.statement,
                    confidence=command.confidence,
                    verification_status=verification,
                    coverage=claim_coverage(command.relations),
                    conflict=verification is ClaimVerificationStatus.CONFLICTED,
                    revision=1,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(claim)
                claim_node = GraphNodeRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    research_run_id=research_run.id,
                    node_type=GraphNodeType.CLAIM,
                    resource_id=claim.id,
                    label=claim.statement[:500],
                    status=RelationStatus.ACTIVE,
                    created_at=created_at,
                )
                session.add(claim_node)
                await session.flush((claim, claim_node))
                for ordinal, requested in enumerate(command.relations, start=1):
                    evidence = evidence_by_id[requested.evidence_id]
                    session.add(
                        ClaimEvidenceRecord(
                            claim_id=claim.id,
                            evidence_id=evidence.id,
                            workspace_id=scope.workspace_id,
                            relation=requested.relation,
                            relation_version=1,
                            status=RelationStatus.ACTIVE,
                            ordinal=ordinal,
                            origin_run_id=command.origin_run_id,
                            origin_step_id=command.origin_step_id,
                            created_at=created_at,
                        )
                    )
                    evidence_node = await session.scalar(
                        select(GraphNodeRecord).where(
                            GraphNodeRecord.workspace_id == scope.workspace_id,
                            GraphNodeRecord.research_run_id == research_run.id,
                            GraphNodeRecord.node_type == GraphNodeType.EVIDENCE,
                            GraphNodeRecord.resource_id == evidence.id,
                        )
                    )
                    if evidence_node is None:
                        evidence_node = GraphNodeRecord(
                            id=uuid4(),
                            workspace_id=scope.workspace_id,
                            research_run_id=research_run.id,
                            node_type=GraphNodeType.EVIDENCE,
                            resource_id=evidence.id,
                            label=evidence.title[:500],
                            status=RelationStatus.ACTIVE,
                            created_at=created_at,
                        )
                        session.add(evidence_node)
                        await session.flush((evidence_node,))
                    session.add(
                        GraphEdgeRecord(
                            id=uuid4(),
                            workspace_id=scope.workspace_id,
                            research_run_id=research_run.id,
                            source_node_id=claim_node.id,
                            target_node_id=evidence_node.id,
                            relation=requested.relation,
                            status=RelationStatus.ACTIVE,
                            created_at=created_at,
                        )
                    )
                research_run.status = ResearchRunStatus.ACTIVE
                research_run.revision += 1
                research_run.updated_at = created_at
                self._audit(
                    session,
                    scope,
                    action="claim.create",
                    resource_id=claim.id,
                    trace_id=str(command.trace_id),
                    now=created_at,
                    metadata={
                        "evidence_count": len(command.relations),
                        "research_run_id": str(research_run.id),
                        "verification_status": verification.value,
                    },
                )
                await session.flush()
                return await self._claim_snapshot(session, claim)
        except (EvidenceNotFoundError, ResearchRunNotFoundError):
            raise
        except IntegrityError as error:
            raise EvidenceConflictError from error
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def list_claims(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        limit: int,
    ) -> tuple[ResearchClaim, ...]:
        try:
            async with self._session_factory() as session:
                await self._research_run_record(session, scope, research_run_id)
                records = (
                    (
                        await session.execute(
                            select(ResearchClaimRecord)
                            .where(
                                ResearchClaimRecord.workspace_id == scope.workspace_id,
                                ResearchClaimRecord.research_run_id == research_run_id,
                            )
                            .order_by(ResearchClaimRecord.created_at.desc())
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                return tuple([await self._claim_snapshot(session, record) for record in records])
        except ResearchRunNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def get_claim(self, scope: WorkspaceScope, claim_id: UUID) -> ResearchClaim:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(ResearchClaimRecord).where(
                        ResearchClaimRecord.id == claim_id,
                        ResearchClaimRecord.workspace_id == scope.workspace_id,
                    )
                )
                if record is None:
                    raise ClaimNotFoundError
                return await self._claim_snapshot(session, record)
        except ClaimNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def get_claim_graph(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ClaimGraph:
        try:
            async with self._session_factory() as session:
                await self._research_run_record(session, scope, research_run_id)
                nodes = (
                    (
                        await session.execute(
                            select(GraphNodeRecord)
                            .where(
                                GraphNodeRecord.workspace_id == scope.workspace_id,
                                GraphNodeRecord.research_run_id == research_run_id,
                            )
                            .order_by(GraphNodeRecord.created_at, GraphNodeRecord.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                edges = (
                    (
                        await session.execute(
                            select(GraphEdgeRecord)
                            .where(
                                GraphEdgeRecord.workspace_id == scope.workspace_id,
                                GraphEdgeRecord.research_run_id == research_run_id,
                            )
                            .order_by(GraphEdgeRecord.created_at, GraphEdgeRecord.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                return ClaimGraph(
                    research_run_id=research_run_id,
                    nodes=tuple(
                        GraphNode(
                            node_id=node.id,
                            node_type=node.node_type,
                            resource_id=node.resource_id,
                            label=node.label,
                            status=node.status,
                        )
                        for node in nodes
                    ),
                    edges=tuple(
                        GraphEdge(
                            edge_id=edge.id,
                            source_node_id=edge.source_node_id,
                            target_node_id=edge.target_node_id,
                            relation=edge.relation,
                            status=edge.status,
                        )
                        for edge in edges
                    ),
                )
        except ResearchRunNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise EvidencePersistenceError(sqlstate=safe_sqlstate(error)) from error

    async def _normalize_source(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        if source.source_type == INDUSTRY_SOURCE_TYPE:
            return await self._normalize_industry_source(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        if source.source_type == SQL_SOURCE_TYPE:
            return await self._normalize_sql_source(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        if source.source_type == KNOWLEDGE_SEC_SOURCE_TYPE:
            return await self._normalize_knowledge_source(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        if source.source_type == SEC_FILING_TEXT_SOURCE_TYPE:
            return await self._normalize_sec_filing_text(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        if source.source_type == SEC_XBRL_FACT_SOURCE_TYPE:
            return await self._normalize_sec_xbrl_fact(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        if source.source_type == FINANCE_CALCULATION_SOURCE_TYPE:
            return await self._normalize_calculation_source(
                session,
                scope,
                call,
                observation,
                source,
                ordinal=ordinal,
                authorization=authorization,
            )
        return None, EvidenceDecisionReason.UNSUPPORTED_SOURCE

    async def _normalize_sec_filing_text(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        try:
            chunk_id = parse_sec_resource_locator(source.locator, resource="filing-chunks")
            if call.resolved_tool_name == SEC_SEARCH_FILING_TOOL_NAME:
                search_output = SecSearchFilingOutput.model_validate_json(
                    observation.model_text, strict=True
                )
                hit = next(item for item in search_output.hits if item.chunk_id == chunk_id)
                financial_scope_payload = search_output.financial_scope
                retrieval_profile_version = search_output.retrieval_profile_version
                retrieval_channels = tuple(hit.retrieval_channels)
                expected_content_hash = hit.content_sha256
                expected_source_version = hit.source_version
            elif call.resolved_tool_name == SEC_READ_FILING_SECTION_TOOL_NAME:
                read_output = SecReadFilingSectionOutput.model_validate_json(
                    observation.model_text, strict=True
                )
                if read_output.chunk_id != chunk_id:
                    raise ValueError
                financial_scope_payload = read_output.financial_scope
                retrieval_profile_version = "direct-read-v1"
                retrieval_channels = ()
                expected_content_hash = read_output.content_sha256
                expected_source_version = read_output.source_version
            else:
                raise ValueError
            if financial_scope_payload is None:
                raise ValueError
            financial_scope = financial_scope_payload.to_domain()
        except (StopIteration, ValueError):
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        if (
            source.content_sha256 != expected_content_hash
            or source.source_version != expected_source_version
        ):
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        vector_record = aliased(DocumentIndexRecord)
        lexical_record = aliased(DocumentIndexRecord)
        row = (
            await session.execute(
                select(
                    DocumentChunkRecord,
                    DocumentVersionRecord,
                    DocumentRecord,
                    vector_record,
                    WorkspaceSecImportRecord,
                    SecFilingRecord,
                    SecSourceSnapshotRecord,
                )
                .join(
                    DocumentVersionRecord,
                    and_(
                        DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                        DocumentVersionRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentRecord,
                    and_(
                        DocumentRecord.id == DocumentChunkRecord.document_id,
                        DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    KnowledgeBaseRecord,
                    and_(
                        KnowledgeBaseRecord.id == DocumentVersionRecord.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == DocumentVersionRecord.workspace_id,
                    ),
                )
                .join(
                    WorkspaceSecImportRecord,
                    and_(
                        WorkspaceSecImportRecord.workspace_id == DocumentChunkRecord.workspace_id,
                        WorkspaceSecImportRecord.document_version_id
                        == DocumentChunkRecord.document_version_id,
                    ),
                )
                .join(SecFilingRecord, SecFilingRecord.id == WorkspaceSecImportRecord.filing_id)
                .join(
                    SecSourceSnapshotRecord,
                    SecSourceSnapshotRecord.id == WorkspaceSecImportRecord.primary_snapshot_id,
                )
                .join(
                    SecFilingDocumentRecord,
                    and_(
                        SecFilingDocumentRecord.id == SecSourceSnapshotRecord.filing_document_id,
                        SecFilingDocumentRecord.current_snapshot_id == SecSourceSnapshotRecord.id,
                    ),
                )
                .join(
                    vector_record,
                    and_(
                        vector_record.chunk_id == DocumentChunkRecord.id,
                        vector_record.document_version_id
                        == DocumentChunkRecord.document_version_id,
                        vector_record.workspace_id == DocumentChunkRecord.workspace_id,
                        vector_record.kind == DocumentIndexKind.VECTOR,
                        vector_record.status == DocumentIndexStatus.SUCCEEDED,
                    ),
                )
                .join(
                    lexical_record,
                    and_(
                        lexical_record.chunk_id == DocumentChunkRecord.id,
                        lexical_record.document_version_id
                        == DocumentChunkRecord.document_version_id,
                        lexical_record.workspace_id == DocumentChunkRecord.workspace_id,
                        lexical_record.kind == DocumentIndexKind.LEXICAL,
                        lexical_record.status == DocumentIndexStatus.SUCCEEDED,
                        lexical_record.index_version == vector_record.index_version,
                    ),
                )
                .where(
                    DocumentChunkRecord.id == chunk_id,
                    DocumentChunkRecord.workspace_id == scope.workspace_id,
                    DocumentChunkRecord.content_hash == bytes.fromhex(source.content_sha256),
                    DocumentVersionRecord.status == DocumentVersionStatus.READY,
                    DocumentRecord.status == DocumentStatus.ACTIVE,
                    DocumentRecord.active_version_id == DocumentVersionRecord.id,
                    KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    WorkspaceSecImportRecord.accession == financial_scope.accession,
                    SecFilingRecord.cik == financial_scope.cik,
                    SecFilingRecord.form == financial_scope.form.value,
                    SecFilingRecord.report_date == financial_scope.report_period,
                    SecFilingRecord.public_available_at <= financial_scope.as_of,
                    SecSourceSnapshotRecord.status == "active",
                    SecSourceSnapshotRecord.source_version == source.source_version,
                    SecSourceSnapshotRecord.source_available_at <= financial_scope.as_of,
                    (
                        (SecSourceSnapshotRecord.valid_to.is_(None))
                        | (SecSourceSnapshotRecord.valid_to >= financial_scope.as_of)
                    ),
                )
            )
        ).one_or_none()
        if row is None:
            return None, EvidenceDecisionReason.RESOURCE_UNAUTHORIZED
        chunk, version, document, vector, _imported, filing, snapshot = row
        locator = SecFilingTextLocatorV1(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            report_period=filing.report_date.isoformat(),
            as_of=financial_scope.as_of.isoformat(),
            filed_at=datetime.combine(
                filing.filed_date, datetime.min.time(), tzinfo=UTC
            ).isoformat(),
            accepted_at=filing.accepted_at.isoformat(),
            canonical_url=snapshot.source_url,
            snapshot_id=snapshot.id,
            source_version=snapshot.source_version,
            source_content_sha256=snapshot.content_sha256.hex(),
            knowledge_base_id=version.knowledge_base_id,
            document_id=document.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            section=chunk.title_path[-1] if chunk.title_path else "Filing excerpt",
            page_number=chunk.page_number,
            content_sha256=chunk.content_hash.hex(),
            parser_version=version.parser_version,
            chunker_version=chunk.chunker_version,
            index_version=vector.index_version,
            retrieval_profile_version=retrieval_profile_version,
            retrieval_channels=retrieval_channels,
        )
        return await self._persist_sec_evidence(
            session,
            scope,
            call,
            observation,
            source,
            ordinal=ordinal,
            authorization=authorization,
            locator=locator,
            title=f"{document.title}: {locator.section}",
            canonical_url=snapshot.source_url,
            excerpt=chunk.text_content,
            source_published_at=filing.accepted_at,
            retrieved_at=snapshot.retrieved_at,
            document_version_id=version.id,
            chunk_id=chunk.id,
            source_resource_version=(
                f"{snapshot.source_version}:{vector.index_version}:{retrieval_profile_version}"
            ),
        )

    async def _normalize_sec_xbrl_fact(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        try:
            fact_id = parse_sec_resource_locator(source.locator, resource="xbrl-facts")
            output = SecGetXbrlFactsOutput.model_validate_json(observation.model_text, strict=True)
            fact = next(item for item in output.facts if item.id == fact_id)
            if output.financial_scope is None or not output.knowledge_base_ids:
                raise ValueError
            financial_scope = output.financial_scope.to_domain()
        except (StopIteration, ValueError):
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        if (
            source.content_sha256 != fact.content_sha256
            or source.source_version != fact.source_version
        ):
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        row = (
            await session.execute(
                select(SecXbrlFactRecord, SecXbrlSourceRecord, SecFilingRecord)
                .join(SecXbrlSourceRecord, SecXbrlSourceRecord.id == SecXbrlFactRecord.source_id)
                .join(SecFilingRecord, SecFilingRecord.id == SecXbrlFactRecord.filing_id)
                .join(
                    WorkspaceSecImportRecord,
                    and_(
                        WorkspaceSecImportRecord.workspace_id == scope.workspace_id,
                        WorkspaceSecImportRecord.filing_id == SecFilingRecord.id,
                    ),
                )
                .join(
                    KnowledgeBaseRecord,
                    and_(
                        KnowledgeBaseRecord.id == WorkspaceSecImportRecord.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentRecord,
                    and_(
                        DocumentRecord.id == WorkspaceSecImportRecord.document_id,
                        DocumentRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentVersionRecord,
                    and_(
                        DocumentVersionRecord.id == WorkspaceSecImportRecord.document_version_id,
                        DocumentVersionRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .where(
                    SecXbrlFactRecord.id == fact_id,
                    SecXbrlFactRecord.accession == financial_scope.accession,
                    SecXbrlFactRecord.form == financial_scope.form.value,
                    SecXbrlSourceRecord.cik == financial_scope.cik,
                    SecXbrlSourceRecord.source_version == source.source_version,
                    SecXbrlSourceRecord.source_available_at <= financial_scope.as_of,
                    SecFilingRecord.report_date == financial_scope.report_period,
                    WorkspaceSecImportRecord.knowledge_base_id.in_(output.knowledge_base_ids),
                    KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    DocumentRecord.status == DocumentStatus.ACTIVE,
                    DocumentRecord.active_version_id == DocumentVersionRecord.id,
                    DocumentVersionRecord.status == DocumentVersionStatus.READY,
                )
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None, EvidenceDecisionReason.RESOURCE_UNAUTHORIZED
        fact_record, source_record, filing = row
        if not _sec_xbrl_fact_matches_records(fact, fact_record, source_record, filing):
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        locator = SecXbrlFactLocatorV1(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            report_period=filing.report_date.isoformat(),
            as_of=financial_scope.as_of.isoformat(),
            fact_id=fact_record.id,
            filing_id=fact_record.filing_id,
            source_id=fact_record.source_id,
            source_snapshot_id=source_record.filing_snapshot_id,
            source_kind=source_record.source_kind,
            taxonomy=fact_record.taxonomy,
            concept=fact_record.concept,
            unit=fact_record.unit,
            period_kind=fact_record.period_kind,
            instant=None if fact_record.instant is None else fact_record.instant.isoformat(),
            start_date=(
                None if fact_record.start_date is None else fact_record.start_date.isoformat()
            ),
            end_date=None if fact_record.end_date is None else fact_record.end_date.isoformat(),
            context_id=fact_record.raw_context_id,
            dimensions=fact_record.dimensions,
            decimals=fact_record.decimals,
            scale=fact_record.scale,
            source_url=source_record.source_url,
            source_version=source_record.source_version,
            source_content_sha256=source_record.content_sha256.hex(),
            content_sha256=fact.content_sha256,
            source_available_at=source_record.source_available_at.isoformat(),
            retrieved_at=source_record.retrieved_at.isoformat(),
        )
        excerpt = json.dumps(
            fact.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return await self._persist_sec_evidence(
            session,
            scope,
            call,
            observation,
            source,
            ordinal=ordinal,
            authorization=authorization,
            locator=locator,
            title=f"{filing.form} {filing.accession}: {fact.taxonomy}:{fact.concept}",
            canonical_url=source_record.source_url,
            excerpt=excerpt,
            source_published_at=filing.accepted_at,
            retrieved_at=source_record.retrieved_at,
            document_version_id=None,
            chunk_id=None,
            source_resource_version=f"{source_record.source_version}:{fact_record.locator_key}",
        )

    async def _persist_sec_evidence(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
        locator: SecFilingTextLocatorV1 | SecXbrlFactLocatorV1,
        title: str,
        canonical_url: str,
        excerpt: str,
        source_published_at: datetime,
        retrieved_at: datetime,
        document_version_id: UUID | None,
        chunk_id: UUID | None,
        source_resource_version: str,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": source.content_sha256,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(scope.workspace_id),
            }
        )
        evidence, reason = await self._existing_or_reason(session, scope, dedupe)
        if evidence is not None or reason is not None:
            return evidence, reason or EvidenceDecisionReason.ACCEPTED
        record = EvidenceRecord(
            id=uuid5(NAMESPACE_URL, f"{scope.workspace_id}:{locator.locator_type.value}:{dedupe}"),
            workspace_id=scope.workspace_id,
            schema_version=1,
            kind=EvidenceKind.FILING,
            title=title,
            canonical_url=canonical_url,
            locator_type=locator.locator_type,
            locator=dict(locator.to_mapping()),
            excerpt=excerpt,
            content_sha256=source.content_sha256,
            source_published_at=source_published_at,
            retrieved_at=retrieved_at,
            license_or_terms="Official SEC public filing data subject to SEC.gov terms.",
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=call.run_id,
            origin_step_id=call.execution_step_id,
            origin_tool_call_id=call.id,
            origin_observation_id=observation.observation_id,
            origin_source_ordinal=ordinal,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            authorization_snapshot=dict(authorization.to_mapping()),
            source_resource_version=source_resource_version,
            source_item_id=None,
            query_run_id=None,
            document_version_id=document_version_id,
            chunk_id=chunk_id,
            deduplication_key=dedupe,
            created_at=authorization.captured_at,
            updated_at=authorization.captured_at,
        )
        session.add(record)
        await session.flush()
        return record, EvidenceDecisionReason.ACCEPTED

    async def _normalize_knowledge_source(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        catalog = self._fixture_catalog
        if catalog is None or source.source_version != catalog.dataset_version:
            return None, EvidenceDecisionReason.SOURCE_VERSION_MISSING
        try:
            parsed = parse_knowledge_source_locator(source.locator)
        except ValueError:
            return None, EvidenceDecisionReason.LOCATOR_INVALID
        fixtures = tuple(
            item
            for item in catalog.filings
            if item.accession == parsed.accession and item.dataset_version == source.source_version
        )
        if len(fixtures) != 1:
            return None, EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
        fixture = fixtures[0]
        vector_record = aliased(DocumentIndexRecord)
        lexical_record = aliased(DocumentIndexRecord)
        row = (
            await session.execute(
                select(
                    DocumentChunkRecord,
                    DocumentVersionRecord,
                    DocumentRecord,
                    FileObject,
                    vector_record,
                )
                .join(
                    DocumentVersionRecord,
                    and_(
                        DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                        DocumentVersionRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentRecord,
                    and_(
                        DocumentRecord.id == DocumentChunkRecord.document_id,
                        DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                    ),
                )
                .join(
                    KnowledgeBaseRecord,
                    and_(
                        KnowledgeBaseRecord.id == DocumentVersionRecord.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == DocumentVersionRecord.workspace_id,
                    ),
                )
                .join(
                    FileObject,
                    and_(
                        FileObject.id == DocumentVersionRecord.file_object_id,
                        FileObject.workspace_id == DocumentVersionRecord.workspace_id,
                    ),
                )
                .join(
                    vector_record,
                    and_(
                        vector_record.chunk_id == DocumentChunkRecord.id,
                        vector_record.document_version_id
                        == DocumentChunkRecord.document_version_id,
                        vector_record.workspace_id == DocumentChunkRecord.workspace_id,
                        vector_record.kind == DocumentIndexKind.VECTOR,
                        vector_record.status == DocumentIndexStatus.SUCCEEDED,
                    ),
                )
                .join(
                    lexical_record,
                    and_(
                        lexical_record.chunk_id == DocumentChunkRecord.id,
                        lexical_record.document_version_id
                        == DocumentChunkRecord.document_version_id,
                        lexical_record.workspace_id == DocumentChunkRecord.workspace_id,
                        lexical_record.kind == DocumentIndexKind.LEXICAL,
                        lexical_record.status == DocumentIndexStatus.SUCCEEDED,
                        lexical_record.index_version == vector_record.index_version,
                    ),
                )
                .where(
                    DocumentChunkRecord.id == parsed.chunk_id,
                    DocumentChunkRecord.document_version_id == parsed.document_version_id,
                    DocumentChunkRecord.workspace_id == scope.workspace_id,
                    DocumentVersionRecord.status == DocumentVersionStatus.READY,
                    DocumentRecord.status == DocumentStatus.ACTIVE,
                    DocumentRecord.active_version_id == DocumentVersionRecord.id,
                    KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    FileObject.status == FileObjectStatus.READY,
                    FileObject.source_sha256 == fixture.content_sha256,
                )
            )
        ).one_or_none()
        if row is None:
            return None, EvidenceDecisionReason.RESOURCE_UNAUTHORIZED
        chunk, version, document, _file, vector = row
        if chunk.content_hash.hex() != source.content_sha256:
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        section, source_page = self._fixture_locator(chunk.text_content, chunk.title_path, fixture)
        locator = SecFilingChunkLocatorV1(
            cik=fixture.cik,
            accession=fixture.accession,
            form=fixture.form,
            report_period=fixture.report_period.isoformat(),
            filed_at=fixture.filed_at.isoformat(),
            accepted_at=fixture.accepted_at.isoformat(),
            primary_document=fixture.primary_document,
            canonical_url=fixture.canonical_url,
            dataset_version=fixture.dataset_version,
            fixture_sha256=fixture.content_sha256,
            knowledge_base_id=version.knowledge_base_id,
            document_id=document.id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            section=section,
            page_number=source_page,
            content_sha256=source.content_sha256,
            parser_version=version.parser_version,
            chunker_version=chunk.chunker_version,
            index_version=vector.index_version,
        )
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": source.content_sha256,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(scope.workspace_id),
            }
        )
        evidence, reason = await self._existing_or_reason(session, scope, dedupe)
        if evidence is not None or reason is not None:
            return evidence, reason or EvidenceDecisionReason.ACCEPTED
        evidence_id = knowledge_evidence_ref(
            workspace_id=scope.workspace_id,
            accession=fixture.accession,
            document_version_id=version.id,
            chunk_id=chunk.id,
            content_sha256=source.content_sha256,
        )
        record = EvidenceRecord(
            id=evidence_id,
            workspace_id=scope.workspace_id,
            schema_version=1,
            kind=EvidenceKind.FILING,
            title=f"{document.title}: {section}",
            canonical_url=fixture.canonical_url,
            locator_type=locator.locator_type,
            locator=dict(locator.to_mapping()),
            excerpt=chunk.text_content,
            content_sha256=source.content_sha256,
            source_published_at=fixture.filed_at,
            retrieved_at=source.observed_at,
            license_or_terms=fixture.license_or_terms,
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=call.run_id,
            origin_step_id=call.execution_step_id,
            origin_tool_call_id=call.id,
            origin_observation_id=observation.observation_id,
            origin_source_ordinal=ordinal,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            authorization_snapshot=dict(authorization.to_mapping()),
            source_resource_version=(
                f"{fixture.dataset_version}:{fixture.accession}:{vector.index_version}"
            ),
            source_item_id=None,
            query_run_id=None,
            document_version_id=version.id,
            chunk_id=chunk.id,
            deduplication_key=dedupe,
            created_at=authorization.captured_at,
            updated_at=authorization.captured_at,
        )
        session.add(record)
        await session.flush()
        return record, EvidenceDecisionReason.ACCEPTED

    async def _normalize_calculation_source(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        if source.source_version != FINANCE_CALCULATION_SOURCE_VERSION:
            return None, EvidenceDecisionReason.SOURCE_VERSION_MISSING
        try:
            locator_hash = parse_calculation_source_locator(source.locator)
        except ValueError:
            return None, EvidenceDecisionReason.LOCATOR_INVALID
        model_hash = hashlib.sha256(observation.model_text.encode("utf-8")).hexdigest()
        if source.content_sha256 != model_hash or locator_hash != model_hash:
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        try:
            output = FinanceCalculateOutput.model_validate_json(
                observation.model_text,
                strict=True,
            )
        except ValueError:
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        if (
            output.status is not KnowledgeSearchStatus.OK
            or output.result is None
            or output.formula is None
            or output.unit is None
            or output.scale is None
            or output.error_code is not None
            or output.evidence_refs != [UUID(item.evidence_ref) for item in output.operands]
        ):
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        evidence_rows = tuple(
            (
                await session.execute(
                    select(EvidenceRecord).where(
                        EvidenceRecord.workspace_id == scope.workspace_id,
                        EvidenceRecord.id.in_(output.evidence_refs),
                        EvidenceRecord.kind == EvidenceKind.FILING,
                        EvidenceRecord.status == EvidenceStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        if {record.id for record in evidence_rows} != set(output.evidence_refs):
            return None, EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
        financial_scope = output.financial_scope.to_domain()
        for record in evidence_rows:
            try:
                filing_locator = parse_evidence_locator(record.locator)
            except ValueError:
                return None, EvidenceDecisionReason.OBSERVATION_INVALID
            if not isinstance(filing_locator, SecFilingChunkLocatorV1) or (
                filing_locator.cik != financial_scope.cik
                or filing_locator.accession != financial_scope.accession
                or filing_locator.form != financial_scope.form.value
                or filing_locator.report_period != financial_scope.report_period.isoformat()
            ):
                return None, EvidenceDecisionReason.RESOURCE_UNAUTHORIZED
        calculation = FinancialCalculation(
            operator=output.operator,
            operands=tuple(
                FinancialOperand(value=item.value, evidence_ref=UUID(item.evidence_ref))
                for item in output.operands
            ),
            decimal_places=output.decimal_places,
            rounding_mode=output.rounding_mode,
        )
        try:
            recomputed = calculate_financial_result(financial_scope, calculation)
        except ValueError:
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        if (
            output.result != recomputed.value
            or output.formula != recomputed.formula
            or output.unit != recomputed.unit
            or output.scale != recomputed.scale
        ):
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        locator = FinancialCalculationLocatorV1(
            financial_scope=dict(financial_scope.to_mapping()),
            operator=output.operator.value,
            operand_values=tuple(item.value for item in output.operands),
            input_evidence_refs=tuple(output.evidence_refs),
            decimal_places=output.decimal_places,
            rounding_mode=output.rounding_mode.value,
            formula=output.formula,
            result=output.result,
            unit=output.unit,
            scale=output.scale,
            observation_sha256=model_hash,
        )
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": model_hash,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(scope.workspace_id),
            }
        )
        evidence, reason = await self._existing_or_reason(session, scope, dedupe)
        if evidence is not None or reason is not None:
            return evidence, reason or EvidenceDecisionReason.ACCEPTED
        record = EvidenceRecord(
            id=uuid5(NAMESPACE_URL, f"{scope.workspace_id}:finance-calculation:{dedupe}"),
            workspace_id=scope.workspace_id,
            schema_version=1,
            kind=EvidenceKind.CALCULATION,
            title=f"Financial calculation: {output.operator.value}",
            canonical_url=None,
            locator_type=locator.locator_type,
            locator=dict(locator.to_mapping()),
            excerpt=observation.model_text,
            content_sha256=model_hash,
            source_published_at=None,
            retrieved_at=source.observed_at,
            license_or_terms=(
                "Deterministic workspace calculation derived from authorized filing Evidence."
            ),
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=call.run_id,
            origin_step_id=call.execution_step_id,
            origin_tool_call_id=call.id,
            origin_observation_id=observation.observation_id,
            origin_source_ordinal=ordinal,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            authorization_snapshot=dict(authorization.to_mapping()),
            source_resource_version=FINANCE_CALCULATION_SOURCE_VERSION,
            source_item_id=None,
            query_run_id=None,
            document_version_id=None,
            chunk_id=None,
            deduplication_key=dedupe,
            created_at=authorization.captured_at,
            updated_at=authorization.captured_at,
        )
        session.add(record)
        await session.flush()
        return record, EvidenceDecisionReason.ACCEPTED

    async def _normalize_industry_source(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        rows = (
            await session.execute(
                select(SourceItemRecord, DataSourceRecord)
                .join(
                    DataSourceRecord,
                    DataSourceRecord.id == SourceItemRecord.data_source_id,
                )
                .where(
                    SourceItemRecord.workspace_id == scope.workspace_id,
                    SourceItemRecord.locator == source.locator,
                )
            )
        ).all()
        if not rows:
            return None, EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
        version_rows = [row for row in rows if row[1].version == source.source_version]
        if not version_rows:
            return None, EvidenceDecisionReason.SOURCE_VERSION_MISSING
        matching = [
            row for row in version_rows if row[0].content_sha256.hex() == source.content_sha256
        ]
        if len(matching) != 1:
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        item, data_source = matching[0]
        if not license_allows_evidence(item.usage_constraints):
            return None, EvidenceDecisionReason.LICENSE_NOT_ALLOWED
        kind_by_source = {
            SourceKind.NEWS: EvidenceKind.NEWS,
            SourceKind.POLICY: EvidenceKind.POLICY,
            SourceKind.TENDER: EvidenceKind.BIDDING,
            SourceKind.STOCK: EvidenceKind.STOCK,
        }
        locator = IndustrySourceLocatorV1(
            source_item_id=item.id,
            source_kind=item.source_kind.value,
            provider=data_source.provider.value,
            source_version=data_source.version,
            content_sha256=source.content_sha256,
        )
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": source.content_sha256,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(scope.workspace_id),
            }
        )
        evidence, reason = await self._existing_or_reason(session, scope, dedupe)
        if evidence is not None or reason is not None:
            return evidence, reason or EvidenceDecisionReason.ACCEPTED
        record = EvidenceRecord(
            id=uuid4(),
            workspace_id=scope.workspace_id,
            schema_version=1,
            kind=kind_by_source[item.source_kind],
            title=item.title,
            canonical_url=item.locator,
            locator_type=locator.locator_type,
            locator=dict(locator.to_mapping()),
            excerpt=item.summary,
            content_sha256=source.content_sha256,
            source_published_at=item.published_at,
            retrieved_at=item.collected_at,
            license_or_terms=item.usage_constraints,
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=call.run_id,
            origin_step_id=call.execution_step_id,
            origin_tool_call_id=call.id,
            origin_observation_id=observation.observation_id,
            origin_source_ordinal=ordinal,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            authorization_snapshot=dict(authorization.to_mapping()),
            source_resource_version=f"{data_source.version}:{source.content_sha256[:32]}",
            source_item_id=item.id,
            query_run_id=None,
            deduplication_key=dedupe,
            created_at=authorization.captured_at,
            updated_at=authorization.captured_at,
        )
        session.add(record)
        await session.flush()
        return record, EvidenceDecisionReason.ACCEPTED

    async def _normalize_sql_source(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        call: ToolCallRecord,
        observation: ToolObservation,
        source: ToolSource,
        *,
        ordinal: int,
        authorization: AuthorizationSnapshot,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason]:
        if source.source_version != SQL_SOURCE_VERSION:
            return None, EvidenceDecisionReason.SOURCE_VERSION_MISSING
        try:
            parsed = parse_sql_source_locator(source.locator)
        except ValueError:
            return None, EvidenceDecisionReason.LOCATOR_INVALID
        query_run = await session.scalar(
            select(QueryRunRecord).where(
                QueryRunRecord.id == parsed.query_run_id,
                QueryRunRecord.workspace_id == scope.workspace_id,
                QueryRunRecord.connection_id == parsed.connection_id,
                QueryRunRecord.tool_call_id == call.id,
                QueryRunRecord.agent_run_id == call.run_id,
            )
        )
        if (
            query_run is None
            or query_run.status is not QueryRunStatus.COMPLETED
            or query_run.schema_snapshot_id is None
            or query_run.validated_sql is None
            or query_run.result_content_sha256 is None
        ):
            return None, EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
        connection = await session.scalar(
            select(DataConnectionRecord).where(
                DataConnectionRecord.id == parsed.connection_id,
                DataConnectionRecord.workspace_id == scope.workspace_id,
            )
        )
        if (
            connection is None
            or connection.status is not DataConnectionStatus.READY
            or parsed.table not in connection.allowed_tables
        ):
            return None, EvidenceDecisionReason.RESOURCE_UNAUTHORIZED
        snapshot = await session.scalar(
            select(SchemaSnapshotRecord).where(
                SchemaSnapshotRecord.id == query_run.schema_snapshot_id,
                SchemaSnapshotRecord.workspace_id == scope.workspace_id,
                SchemaSnapshotRecord.connection_id == parsed.connection_id,
            )
        )
        result = await session.scalar(
            select(QueryResultRecord).where(
                QueryResultRecord.query_run_id == query_run.id,
                QueryResultRecord.workspace_id == scope.workspace_id,
            )
        )
        if snapshot is None or result is None:
            return None, EvidenceDecisionReason.SOURCE_SNAPSHOT_MISSING
        if (
            result.content_sha256 != source.content_sha256
            or query_run.result_content_sha256 != source.content_sha256
        ):
            return None, EvidenceDecisionReason.SOURCE_HASH_MISMATCH
        try:
            available_columns = schema_columns_for_table(snapshot.tables, parsed.table)
            source_columns = referenced_sql_columns(
                query_run.validated_sql,
                available_columns,
            )
        except (ValueError, TypeError):
            return None, EvidenceDecisionReason.OBSERVATION_INVALID
        locator = SqlResultLocatorV1(
            query_run_id=query_run.id,
            connection_id=connection.id,
            schema_snapshot_id=snapshot.id,
            schema_snapshot_sha256=snapshot.content_sha256,
            tables=(parsed.table,),
            columns=source_columns,
            row_start=0,
            row_end=len(result.rows),
        )
        dedupe = canonical_fingerprint(
            {
                "authorization_role": authorization.role,
                "content_sha256": source.content_sha256,
                "locator": dict(locator.to_mapping()),
                "workspace_id": str(scope.workspace_id),
            }
        )
        evidence, reason = await self._existing_or_reason(session, scope, dedupe)
        if evidence is not None or reason is not None:
            return evidence, reason or EvidenceDecisionReason.ACCEPTED
        excerpt = json.dumps(
            {
                "columns": result.columns,
                "row_count": query_run.row_count,
                "rows": result.rows[:3],
                "truncated": result.truncated or len(result.rows) > 3,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        record = EvidenceRecord(
            id=uuid4(),
            workspace_id=scope.workspace_id,
            schema_version=1,
            kind=EvidenceKind.SQL_RESULT,
            title=f"SQL result: {parsed.table}",
            canonical_url=None,
            locator_type=locator.locator_type,
            locator=dict(locator.to_mapping()),
            excerpt=excerpt,
            content_sha256=source.content_sha256,
            source_published_at=None,
            retrieved_at=result.created_at,
            license_or_terms=(
                "Workspace-authorized read-only query result; access is rechecked on read."
            ),
            status=EvidenceStatus.ACTIVE,
            revision=1,
            invalidated_at=None,
            invalidation_reason=None,
            origin_run_id=call.run_id,
            origin_step_id=call.execution_step_id,
            origin_tool_call_id=call.id,
            origin_observation_id=observation.observation_id,
            origin_source_ordinal=ordinal,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            authorization_snapshot=dict(authorization.to_mapping()),
            source_resource_version=f"{SQL_SOURCE_VERSION}:{snapshot.content_sha256[:32]}",
            source_item_id=None,
            query_run_id=query_run.id,
            deduplication_key=dedupe,
            created_at=authorization.captured_at,
            updated_at=authorization.captured_at,
        )
        session.add(record)
        await session.flush()
        return record, EvidenceDecisionReason.ACCEPTED

    async def _existing_or_reason(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        dedupe: str,
    ) -> tuple[EvidenceRecord | None, EvidenceDecisionReason | None]:
        existing = await session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.workspace_id == scope.workspace_id,
                EvidenceRecord.deduplication_key == dedupe,
            )
        )
        if existing is None:
            return None, None
        if existing.status is not EvidenceStatus.ACTIVE:
            return None, EvidenceDecisionReason.DEPENDENCY_UNAVAILABLE
        return existing, None

    async def _decision_result(
        self,
        session: AsyncSession,
        observation: ToolObservation,
        decisions: Sequence[EvidenceNormalizationDecisionRecord],
    ) -> EvidenceNormalizationResult:
        evidence_ids = [item.evidence_id for item in decisions if item.evidence_id is not None]
        evidence_by_id: dict[UUID, EvidenceRecord] = {}
        if evidence_ids:
            evidence_records = (
                (
                    await session.execute(
                        select(EvidenceRecord).where(EvidenceRecord.id.in_(evidence_ids))
                    )
                )
                .scalars()
                .all()
            )
            evidence_by_id = {item.id: item for item in evidence_records}
        items: list[EvidenceNormalizationItem] = []
        for decision in decisions:
            evidence_record = (
                None if decision.evidence_id is None else evidence_by_id.get(decision.evidence_id)
            )
            if decision.evidence_id is not None and evidence_record is None:
                raise EvidencePersistenceError
            items.append(
                EvidenceNormalizationItem(
                    source_ordinal=decision.source_ordinal,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence=(
                        None
                        if evidence_record is None
                        else self._evidence_snapshot(evidence_record)
                    ),
                )
            )
        return EvidenceNormalizationResult(
            observation_id=observation.observation_id,
            tool_call_id=observation.call_id,
            normalizer_version=EVIDENCE_NORMALIZER_VERSION,
            items=tuple(items),
        )

    async def _is_available(self, session: AsyncSession, record: EvidenceRecord) -> bool:
        if record.source_item_id is not None:
            item = await session.scalar(
                select(SourceItemRecord).where(
                    SourceItemRecord.id == record.source_item_id,
                    SourceItemRecord.workspace_id == record.workspace_id,
                    SourceItemRecord.content_sha256 == bytes.fromhex(record.content_sha256),
                )
            )
            return item is not None and license_allows_evidence(item.usage_constraints)
        if record.query_run_id is not None:
            try:
                locator = parse_evidence_locator(record.locator)
            except ValueError:
                raise EvidencePersistenceError from None
            if not isinstance(locator, SqlResultLocatorV1):
                raise EvidencePersistenceError
            query_run = await session.scalar(
                select(QueryRunRecord).where(
                    QueryRunRecord.id == record.query_run_id,
                    QueryRunRecord.workspace_id == record.workspace_id,
                    QueryRunRecord.status == QueryRunStatus.COMPLETED,
                    QueryRunRecord.result_content_sha256 == record.content_sha256,
                )
            )
            connection = await session.scalar(
                select(DataConnectionRecord).where(
                    DataConnectionRecord.id == locator.connection_id,
                    DataConnectionRecord.workspace_id == record.workspace_id,
                    DataConnectionRecord.status == DataConnectionStatus.READY,
                )
            )
            return (
                query_run is not None
                and connection is not None
                and set(locator.tables).issubset(connection.allowed_tables)
            )
        if record.document_version_id is not None and record.chunk_id is not None:
            try:
                locator = parse_evidence_locator(record.locator)
            except ValueError:
                raise EvidencePersistenceError from None
            if isinstance(locator, SecFilingTextLocatorV1):
                vector_record = aliased(DocumentIndexRecord)
                lexical_record = aliased(DocumentIndexRecord)
                as_of = datetime.fromisoformat(locator.as_of)
                live_row = (
                    await session.execute(
                        select(DocumentChunkRecord.id)
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == DocumentChunkRecord.document_id,
                                DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            KnowledgeBaseRecord,
                            and_(
                                KnowledgeBaseRecord.id == DocumentVersionRecord.knowledge_base_id,
                                KnowledgeBaseRecord.workspace_id
                                == DocumentVersionRecord.workspace_id,
                            ),
                        )
                        .join(
                            WorkspaceSecImportRecord,
                            and_(
                                WorkspaceSecImportRecord.workspace_id
                                == DocumentChunkRecord.workspace_id,
                                WorkspaceSecImportRecord.document_version_id
                                == DocumentChunkRecord.document_version_id,
                            ),
                        )
                        .join(
                            SecFilingRecord,
                            SecFilingRecord.id == WorkspaceSecImportRecord.filing_id,
                        )
                        .join(
                            SecSourceSnapshotRecord,
                            SecSourceSnapshotRecord.id
                            == WorkspaceSecImportRecord.primary_snapshot_id,
                        )
                        .join(
                            SecFilingDocumentRecord,
                            and_(
                                SecFilingDocumentRecord.id
                                == SecSourceSnapshotRecord.filing_document_id,
                                SecFilingDocumentRecord.current_snapshot_id
                                == SecSourceSnapshotRecord.id,
                            ),
                        )
                        .join(
                            vector_record,
                            and_(
                                vector_record.chunk_id == DocumentChunkRecord.id,
                                vector_record.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                vector_record.workspace_id == DocumentChunkRecord.workspace_id,
                                vector_record.kind == DocumentIndexKind.VECTOR,
                                vector_record.status == DocumentIndexStatus.SUCCEEDED,
                                vector_record.index_version == locator.index_version,
                            ),
                        )
                        .join(
                            lexical_record,
                            and_(
                                lexical_record.chunk_id == DocumentChunkRecord.id,
                                lexical_record.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                lexical_record.workspace_id == DocumentChunkRecord.workspace_id,
                                lexical_record.kind == DocumentIndexKind.LEXICAL,
                                lexical_record.status == DocumentIndexStatus.SUCCEEDED,
                                lexical_record.index_version == vector_record.index_version,
                            ),
                        )
                        .where(
                            DocumentChunkRecord.id == record.chunk_id,
                            DocumentChunkRecord.document_version_id == record.document_version_id,
                            DocumentChunkRecord.workspace_id == record.workspace_id,
                            DocumentChunkRecord.content_hash
                            == bytes.fromhex(record.content_sha256),
                            DocumentVersionRecord.status == DocumentVersionStatus.READY,
                            DocumentRecord.status == DocumentStatus.ACTIVE,
                            DocumentRecord.active_version_id == DocumentVersionRecord.id,
                            KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                            WorkspaceSecImportRecord.knowledge_base_id == locator.knowledge_base_id,
                            WorkspaceSecImportRecord.primary_snapshot_id == locator.snapshot_id,
                            SecFilingRecord.cik == locator.cik,
                            SecFilingRecord.accession == locator.accession,
                            SecFilingRecord.form == locator.form,
                            SecFilingRecord.report_date
                            == date.fromisoformat(locator.report_period),
                            SecFilingRecord.public_available_at <= as_of,
                            SecSourceSnapshotRecord.status == "active",
                            SecSourceSnapshotRecord.source_version == locator.source_version,
                            SecSourceSnapshotRecord.content_sha256
                            == bytes.fromhex(locator.source_content_sha256),
                            SecSourceSnapshotRecord.source_available_at <= as_of,
                            (
                                SecSourceSnapshotRecord.valid_to.is_(None)
                                | (SecSourceSnapshotRecord.valid_to >= as_of)
                            ),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                return live_row is not None
            if not isinstance(locator, SecFilingChunkLocatorV1):
                raise EvidencePersistenceError
            fixture_row = (
                await session.execute(
                    select(DocumentChunkRecord, DocumentVersionRecord, DocumentRecord, FileObject)
                    .join(
                        DocumentVersionRecord,
                        and_(
                            DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                            DocumentVersionRecord.workspace_id == DocumentChunkRecord.workspace_id,
                        ),
                    )
                    .join(
                        DocumentRecord,
                        and_(
                            DocumentRecord.id == DocumentChunkRecord.document_id,
                            DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                        ),
                    )
                    .join(
                        FileObject,
                        and_(
                            FileObject.id == DocumentVersionRecord.file_object_id,
                            FileObject.workspace_id == DocumentVersionRecord.workspace_id,
                        ),
                    )
                    .where(
                        DocumentChunkRecord.id == record.chunk_id,
                        DocumentChunkRecord.document_version_id == record.document_version_id,
                        DocumentChunkRecord.workspace_id == record.workspace_id,
                        DocumentChunkRecord.content_hash == bytes.fromhex(record.content_sha256),
                        DocumentVersionRecord.status == DocumentVersionStatus.READY,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                        DocumentRecord.active_version_id == DocumentVersionRecord.id,
                        FileObject.status == FileObjectStatus.READY,
                        FileObject.source_sha256 == locator.fixture_sha256,
                    )
                )
            ).one_or_none()
            return fixture_row is not None
        try:
            locator = parse_evidence_locator(record.locator)
        except ValueError:
            raise EvidencePersistenceError from None
        if isinstance(locator, FinancialCalculationLocatorV1):
            inputs = tuple(
                (
                    await session.execute(
                        select(EvidenceRecord).where(
                            EvidenceRecord.workspace_id == record.workspace_id,
                            EvidenceRecord.id.in_(locator.input_evidence_refs),
                            EvidenceRecord.status == EvidenceStatus.ACTIVE,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if {item.id for item in inputs} != set(locator.input_evidence_refs):
                return False
            for input_record in inputs:
                if not await self._is_available(session, input_record):
                    return False
            return True
        if isinstance(locator, SecXbrlFactLocatorV1):
            as_of = datetime.fromisoformat(locator.as_of)
            fact_id = await session.scalar(
                select(SecXbrlFactRecord.id)
                .join(SecXbrlSourceRecord, SecXbrlSourceRecord.id == SecXbrlFactRecord.source_id)
                .join(SecFilingRecord, SecFilingRecord.id == SecXbrlFactRecord.filing_id)
                .join(
                    WorkspaceSecImportRecord,
                    and_(
                        WorkspaceSecImportRecord.workspace_id == record.workspace_id,
                        WorkspaceSecImportRecord.filing_id == SecFilingRecord.id,
                    ),
                )
                .join(
                    KnowledgeBaseRecord,
                    and_(
                        KnowledgeBaseRecord.id == WorkspaceSecImportRecord.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentRecord,
                    and_(
                        DocumentRecord.id == WorkspaceSecImportRecord.document_id,
                        DocumentRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .join(
                    DocumentVersionRecord,
                    and_(
                        DocumentVersionRecord.id == WorkspaceSecImportRecord.document_version_id,
                        DocumentVersionRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
                    ),
                )
                .where(
                    SecXbrlFactRecord.id == locator.fact_id,
                    SecXbrlFactRecord.filing_id == locator.filing_id,
                    SecXbrlFactRecord.source_id == locator.source_id,
                    SecXbrlFactRecord.accession == locator.accession,
                    SecXbrlFactRecord.form == locator.form,
                    SecXbrlFactRecord.taxonomy == locator.taxonomy,
                    SecXbrlFactRecord.concept == locator.concept,
                    SecXbrlFactRecord.period_kind == locator.period_kind,
                    SecXbrlFactRecord.unit == locator.unit,
                    SecXbrlSourceRecord.cik == locator.cik,
                    SecXbrlSourceRecord.source_kind == locator.source_kind,
                    SecXbrlSourceRecord.source_version == locator.source_version,
                    SecXbrlSourceRecord.content_sha256
                    == bytes.fromhex(locator.source_content_sha256),
                    SecXbrlSourceRecord.source_available_at <= as_of,
                    SecFilingRecord.report_date == date.fromisoformat(locator.report_period),
                    KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    DocumentRecord.status == DocumentStatus.ACTIVE,
                    DocumentRecord.active_version_id == DocumentVersionRecord.id,
                    DocumentVersionRecord.status == DocumentVersionStatus.READY,
                )
                .limit(1)
            )
            return fact_id is not None
        return False

    @staticmethod
    def _fixture_locator(
        text: str,
        title_path: list[str],
        fixture: SecFilingFixture,
    ) -> tuple[str, int]:
        matching = tuple(
            fact for fact in fixture.facts if fact.anchor.casefold() in text.casefold()
        )
        if matching:
            return matching[0].section, matching[0].source_page
        if title_path:
            return title_path[-1], 1
        return "Filing excerpt", 1

    async def _invalidate_claim_relations(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        evidence: EvidenceRecord,
        *,
        invalidated_at: datetime,
    ) -> None:
        links = (
            (
                await session.execute(
                    select(ClaimEvidenceRecord).where(
                        ClaimEvidenceRecord.workspace_id == scope.workspace_id,
                        ClaimEvidenceRecord.evidence_id == evidence.id,
                        ClaimEvidenceRecord.status == RelationStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not links:
            return
        claim_ids = {link.claim_id for link in links}
        for link in links:
            link.status = RelationStatus.INVALIDATED
            link.relation_version += 1
        evidence_nodes = (
            (
                await session.execute(
                    select(GraphNodeRecord).where(
                        GraphNodeRecord.workspace_id == scope.workspace_id,
                        GraphNodeRecord.node_type == GraphNodeType.EVIDENCE,
                        GraphNodeRecord.resource_id == evidence.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        node_ids = {node.id for node in evidence_nodes}
        for node in evidence_nodes:
            node.status = RelationStatus.INVALIDATED
        if node_ids:
            edges = (
                (
                    await session.execute(
                        select(GraphEdgeRecord).where(
                            GraphEdgeRecord.workspace_id == scope.workspace_id,
                            GraphEdgeRecord.target_node_id.in_(node_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for edge in edges:
                edge.status = RelationStatus.INVALIDATED
        await session.flush()
        for claim_id in claim_ids:
            claim = await session.scalar(
                select(ResearchClaimRecord)
                .where(
                    ResearchClaimRecord.id == claim_id,
                    ResearchClaimRecord.workspace_id == scope.workspace_id,
                )
                .with_for_update()
            )
            if claim is None:
                raise EvidencePersistenceError
            active_links = (
                (
                    await session.execute(
                        select(ClaimEvidenceRecord).where(
                            ClaimEvidenceRecord.claim_id == claim.id,
                            ClaimEvidenceRecord.status == RelationStatus.ACTIVE,
                        )
                    )
                )
                .scalars()
                .all()
            )
            relations = tuple(
                ClaimEvidenceInput(evidence_id=link.evidence_id, relation=link.relation)
                for link in active_links
            )
            verification = claim_verification_status(relations)
            claim.verification_status = verification
            claim.coverage = claim_coverage(relations)
            claim.conflict = verification is ClaimVerificationStatus.CONFLICTED
            claim.revision += 1
            claim.updated_at = invalidated_at

    async def _evidence_record(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        evidence_id: UUID,
        *,
        lock: bool = False,
    ) -> EvidenceRecord:
        statement = select(EvidenceRecord).where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.workspace_id == scope.workspace_id,
        )
        if lock:
            statement = statement.with_for_update()
        record = await session.scalar(statement)
        if record is None:
            raise EvidenceNotFoundError
        return record

    async def _research_run_record(
        self,
        session: AsyncSession,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ResearchRunRecord:
        record = await session.scalar(
            select(ResearchRunRecord).where(
                ResearchRunRecord.id == research_run_id,
                ResearchRunRecord.workspace_id == scope.workspace_id,
            )
        )
        if record is None:
            raise ResearchRunNotFoundError
        return record

    async def _claim_snapshot(
        self,
        session: AsyncSession,
        record: ResearchClaimRecord,
    ) -> ResearchClaim:
        links = (
            await session.execute(
                select(ClaimEvidenceRecord, EvidenceRecord)
                .join(
                    EvidenceRecord,
                    (EvidenceRecord.id == ClaimEvidenceRecord.evidence_id)
                    & (EvidenceRecord.workspace_id == ClaimEvidenceRecord.workspace_id),
                )
                .where(ClaimEvidenceRecord.claim_id == record.id)
                .order_by(ClaimEvidenceRecord.ordinal)
            )
        ).all()
        return ResearchClaim(
            claim_id=record.id,
            workspace_id=record.workspace_id,
            research_run_id=record.research_run_id,
            statement=record.statement,
            confidence=record.confidence,
            verification_status=record.verification_status,
            coverage=record.coverage,
            conflict=record.conflict,
            revision=record.revision,
            relations=tuple(
                ClaimEvidenceLink(
                    evidence=self._evidence_snapshot(evidence),
                    relation=link.relation,
                    relation_version=link.relation_version,
                    status=link.status,
                    ordinal=link.ordinal,
                    origin_run_id=link.origin_run_id,
                    origin_step_id=link.origin_step_id,
                )
                for link, evidence in links
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _evidence_snapshot(record: EvidenceRecord) -> Evidence:
        authorization = record.authorization_snapshot
        captured_at = authorization.get("captured_at")
        if not isinstance(captured_at, str):
            raise EvidencePersistenceError
        return Evidence(
            evidence_id=record.id,
            workspace_id=record.workspace_id,
            kind=record.kind,
            title=record.title,
            canonical_url=record.canonical_url,
            locator=parse_evidence_locator(record.locator),
            excerpt=record.excerpt,
            content_sha256=record.content_sha256,
            source_published_at=record.source_published_at,
            retrieved_at=record.retrieved_at,
            license_or_terms=record.license_or_terms,
            status=record.status,
            revision=record.revision,
            invalidated_at=record.invalidated_at,
            invalidation_reason=record.invalidation_reason,
            origin_run_id=record.origin_run_id,
            origin_step_id=record.origin_step_id,
            origin_tool_call_id=record.origin_tool_call_id,
            origin_observation_id=record.origin_observation_id,
            origin_source_ordinal=record.origin_source_ordinal,
            normalizer_version=record.normalizer_version,
            authorization_snapshot=AuthorizationSnapshot(
                workspace_id=UUID(str(authorization.get("workspace_id"))),
                actor_user_id=UUID(str(authorization.get("actor_user_id"))),
                role=str(authorization.get("role")),
                action=str(authorization.get("action")),
                captured_at=datetime.fromisoformat(captured_at),
            ),
            source_resource_version=record.source_resource_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _research_run_snapshot(record: ResearchRunRecord) -> ResearchRun:
        return ResearchRun(
            research_run_id=record.id,
            workspace_id=record.workspace_id,
            owner_user_id=record.owner_user_id,
            agent_run_id=record.agent_run_id,
            status=record.status,
            revision=record.revision,
            graph_version=record.graph_version,
            state_schema_version=record.state_schema_version,
            current_node=record.current_node,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _audit(
        session: AsyncSession,
        scope: WorkspaceScope,
        *,
        action: str,
        resource_id: UUID,
        trace_id: str,
        now: datetime,
        metadata: dict[str, object],
    ) -> None:
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=scope.workspace_id,
                actor_user_id=scope.user_id,
                action=action,
                resource_type="evidence_ledger",
                resource_id=resource_id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=trace_id,
                sanitized_metadata=metadata,
                created_at=now,
                updated_at=now,
            )
        )
