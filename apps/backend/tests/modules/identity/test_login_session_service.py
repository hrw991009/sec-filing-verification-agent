"""Tests for complete login-session application orchestration."""

from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID

import pytest
from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenClaims,
    AccessTokenGenerationError,
    AuthenticateCredentialsCommand,
    CreateLoginSessionCommand,
    CsrfToken,
    CsrfTokenHash,
    DeviceToken,
    DeviceTokenHash,
    InvalidCredentialsError,
    IssueAccessTokenCommand,
    IssuedAccessToken,
    IssuedLoginSessionTokens,
    LoginSessionPersistenceError,
    LoginSessionRecord,
    NormalizedEmail,
    PasswordHash,
    RefreshToken,
    RefreshTokenHash,
    SessionTokenGenerationError,
    TraceId,
    VerifiedCredentials,
)
from industry_platform.modules.identity.service import (
    REFRESH_SESSION_ABSOLUTE_TTL,
    REFRESH_SESSION_IDLE_TTL,
    LoginSessionService,
)

USER_ID = UUID("88888888-8888-4888-8888-888888888888")
ROTATION_FAMILY_ID = UUID("99999999-9999-4999-8999-999999999999")
SESSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CLOCK_AT = datetime(2026, 8, 11, 3, 0, 0, 987_654, tzinfo=UTC)
ISSUED_AT = CLOCK_AT.replace(microsecond=0)
EXPECTED_HASH = PasswordHash("$argon2id$expected-login-value")
REPLACEMENT_HASH = PasswordHash("$argon2id$replacement-login-value")
RAW_VALUE = "correct horse battery staple"


def issued_tokens() -> IssuedLoginSessionTokens:
    """Return deterministic plaintext and digest pairs for orchestration tests."""

    return IssuedLoginSessionTokens(
        refresh_token=RefreshToken.from_transport("r" * 43),
        csrf_token=CsrfToken.from_transport("c" * 43),
        device_token=DeviceToken.from_transport("d" * 43),
        refresh_token_hash=RefreshTokenHash(b"r" * 32),
        csrf_token_hash=CsrfTokenHash(b"c" * 32),
        device_token_hash=DeviceTokenHash(b"d" * 32),
    )


