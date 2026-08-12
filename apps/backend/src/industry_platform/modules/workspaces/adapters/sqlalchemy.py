"""SQLAlchemy transaction adapter for workspace membership mutations."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import (
    AccountStatus,
    NormalizedEmail,
    TraceId,
    WorkspaceRoleName,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceMembershipRecord,
    WorkspaceMemberSummary,
    WorkspacePersistenceError,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.ports import (
    LockedWorkspaceAuthorization,
    LockedWorkspaceTargetUsers,
    WorkspaceMembershipWriter,
)


@dataclass(slots=True)
class SqlAlchemyWorkspaceMembershipWriter:
    """Implement locked membership reads and writes in one session."""

    session: AsyncSession
    _locked_workspace_id: UUID | None = field(init=False, default=None, repr=False)
    _memberships: dict[UUID, WorkspaceMembership] = field(
        init=False, default_factory=dict, repr=False
    )

    async def lock_workspace_authorization(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> LockedWorkspaceAuthorization | None:
        """Hint without locks, then lock and revalidate only a plausible actor."""

        authorization_hint = await self.session.scalar(
            select(WorkspaceMembership.id)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor_user_id,
                Workspace.status == WorkspaceStatus.ACTIVE,
                User.status == UserStatus.ACTIVE,
            )
        )
        if authorization_hint is None:
            return None

        workspace_id_result = await self.session.scalar(
            select(Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.status == WorkspaceStatus.ACTIVE,
            )
            .with_for_update()
        )
        if workspace_id_result is None:
            return None

        self._locked_workspace_id = workspace_id
        actor = await self.session.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor_user_id,
            )
            .with_for_update()
        )
        if actor is None:
            return None

        actor_status = await self.session.scalar(
            select(User.status).where(User.id == actor_user_id).with_for_update()
        )

        actor_scope = (
            None
            if actor_status is not UserStatus.ACTIVE
            else WorkspaceScope(
                workspace_id=workspace_id,
                user_id=actor.user_id,
                role=actor.role.value,
            )
        )
        return LockedWorkspaceAuthorization(
            workspace_id=workspace_id,
            actor_scope=actor_scope,
        )

    async def lock_authorized_workspace_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMembershipRecord, ...]:
        """Lock target-bearing membership rows only after actor authorization."""

        self._require_locked_workspace(workspace_id)
        memberships = tuple(
            await self.session.scalars(
                select(WorkspaceMembership)
                .where(WorkspaceMembership.workspace_id == workspace_id)
                .order_by(WorkspaceMembership.user_id)
                .with_for_update()
            )
        )
        self._memberships = {membership.user_id: membership for membership in memberships}
        return tuple(self._to_record(membership) for membership in memberships)

    async def lock_authorized_target_users(
        self,
        *,
        target_user_id: UUID,
        owner_user_ids: frozenset[UUID],
    ) -> LockedWorkspaceTargetUsers:
        """Lock target and owner Users after authorization without waiting in cycles."""

        if self._locked_workspace_id is None:
            message = "Workspace must be locked before target users"
            raise RuntimeError(message)

        user_ids = tuple(
            sorted(
                owner_user_ids | {target_user_id},
                key=lambda user_id: user_id.int,
            )
        )
        user_rows = (
            await self.session.execute(
                select(User.id, User.status)
                .where(User.id.in_(user_ids))
                .order_by(User.id)
                .with_for_update(nowait=True)
            )
        ).all()
        active_user_ids = frozenset(
            user_id for user_id, user_status in user_rows if user_status is UserStatus.ACTIVE
        )
        return LockedWorkspaceTargetUsers(
            target_user_is_active=target_user_id in active_user_ids,
            active_owner_user_ids=owner_user_ids & active_user_ids,
        )

    async def add_membership(
        self, *, workspace_id: UUID, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord:
        self._require_locked_workspace(workspace_id)
        membership = WorkspaceMembership(
            id=uuid4(),
            workspace_id=workspace_id,
            user_id=user_id,
            role=WorkspaceRole(role),
        )
        self.session.add(membership)
        self._memberships[user_id] = membership
        return self._to_record(membership)

    async def change_membership_role(
        self, *, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord:
        membership = self._require_locked_membership(user_id)
        membership.role = WorkspaceRole(role)
        return self._to_record(membership)

    async def remove_membership(self, *, user_id: UUID) -> WorkspaceMembershipRecord:
        membership = self._require_locked_membership(user_id)
        record = self._to_record(membership)
        await self.session.delete(membership)
        del self._memberships[user_id]
        return record

    async def record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        action: str,
        resource_id: UUID,
        outcome: str,
        reason: str | None,
        trace_id: TraceId,
    ) -> None:
        metadata = {} if reason is None else {"reason": reason}
        self.session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                resource_type="workspace_membership",
                resource_id=resource_id,
                outcome=AuditOutcome(outcome),
                trace_id=trace_id,
                sanitized_metadata=metadata,
            )
        )

    def _require_locked_workspace(self, workspace_id: UUID) -> None:
        if self._locked_workspace_id != workspace_id:
            message = "Workspace must be locked before a membership mutation"
            raise RuntimeError(message)

    def _require_locked_membership(self, user_id: UUID) -> WorkspaceMembership:
        if self._locked_workspace_id is None:
            message = "Workspace must be locked before a membership mutation"
            raise RuntimeError(message)
        membership = self._memberships.get(user_id)
        if membership is None:
            message = "Locked workspace membership is missing"
            raise RuntimeError(message)
        return membership

    @staticmethod
    def _to_record(membership: WorkspaceMembership) -> WorkspaceMembershipRecord:
        return WorkspaceMembershipRecord(
            membership_id=membership.id,
            workspace_id=membership.workspace_id,
            user_id=membership.user_id,
            role=membership.role.value,
        )


@dataclass(frozen=True, slots=True)
class SqlAlchemyWorkspaceMembershipTransactionFactory:
    """Open a transaction and translate infrastructure failures."""

    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[WorkspaceMembershipWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[WorkspaceMembershipWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyWorkspaceMembershipWriter(session)
        except SQLAlchemyError as error:
            raise WorkspacePersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyWorkspaceQueryRepository:
    """Read members while independently rechecking the actor's current role."""

    session_factory: AsyncSessionFactory

    async def list_members(self, scope: WorkspaceScope) -> tuple[WorkspaceMemberSummary, ...]:
        actor_membership = aliased(WorkspaceMembership)
        actor_user = aliased(User)
        actor_is_authorized = exists(
            select(actor_membership.id)
            .join(actor_user, actor_user.id == actor_membership.user_id)
            .where(
                actor_membership.workspace_id == scope.workspace_id,
                actor_membership.user_id == scope.user_id,
                actor_membership.role.in_((WorkspaceRole.OWNER, WorkspaceRole.ADMIN)),
                actor_user.status == UserStatus.ACTIVE,
            )
        )

        try:
            async with self.session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            WorkspaceMembership.id,
                            WorkspaceMembership.user_id,
                            User.email,
                            WorkspaceMembership.role,
                            User.status,
                        )
                        .join(User, User.id == WorkspaceMembership.user_id)
                        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                        .where(
                            WorkspaceMembership.workspace_id == scope.workspace_id,
                            Workspace.status == WorkspaceStatus.ACTIVE,
                            actor_is_authorized,
                        )
                        .order_by(User.email, User.id)
                    )
                ).all()
        except SQLAlchemyError as error:
            raise WorkspacePersistenceError(sqlstate=safe_sqlstate(error)) from None

        if not rows:
            raise WorkspaceAccessDeniedError

        return tuple(
            WorkspaceMemberSummary(
                membership_id=membership_id,
                user_id=user_id,
                email=NormalizedEmail(email),
                role=cast(WorkspaceRoleName, role.value),
                account_status=cast(AccountStatus, account_status.value),
            )
            for membership_id, user_id, email, role, account_status in rows
        )
