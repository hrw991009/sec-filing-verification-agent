"""Tests for first-use rotation, direct recovery, and replay revocation."""

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
    AccessToken,
    AccessTokenClaims,
    AccessTokenGenerationError,
    CsrfToken,
    DeviceToken,
    InvalidAccessTokenError,
    InvalidRefreshSessionError,
    IssueAccessTokenCommand,
    IssuedAccessToken,
    LockedRefreshRotation,
    LockedRefreshSessionState,
    LoginSessionRecord,
    PersistRefreshSuccessorCommand,
    RecordRefreshRecoveryCommand,
    RefreshRecoveryContext,
    RefreshRecoveryEnvelope,
    RefreshSessionCommand,
    RefreshSessionPersistenceError,
    RefreshSuccessorTokens,
    RefreshToken,
    RefreshTokenHash,
    RevokeRefreshFamilyCommand,
    TraceId,
)
from industry_platform.modules.identity.service import (
    REFRESH_RECOVERY_GRACE,
    REFRESH_SESSION_IDLE_TTL,
    RefreshSessionService,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
FAMILY_ID = UUID("22222222-2222-4222-8222-222222222222")
PREDECESSOR_ID = UUID("33333333-3333-4333-8333-333333333333")
SUCCESSOR_ID = UUID("44444444-4444-4444-8444-444444444444")
JWT_ID = UUID("55555555-5555-4555-8555-555555555555")
CHECKED_AT = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
ABSOLUTE_EXPIRES_AT = CHECKED_AT + timedelta(days=30)


def encoded_value(fill: bytes) -> str:
    return urlsafe_b64encode(fill * 32).rstrip(b"=").decode("ascii")


OLD_REFRESH_VALUE = encoded_value(b"o")
OLD_CSRF_VALUE = encoded_value(b"p")
DEVICE_VALUE = encoded_value(b"d")
SUCCESSOR_REFRESH_VALUE = encoded_value(b"r")
SUCCESSOR_CSRF_VALUE = encoded_value(b"c")


class SequenceRandomBytes:
    def __init__(self, *values: bytes) -> None:
        self._values = iter(values)
        self.requests: list[int] = []

    def __call__(self, byte_count: int) -> bytes:
        self.requests.append(byte_count)
        return next(self._values)


class RecordingRecoveryCodec:
    def __init__(
        self,
        events: list[str],
        *,
        recovered: RefreshSuccessorTokens | None = None,
    ) -> None:
        self.events = events
        self.recovered = recovered
        self.sealed_contexts: list[RefreshRecoveryContext] = []
        self.opened_contexts: list[RefreshRecoveryContext] = []
        self.envelope = RefreshRecoveryEnvelope(b"encrypted-successor-envelope")

    def seal(
        self,
        tokens: RefreshSuccessorTokens,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshRecoveryEnvelope:
        self.events.append("recovery.seal")
        self.sealed_contexts.append(context)
        assert tokens.refresh_token.reveal_for_transport() == SUCCESSOR_REFRESH_VALUE
        assert tokens.csrf_token.reveal_for_transport() == SUCCESSOR_CSRF_VALUE
        return self.envelope

    def open(
        self,
        envelope: RefreshRecoveryEnvelope,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshSuccessorTokens:
        self.events.append("recovery.open")
        self.opened_contexts.append(context)
        assert envelope == self.envelope
        if self.recovered is None:
            raise AssertionError("Recovery was not expected")
        return self.recovered


class RecordingAccessTokenCodec:
    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.commands: list[IssueAccessTokenCommand] = []

    def issue(self, command: IssueAccessTokenCommand) -> IssuedAccessToken:
        self.events.append("access.issue")
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure
        return IssuedAccessToken(
            token=AccessToken.from_transport("header.payload.signature"),
            claims=AccessTokenClaims(
                user_id=command.user_id,
                session_id=command.session_id,
                jwt_id=JWT_ID,
                issued_at=command.issued_at,
                not_before=command.issued_at,
                expires_at=command.issued_at + timedelta(minutes=10),
            ),
        )

    def verify(
        self,
        token: AccessToken,
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        del token, now
        raise InvalidAccessTokenError


class RecordingRefreshWriter:
    def __init__(self, events: list[str], rotation: LockedRefreshRotation) -> None:
        self.events = events
        self.rotation = rotation
        self.persisted: list[PersistRefreshSuccessorCommand] = []
        self.recoveries: list[RecordRefreshRecoveryCommand] = []
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
        self.events.append("writer.persist")
        self.persisted.append(command)
        return LoginSessionRecord(
            user_id=command.user_id,
            rotation_family_id=command.rotation_family_id,
            session_id=command.successor_session_id,
            issued_at=command.issued_at,
            idle_expires_at=command.idle_expires_at,
            absolute_expires_at=command.absolute_expires_at,
        )

    async def record_recovery(
        self,
        command: RecordRefreshRecoveryCommand,
    ) -> None:
        self.events.append("writer.recovery")
        self.recoveries.append(command)

    async def revoke_family(self, command: RevokeRefreshFamilyCommand) -> None:
        self.events.append("writer.revoke")
        self.revocations.append(command)


class RecordingRefreshTransaction:
    def __init__(
        self,
        events: list[str],
        writer: RecordingRefreshWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    async def __aenter__(self) -> RecordingRefreshWriter:
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


class RecordingRefreshTransactionFactory:
    def __init__(
        self,
        events: list[str],
        writer: RecordingRefreshWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    def __call__(self) -> RecordingRefreshTransaction:
        self.events.append("transaction.create")
        return RecordingRefreshTransaction(
            self.events,
            self.writer,
            commit_failure=self.commit_failure,
        )


def token_service(
    *,
    random_bytes: SequenceRandomBytes | None = None,
) -> HmacSessionTokenService:
    if random_bytes is None:
        return HmacSessionTokenService(
            refresh_hmac_key=SecretBytes(b"r" * 32),
            csrf_hmac_key=SecretBytes(b"c" * 32),
            device_hmac_key=SecretBytes(b"d" * 32),
        )

    return HmacSessionTokenService(
        refresh_hmac_key=SecretBytes(b"r" * 32),
        csrf_hmac_key=SecretBytes(b"c" * 32),
        device_hmac_key=SecretBytes(b"d" * 32),
        random_bytes=random_bytes,
    )


def refresh_command() -> RefreshSessionCommand:
    return RefreshSessionCommand(
        origin="https://localhost:5173",
        refresh_token=RefreshToken.from_transport(OLD_REFRESH_VALUE),
        csrf_cookie_value=OLD_CSRF_VALUE,
        csrf_header_value=OLD_CSRF_VALUE,
        device_token=DeviceToken.from_transport(DEVICE_VALUE),
        trace_id=TraceId("refresh-service-trace"),
    )


def active_rotation(service: HmacSessionTokenService) -> LockedRefreshRotation:
    presented = LockedRefreshSessionState(
        user_id=USER_ID,
        rotation_family_id=FAMILY_ID,
        session_id=PREDECESSOR_ID,
        previous_session_id=None,
        replaced_by_session_id=None,
        refresh_token_hash=service.digest_refresh(RefreshToken.from_transport(OLD_REFRESH_VALUE)),
        csrf_token_hash=service.digest_csrf(CsrfToken.from_transport(OLD_CSRF_VALUE)),
        device_token_hash=service.digest_device(DeviceToken.from_transport(DEVICE_VALUE)),
        idle_expires_at=CHECKED_AT + timedelta(days=7),
        absolute_expires_at=ABSOLUTE_EXPIRES_AT,
        used_at=None,
        revoked_at=None,
        recovery_envelope=None,
        recovery_expires_at=None,
    )
    return LockedRefreshRotation(
        user_status="active",
        family_id=FAMILY_ID,
        family_current_session_id=PREDECESSOR_ID,
        family_absolute_expires_at=ABSOLUTE_EXPIRES_AT,
        family_revoked_at=None,
        checked_at=CHECKED_AT,
        presented=presented,
        current=presented,
    )


def recovery_rotation(
    service: HmacSessionTokenService,
    recovery_codec: RecordingRecoveryCodec,
    *,
    recovery_expires_at: datetime | None = None,
) -> LockedRefreshRotation:
    recovered = recovery_codec.recovered
    assert recovered is not None
    presented = active_rotation(service).presented
    presented = LockedRefreshSessionState(
        user_id=presented.user_id,
        rotation_family_id=presented.rotation_family_id,
        session_id=presented.session_id,
        previous_session_id=None,
        replaced_by_session_id=SUCCESSOR_ID,
        refresh_token_hash=presented.refresh_token_hash,
        csrf_token_hash=presented.csrf_token_hash,
        device_token_hash=presented.device_token_hash,
        idle_expires_at=presented.idle_expires_at,
        absolute_expires_at=presented.absolute_expires_at,
        used_at=CHECKED_AT - timedelta(seconds=1),
        revoked_at=None,
        recovery_envelope=recovery_codec.envelope,
        recovery_expires_at=(
            CHECKED_AT + timedelta(seconds=4)
            if recovery_expires_at is None
            else recovery_expires_at
        ),
    )
    current = LockedRefreshSessionState(
        user_id=USER_ID,
        rotation_family_id=FAMILY_ID,
        session_id=SUCCESSOR_ID,
        previous_session_id=PREDECESSOR_ID,
        replaced_by_session_id=None,
        refresh_token_hash=service.digest_refresh(recovered.refresh_token),
        csrf_token_hash=service.digest_csrf(recovered.csrf_token),
        device_token_hash=presented.device_token_hash,
        idle_expires_at=CHECKED_AT + timedelta(days=7),
        absolute_expires_at=ABSOLUTE_EXPIRES_AT,
        used_at=None,
        revoked_at=None,
        recovery_envelope=None,
        recovery_expires_at=None,
    )
    return LockedRefreshRotation(
        user_status="active",
        family_id=FAMILY_ID,
        family_current_session_id=SUCCESSOR_ID,
        family_absolute_expires_at=ABSOLUTE_EXPIRES_AT,
        family_revoked_at=None,
        checked_at=CHECKED_AT,
        presented=presented,
        current=current,
    )


def build_service(
    *,
    events: list[str],
    service_tokens: HmacSessionTokenService,
    writer: RecordingRefreshWriter,
    recovery_codec: RecordingRecoveryCodec,
    access_failure: Exception | None = None,
    commit_failure: Exception | None = None,
) -> tuple[RefreshSessionService, RecordingAccessTokenCodec]:
    access_codec = RecordingAccessTokenCodec(events, failure=access_failure)
    service = RefreshSessionService(
        session_token_service=service_tokens,
        access_token_codec=access_codec,
        browser_request_guard=ExactBrowserSessionRequestGuard(
            trusted_origins=("https://localhost:5173",),
            token_service=service_tokens,
        ),
        recovery_codec=recovery_codec,
        transaction_factory=RecordingRefreshTransactionFactory(
            events,
            writer,
            commit_failure=commit_failure,
        ),
        session_id_source=lambda: SUCCESSOR_ID,
    )
    return service, access_codec


@pytest.mark.asyncio
async def test_first_use_rotates_once_before_commit() -> None:
    events: list[str] = []
    random_bytes = SequenceRandomBytes(b"r" * 32, b"c" * 32)
    service_tokens = token_service(random_bytes=random_bytes)
    recovery_codec = RecordingRecoveryCodec(events)
    writer = RecordingRefreshWriter(events, active_rotation(service_tokens))
    service, access_codec = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )

    result = await service.refresh(refresh_command())

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "recovery.seal",
        "writer.persist",
        "access.issue",
        "transaction.commit",
    ]
    assert random_bytes.requests == [32, 32]
    assert result.recovered is False
    assert result.session.session_id == SUCCESSOR_ID
    assert result.refresh_token.reveal_for_transport() == SUCCESSOR_REFRESH_VALUE
    assert result.csrf_token.reveal_for_transport() == SUCCESSOR_CSRF_VALUE
    assert access_codec.commands[0].session_id == SUCCESSOR_ID
    persisted = writer.persisted[0]
    assert persisted.idle_expires_at == CHECKED_AT + REFRESH_SESSION_IDLE_TTL
    assert persisted.recovery_expires_at == CHECKED_AT + REFRESH_RECOVERY_GRACE
    assert persisted.device_token_hash == writer.rotation.presented.device_token_hash
    assert SUCCESSOR_REFRESH_VALUE not in repr(persisted)
    assert SUCCESSOR_CSRF_VALUE not in repr(persisted)


@pytest.mark.asyncio
async def test_direct_predecessor_recovers_the_same_successor() -> None:
    events: list[str] = []
    service_tokens = token_service()
    recovered = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(SUCCESSOR_REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(SUCCESSOR_CSRF_VALUE),
    )
    recovery_codec = RecordingRecoveryCodec(events, recovered=recovered)
    writer = RecordingRefreshWriter(
        events,
        recovery_rotation(service_tokens, recovery_codec),
    )
    service, access_codec = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )

    result = await service.refresh(refresh_command())

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "recovery.open",
        "writer.recovery",
        "access.issue",
        "transaction.commit",
    ]
    assert result.recovered is True
    assert result.refresh_token == recovered.refresh_token
    assert result.csrf_token == recovered.csrf_token
    assert writer.persisted == []
    assert writer.revocations == []
    assert access_codec.commands[0].session_id == SUCCESSOR_ID


@pytest.mark.asyncio
async def test_invalid_direct_recovery_commits_revocation_before_rejecting() -> None:
    events: list[str] = []
    service_tokens = token_service()
    recovered = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(SUCCESSOR_REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(SUCCESSOR_CSRF_VALUE),
    )
    recovery_codec = RecordingRecoveryCodec(events, recovered=recovered)
    writer = RecordingRefreshWriter(
        events,
        recovery_rotation(
            service_tokens,
            recovery_codec,
            recovery_expires_at=CHECKED_AT,
        ),
    )
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )

    with pytest.raises(InvalidRefreshSessionError):
        await service.refresh(refresh_command())

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "writer.revoke",
        "transaction.commit",
    ]
    assert len(writer.revocations) == 1
    assert writer.revocations[0].reason == "refresh_replay_detected"


