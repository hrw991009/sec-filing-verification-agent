"""PostgreSQL integration coverage for workspace membership invariants."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.domain import TraceId
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
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    LastWorkspaceOwnerError,
    RemoveWorkspaceMemberCommand,
    WorkspaceAccessDeniedError,
    WorkspaceMembershipNotFoundError,
)
from industry_platform.modules.workspaces.resources import (
    create_workspace_membership_service,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


def test_unauthorized_mutations_do_not_wait_for_target_row_locks(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Target membership and User locks must not delay authorization rejection."""

    owner_id = uuid4()
    viewer_id = uuid4()
    nonmember_id = uuid4()
    target_id = uuid4()
    target_membership_id = uuid4()
    workspace_id = uuid4()
    other_workspace_id = uuid4()

    with Session(migrated_postgres_probe.engine) as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    email=f"authorization-order-{user_id}@example.com",
                    password_hash=str(user_id),
                    status=UserStatus.ACTIVE,
                )
                for user_id in (owner_id, viewer_id, nonmember_id, target_id)
            ]
        )
        session.add_all(
            [
                Workspace(
                    id=workspace_id,
                    name="Authorization before target lock",
                    created_by_user_id=owner_id,
                    status=WorkspaceStatus.ACTIVE,
                ),
                Workspace(
                    id=other_workspace_id,
                    name="Foreign actor workspace",
                    created_by_user_id=nonmember_id,
                    status=WorkspaceStatus.ACTIVE,
                ),
            ]
        )
        session.add_all(
            [
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=viewer_id,
                    role=WorkspaceRole.VIEWER,
                ),
                WorkspaceMembership(
                    id=target_membership_id,
                    workspace_id=workspace_id,
                    user_id=target_id,
                    role=WorkspaceRole.MEMBER,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=other_workspace_id,
                    user_id=nonmember_id,
                    role=WorkspaceRole.OWNER,
                ),
            ]
        )
        session.commit()

    async def exercise(*actor_ids: UUID) -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        service = create_workspace_membership_service(create_database_session_factory(engine))
        try:
            for actor_id in actor_ids:
                async with asyncio.timeout(3):
                    with pytest.raises(WorkspaceAccessDeniedError):
                        await service.remove_member(
                            RemoveWorkspaceMemberCommand(
                                workspace_id,
                                actor_id,
                                target_id,
                                TraceId("authorization-before-target-lock"),
                            )
                        )
        finally:
            await engine.dispose()

    with Session(migrated_postgres_probe.engine) as workspace_blocker:
        locked_workspace_id = workspace_blocker.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(nowait=True)
        )
        assert locked_workspace_id == workspace_id

        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(exercise(nonmember_id))

    with Session(migrated_postgres_probe.engine) as blocker:
        locked_target_membership_id = blocker.scalar(
            select(WorkspaceMembership.id)
            .where(WorkspaceMembership.id == target_membership_id)
            .with_for_update(nowait=True)
        )
        locked_target_id = blocker.scalar(
            select(User.id).where(User.id == target_id).with_for_update(nowait=True)
        )
        assert locked_target_membership_id == target_membership_id
        assert locked_target_id == target_id

        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(exercise(viewer_id, nonmember_id))


