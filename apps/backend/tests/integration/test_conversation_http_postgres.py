"""Exercise conversation management HTTP contracts against real PostgreSQL state."""

from ipaddress import IPv6Address
from typing import TypedDict, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from industry_platform.main import create_app
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
        root = f"/api/v1/workspaces/{workspace_id}/conversations"
        submit_headers = {
            **_bearer(access_token),
            "Idempotency-Key": "conversation-http-turn-1",
        }
        accepted = client.post(
            root,
            headers=submit_headers,
            json={"question": "Explain this market.", "mode": "none"},
        )
        repeated = client.post(
            root,
            headers=submit_headers,
            json={"question": "Explain this market.", "mode": "none"},
        )
        changed_retry = client.post(
            root,
            headers=submit_headers,
            json={"question": "Explain a different market.", "mode": "none"},
        )
        unavailable_mode = client.post(
            root,
            headers={
                **_bearer(access_token),
                "Idempotency-Key": "conversation-http-web-1",
            },
            json={"question": "Search for this market.", "mode": "web"},
        )
        accepted_body = accepted.json()
        conversation_id = accepted_body["conversation_id"]

        listed = client.get(root, headers=_bearer(access_token))
        detail = client.get(f"{root}/{conversation_id}", headers=_bearer(access_token))
        messages = client.get(
            f"{root}/{conversation_id}/messages",
            headers=_bearer(access_token),
        )
        renamed = client.patch(
            f"{root}/{conversation_id}",
            headers=_bearer(access_token),
            json={"title": "Renamed in PostgreSQL"},
        )
        outsider = client.get(root, headers=_bearer(outsider_token))
        deleted = client.delete(f"{root}/{conversation_id}", headers=_bearer(access_token))
        repeated_delete = client.delete(f"{root}/{conversation_id}", headers=_bearer(access_token))
        after_delete = client.get(f"{root}/{conversation_id}", headers=_bearer(access_token))

    assert accepted.status_code == 202
    assert accepted_body["created"] is True
    assert repeated.status_code == 202
    assert repeated.json() == {**accepted_body, "created": False}
    assert changed_retry.status_code == 409
    assert changed_retry.json()["code"] == "CONVERSATION_IDEMPOTENCY_CONFLICT"
    assert unavailable_mode.status_code == 409
    assert unavailable_mode.json()["code"] == "CONVERSATION_MODE_NOT_READY"
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["id"] == conversation_id
    assert detail.status_code == 200
    assert detail.json()["turn_count"] == 1
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["content_markdown"] == "Explain this market."
    assert messages.json()["messages"][0]["search_mode"] == "none"
    assert messages.json()["messages"][0]["industry_id"] is None
    assert messages.json()["messages"][0]["knowledge_base_ids"] == []
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


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}