@pytest.mark.asyncio
async def test_malformed_device_on_direct_recovery_revokes_the_family() -> None:
    events: list[str] = []
    service_tokens = token_service()
    recovered = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(SUCCESSOR_REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(SUCCESSOR_CSRF_VALUE),
    )
    recovery_codec = RecordingRecoveryCodec(events, recovered=recovered)
    writer = RecordingRefreshWriter(
        events,
        recovery_rotation(service_tokens, recovery_codec),
    )
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )
    command = refresh_command()
    command = RefreshSessionCommand(
        origin=command.origin,
        refresh_token=command.refresh_token,
        csrf_cookie_value=command.csrf_cookie_value,
        csrf_header_value=command.csrf_header_value,
        device_token=DeviceToken.from_transport("not-canonical"),
        trace_id=command.trace_id,
    )

    with pytest.raises(InvalidRefreshSessionError):
        await service.refresh(command)

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "writer.revoke",
        "transaction.commit",
    ]
    assert len(writer.revocations) == 1


@pytest.mark.asyncio
async def test_recovered_plaintext_mismatch_revokes_instead_of_escaping() -> None:
    events: list[str] = []
    service_tokens = token_service()
    expected = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(SUCCESSOR_REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(SUCCESSOR_CSRF_VALUE),
    )
    recovery_codec = RecordingRecoveryCodec(events, recovered=expected)
    rotation = recovery_rotation(service_tokens, recovery_codec)
    recovery_codec.recovered = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(encoded_value(b"x")),
        csrf_token=CsrfToken.from_transport(encoded_value(b"y")),
    )
    writer = RecordingRefreshWriter(events, rotation)
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )

    with pytest.raises(InvalidRefreshSessionError):
        await service.refresh(refresh_command())

    assert events[-2:] == ["writer.revoke", "transaction.commit"]
    assert writer.recoveries == []
    assert len(writer.revocations) == 1


