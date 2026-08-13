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
from industry_platform.modules.conversations.models import Conversation, Message, Turn
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
    Workspace,
    WorkspaceMembership,
)
from industry_platform.modules.jobs.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
)

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
    AgentRunRecord,
    Message,
    AgentStepRecord,
    AgentEventRecord,
    ContextManifestRecord,
    RunArtifactRecord,
    AgentCheckpointRecord,
)

metadata: MetaData = Base.metadata
