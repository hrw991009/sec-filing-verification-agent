"""Exercise conversation management HTTP contracts against real PostgreSQL state."""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import IPv6Address
from typing import TypedDict, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import (
    DirectAnswerTurnReceipt,
    StartDirectAnswerTurn,
)
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
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


def test_conversation_http_is_workspace_scoped_and_soft_deletes(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    application = create_app(settings=settings)
    trusted_origin = settings.browser_trusted_origins[0]

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(str(IPv6Address(uuid4().int)), 50_020),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registration, access_token = _register_and_login(client, "member")
        _outsider, outsider_token = _register_and_login(client, "outsider")
        workspace_id = UUID(registration["workspace"]["id"])
        user_id = UUID(registration["user"]["id"])
        receipt = _seed_conversation(
            migrated_postgres_probe,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        root = f"/api/v1/workspaces/{workspace_id}/conversations"

        listed = client.get(root, headers=_bearer(access_token))
        detail = client.get(f"{root}/{receipt.conversation_id}", headers=_bearer(access_token))
        messages = client.get(
            f"{root}/{receipt.conversation_id}/messages",
            headers=_bearer(access_token),
        )
        renamed = client.patch(
            f"{root}/{receipt.conversation_id}",
            headers=_bearer(access_token),
            json={"title": "Renamed in PostgreSQL"},
        )
        outsider = client.get(root, headers=_bearer(outsider_token))
        deleted = client.delete(f"{root}/{receipt.conversation_id}", headers=_bearer(access_token))
        repeated_delete = client.delete(
            f"{root}/{receipt.conversation_id}", headers=_bearer(access_token)
        )
        after_delete = client.get(
            f"{root}/{receipt.conversation_id}", headers=_bearer(access_token)
        )

    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["id"] == str(receipt.conversation_id)
    assert detail.status_code == 200
    assert detail.json()["turn_count"] == 1
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["content_markdown"] == "Explain this market."
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed in PostgreSQL"
    assert outsider.status_code == 403
    assert outsider.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert deleted.status_code == 204
    assert repeated_delete.status_code == 204
    assert after_delete.status_code == 404
    assert after_delete.json()["code"] == "CONVERSATION_NOT_FOUND"


def _register_and_login(
    client: TestClient,
    label: str,
) -> tuple[RegistrationPayload, str]:
    email = f"conversation-http-{label}-{uuid4().hex}@example.com"
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


def _seed_conversation(
    probe: PostgresProbe,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> DirectAnswerTurnReceipt:
    async def seed() -> DirectAnswerTurnReceipt:
        engine = create_database_engine(probe.settings)
        session_factory = create_database_session_factory(engine)
        now = datetime.now(UTC)
        try:
            service = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: now,
            )
            return await service.start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    trace_id=TraceId(f"trace-{uuid4().hex}"),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=2,
                        max_total_tokens=1_000,
                        max_cost_micro_usd=100_000,
                        deadline=now + timedelta(minutes=10),
                    ),
                    runtime_version="direct-answer-runtime-v0",
                    harness_version="harness-v0",
                    idempotency_key=f"conversation-http-{uuid4().hex}",
                    question="Explain this market.",
                    new_conversation_title=None,
                )
            )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        return runner.run(seed())


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}
