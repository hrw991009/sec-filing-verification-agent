"""Explicit registry of every SQLAlchemy persistence model."""

from sqlalchemy import MetaData

from industry_platform.core.database import Base
from industry_platform.modules.agent_runtime.models import (
    AgentCheckpointRecord,
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
    RunArtifactRecord,
)
from industry_platform.modules.conversations.models import (
    Conversation,
    Message,
    MessageAttachment,
    Turn,
)
from industry_platform.modules.data_explorer.models import (
    ChartSpecRecord,
    DataConnectionRecord,
    QueryResultRecord,
    QueryRunRecord,
    SampleCompanyMetricRecord,
    SchemaSnapshotRecord,
)
from industry_platform.modules.evidence.models import (
    ClaimEvidenceRecord,
    EvidenceNormalizationDecisionRecord,
    EvidenceRecord,
    GraphEdgeRecord,
    GraphNodeRecord,
    ResearchClaimRecord,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
    Workspace,
    WorkspaceMembership,
)
from industry_platform.modules.industry.models import (
    BiddingItemRecord,
    CollectionCursorRecord,
    CollectionRunItemRecord,
    CollectionRunRecord,
    DataSourceRecord,
    IndustryRecord,
    MarketSnapshotRecord,
    NewsItemRecord,
    PolicyItemRecord,
    SourceItemRecord,
    UserIndustryPreference,
)
from industry_platform.modules.jobs.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
)
from industry_platform.modules.memory.models import (
    MemoryCandidateRecord,
    MemoryCandidateSourceRecord,
    MemoryFeedbackRecord,
    MemoryRecord,
    MemoryRevisionRecord,
    MemoryRevisionSourceRecord,
    ThreadMemoryStateRecord,
)
from industry_platform.modules.research.models import ResearchRunRecord
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord

REGISTERED_MODELS: tuple[type[Base], ...] = (
    User,
    Workspace,
    WorkspaceMembership,
    RefreshSessionFamily,
    RefreshSession,
    AuditLog,
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
    Conversation,
    Turn,
    FileObject,
    AgentRunRecord,
    Message,
    MessageAttachment,
    AgentStepRecord,
    AgentEventRecord,
    ContextManifestRecord,
    RunArtifactRecord,
    AgentCheckpointRecord,
    ToolCallRecord,
    ToolRunRecord,
    IndustryRecord,
    UserIndustryPreference,
    DataSourceRecord,
    CollectionRunRecord,
    CollectionCursorRecord,
    SourceItemRecord,
    CollectionRunItemRecord,
    NewsItemRecord,
    PolicyItemRecord,
    BiddingItemRecord,
    MarketSnapshotRecord,
    SampleCompanyMetricRecord,
    DataConnectionRecord,
    SchemaSnapshotRecord,
    QueryRunRecord,
    QueryResultRecord,
    ChartSpecRecord,
    ResearchRunRecord,
    EvidenceRecord,
    EvidenceNormalizationDecisionRecord,
    ResearchClaimRecord,
    ClaimEvidenceRecord,
    GraphNodeRecord,
    GraphEdgeRecord,
    ThreadMemoryStateRecord,
    MemoryRecord,
    MemoryRevisionRecord,
    MemoryCandidateRecord,
    MemoryCandidateSourceRecord,
    MemoryRevisionSourceRecord,
    MemoryFeedbackRecord,
)

metadata: MetaData = Base.metadata
