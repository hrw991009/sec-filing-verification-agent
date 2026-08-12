"""Workspace membership orchestration with current database authorization."""

from dataclasses import dataclass
from uuid import UUID

from industry_platform.modules.workspaces.domain import (
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    LastWorkspaceOwnerError,
    RemoveWorkspaceMemberCommand,
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceDenialReason,
    WorkspaceMembershipCommand,
    WorkspaceMembershipConflictError,
    WorkspaceMembershipNotFoundError,
    WorkspaceMembershipRecord,
    WorkspaceMutationDecision,
)
from industry_platform.modules.workspaces.policy import (
    can_add_role,
    can_manage_membership,
    is_self_promotion,
    scope_allows,
)
from industry_platform.modules.workspaces.ports import (
    WorkspaceMembershipTransactionFactory,
    WorkspaceMembershipWriter,
)


@dataclass(frozen=True, slots=True)
class WorkspaceMembershipService:
    """Authorize and mutate memberships in the same locked transaction."""

    transaction_factory: WorkspaceMembershipTransactionFactory

    async def add_member(self, command: AddWorkspaceMemberCommand) -> WorkspaceMembershipRecord:
        return self._unwrap(await self._execute(command))

    async def change_member_role(
        self, command: ChangeWorkspaceMemberRoleCommand
    ) -> WorkspaceMembershipRecord:
        return self._unwrap(await self._execute(command))

    async def remove_member(
        self, command: RemoveWorkspaceMemberCommand
    ) -> WorkspaceMembershipRecord:
        return self._unwrap(await self._execute(command))

    async def _execute(self, command: WorkspaceMembershipCommand) -> WorkspaceMutationDecision:
        async with self.transaction_factory() as writer:
            authorization = await writer.lock_workspace_authorization(
                workspace_id=command.workspace_id,
                actor_user_id=command.actor_user_id,
            )
            if authorization is None:
                return WorkspaceMutationDecision(denial_reason="workspace_not_found")
            if authorization.actor_scope is None:
                return WorkspaceMutationDecision(denial_reason="actor_not_member")

            actor_scope = authorization.actor_scope
            action = self._action_for(command)
            if not scope_allows(actor_scope, action):
                return await self._deny(
                    writer,
                    command,
                    action=action,
                    reason="action_not_allowed",
                )

            if isinstance(command, AddWorkspaceMemberCommand) and not can_add_role(
                actor_scope,
                command.role,
            ):
                return await self._deny(
                    writer,
                    command,
                    action=WorkspaceAction.ADD_MEMBER,
                    reason="protected_role",
                )

            memberships = await writer.lock_authorized_workspace_memberships(
                workspace_id=command.workspace_id,
            )
            target = next(
                (
                    membership
                    for membership in memberships
                    if membership.user_id == command.target_user_id
                ),
                None,
            )

            if isinstance(command, AddWorkspaceMemberCommand):
                if target is not None:
                    return WorkspaceMutationDecision(denial_reason="target_already_member")
                target_users = await writer.lock_authorized_target_users(
                    target_user_id=command.target_user_id,
                    owner_user_ids=frozenset(),
                )
                if not target_users.target_user_is_active:
                    return WorkspaceMutationDecision(denial_reason="target_not_found")
                membership = await writer.add_membership(
                    workspace_id=command.workspace_id,
                    user_id=command.target_user_id,
                    role=command.role,
                )
                await self._succeed(writer, command, WorkspaceAction.ADD_MEMBER)
                return WorkspaceMutationDecision(membership=membership)

            if target is None:
                return WorkspaceMutationDecision(denial_reason="target_not_found")

            if isinstance(command, ChangeWorkspaceMemberRoleCommand):
                if is_self_promotion(actor_scope, target, command.role):
                    return await self._deny(
                        writer,
                        command,
                        action=WorkspaceAction.CHANGE_MEMBER_ROLE,
                        reason="self_promotion",
                    )
                if not can_manage_membership(
                    actor_scope,
                    target,
                    action=WorkspaceAction.CHANGE_MEMBER_ROLE,
                    requested_role=command.role,
                ):
                    return await self._deny(
                        writer,
                        command,
                        action=WorkspaceAction.CHANGE_MEMBER_ROLE,
                        reason="protected_role",
                    )
                target_users = await writer.lock_authorized_target_users(
                    target_user_id=command.target_user_id,
                    owner_user_ids=self._owner_user_ids(memberships),
                )
                if (
                    target.role == "owner"
                    and target.user_id in target_users.active_owner_user_ids
                    and command.role != "owner"
                    and len(target_users.active_owner_user_ids) == 1
                ):
                    return await self._deny(
                        writer,
                        command,
                        action=WorkspaceAction.CHANGE_MEMBER_ROLE,
                        reason="last_owner",
                    )
                membership = await writer.change_membership_role(
                    user_id=command.target_user_id,
                    role=command.role,
                )
                await self._succeed(writer, command, WorkspaceAction.CHANGE_MEMBER_ROLE)
                return WorkspaceMutationDecision(membership=membership)

            if not can_manage_membership(
                actor_scope,
                target,
                action=WorkspaceAction.REMOVE_MEMBER,
            ):
                return await self._deny(
                    writer,
                    command,
                    action=WorkspaceAction.REMOVE_MEMBER,
                    reason="protected_role",
                )
            target_users = await writer.lock_authorized_target_users(
                target_user_id=command.target_user_id,
                owner_user_ids=self._owner_user_ids(memberships),
            )
            if (
                target.role == "owner"
                and target.user_id in target_users.active_owner_user_ids
                and len(target_users.active_owner_user_ids) == 1
            ):
                return await self._deny(
                    writer,
                    command,
                    action=WorkspaceAction.REMOVE_MEMBER,
                    reason="last_owner",
                )
            membership = await writer.remove_membership(user_id=command.target_user_id)
            await self._succeed(writer, command, WorkspaceAction.REMOVE_MEMBER)
            return WorkspaceMutationDecision(membership=membership)

    @staticmethod
    async def _deny(
        writer: WorkspaceMembershipWriter,
        command: WorkspaceMembershipCommand,
        *,
        action: WorkspaceAction,
        reason: WorkspaceDenialReason,
    ) -> WorkspaceMutationDecision:
        await writer.record_audit(
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            action=action.value,
            resource_id=command.target_user_id,
            outcome="denied",
            reason=reason,
            trace_id=command.trace_id,
        )
        return WorkspaceMutationDecision(denial_reason=reason)

    @staticmethod
    async def _succeed(
        writer: WorkspaceMembershipWriter,
        command: WorkspaceMembershipCommand,
        action: WorkspaceAction,
    ) -> None:
        await writer.record_audit(
            workspace_id=command.workspace_id,
            actor_user_id=command.actor_user_id,
            action=action.value,
            resource_id=command.target_user_id,
            outcome="succeeded",
            reason=None,
            trace_id=command.trace_id,
        )

    @staticmethod
    def _action_for(command: WorkspaceMembershipCommand) -> WorkspaceAction:
        if isinstance(command, AddWorkspaceMemberCommand):
            return WorkspaceAction.ADD_MEMBER
        if isinstance(command, ChangeWorkspaceMemberRoleCommand):
            return WorkspaceAction.CHANGE_MEMBER_ROLE
        return WorkspaceAction.REMOVE_MEMBER

    @staticmethod
    def _owner_user_ids(
        memberships: tuple[WorkspaceMembershipRecord, ...],
    ) -> frozenset[UUID]:
        return frozenset(
            membership.user_id for membership in memberships if membership.role == "owner"
        )

    @staticmethod
    def _unwrap(decision: WorkspaceMutationDecision) -> WorkspaceMembershipRecord:
        if decision.membership is not None:
            return decision.membership
        if decision.denial_reason == "last_owner":
            raise LastWorkspaceOwnerError
        if decision.denial_reason == "target_not_found":
            raise WorkspaceMembershipNotFoundError
        if decision.denial_reason == "target_already_member":
            raise WorkspaceMembershipConflictError
        raise WorkspaceAccessDeniedError
