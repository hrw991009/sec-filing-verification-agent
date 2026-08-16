"""HTTP contract tests for Agent fetch-SSE replay and explicit cancellation."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse
from starlette.requests import Request

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime import router as delivery_router
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryUnavailableError,
    AgentRunDeliveryUseCase,
    AgentRunNotFoundError,
    AgentRunStreamDescriptor,
    PreparedAgentEventStream,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.router import get_agent_run_delivery_service
from industry_platform.modules.agent_runtime.streaming import (
    InvalidStreamCursorError,
    StreamCursorAheadError,
    StreamReplay,
    StreamResetRequiredError,
    StreamSnapshot,
    encode_agent_event_sse,
    encode_heartbeat_sse,
    encode_snapshot_sse,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 14, 12, 30, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
STREAM_ID = UUID("66666666-6666-4666-8666-666666666666")
TRACE_ID = TraceId("agent-delivery-api-test")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubDeliveryService:
    failure: Exception | None = None
    prepared_value: PreparedAgentEventStream | None = None
    follow_batches: list[tuple[AgentEvent, ...]] = field(default_factory=list)
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def prepare_stream(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        last_event_id: str | None,
    ) -> PreparedAgentEventStream:
        self.calls.append(("prepare", (scope, run_id, last_event_id)))
        if self.failure is not None:
            raise self.failure
        if self.prepared_value is not None:
            return self.prepared_value
        events = terminal_events()
        cursor = int(last_event_id) if last_event_id is not None else 0
        return PreparedAgentEventStream(
            descriptor=descriptor(),
            replay=StreamReplay(
                cursor=cursor,
                snapshot=None,
                events=tuple(event for event in events if event.sequence > cursor),
            ),
        )

    async def load_events_after(
        self,
        scope: WorkspaceScope,
        *,
        descriptor: AgentRunStreamDescriptor,
        after_sequence: int,
    ) -> tuple[AgentEvent, ...]:
        self.calls.append(("follow", (scope, descriptor, after_sequence)))
        return self.follow_batches.pop(0) if self.follow_batches else ()

    async def request_cancel(self, scope: WorkspaceScope, *, run_id: UUID) -> None:
        self.calls.append(("cancel", (scope, run_id)))
        if self.failure is not None:
            raise self.failure


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def descriptor(
    *,
    status: AgentRunStatus = AgentRunStatus.FAILED,
    latest_committed_sequence: int = 2,
) -> AgentRunStreamDescriptor:
    return AgentRunStreamDescriptor(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        stream_id=STREAM_ID,
        trace_id=TRACE_ID,
        status=status,
        latest_committed_sequence=latest_committed_sequence,
    )


def terminal_events() -> tuple[AgentEvent, AgentEvent]:
    return (
        AgentEvent(
            schema_version=1,
            stream_id=STREAM_ID,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            sequence=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            event_type=AgentEventType.RUN_QUEUED,
            payload={},
        ),
        AgentEvent(
            schema_version=1,
            stream_id=STREAM_ID,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            sequence=2,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            event_type=AgentEventType.RUN_FAILED,
            payload={"stop_reason": "runtime_error"},
        ),
    )


@contextmanager
def delivery_client(
    settings: Settings,
    service: AgentRunDeliveryUseCase,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_agent_run_delivery_service] = lambda: service
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def assert_problem(response: HttpxResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == code


def test_fetch_sse_replays_committed_events_and_honors_last_event_id(
    test_settings: Settings,
) -> None:
    service = StubDeliveryService()
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/events"
    with delivery_client(test_settings, service) as client:
        initial = client.get(url, headers=bearer_header())
        resumed = client.get(url, headers={**bearer_header(), "Last-Event-ID": "1"})
        already_complete = client.get(
            url,
            headers={**bearer_header(), "Last-Event-ID": "2"},
        )
        openapi = client.get("/openapi.json").json()

    queued, failed = terminal_events()
    assert initial.status_code == 200
    assert initial.headers["content-type"].startswith("text/event-stream")
    assert initial.headers["cache-control"] == "no-store"
    assert initial.headers["x-accel-buffering"] == "no"
    assert initial.content == encode_agent_event_sse(queued) + encode_agent_event_sse(failed)
    assert resumed.content == encode_agent_event_sse(failed)
    assert already_complete.content == b""
    operation = openapi["paths"]["/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/events"][
        "get"
    ]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert [call[0] for call in service.calls] == ["prepare", "prepare", "prepare"]


def test_expired_cursor_receives_authoritative_snapshot_and_terminal_stream_closes(
    test_settings: Settings,
) -> None:
    snapshot = StreamSnapshot(
        schema_version=1,
        stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        trace_id=TRACE_ID,
        last_sequence=24,
        occurred_at=NOW,
        payload={
            "run_id": str(RUN_ID),
            "status": "failed",
            "stop_reason": "runtime_error",
            "terminal": True,
            "content_markdown": "已提交的回答片段",
        },
    )
    fallback_terminal = AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=25,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=AgentEventType.RUN_FAILED,
        payload={"stop_reason": "runtime_error"},
    )
    service = StubDeliveryService(
        prepared_value=PreparedAgentEventStream(
            descriptor=descriptor(
                status=AgentRunStatus.RUNNING,
                latest_committed_sequence=23,
            ),
            replay=StreamReplay(cursor=1, snapshot=snapshot, events=()),
        ),
        follow_batches=[(fallback_terminal,)],
    )
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/events"

    with delivery_client(test_settings, service) as client:
        response = client.get(
            url,
            headers={**bearer_header(), "Last-Event-ID": "1"},
        )

    assert response.status_code == 200
    assert response.content == encode_snapshot_sse(snapshot)
    assert [call[0] for call in service.calls] == ["prepare"]


def test_cancel_is_explicit_idempotent_http_202(test_settings: Settings) -> None:
    service = StubDeliveryService()
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/cancel"
    with delivery_client(test_settings, service) as client:
        response = client.post(url, headers=bearer_header())

    assert response.status_code == 202
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == [("cancel", (WorkspaceScope(WORKSPACE_ID, USER_ID, "member"), RUN_ID))]


@pytest.mark.asyncio
async def test_live_stream_heartbeats_without_advancing_cursor_then_closes_on_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued, failed = terminal_events()
    service = StubDeliveryService(follow_batches=[(), (failed,)])
    prepared = PreparedAgentEventStream(
        descriptor=descriptor(
            status=AgentRunStatus.RUNNING,
            latest_committed_sequence=1,
        ),
        replay=StreamReplay(cursor=0, snapshot=None, events=(queued,)),
    )

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr(delivery_router, "DEFAULT_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(delivery_router, "POLL_INTERVAL_SECONDS", 0)
    frames = [
        frame
        async for frame in delivery_router._committed_event_frames(
            cast(Request, ConnectedRequest()),
            service,
            WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
            prepared,
        )
    ]

    assert frames == [
        encode_agent_event_sse(queued),
        encode_heartbeat_sse(last_sequence=1),
        encode_agent_event_sse(failed),
    ]
    assert all(call[0] != "cancel" for call in service.calls)


@pytest.mark.asyncio
async def test_live_stream_pulls_next_bounded_batch_only_after_current_batch_is_consumed() -> None:
    queued, _ = terminal_events()
    delta_two = AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=2,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=AgentEventType.MODEL_DELTA,
        payload={"delta": "慢"},
    )
    delta_three = AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=3,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=AgentEventType.MODEL_DELTA,
        payload={"delta": "客户端"},
    )
    failed = AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=4,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=AgentEventType.RUN_FAILED,
        payload={"stop_reason": "runtime_error"},
    )
    service = StubDeliveryService(follow_batches=[(delta_two, delta_three), (failed,)])
    prepared = PreparedAgentEventStream(
        descriptor=descriptor(
            status=AgentRunStatus.RUNNING,
            latest_committed_sequence=1,
        ),
        replay=StreamReplay(cursor=0, snapshot=None, events=(queued,)),
    )

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    frames = delivery_router._committed_event_frames(
        cast(Request, ConnectedRequest()),
        service,
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        prepared,
    )

    assert await anext(frames) == encode_agent_event_sse(queued)
    assert [call[0] for call in service.calls] == []
    assert await anext(frames) == encode_agent_event_sse(delta_two)
    assert [call[0] for call in service.calls] == ["follow"]
    assert await anext(frames) == encode_agent_event_sse(delta_three)
    assert [call[0] for call in service.calls] == ["follow"]
    assert await anext(frames) == encode_agent_event_sse(failed)
    assert [call[0] for call in service.calls] == ["follow", "follow"]
    with pytest.raises(StopAsyncIteration):
        await anext(frames)


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (AgentRunNotFoundError(), 404, "AGENT_RUN_NOT_FOUND"),
        (InvalidStreamCursorError(), 400, "INVALID_STREAM_CURSOR"),
        (StreamCursorAheadError(), 409, "STREAM_CURSOR_AHEAD"),
        (StreamResetRequiredError(), 409, "STREAM_RESET_REQUIRED"),
        (
            AgentRunDeliveryUnavailableError(sqlstate="08006"),
            503,
            "AGENT_EVENT_DELIVERY_UNAVAILABLE",
        ),
    ],
)
def test_preflight_failures_remain_problem_responses_before_sse_headers(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubDeliveryService(failure=failure)
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/events"
    with delivery_client(test_settings, service) as client:
        response = client.get(url, headers=bearer_header())

    assert_problem(response, status_code, code)


def test_stream_requires_authentication_and_current_workspace_membership(
    test_settings: Settings,
) -> None:
    service = StubDeliveryService()
    root = "/api/v1/workspaces"
    with delivery_client(test_settings, service) as client:
        unauthenticated = client.get(f"{root}/{WORKSPACE_ID}/agent-runs/{RUN_ID}/events")
        outside_scope = client.get(
            f"{root}/{OTHER_WORKSPACE_ID}/agent-runs/{RUN_ID}/events",
            headers=bearer_header(),
        )
        cors = client.options(
            f"{root}/{WORKSPACE_ID}/agent-runs/{RUN_ID}/events",
            headers={
                "Origin": "https://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization,Last-Event-ID",
            },
        )

    assert_problem(unauthenticated, 401, "INVALID_AUTHENTICATED_SESSION")
    assert_problem(outside_scope, 403, "WORKSPACE_ACCESS_DENIED")
    assert service.calls == []
    assert cors.status_code == 200
    assert "last-event-id" in cors.headers["access-control-allow-headers"].lower()
