"""Exercise the Memory write journey against real PostgreSQL and HTTP contracts."""

import asyncio
from ipaddress import IPv6Address
from typing import TypedDict, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.memory.domain import (
    MemoryConflictError,
    MemoryKind,
    MemoryResolutionResult,
    MemoryScope,
    MemoryWriteAction,
    ResolveMemoryCandidate,
)
from industry_platform.modules.memory.resources import create_memory_resources
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"


class RegisteredUser(TypedDict):
    id: str


class RegisteredWorkspace(TypedDict):
    id: str


class RegistrationPayload(TypedDict):
    user: RegisteredUser
    workspace: RegisteredWorkspace


def test_memory_candidate_confirmation_is_durable_idempotent_and_scoped(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    application = create_app(settings=settings)
    trusted_origin = settings.browser_trusted_origins[0]

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(str(IPv6Address(uuid4().int)), 50_025),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registration, access_token = _register_and_login(client, "owner")
        outsider_registration, outsider_token = _register_and_login(client, "outsider")
        workspace_id = UUID(registration["workspace"]["id"])
        outsider_workspace_id = UUID(outsider_registration["workspace"]["id"])
        conversation_id, message_id = _start_message(
            client,
            workspace_id,
            access_token,
            question="以后请优先使用中文回答。",
            key="memory-source-turn-1",
        )
        root = f"/api/v1/workspaces/{workspace_id}/memories"
        candidate_headers = {
            **_bearer(access_token),
            "Idempotency-Key": "memory-candidate-postgres-1",
        }
        candidate_payload = {
            "conversation_id": str(conversation_id),
            "message_ids": [str(message_id)],
            "scope": "user",
        }

        created = client.post(
            f"{root}/candidates", headers=candidate_headers, json=candidate_payload
        )
        repeated = client.post(
            f"{root}/candidates", headers=candidate_headers, json=candidate_payload
        )
        candidate_id = created.json()["id"]
        confirm_payload = {
            "action": "create",
            "content": "默认使用中文回答。",
            "expires_at": None,
            "kind": "preference",
            "scope": "user",
            "target_memory_id": None,
            "target_revision": None,
        }
        confirmed = client.post(
            f"{root}/candidates/{candidate_id}/confirm",
            headers={**_bearer(access_token), "If-Match": '"1"'},
            json=confirm_payload,
        )
        assert confirmed.status_code == 200, confirmed.text
        repeated_confirm = client.post(
            f"{root}/candidates/{candidate_id}/confirm",
            headers={**_bearer(access_token), "If-Match": '"1"'},
            json=confirm_payload,
        )
        changed_retry = client.post(
            f"{root}/candidates/{candidate_id}/confirm",
            headers={**_bearer(access_token), "If-Match": '"1"'},
            json={**confirm_payload, "content": "改成另一条内容。"},
        )
        memory_id = confirmed.json()["memory"]["memory"]["id"]

        update_candidate = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-candidate-postgres-update",
            },
            json=candidate_payload,
        )
        updated = client.post(
            f"{root}/candidates/{update_candidate.json()['id']}/confirm",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                **confirm_payload,
                "action": "update",
                "content": "默认使用中文回答，并保持简洁。",  # noqa: RUF001
                "target_memory_id": memory_id,
                "target_revision": 1,
            },
        )
        merge_candidate = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-candidate-postgres-merge",
            },
            json=candidate_payload,
        )
        merged = client.post(
            f"{root}/candidates/{merge_candidate.json()['id']}/confirm",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                **confirm_payload,
                "action": "merge",
                "content": "默认使用中文回答，并保持简洁且附来源。",  # noqa: RUF001
                "target_memory_id": memory_id,
                "target_revision": 2,
            },
        )
        reject_candidate = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-candidate-postgres-reject",
            },
            json=candidate_payload,
        )
        rejected = client.post(
            f"{root}/candidates/{reject_candidate.json()['id']}/reject",
            headers={**_bearer(access_token), "If-Match": "1"},
        )
        listed = client.get(root, headers=_bearer(access_token))
        detail = client.get(f"{root}/{memory_id}", headers=_bearer(access_token))
        outsider = client.get(
            f"/api/v1/workspaces/{outsider_workspace_id}/memories/{memory_id}",
            headers=_bearer(outsider_token),
        )
        cross_workspace_source = client.post(
            f"/api/v1/workspaces/{outsider_workspace_id}/memories/candidates",
            headers={
                **_bearer(outsider_token),
                "Idempotency-Key": "memory-candidate-cross-workspace",
            },
            json=candidate_payload,
        )

        sensitive_conversation_id, sensitive_message_id = _start_message(
            client,
            workspace_id,
            access_token,
            question="api_key = sk-sensitive-value-do-not-store",
            key="memory-source-turn-sensitive",
        )
        sensitive = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-candidate-sensitive-1",
            },
            json={
                "conversation_id": str(sensitive_conversation_id),
                "message_ids": [str(sensitive_message_id)],
                "scope": "user",
            },
        )
        deleted_conversation_id, deleted_message_id = _start_message(
            client,
            workspace_id,
            access_token,
            question="这条来源将在确认前失效。",
            key="memory-source-turn-deleted",
        )
        unavailable_candidate = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-candidate-unavailable-1",
            },
            json={
                "conversation_id": str(deleted_conversation_id),
                "message_ids": [str(deleted_message_id)],
                "scope": "user",
            },
        )
        deleted_source = client.delete(
            f"/api/v1/workspaces/{workspace_id}/conversations/{deleted_conversation_id}",
            headers=_bearer(access_token),
        )
        unavailable_confirmation = client.post(
            f"{root}/candidates/{unavailable_candidate.json()['id']}/confirm",
            headers={**_bearer(access_token), "If-Match": "1"},
            json=confirm_payload,
        )

    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["status"] == "candidate"
    assert created.json()["suggested_content"] == "以后请优先使用中文回答。"
    assert repeated.status_code == 201
    assert repeated.json() == {**created.json(), "created": False}
    assert confirmed.status_code == 200
    assert confirmed.json()["created"] is True
    assert confirmed.json()["memory"]["current_revision"]["content"] == "默认使用中文回答。"
    assert repeated_confirm.status_code == 200
    assert repeated_confirm.json() == {**confirmed.json(), "created": False}
    assert changed_retry.status_code == 409
    assert changed_retry.json()["code"] == "MEMORY_CONFLICT"
    assert update_candidate.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["action"] == "update"
    assert updated.json()["memory"]["memory"]["current_version"] == 2
    assert merge_candidate.status_code == 201
    assert merged.status_code == 200
    assert merged.json()["action"] == "merge"
    assert merged.json()["memory"]["memory"]["current_version"] == 3
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["revision"] == 2
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["id"] == memory_id
    assert detail.status_code == 200
    assert detail.json()["memory"]["current_version"] == 3
    assert detail.json()["current_revision"]["content"] == (
        "默认使用中文回答，并保持简洁且附来源。"  # noqa: RUF001
    )
    assert detail.json()["current_revision"]["source_message_ids"] == [str(message_id)]
    assert outsider.status_code == 404
    assert outsider.json()["code"] == "MEMORY_NOT_FOUND"
    assert cross_workspace_source.status_code == 404
    assert cross_workspace_source.json()["code"] == "MEMORY_SOURCE_NOT_FOUND"
    assert sensitive.status_code == 201
    assert sensitive.json()["status"] == "rejected"
    assert sensitive.json()["policy_reason"] == "sensitive_content"
    assert sensitive.json()["suggested_content"] is None
    assert "sk-sensitive-value" not in sensitive.text
    assert unavailable_candidate.status_code == 201
    assert deleted_source.status_code == 204
    assert unavailable_confirmation.status_code == 404
    assert unavailable_confirmation.json()["code"] == "MEMORY_SOURCE_NOT_FOUND"

    with migrated_postgres_probe.engine.connect() as connection:
        revision_count = connection.execute(
            text("SELECT count(*) FROM memory_revisions WHERE memory_id = :memory_id"),
            {"memory_id": UUID(memory_id)},
        ).scalar_one()
        audit_metadata = (
            connection.execute(
                text(
                    "SELECT sanitized_metadata::text FROM audit_logs "
                    "WHERE resource_type IN ('memory', 'memory_candidate')"
                )
            )
            .scalars()
            .all()
        )

    assert revision_count == 3
    assert all("默认使用中文回答" not in metadata for metadata in audit_metadata)
    assert all("sk-sensitive-value" not in metadata for metadata in audit_metadata)


