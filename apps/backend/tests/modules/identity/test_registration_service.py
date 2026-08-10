"""Tests for registration application orchestration."""

from types import TracebackType
from uuid import UUID

import pytest
from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    InvalidEmailAddressError,
    NormalizedEmail,
    PasswordHash,
    RegisterUserCommand,
    RegistrationRecord,
    TraceId,
)
from industry_platform.modules.identity.passwords import (
    PasswordPolicyError,
    ValidatedPassword,
)
from industry_platform.modules.identity.service import RegistrationService

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


class RecordingPasswordHasher:
    """Record the secret presented to the hashing boundary."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.received_values: list[str] = []
        self.result = PasswordHash("$argon2id$test-only-encoded-value")

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        self.events.append("hash")
        self.received_values.append(password.reveal())
        return self.result

    async def verify(self, password_hash: PasswordHash, password: SecretStr) -> bool:
        del password_hash, password
        raise AssertionError("Registration must not verify an existing password")

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        del password_hash
        raise AssertionError("Registration must not inspect an existing password hash")


class RecordingRegistrationWriter:
    """Record only persistence-safe values crossing into the transaction."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[NormalizedEmail, PasswordHash, str, TraceId]] = []

    async def create_registration(
        self,
        *,
        email: NormalizedEmail,
        password_hash: PasswordHash,
        workspace_name: str,
        trace_id: TraceId,
    ) -> RegistrationRecord:
        self.events.append("writer.create_registration")
        self.calls.append((email, password_hash, workspace_name, trace_id))
        return RegistrationRecord(
            user_id=USER_ID,
            email=email,
            workspace_id=WORKSPACE_ID,
            workspace_name=workspace_name,
        )


class RecordingTransaction:
    """Minimal async transaction context used by the service test."""

    def __init__(self, events: list[str], writer: RecordingRegistrationWriter) -> None:
        self.events = events
        self.writer = writer

    async def __aenter__(self) -> RecordingRegistrationWriter:
        self.events.append("transaction.enter")
        return self.writer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.events.append("transaction.exit")


class RecordingTransactionFactory:
    """Create one observable transaction per registration attempt."""

    def __init__(self, events: list[str], writer: RecordingRegistrationWriter) -> None:
        self.events = events
        self.writer = writer
        self.call_count = 0

    def __call__(self) -> RecordingTransaction:
        self.call_count += 1
        self.events.append("transaction.create")
        return RecordingTransaction(self.events, self.writer)


@pytest.mark.asyncio
async def test_registration_hashes_before_opening_the_database_transaction() -> None:
    events: list[str] = []
    hasher = RecordingPasswordHasher(events)
    writer = RecordingRegistrationWriter(events)
    transaction_factory = RecordingTransactionFactory(events, writer)
    service = RegistrationService(
        password_hasher=hasher,
        transaction_factory=transaction_factory,
    )
    plaintext = "  keep-this-exactly  "
    trace_id = TraceId("registration-service-trace")

    command = RegisterUserCommand(
        email="  New.User@Example.COM ",
        password=SecretStr(plaintext),
        trace_id=trace_id,
    )
    result = await service.register(command)

    assert events == [
        "hash",
        "transaction.create",
        "transaction.enter",
        "writer.create_registration",
        "transaction.exit",
    ]
    assert hasher.received_values == [plaintext]
    assert transaction_factory.call_count == 1
    assert len(writer.calls) == 1

    email, encoded_hash, workspace_name, persisted_trace_id = writer.calls[0]

    assert email == NormalizedEmail("new.user@example.com")
    assert encoded_hash == hasher.result
    assert plaintext not in repr(writer.calls)
    assert plaintext not in repr(command)
    assert workspace_name == "My Workspace"
    assert persisted_trace_id == trace_id
    assert result.workspace_role == "owner"


@pytest.mark.asyncio
async def test_registration_rejects_an_invalid_email_before_hashing_or_persistence() -> None:
    events: list[str] = []
    hasher = RecordingPasswordHasher(events)
    writer = RecordingRegistrationWriter(events)
    transaction_factory = RecordingTransactionFactory(events, writer)
    service = RegistrationService(
        password_hasher=hasher,
        transaction_factory=transaction_factory,
    )

    with pytest.raises(InvalidEmailAddressError):
        await service.register(
            RegisterUserCommand(
                email="not-an-email",
                password=SecretStr("valid-password-value"),
                trace_id=TraceId("invalid-email-trace"),
            )
        )

    assert events == []
    assert transaction_factory.call_count == 0
    assert writer.calls == []


@pytest.mark.asyncio
async def test_registration_rejects_an_invalid_password_before_hashing_or_persistence() -> None:
    events: list[str] = []
    hasher = RecordingPasswordHasher(events)
    writer = RecordingRegistrationWriter(events)
    transaction_factory = RecordingTransactionFactory(events, writer)
    service = RegistrationService(
        password_hasher=hasher,
        transaction_factory=transaction_factory,
    )
    plaintext = "too-short"

    with pytest.raises(PasswordPolicyError) as exc_info:
        await service.register(
            RegisterUserCommand(
                email="person@example.com",
                password=SecretStr(plaintext),
                trace_id=TraceId("invalid-password-trace"),
            )
        )

    assert events == []
    assert transaction_factory.call_count == 0
    assert writer.calls == []
    assert plaintext not in str(exc_info.value)
