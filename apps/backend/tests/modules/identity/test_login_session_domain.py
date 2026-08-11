"""Tests for persistence-safe login-session domain values."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from industry_platform.modules.identity.domain import (
    AccessToken,
    CreateLoginSessionCommand,
    CsrfToken,
    CsrfTokenHash,
    DeviceToken,
    DeviceTokenHash,
    EstablishedLoginSession,
    LoginSessionRecord,
    NormalizedEmail,
    PasswordHash,
    RefreshToken,
    RefreshTokenHash,
    TraceId,
)

USER_ID = UUID("55555555-5555-4555-8555-555555555555")
ISSUED_AT = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def create_command(
    *,
    issued_at: datetime = ISSUED_AT,
    idle_expires_at: datetime = ISSUED_AT + timedelta(days=7),
    absolute_expires_at: datetime = ISSUED_AT + timedelta(days=30),
    refresh_token_hash: bytes = b"r" * 32,
) -> CreateLoginSessionCommand:
    """Build a valid command while allowing one invariant to change."""

    return CreateLoginSessionCommand(
        user_id=USER_ID,
        expected_password_hash=PasswordHash("$argon2id$expected-value"),
        refresh_token_hash=RefreshTokenHash(refresh_token_hash),
        csrf_token_hash=CsrfTokenHash(b"c" * 32),
        device_token_hash=DeviceTokenHash(b"d" * 32),
        issued_at=issued_at,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
        trace_id=TraceId("login-session-domain-trace"),
    )


def test_login_session_command_accepts_utc_ordered_expirations() -> None:
    command = create_command()

    assert command.idle_expires_at == ISSUED_AT + timedelta(days=7)
    assert command.absolute_expires_at == ISSUED_AT + timedelta(days=30)
    assert "$argon2id$expected-value" not in repr(command)
    assert (b"r" * 32).hex() not in repr(command)


def test_login_session_command_rejects_non_32_byte_token_hashes() -> None:
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        create_command(refresh_token_hash=b"short")


def test_login_session_command_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_command(issued_at=ISSUED_AT.replace(tzinfo=None))


def test_login_session_command_rejects_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="must use UTC"):
        create_command(issued_at=ISSUED_AT.astimezone(timezone(timedelta(hours=8))))


@pytest.mark.parametrize(
    ("idle_expires_at", "absolute_expires_at"),
    [
        (ISSUED_AT, ISSUED_AT + timedelta(days=30)),
        (ISSUED_AT + timedelta(days=31), ISSUED_AT + timedelta(days=30)),
    ],
)
def test_login_session_command_rejects_invalid_expiration_order(
    idle_expires_at: datetime,
    absolute_expires_at: datetime,
) -> None:
    with pytest.raises(ValueError, match="expiration order"):
        create_command(
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )


def test_established_session_representation_hides_browser_credentials() -> None:
    access_value = "signed-access-transport-value"
    refresh_value = "refresh-transport-value"
    csrf_value = "csrf-transport-value"
    device_value = "device-transport-value"
    session = LoginSessionRecord(
        user_id=USER_ID,
        rotation_family_id=UUID("66666666-6666-4666-8666-666666666666"),
        session_id=UUID("77777777-7777-4777-8777-777777777777"),
        issued_at=ISSUED_AT,
        idle_expires_at=ISSUED_AT + timedelta(days=7),
        absolute_expires_at=ISSUED_AT + timedelta(days=30),
    )
    result = EstablishedLoginSession(
        email=NormalizedEmail("learner@example.com"),
        session=session,
        access_token=AccessToken.from_transport(access_value),
        access_token_expires_at=ISSUED_AT + timedelta(minutes=10),
        refresh_token=RefreshToken.from_transport(refresh_value),
        csrf_token=CsrfToken.from_transport(csrf_value),
        device_token=DeviceToken.from_transport(device_value),
    )

    rendered = repr(result)

    assert result.session.session_id == session.session_id
    assert str(result.email) not in rendered
    assert access_value not in rendered
    assert refresh_value not in rendered
    assert csrf_value not in rendered
    assert device_value not in rendered


@pytest.mark.parametrize(
    "access_token_expires_at",
    [
        ISSUED_AT,
        (ISSUED_AT + timedelta(minutes=10)).replace(tzinfo=None),
    ],
)
def test_established_session_rejects_an_invalid_access_token_expiration(
    access_token_expires_at: datetime,
) -> None:
    session = LoginSessionRecord(
        user_id=USER_ID,
        rotation_family_id=UUID("66666666-6666-4666-8666-666666666666"),
        session_id=UUID("77777777-7777-4777-8777-777777777777"),
        issued_at=ISSUED_AT,
        idle_expires_at=ISSUED_AT + timedelta(days=7),
        absolute_expires_at=ISSUED_AT + timedelta(days=30),
    )

    with pytest.raises(ValueError, match="Access token"):
        EstablishedLoginSession(
            email=NormalizedEmail("learner@example.com"),
            session=session,
            access_token=AccessToken.from_transport("invalid-window-value"),
            access_token_expires_at=access_token_expires_at,
            refresh_token=RefreshToken.from_transport("r" * 43),
            csrf_token=CsrfToken.from_transport("c" * 43),
            device_token=DeviceToken.from_transport("d" * 43),
        )