@pytest.mark.asyncio
async def test_first_use_bad_origin_rolls_back_without_revocation() -> None:
    events: list[str] = []
    service_tokens = token_service()
    recovery_codec = RecordingRecoveryCodec(events)
    writer = RecordingRefreshWriter(events, active_rotation(service_tokens))
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
    )
    command = refresh_command()
    command = RefreshSessionCommand(
        origin="https://attacker.invalid",
        refresh_token=command.refresh_token,
        csrf_cookie_value=command.csrf_cookie_value,
        csrf_header_value=command.csrf_header_value,
        device_token=command.device_token,
        trace_id=command.trace_id,
    )

    with pytest.raises(InvalidRefreshSessionError):
        await service.refresh(command)

    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.lock",
        "transaction.rollback",
    ]
    assert writer.revocations == []


@pytest.mark.asyncio
async def test_access_signing_failure_rolls_back_pending_rotation() -> None:
    events: list[str] = []
    service_tokens = token_service(random_bytes=SequenceRandomBytes(b"r" * 32, b"c" * 32))
    recovery_codec = RecordingRecoveryCodec(events)
    writer = RecordingRefreshWriter(events, active_rotation(service_tokens))
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
        access_failure=AccessTokenGenerationError(),
    )

    with pytest.raises(AccessTokenGenerationError):
        await service.refresh(refresh_command())

    assert events[-3:] == [
        "writer.persist",
        "access.issue",
        "transaction.rollback",
    ]
    assert len(writer.persisted) == 1


@pytest.mark.asyncio
async def test_commit_failure_never_returns_the_prepared_credentials() -> None:
    events: list[str] = []
    service_tokens = token_service(random_bytes=SequenceRandomBytes(b"r" * 32, b"c" * 32))
    recovery_codec = RecordingRecoveryCodec(events)
    writer = RecordingRefreshWriter(events, active_rotation(service_tokens))
    service, _ = build_service(
        events=events,
        service_tokens=service_tokens,
        writer=writer,
        recovery_codec=recovery_codec,
        commit_failure=RefreshSessionPersistenceError(),
    )

    with pytest.raises(RefreshSessionPersistenceError):
        await service.refresh(refresh_command())

    assert events[-2:] == ["access.issue", "transaction.commit_failed"]