class RecordingAuthenticationService:
    """Return one proof or fail before session side effects begin."""

    def __init__(
        self,
        events: list[str],
        *,
        rehash_required: bool = False,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.rehash_required = rehash_required
        self.failure = failure
        self.commands: list[AuthenticateCredentialsCommand] = []

    async def authenticate(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> VerifiedCredentials:
        self.events.append("authenticate")
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure

        return VerifiedCredentials(
            user_id=USER_ID,
            email=NormalizedEmail("learner@example.com"),
            expected_password_hash=EXPECTED_HASH,
            password_rehash_required=self.rehash_required,
        )


class RecordingPasswordRehasher:
    """Record only verified values sent to the legacy-hash upgrade boundary."""

    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.received_values: list[str] = []

    async def rehash_verified(self, password: SecretStr) -> PasswordHash:
        self.events.append("rehash_verified")
        self.received_values.append(password.get_secret_value())

        if self.failure is not None:
            raise self.failure

        return REPLACEMENT_HASH


class RecordingTokenService:
    """Issue one deterministic bundle while rejecting unrelated digest calls."""

    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.result = issued_tokens()

    def issue(self) -> IssuedLoginSessionTokens:
        self.events.append("tokens.issue")

        if self.failure is not None:
            raise self.failure

        return self.result

    def digest_refresh(self, token: RefreshToken) -> RefreshTokenHash:
        del token
        raise AssertionError("Login issuance must use the precomputed refresh digest")

    def digest_csrf(self, token: CsrfToken) -> CsrfTokenHash:
        del token
        raise AssertionError("Login issuance must use the precomputed CSRF digest")

    def digest_device(self, token: DeviceToken) -> DeviceTokenHash:
        del token
        raise AssertionError("Login issuance must use the precomputed device digest")


class RecordingAccessTokenCodec:
    """Record JWT issuance while keeping verification outside this service."""

    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
        claims_issued_at: datetime | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.claims_issued_at = claims_issued_at
        self.commands: list[IssueAccessTokenCommand] = []
        self.results: list[IssuedAccessToken] = []

    def issue(self, command: IssueAccessTokenCommand) -> IssuedAccessToken:
        self.events.append("access_token.issue")
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure

        claims_issued_at = (
            self.claims_issued_at if self.claims_issued_at is not None else command.issued_at
        )
        result = IssuedAccessToken(
            token=AccessToken.from_transport(".".join(("header", "payload", "signature"))),
            claims=AccessTokenClaims(
                user_id=command.user_id,
                session_id=command.session_id,
                jwt_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                issued_at=claims_issued_at,
                not_before=claims_issued_at,
                expires_at=claims_issued_at + timedelta(minutes=10),
            ),
        )
        self.results.append(result)
        return result

    def verify(
        self,
        token: AccessToken,
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        del token, now
        raise AssertionError("Login orchestration must not verify its newly issued token")


class RecordingLoginSessionWriter:
    """Record the persistence-safe command presented inside the transaction."""

    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.commands: list[CreateLoginSessionCommand] = []

    async def create_login_session(
        self,
        command: CreateLoginSessionCommand,
    ) -> LoginSessionRecord:
        self.events.append("writer.create_login_session")
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure

        return LoginSessionRecord(
            user_id=command.user_id,
            rotation_family_id=ROTATION_FAMILY_ID,
            session_id=SESSION_ID,
            issued_at=command.issued_at,
            idle_expires_at=command.idle_expires_at,
            absolute_expires_at=command.absolute_expires_at,
        )


class RecordingLoginTransaction:
    """Expose transaction entry and whether exit commits or rolls back."""

    def __init__(
        self,
        events: list[str],
        writer: RecordingLoginSessionWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    async def __aenter__(self) -> RecordingLoginSessionWriter:
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


class RecordingLoginTransactionFactory:
    """Create one observable login transaction on demand."""

    def __init__(
        self,
        events: list[str],
        writer: RecordingLoginSessionWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure
        self.call_count = 0

    def __call__(self) -> RecordingLoginTransaction:
        self.call_count += 1
        self.events.append("transaction.create")
        return RecordingLoginTransaction(
            self.events,
            self.writer,
            commit_failure=self.commit_failure,
        )


def login_command() -> AuthenticateCredentialsCommand:
    """Build one secret-bearing login request without exposing it in repr."""

    return AuthenticateCredentialsCommand(
        email="learner@example.com",
        password=SecretStr(RAW_VALUE),
        trace_id=TraceId("login-service-trace"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rehash_required", [False, True])
async def test_login_commits_safe_digests_before_returning_browser_tokens(
    rehash_required: bool,
) -> None:
    events: list[str] = []
    authentication = RecordingAuthenticationService(
        events,
        rehash_required=rehash_required,
    )
    rehasher = RecordingPasswordRehasher(events)
    token_service = RecordingTokenService(events)
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events)
    transaction_factory = RecordingLoginTransactionFactory(events, writer)

    def clock() -> datetime:
        events.append("clock")
        return CLOCK_AT

    service = LoginSessionService(
        authentication_service=authentication,
        password_rehasher=rehasher,
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=transaction_factory,
        clock=clock,
    )

    result = await service.login(login_command())

    expected_before_tokens = (
        ["authenticate", "rehash_verified"] if rehash_required else ["authenticate"]
    )
    assert events == [
        *expected_before_tokens,
        "tokens.issue",
        "clock",
        "transaction.create",
        "transaction.enter",
        "writer.create_login_session",
        "access_token.issue",
        "transaction.commit",
    ]
    assert rehasher.received_values == ([RAW_VALUE] if rehash_required else [])
    assert transaction_factory.call_count == 1
    assert len(writer.commands) == 1
    assert access_token_codec.commands == [
        IssueAccessTokenCommand(
            user_id=USER_ID,
            session_id=SESSION_ID,
            issued_at=ISSUED_AT,
        )
    ]

    persisted = writer.commands[0]
    assert persisted.user_id == USER_ID
    assert persisted.expected_password_hash == EXPECTED_HASH
    assert persisted.replacement_password_hash == (REPLACEMENT_HASH if rehash_required else None)
    assert persisted.refresh_token_hash == token_service.result.refresh_token_hash
    assert persisted.csrf_token_hash == token_service.result.csrf_token_hash
    assert persisted.device_token_hash == token_service.result.device_token_hash
    assert persisted.issued_at == ISSUED_AT
    assert persisted.idle_expires_at == ISSUED_AT + REFRESH_SESSION_IDLE_TTL
    assert persisted.absolute_expires_at == ISSUED_AT + REFRESH_SESSION_ABSOLUTE_TTL
    assert persisted.trace_id == TraceId("login-service-trace")
    assert RAW_VALUE not in repr(persisted)
    assert token_service.result.refresh_token.reveal_for_transport() not in repr(persisted)

    assert result.email == NormalizedEmail("learner@example.com")
    assert result.session.session_id == SESSION_ID
    assert result.access_token == access_token_codec.results[0].token
    assert result.access_token_expires_at == access_token_codec.results[0].claims.expires_at
    assert result.refresh_token == token_service.result.refresh_token
    assert result.csrf_token == token_service.result.csrf_token
    assert result.device_token == token_service.result.device_token
    assert RAW_VALUE not in repr(result)
    assert result.access_token.reveal_for_transport() not in repr(result)
    assert (b"r" * 32).hex() not in repr(result)


@pytest.mark.asyncio
async def test_authentication_failure_prevents_rehash_tokens_and_transaction() -> None:
    events: list[str] = []
    authentication = RecordingAuthenticationService(
        events,
        failure=InvalidCredentialsError(),
    )
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events)
    transaction_factory = RecordingLoginTransactionFactory(events, writer)
    service = LoginSessionService(
        authentication_service=authentication,
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=RecordingTokenService(events),
        access_token_codec=access_token_codec,
        transaction_factory=transaction_factory,
    )

    with pytest.raises(InvalidCredentialsError):
        await service.login(login_command())

    assert events == ["authenticate"]
    assert transaction_factory.call_count == 0
    assert writer.commands == []
    assert access_token_codec.commands == []


@pytest.mark.asyncio
async def test_token_generation_failure_prevents_database_transaction() -> None:
    events: list[str] = []
    token_service = RecordingTokenService(
        events,
        failure=SessionTokenGenerationError(),
    )
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events)
    transaction_factory = RecordingLoginTransactionFactory(events, writer)
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(events),
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=transaction_factory,
    )

    with pytest.raises(SessionTokenGenerationError):
        await service.login(login_command())

    assert events == ["authenticate", "tokens.issue"]
    assert transaction_factory.call_count == 0
    assert writer.commands == []
    assert access_token_codec.commands == []


@pytest.mark.asyncio
async def test_rehash_failure_prevents_tokens_and_database_transaction() -> None:
    events: list[str] = []
    rehasher = RecordingPasswordRehasher(
        events,
        failure=RuntimeError("controlled rehash failure"),
    )
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events)
    transaction_factory = RecordingLoginTransactionFactory(events, writer)
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(
            events,
            rehash_required=True,
        ),
        password_rehasher=rehasher,
        session_token_service=RecordingTokenService(events),
        access_token_codec=access_token_codec,
        transaction_factory=transaction_factory,
    )

    with pytest.raises(RuntimeError, match="controlled rehash failure"):
        await service.login(login_command())

    assert events == ["authenticate", "rehash_verified"]
    assert rehasher.received_values == [RAW_VALUE]
    assert transaction_factory.call_count == 0
    assert writer.commands == []
    assert access_token_codec.commands == []


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_and_does_not_return_browser_tokens() -> None:
    events: list[str] = []
    failure = LoginSessionPersistenceError(sqlstate="08006")
    token_service = RecordingTokenService(events)
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events, failure=failure)
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(events),
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=RecordingLoginTransactionFactory(events, writer),
    )

    with pytest.raises(LoginSessionPersistenceError) as exc_info:
        await service.login(login_command())

    assert events == [
        "authenticate",
        "tokens.issue",
        "transaction.create",
        "transaction.enter",
        "writer.create_login_session",
        "transaction.rollback",
    ]
    assert access_token_codec.commands == []
    assert str(exc_info.value) == "Login session persistence failed"
    assert token_service.result.refresh_token.reveal_for_transport() not in str(exc_info.value)


