"""Tests for password replacement and all-session revocation orchestration."""

from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest
from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    ChangePasswordCommand,
    CsrfToken,
    CsrfTokenHash,
    InvalidAuthenticatedSessionError,
    InvalidBrowserSessionRequestError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    InvalidPasswordChangeError,
    NewPasswordMatchesCurrentError,
    NormalizedEmail,
    PasswordHash,
    PersistPasswordChangeCommand,
    TraceId,
    VerifiedCredentials,
)
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import PasswordChangeService

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
CHANGED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
CURRENT_RAW_VALUE = "current-horse-battery-staple"
NEW_RAW_VALUE = "replacement-horse-battery-staple"
STORED_DIGEST = PasswordHash("$argon2id$stored")
REPLACEMENT_DIGEST = PasswordHash("$argon2id$replacement")


class RecordingAuthenticationService:
    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
        user_id: UUID = USER_ID,
    ) -> None:
        self.events = events
        self.failure = failure
        self.user_id = user_id

    async def authenticate(self, command: object) -> VerifiedCredentials:
        del command
        self.events.append("authenticate")
        if self.failure is not None:
            raise self.failure
        return VerifiedCredentials(
            user_id=self.user_id,
            email=NormalizedEmail("learner@example.com"),
            expected_password_hash=STORED_DIGEST,
            password_rehash_required=False,
        )


class RecordingHasher:
    def __init__(self, events: list[str], *, new_matches: bool = False) -> None:
        self.events = events
        self.new_matches = new_matches

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        assert password.reveal() == NEW_RAW_VALUE
        self.events.append("hash")
        return REPLACEMENT_DIGEST

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        assert password_hash == STORED_DIGEST
        assert password.get_secret_value() == NEW_RAW_VALUE
        self.events.append("compare-new")
        return self.new_matches

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        del password_hash
        raise AssertionError("Password change does not inspect rehash state")


class RecordingGuard:
    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.failure = failure

    def validate_origin(self, origin: str | None) -> None:
        assert origin == "https://localhost:5173"
        self.events.append("origin")
        if self.failure is not None:
            raise self.failure

    def validate_csrf(
        self,
        *,
        cookie_value: str | None,
        header_value: str | None,
        expected_hash: CsrfTokenHash,
    ) -> CsrfToken:
        del cookie_value, header_value, expected_hash
        raise AssertionError("Password change uses Access plus Origin, not CSRF")


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.commands: list[PersistPasswordChangeCommand] = []

    async def persist_password_change(
        self,
        command: PersistPasswordChangeCommand,
    ) -> None:
        self.events.append("persist")
        self.commands.append(command)


class RecordingTransaction:
    def __init__(self, events: list[str], writer: RecordingWriter) -> None:
        self.events = events
        self.writer = writer

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
        self.events.append("transaction.rollback" if exc_type else "transaction.commit")


class RecordingTransactionFactory:
    def __init__(self, events: list[str], writer: RecordingWriter) -> None:
        self.events = events
        self.writer = writer

    def __call__(self) -> RecordingTransaction:
        self.events.append("transaction.create")
        return RecordingTransaction(self.events, self.writer)


def command(*, new_raw_value: str = NEW_RAW_VALUE) -> ChangePasswordCommand:
    return ChangePasswordCommand(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("learner@example.com"),
        origin="https://localhost:5173",
        current_password=SecretStr(CURRENT_RAW_VALUE),
        new_password=SecretStr(new_raw_value),
        trace_id=TraceId("change-password-trace"),
    )


def service(
    events: list[str],
    *,
    authentication_failure: Exception | None = None,
    authenticated_user_id: UUID = USER_ID,
    guard_failure: Exception | None = None,
    new_matches: bool = False,
) -> tuple[PasswordChangeService, RecordingWriter]:
    writer = RecordingWriter(events)
    return (
        PasswordChangeService(
            authentication_service=RecordingAuthenticationService(
                events,
                failure=authentication_failure,
                user_id=authenticated_user_id,
            ),
            password_hasher=RecordingHasher(events, new_matches=new_matches),
            browser_request_guard=RecordingGuard(events, failure=guard_failure),
            transaction_factory=RecordingTransactionFactory(events, writer),
            clock=lambda: CHANGED_AT,
        ),
        writer,
    )


@pytest.mark.asyncio
async def test_password_change_prepares_secrets_before_one_atomic_commit() -> None:
    events: list[str] = []
    use_case, writer = service(events)

    await use_case.change_password(command())

    assert events == [
        "origin",
        "authenticate",
        "compare-new",
        "hash",
        "transaction.create",
        "transaction.enter",
        "persist",
        "transaction.commit",
    ]
    persisted = writer.commands[0]
    assert persisted.user_id == USER_ID
    assert persisted.authenticated_session_id == SESSION_ID
    assert persisted.changed_at == CHANGED_AT
    assert CURRENT_RAW_VALUE not in repr(persisted)
    assert NEW_RAW_VALUE not in repr(persisted)


@pytest.mark.asyncio
async def test_wrong_current_password_never_hashes_or_opens_transaction() -> None:
    events: list[str] = []
    use_case, writer = service(
        events,
        authentication_failure=InvalidCredentialsError(),
    )

    with pytest.raises(InvalidCurrentPasswordError):
        await use_case.change_password(command())

    assert events == ["origin", "authenticate"]
    assert writer.commands == []


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected_before_authentication() -> None:
    events: list[str] = []
    use_case, writer = service(
        events,
        guard_failure=InvalidBrowserSessionRequestError(),
    )

    with pytest.raises(InvalidPasswordChangeError):
        await use_case.change_password(command())

    assert events == ["origin"]
    assert writer.commands == []


@pytest.mark.asyncio
async def test_authenticated_identity_must_match_verified_credentials() -> None:
    events: list[str] = []
    use_case, writer = service(
        events,
        authenticated_user_id=UUID("33333333-3333-4333-8333-333333333333"),
    )

    with pytest.raises(InvalidAuthenticatedSessionError):
        await use_case.change_password(command())

    assert events == ["origin", "authenticate"]
    assert writer.commands == []


@pytest.mark.asyncio
async def test_reusing_current_password_never_hashes_or_opens_transaction() -> None:
    events: list[str] = []
    use_case, writer = service(events, new_matches=True)

    with pytest.raises(NewPasswordMatchesCurrentError):
        await use_case.change_password(command())

    assert events == ["origin", "authenticate", "compare-new"]
    assert writer.commands == []


def test_password_change_command_repr_hides_both_passwords() -> None:
    rendered = repr(command())
    assert CURRENT_RAW_VALUE not in rendered
    assert NEW_RAW_VALUE not in rendered
