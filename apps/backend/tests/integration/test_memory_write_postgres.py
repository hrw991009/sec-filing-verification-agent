"""Exercise the Memory write journey against real PostgreSQL and HTTP contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import IPv6Address
from typing import TypedDict, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.context import (
    ContextDecisionReason,
    MemoryContextBundle,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.memory.adapters.context import SqlAlchemyMemoryContextLoader
from industry_platform.modules.memory.adapters.sqlalchemy import SqlAlchemyMemoryRepository
from industry_platform.modules.memory.domain import (
    Memory,
    MemoryConflictError,
    MemoryKind,
    MemoryResolutionResult,
    MemoryScope,
    MemoryStatus,
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


def test_memory_recall_governance_and_deletion_have_no_next_run_residual(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    application = create_app(settings=settings)
    trusted_origin = settings.browser_trusted_origins[0]

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(str(IPv6Address(uuid4().int)), 50_027),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registration, access_token = _register_and_login(client, "recall-owner")
        outsider_registration, outsider_token = _register_and_login(client, "recall-outsider")
        workspace_id = UUID(registration["workspace"]["id"])
        outsider_workspace_id = UUID(outsider_registration["workspace"]["id"])
        conversation_a, message_id = _start_message(
            client,
            workspace_id,
            access_token,
            question="钢铁报告默认使用中文回答。",
            key="memory-recall-source-a",
        )
        conversation_b, _ = _start_message(
            client,
            workspace_id,
            access_token,
            question="钢铁报告应该使用什么语言?",
            key="memory-recall-target-b",
        )
        root = f"/api/v1/workspaces/{workspace_id}/memories"
        candidate = client.post(
            f"{root}/candidates",
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "memory-recall-candidate",
            },
            json={
                "conversation_id": str(conversation_a),
                "message_ids": [str(message_id)],
                "scope": "user",
            },
        )
        confirmed = client.post(
            f"{root}/candidates/{candidate.json()['id']}/confirm",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                "action": "create",
                "content": "钢铁报告默认使用中文回答。",
                "expires_at": None,
                "kind": "preference",
                "scope": "user",
                "target_memory_id": None,
                "target_revision": None,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        memory = confirmed.json()["memory"]["memory"]
        memory_id = UUID(memory["id"])
        memory_revision_id = UUID(memory["current_revision_id"])

        outsider_conversation, outsider_message = _start_message(
            client,
            outsider_workspace_id,
            outsider_token,
            question="钢铁报告默认使用外部 Workspace 模板。",
            key="memory-recall-outsider-source",
        )
        outsider_root = f"/api/v1/workspaces/{outsider_workspace_id}/memories"
        outsider_candidate = client.post(
            f"{outsider_root}/candidates",
            headers={
                **_bearer(outsider_token),
                "Idempotency-Key": "memory-recall-outsider-candidate",
            },
            json={
                "conversation_id": str(outsider_conversation),
                "message_ids": [str(outsider_message)],
                "scope": "user",
            },
        )
        outsider_confirmed = client.post(
            f"{outsider_root}/candidates/{outsider_candidate.json()['id']}/confirm",
            headers={**_bearer(outsider_token), "If-Match": "1"},
            json={
                "action": "create",
                "content": "钢铁报告默认使用外部 Workspace 模板。",
                "expires_at": None,
                "kind": "preference",
                "scope": "user",
                "target_memory_id": None,
                "target_revision": None,
            },
        )
        assert outsider_confirmed.status_code == 200, outsider_confirmed.text

        initial = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
        )
        searched = client.get(
            root,
            headers=_bearer(access_token),
            params={"query": "钢铁", "status": "confirmed", "scope": "user"},
        )
        negative = client.post(
            f"{root}/{memory_id}/feedback",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                "memory_revision_id": str(memory_revision_id),
                "value": "not_helpful",
                "reason": "本次任务不需要",
            },
        )
        after_negative = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
        )
        helpful = client.post(
            f"{root}/{memory_id}/feedback",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                "memory_revision_id": str(memory_revision_id),
                "value": "helpful",
                "reason": None,
            },
        )
        updated = client.patch(
            f"{root}/{memory_id}",
            headers={**_bearer(access_token), "If-Match": "1"},
            json={
                "content": "钢铁报告默认使用英文回答。",
                "scope": "user",
                "kind": "preference",
                "expires_at": None,
            },
        )
        after_update = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
        )
        disabled = client.post(
            f"{root}/{memory_id}/disable",
            headers={**_bearer(access_token), "If-Match": "2"},
        )
        after_disable = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
        )
        enabled = client.post(
            f"{root}/{memory_id}/enable",
            headers={**_bearer(access_token), "If-Match": "3"},
        )
        expires_at = datetime.now(UTC) + timedelta(days=1)
        expiring = client.patch(
            f"{root}/{memory_id}",
            headers={**_bearer(access_token), "If-Match": "4"},
            json={
                "content": "钢铁报告默认使用英文回答。",
                "scope": "user",
                "kind": "preference",
                "expires_at": expires_at.isoformat(),
            },
        )
        after_expiry = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
            now=expires_at + timedelta(seconds=1),
        )
        expired_management = _list_memories_at(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            now=expires_at + timedelta(seconds=1),
            status=MemoryStatus.EXPIRED,
        )
        confirmed_after_expiry = _list_memories_at(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            now=expires_at + timedelta(seconds=1),
            status=MemoryStatus.CONFIRMED,
        )
        restored = client.patch(
            f"{root}/{memory_id}",
            headers={**_bearer(access_token), "If-Match": "5"},
            json={
                "content": "钢铁报告默认使用英文回答。",
                "scope": "user",
                "kind": "preference",
                "expires_at": None,
            },
        )
        deleted = client.delete(
            f"{root}/{memory_id}",
            headers={**_bearer(access_token), "If-Match": "6"},
        )
        repeated_delete = client.delete(
            f"{root}/{memory_id}",
            headers={**_bearer(access_token), "If-Match": "6"},
        )
        after_delete = _load_memory_context(
            settings,
            workspace_id=workspace_id,
            user_id=UUID(registration["user"]["id"]),
            conversation_id=conversation_b,
            goal="钢铁报告应该使用什么语言?",
        )
        listed_after_delete = client.get(root, headers=_bearer(access_token))
        detail_after_delete = client.get(f"{root}/{memory_id}", headers=_bearer(access_token))

    assert searched.status_code == 200
    assert searched.json()["memories"][0]["id"] == str(memory_id)
    assert len(initial.long_term) == 1
    assert initial.long_term[0].memory_id == memory_id
    assert initial.long_term[0].decision_reason is ContextDecisionReason.INCLUDED
    assert initial.long_term[0].content == "钢铁报告默认使用中文回答。"
    assert negative.status_code == 200
    assert after_negative.long_term[0].decision_reason is (
        ContextDecisionReason.EXCLUDED_NEGATIVE_FEEDBACK
    )
    assert helpful.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["memory"]["revision"] == 2
    assert after_update.long_term[0].revision == 2
    assert after_update.long_term[0].content == "钢铁报告默认使用英文回答。"
    assert disabled.status_code == 200
    assert after_disable.long_term[0].decision_reason is ContextDecisionReason.EXCLUDED_DISABLED
    assert enabled.status_code == 200
    assert expiring.status_code == 200
    assert after_expiry.long_term[0].decision_reason is ContextDecisionReason.EXCLUDED_EXPIRED
    assert len(expired_management) == 1
    assert expired_management[0].memory_id == memory_id
    assert expired_management[0].status is MemoryStatus.EXPIRED
    assert confirmed_after_expiry == ()
    assert restored.status_code == 200
    assert deleted.status_code == 204
    assert repeated_delete.status_code == 204
    assert after_delete.long_term == ()
    assert listed_after_delete.json()["memories"] == []
    assert detail_after_delete.status_code == 404

    with migrated_postgres_probe.engine.connect() as connection:
        revision_contents = (
            connection.execute(
                text("SELECT content FROM memory_revisions WHERE memory_id = :memory_id"),
                {"memory_id": memory_id},
            )
            .scalars()
            .all()
        )
        source_count = connection.execute(
            text("SELECT count(*) FROM memory_revision_sources WHERE memory_id = :memory_id"),
            {"memory_id": memory_id},
        ).scalar_one()
    assert revision_contents
    assert set(revision_contents) == {"[deleted]"}
    assert source_count == 0


def _load_memory_context(
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    goal: str,
    now: datetime | None = None,
) -> MemoryContextBundle:
    selected_now = now or datetime.now(UTC)

    async def exercise() -> MemoryContextBundle:
        engine = create_database_engine(settings)
        loader = SqlAlchemyMemoryContextLoader(
            create_database_session_factory(engine),
            clock=lambda: selected_now,
        )
        try:
            return await loader.load(
                WorkspaceScope(workspace_id, user_id, "owner"),
                conversation_id=conversation_id,
                current_goal=goal,
                max_input_tokens=2_048,
            )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        return runner.run(exercise())


def _list_memories_at(
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    status: MemoryStatus,
) -> tuple[Memory, ...]:
    async def exercise() -> tuple[Memory, ...]:
        engine = create_database_engine(settings)
        repository = SqlAlchemyMemoryRepository(
            create_database_session_factory(engine),
            clock=lambda: now,
        )
        try:
            return await repository.list_memories(
                WorkspaceScope(workspace_id, user_id, "owner"),
                query=None,
                status=status,
                memory_scope=None,
                kind=None,
                limit=100,
            )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        return runner.run(exercise())


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