def test_workspace_mutations_preserve_role_and_tenant_invariants(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    owner_id = uuid4()
    admin_id = uuid4()
    member_id = uuid4()
    outsider_id = uuid4()
    disabled_id = uuid4()
    missing_id = uuid4()
    workspace_id = uuid4()
    other_workspace_id = uuid4()

    with Session(migrated_postgres_probe.engine) as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    email=f"{label}-{user_id}@example.com",
                    password_hash=str(user_id),
                    status=UserStatus.ACTIVE,
                )
                for label, user_id in (
                    ("owner", owner_id),
                    ("admin", admin_id),
                    ("member", member_id),
                    ("outsider", outsider_id),
                )
            ]
        )
        session.add(
            User(
                id=disabled_id,
                email=f"disabled-{disabled_id}@example.com",
                password_hash=str(disabled_id),
                status=UserStatus.DISABLED,
            )
        )
        session.add_all(
            [
                Workspace(
                    id=workspace_id,
                    name="Workspace policy probe",
                    created_by_user_id=owner_id,
                    status=WorkspaceStatus.ACTIVE,
                ),
                Workspace(
                    id=other_workspace_id,
                    name="Other workspace policy probe",
                    created_by_user_id=outsider_id,
                    status=WorkspaceStatus.ACTIVE,
                ),
            ]
        )
        session.add_all(
            [
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=admin_id,
                    role=WorkspaceRole.ADMIN,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=member_id,
                    role=WorkspaceRole.MEMBER,
                ),
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=other_workspace_id,
                    user_id=outsider_id,
                    role=WorkspaceRole.OWNER,
                ),
            ]
        )
        session.commit()

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        service = create_workspace_membership_service(create_database_session_factory(engine))
        try:
            with pytest.raises(WorkspaceAccessDeniedError):
                await service.remove_member(
                    RemoveWorkspaceMemberCommand(
                        workspace_id,
                        admin_id,
                        owner_id,
                        TraceId("admin-owner-denied"),
                    )
                )

            changed = await service.change_member_role(
                ChangeWorkspaceMemberRoleCommand(
                    workspace_id,
                    owner_id,
                    member_id,
                    "admin",
                    TraceId("owner-promotes-member"),
                )
            )
            assert changed.role == "admin"

            with pytest.raises(LastWorkspaceOwnerError):
                await service.remove_member(
                    RemoveWorkspaceMemberCommand(
                        workspace_id,
                        owner_id,
                        owner_id,
                        TraceId("last-owner-denied"),
                    )
                )

            with pytest.raises(WorkspaceMembershipNotFoundError):
                await service.remove_member(
                    RemoveWorkspaceMemberCommand(
                        workspace_id,
                        owner_id,
                        outsider_id,
                        TraceId("cross-tenant-denied"),
                    )
                )

            with pytest.raises(WorkspaceAccessDeniedError):
                await service.remove_member(
                    RemoveWorkspaceMemberCommand(
                        workspace_id,
                        outsider_id,
                        member_id,
                        TraceId("cross-tenant-actor-denied"),
                    )
                )

            for unavailable_user_id in (disabled_id, missing_id):
                with pytest.raises(WorkspaceMembershipNotFoundError):
                    await service.add_member(
                        AddWorkspaceMemberCommand(
                            workspace_id,
                            owner_id,
                            unavailable_user_id,
                            "viewer",
                            TraceId("unavailable-target-denied"),
                        )
                    )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())

    with Session(migrated_postgres_probe.engine) as session:
        workspace_memberships = session.scalars(
            select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id)
        ).all()
        other_membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == other_workspace_id,
                WorkspaceMembership.user_id == outsider_id,
            )
        )
        last_owner_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "workspace.members.remove",
                AuditLog.actor_user_id == owner_id,
                AuditLog.resource_id == owner_id,
            )
        )

        roles = {membership.user_id: membership.role for membership in workspace_memberships}
        assert roles[owner_id] is WorkspaceRole.OWNER
        assert roles[member_id] is WorkspaceRole.ADMIN
        assert other_membership is not None
        assert other_membership.role is WorkspaceRole.OWNER
        assert last_owner_audit is not None
        assert last_owner_audit.outcome is AuditOutcome.DENIED
        assert last_owner_audit.sanitized_metadata == {"reason": "last_owner"}


def test_concurrent_owner_removals_preserve_one_owner(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    first_owner_id = uuid4()
    second_owner_id = uuid4()
    workspace_id = uuid4()

    with Session(migrated_postgres_probe.engine) as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    email=f"concurrent-owner-{user_id}@example.com",
                    password_hash=str(user_id),
                    status=UserStatus.ACTIVE,
                )
                for user_id in (first_owner_id, second_owner_id)
            ]
        )
        session.add(
            Workspace(
                id=workspace_id,
                name="Concurrent owner protection",
                created_by_user_id=first_owner_id,
                status=WorkspaceStatus.ACTIVE,
            )
        )
        session.add_all(
            [
                WorkspaceMembership(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=WorkspaceRole.OWNER,
                )
                for user_id in (first_owner_id, second_owner_id)
            ]
        )
        session.commit()

    async def exercise() -> tuple[str, str]:
        engine = create_database_engine(migrated_postgres_probe.settings)
        service = create_workspace_membership_service(create_database_session_factory(engine))

        async def remove_self(user_id: UUID) -> str:
            try:
                await service.remove_member(
                    RemoveWorkspaceMemberCommand(
                        workspace_id,
                        user_id,
                        user_id,
                        TraceId("concurrent-owner-removal"),
                    )
                )
            except LastWorkspaceOwnerError:
                return "last_owner"
            return "removed"

        try:
            async with asyncio.timeout(30):
                first, second = await asyncio.gather(
                    remove_self(first_owner_id),
                    remove_self(second_owner_id),
                )
                return first, second
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        outcomes = runner.run(exercise())

    assert sorted(outcomes) == ["last_owner", "removed"]

    with Session(migrated_postgres_probe.engine) as session:
        remaining_owners = session.scalars(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == WorkspaceRole.OWNER,
            )
        ).all()
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "workspace.members.remove",
            )
        ).all()

        assert len(remaining_owners) == 1
        assert {audit.outcome for audit in audits} == {
            AuditOutcome.SUCCEEDED,
            AuditOutcome.DENIED,
        }