def test_concurrent_memory_confirmations_have_one_cas_winner(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    application = create_app(settings=settings)
    trusted_origin = settings.browser_trusted_origins[0]

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(str(IPv6Address(uuid4().int)), 50_026),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registration, access_token = _register_and_login(client, "concurrent")
        workspace_id = UUID(registration["workspace"]["id"])
        user_id = UUID(registration["user"]["id"])
        conversation_id, message_id = _start_message(
            client,
            workspace_id,
            access_token,
            question="这条偏好只能确认一次。",
            key="memory-concurrent-source",
        )
        candidate = client.post(
            f"/api/v1/workspaces/{workspace_id}/memories/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-concurrent-candidate",
            },
            json={
                "conversation_id": str(conversation_id),
                "message_ids": [str(message_id)],
                "scope": "user",
            },
        )
        assert candidate.status_code == 201
        candidate_id = UUID(candidate.json()["id"])

    async def exercise() -> list[MemoryResolutionResult | BaseException]:
        engine = create_database_engine(settings)
        resources = create_memory_resources(create_database_session_factory(engine))
        scoped_owner = WorkspaceScope(workspace_id, user_id, "owner")
        try:
            commands = (
                ResolveMemoryCandidate(
                    candidate_id=candidate_id,
                    expected_candidate_revision=1,
                    action=MemoryWriteAction.CREATE,
                    content=content,
                    scope=MemoryScope.USER,
                    kind=MemoryKind.PREFERENCE,
                    expires_at=None,
                    target_memory_id=None,
                    expected_target_revision=None,
                    trace_id=TraceId(f"memory-concurrent-{index}"),
                )
                for index, content in enumerate(("偏好版本 A", "偏好版本 B"), start=1)
            )
            return list(
                await asyncio.gather(
                    *(
                        resources.service.resolve_candidate(scoped_owner, command)
                        for command in commands
                    ),
                    return_exceptions=True,
                )
            )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        outcomes = runner.run(exercise())

    assert sum(isinstance(outcome, MemoryResolutionResult) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, MemoryConflictError) for outcome in outcomes) == 1


def _start_message(
    client: TestClient,
    workspace_id: UUID,
    access_token: str,
    *,
    question: str,
    key: str,
) -> tuple[UUID, UUID]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        headers={**_bearer(access_token), "Idempotency-Key": key},
        json={"question": question, "mode": "none"},
    )
    assert response.status_code == 202
    return UUID(response.json()["conversation_id"]), UUID(response.json()["user_message_id"])


def _register_and_login(
    client: TestClient,
    label: str,
) -> tuple[RegistrationPayload, str]:
    email = f"memory-http-{label}-{uuid4().hex}@example.com"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": RAW_VALUE},
    )
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": RAW_VALUE},
    )
    assert registered.status_code == 201
    assert logged_in.status_code == 200
    access_token = logged_in.json()["access_token"]
    if not isinstance(access_token, str):
        raise AssertionError("Login did not return an access token")
    return cast(RegistrationPayload, registered.json()), access_token


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}