@pytest.mark.asyncio
async def test_access_token_signing_failure_rolls_back_the_pending_session() -> None:
    events: list[str] = []
    failure = AccessTokenGenerationError()
    token_service = RecordingTokenService(events)
    access_token_codec = RecordingAccessTokenCodec(events, failure=failure)
    writer = RecordingLoginSessionWriter(events)
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(events),
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=RecordingLoginTransactionFactory(events, writer),
    )

    with pytest.raises(AccessTokenGenerationError) as exc_info:
        await service.login(login_command())

    assert events == [
        "authenticate",
        "tokens.issue",
        "transaction.create",
        "transaction.enter",
        "writer.create_login_session",
        "access_token.issue",
        "transaction.rollback",
    ]
    assert len(writer.commands) == 1
    assert access_token_codec.commands == [
        IssueAccessTokenCommand(
            user_id=USER_ID,
            session_id=SESSION_ID,
            issued_at=writer.commands[0].issued_at,
        )
    ]
    assert str(exc_info.value) == "Access token generation failed"
    assert token_service.result.refresh_token.reveal_for_transport() not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_access_token_result_rolls_back_before_commit() -> None:
    events: list[str] = []
    token_service = RecordingTokenService(events)
    access_token_codec = RecordingAccessTokenCodec(
        events,
        claims_issued_at=ISSUED_AT - timedelta(hours=1),
    )
    writer = RecordingLoginSessionWriter(events)
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(events),
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=RecordingLoginTransactionFactory(events, writer),
        clock=lambda: CLOCK_AT,
    )

    with pytest.raises(ValueError, match="Access token must expire"):
        await service.login(login_command())

    assert events == [
        "authenticate",
        "tokens.issue",
        "transaction.create",
        "transaction.enter",
        "writer.create_login_session",
        "access_token.issue",
        "transaction.rollback",
    ]


@pytest.mark.asyncio
async def test_commit_failure_prevents_the_success_result_from_escaping() -> None:
    events: list[str] = []
    failure = LoginSessionPersistenceError(sqlstate="40001")
    token_service = RecordingTokenService(events)
    access_token_codec = RecordingAccessTokenCodec(events)
    writer = RecordingLoginSessionWriter(events)
    transaction_factory = RecordingLoginTransactionFactory(
        events,
        writer,
        commit_failure=failure,
    )
    service = LoginSessionService(
        authentication_service=RecordingAuthenticationService(events),
        password_rehasher=RecordingPasswordRehasher(events),
        session_token_service=token_service,
        access_token_codec=access_token_codec,
        transaction_factory=transaction_factory,
    )

    with pytest.raises(LoginSessionPersistenceError) as exc_info:
        await service.login(login_command())

    assert events == [
        "authenticate",
        "tokens.issue",
        "transaction.create",
        "transaction.enter",
        "writer.create_login_session",
        "access_token.issue",
        "transaction.commit_failed",
    ]
    assert len(access_token_codec.commands) == 1
    assert str(exc_info.value) == "Login session persistence failed"
    assert token_service.result.refresh_token.reveal_for_transport() not in str(exc_info.value)
