"""Explicit registry of every SQLAlchemy persistence model."""

from sqlalchemy import MetaData

from industry_platform.core.database import Base
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
    Workspace,
    WorkspaceMembership,
)

REGISTERED_MODELS: tuple[type[Base], ...] = (
    User,
    Workspace,
    WorkspaceMembership,
    RefreshSessionFamily,
    RefreshSession,
    AuditLog,
)

metadata: MetaData = Base.metadata
