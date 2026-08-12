"""PostgreSQL-backed resolution of authenticated request principals."""

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import (
    AccessTokenClaims,
    AuthenticatedPrincipal,
    AuthenticatedSessionPersistenceError,
    AuthenticatedWorkspace,
    NormalizedEmail,
    WorkspaceRoleName,
)
from industry_platform.modules.identity.models import (
    RefreshSession,
    RefreshSessionFamily,
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceStatus,
)


class SqlAlchemyAuthenticatedSessionReader:
    """Recheck JWT identity against current account, session, and tenant state."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def find_active(
        self,
        claims: AccessTokenClaims,
        *,
        now: datetime,
    ) -> AuthenticatedPrincipal | None:
        """Return current identity only when every server-side guard is valid."""

        try:
            async with self._session_factory() as session:
                user_row = (
                    await session.execute(
                        select(User.id, User.email)
                        .join(
                            RefreshSession,
                            (RefreshSession.user_id == User.id)
                            & (RefreshSession.id == claims.session_id),
                        )
                        .join(
                            RefreshSessionFamily,
                            (RefreshSessionFamily.id == RefreshSession.rotation_family_id)
                            & (RefreshSessionFamily.user_id == User.id),
                        )
                        .where(
                            User.id == claims.user_id,
                            User.status == UserStatus.ACTIVE,
                            RefreshSession.revoked_at.is_(None),
                            RefreshSession.idle_expires_at > now,
                            RefreshSession.absolute_expires_at > now,
                            RefreshSessionFamily.revoked_at.is_(None),
                            RefreshSessionFamily.current_session_id.is_not(None),
                            RefreshSessionFamily.absolute_expires_at > now,
                        )
                    )
                ).one_or_none()

                if user_row is None:
                    return None

                user_id, email = user_row
                membership_rows = (
                    await session.execute(
                        select(
                            Workspace.id,
                            Workspace.name,
                            WorkspaceMembership.role,
                        )
                        .join(
                            WorkspaceMembership,
                            WorkspaceMembership.workspace_id == Workspace.id,
                        )
                        .where(
                            WorkspaceMembership.user_id == user_id,
                            Workspace.status == WorkspaceStatus.ACTIVE,
                        )
                        .order_by(Workspace.created_at, Workspace.id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise AuthenticatedSessionPersistenceError(
                sqlstate=safe_sqlstate(error),
            ) from None

        return AuthenticatedPrincipal(
            user_id=user_id,
            session_id=claims.session_id,
            email=NormalizedEmail(email),
            workspaces=tuple(
                AuthenticatedWorkspace(
                    workspace_id=workspace_id,
                    name=workspace_name,
                    role=cast(WorkspaceRoleName, role.value),
                )
                for workspace_id, workspace_name, role in membership_rows
            ),
        )
