"""Tests for browser-bound and idempotent session-family logout."""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID

import pytest
from pydantic import SecretBytes

from industry_platform.modules.identity.adapters.browser_requests import (
    ExactBrowserSessionRequestGuard,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.domain import (
    CsrfToken,
    DeviceToken,
    InvalidLogoutSessionError,
    LockedRefreshRotation,
    LockedRefreshSessionState,
    LoginSessionRecord,
    LogoutSessionCommand,
    LogoutSessionUnavailableError,
    PersistRefreshSuccessorCommand,
    RecordRefreshRecoveryCommand,
    RefreshSessionPersistenceError,
    RefreshToken,
    RefreshTokenHash,
    RevokeRefreshFamilyCommand,
    TraceId,
)
from industry_platform.modules.identity.service import LogoutSessionService

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
FAMILY_ID = UUID("22222222-2222-4222-8222-222222222222")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHECKED_AT = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)


def encoded_value(fill: bytes) -> str:
    return urlsafe_b64encode(fill * 32).rstrip(b"=").decode("ascii")


REFRESH_VALUE = encoded_value(b"r")
CSRF_VALUE = encoded_value(b"c")
DEVICE_VALUE = encoded_value(b"d")


def token_service() -> HmacSessionTokenService:
    return HmacSessionTokenService(
        refresh_hmac_key=SecretBytes(b"r" * 32),
        csrf_hmac_key=SecretBytes(b"c" * 32),
        device_hmac_key=SecretBytes(b"d" * 32),
    )


def logout_command(
    *,
    origin: str = "https://localhost:5173",
    csrf_header: str = CSRF_VALUE,
) -> LogoutSessionCommand:
    return LogoutSessionCommand(
        origin=origin,
        refresh_token=RefreshToken.from_transport(REFRESH_VALUE),
        csrf_cookie_value=CSRF_VALUE,
        csrf_header_value=csrf_header,
        device_token=DeviceToken.from_transport(DEVICE_VALUE),
        trace_id=TraceId("logout-service-trace"),
    )


def locked_rotation(
    tokens: HmacSessionTokenService,
    *,
    revoked: bool = False,
) -> LockedRefreshRotation:
    revoked_at = CHECKED_AT - timedelta(minutes=1) if revoked else None
    state = LockedRefreshSessionState(
        user_id=USER_ID,
        rotation_family_id=FAMILY_ID,
        session_id=SESSION_ID,
        previous_session_id=None,
        replaced_by_session_id=None,
        refresh_token_hash=tokens.digest_refresh(RefreshToken.from_transport(REFRESH_VALUE)),
        csrf_token_hash=tokens.digest_csrf(CsrfToken.from_transport(CSRF_VALUE)),
        device_token_hash=tokens.digest_device(DeviceToken.from_transport(DEVICE_VALUE)),
        idle_expires_at=CHECKED_AT + timedelta(days=7),
        absolute_expires_at=CHECKED_AT + timedelta(days=30),
        used_at=None,
        revoked_at=revoked_at,
        recovery_envelope=None,
        recovery_expires_at=None,
    )
    return LockedRefreshRotation(
        user_status="active",
        family_id=FAMILY_ID,
        family_current_session_id=SESSION_ID,
        family_absolute_expires_at=CHECKED_AT + timedelta(days=30),
        family_revoked_at=revoked_at,
        checked_at=CHECKED_AT,
        presented=state,
        current=state,
    )


class RecordingWriter:
    def __init__(self, events: list[str], rotation: LockedRefreshRotation) -> None:
        self.events = events
        self.rotation = rotation
        self.revocations: list[RevokeRefreshFamilyCommand] = []

    async def lock_rotation(
        self,
        refresh_token_hash: RefreshTokenHash,
    ) -> LockedRefreshRotation:
        del refresh_token_hash
        self.events.append("writer.lock")
        return self.rotation

    async def persist_successor(
        self,
        command: PersistRefreshSuccessorCommand,
    ) -> LoginSessionRecord:
        raise AssertionError(f"Logout must not persist successor {command.successor_session_id}")

    async def record_recovery(self, command: RecordRefreshRecoveryCommand) -> None:
        raise AssertionError(f"Logout must not recover session {command.session_id}")

    async def revoke_family(self, command: RevokeRefreshFamilyCommand) -> None:
        self.events.append("writer.revoke")
        self.revocations.append(command)


