"""Recovery delivery does not bypass authentication, owner policy or bounded replay."""

from dataclasses import dataclass, replace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from modules.knowledge.test_api import (
    JOB_ID,
    OUTBOX_ID,
    WORKSPACE_ID,
    StubPrincipalResolver,
    principal,
)

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import AuthenticatedWorkspace, WorkspaceRoleName
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.jobs.domain import (
    JobPersistenceError,
    JobStatus,
    JobSubmissionRecord,
)
from industry_platform.modules.jobs.recovery import (
    JobRecoveryConflictError,
    JobRecoveryNotFoundError,
    JobRecoveryService,
    ReplayDeadLetter,
)
from industry_platform.modules.jobs.router import get_recovery_service
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass
class RecoveryRepository:
    error: Exception | None = None
    calls: int = 0

    async def replay(self, scope: WorkspaceScope, command: ReplayDeadLetter) -> JobSubmissionRecord:
        self.calls += 1
        assert scope.workspace_id == WORKSPACE_ID
        assert command.job_id == JOB_ID
        if self.error is not None:
            raise self.error
        return JobSubmissionRecord(JOB_ID, OUTBOX_ID, JobStatus.RETRY_WAIT, 2, True)


@pytest.mark.parametrize("role", ["owner", "member"])
def test_replay_requires_owner_and_strict_bounded_payload(
    test_settings: Settings, role: WorkspaceRoleName
) -> None:
    app = create_app(settings=test_settings)
    repository = RecoveryRepository()
    app.dependency_overrides[get_recovery_service] = lambda: JobRecoveryService(repository)
    identity = replace(
        principal(), workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Test", role),)
    )
    app.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(identity)
    route = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{JOB_ID}/replay"
    headers = {"Authorization": "Bearer test-token", "Idempotency-Key": "replay-request-key"}
    with TestClient(app, base_url="https://localhost") as client:
        assert client.post(route, json={"expected_dispatch_generation": 1}).status_code == 401
        assert (
            client.post(
                route,
                headers=headers,
                json={
                    "expected_dispatch_generation": 1,
                    "additional_attempts": 4,
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                route,
                headers=headers,
                json={
                    "expected_dispatch_generation": True,
                },
            ).status_code
            == 422
        )
        response = client.post(route, headers=headers, json={"expected_dispatch_generation": 1})
        assert response.status_code == (202 if role == "owner" else 403)
        if role == "owner":
            assert response.json()["job_id"] == str(JOB_ID)
            assert response.headers["Cache-Control"] == "no-store"
    assert repository.calls == (1 if role == "owner" else 0)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (JobRecoveryConflictError(), 409),
        (JobRecoveryNotFoundError(), 404),
        (JobPersistenceError(), 503),
    ],
)
def test_replay_errors_are_typed(test_settings: Settings, error: Exception, status: int) -> None:
    app = create_app(settings=test_settings)
    repository = RecoveryRepository(error=error)
    app.dependency_overrides[get_recovery_service] = lambda: JobRecoveryService(repository)
    owner = replace(
        principal(), workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Test", "owner"),)
    )
    app.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(owner)
    with TestClient(app, base_url="https://localhost") as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{JOB_ID}/replay",
            headers={"Authorization": "Bearer test-token", "Idempotency-Key": "replay-request-key"},
            json={"expected_dispatch_generation": 1},
        )
        assert response.status_code == status


def test_replay_command_validation() -> None:
    from industry_platform.modules.identity.domain import TraceId

    with pytest.raises(ValueError, match="non-nil"):
        ReplayDeadLetter(UUID(int=0), 1, 1, "test-key", TraceId("test"))
    with pytest.raises(ValueError, match="one to three"):
        ReplayDeadLetter(JOB_ID, 1, 4, "test-key", TraceId("test"))