class RecordingTransaction:
    def __init__(
        self,
        events: list[str],
        writer: RecordingWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    async def __aenter__(self) -> RecordingWriter:
        self.events.append("transaction.enter")
        return self.writer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is not None:
            self.events.append("transaction.rollback")
            return
        if self.commit_failure is not None:
            self.events.append("transaction.commit_failed")
            raise self.commit_failure
        self.events.append("transaction.commit")


class RecordingTransactionFactory:
    def __init__(
        self,
        events: list[str],
        writer: RecordingWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    def __call__(self) -> RecordingTransaction:
        self.events.append("transaction.create")
        return RecordingTransaction(
            self.events,
            self.writer,
            commit_failure=self.commit_failure,
        )


def build_service(
    tokens: HmacSessionTokenService,
    events: list[str],
    writer: RecordingWriter,
    *,
    commit_failure: Exception | None = None,
) -> LogoutSessionService:
    return LogoutSessionService(
        session_token_service=tokens,
        browser_request_guard=ExactBrowserSessionRequestGuard(
            trusted_origins=("https://localhost:5173",),
            token_service=tokens,
        ),
        transaction_factory=RecordingTransactionFactory(
            events,
            writer,
            commit_failure=commit_failure,
        ),
    )


@pytest.mark.asyncio
async def test_logout_revokes_family_then_commits() -> None:
    events: list[str] = []
    tokens = token_service()
    writer = RecordingWriter(events, locked_rotation(tokens))
    service = build_service(tokens, events, writer)

    await service.logout(logout_command())

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "writer.revoke",
        "transaction.commit",
    ]
    assert len(writer.revocations) == 1
    assert writer.revocations[0].reason == "logout"
    assert writer.revocations[0].trace_id == TraceId("logout-service-trace")


@pytest.mark.asyncio
async def test_repeating_proven_logout_is_idempotent() -> None:
    events: list[str] = []
    tokens = token_service()
    writer = RecordingWriter(events, locked_rotation(tokens, revoked=True))
    service = build_service(tokens, events, writer)

    await service.logout(logout_command())

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "transaction.commit",
    ]
    assert writer.revocations == []


@pytest.mark.asyncio
async def test_invalid_browser_proof_never_revokes_family() -> None:
    events: list[str] = []
    tokens = token_service()
    writer = RecordingWriter(events, locked_rotation(tokens))
    service = build_service(tokens, events, writer)

    with pytest.raises(InvalidLogoutSessionError):
        await service.logout(logout_command(csrf_header=encoded_value(b"x")))

    assert writer.revocations == []
    assert events[-1] == "transaction.rollback"


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected_before_opening_transaction() -> None:
    events: list[str] = []
    tokens = token_service()
    writer = RecordingWriter(events, locked_rotation(tokens))
    service = build_service(tokens, events, writer)

    with pytest.raises(InvalidLogoutSessionError):
        await service.logout(logout_command(origin="https://attacker.invalid"))

    assert events == []
    assert writer.revocations == []


@pytest.mark.asyncio
async def test_commit_failure_returns_retryable_logout_error() -> None:
    events: list[str] = []
    tokens = token_service()
    writer = RecordingWriter(events, locked_rotation(tokens))
    service = build_service(
        tokens,
        events,
        writer,
        commit_failure=RefreshSessionPersistenceError(sqlstate="40001"),
    )

    with pytest.raises(LogoutSessionUnavailableError) as exc_info:
        await service.logout(logout_command())

    assert exc_info.value.sqlstate == "40001"
    assert events[-1] == "transaction.commit_failed"
