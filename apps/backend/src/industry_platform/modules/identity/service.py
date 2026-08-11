"""Identity application services."""

import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    CreateLoginSessionCommand,
    CsrfToken,
    DeviceTokenHash,
    EstablishedLoginSession,
    InvalidBrowserSessionRequestError,
    InvalidCredentialsError,
    InvalidEmailAddressError,
    InvalidRefreshSessionError,
    InvalidSessionTokenError,
    IssueAccessTokenCommand,
    LockedRefreshRotation,
    LoginSessionRecord,
    PasswordHash,
    PersistRefreshSuccessorCommand,
    RecordRefreshRecoveryCommand,
    RefreshedSession,
    RefreshRecoveryContext,
    RefreshRecoveryError,
    RefreshRevocationReason,
    RefreshSessionCommand,
    RefreshSuccessorTokens,
    RefreshToken,
    RegisterUserCommand,
    RegistrationRecord,
    RevokeRefreshFamilyCommand,
    SessionTokenGenerationError,
    VerifiedCredentials,
)
from industry_platform.modules.identity.emails import normalize_email_address
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.ports import (
    AccessTokenCodec,
    BrowserSessionRequestGuard,
    CredentialAuthenticationUseCase,
    CredentialReader,
    LoginSessionTokenService,
    LoginSessionTransactionFactory,
    PasswordHasher,
    RefreshRecoveryCodec,
    RefreshSessionIdSource,
    RefreshSessionTokenService,
    RefreshSessionTransactionFactory,
    RefreshSessionWriter,
    RegistrationTransactionFactory,
    VerifiedPasswordRehasher,
)

DEFAULT_WORKSPACE_NAME = "My Workspace"
REFRESH_SESSION_IDLE_TTL = timedelta(days=7)
REFRESH_SESSION_ABSOLUTE_TTL = timedelta(days=30)
REFRESH_RECOVERY_GRACE = timedelta(seconds=5)

type UtcClock = Callable[[], datetime]


class _RefreshMode(StrEnum):
    FIRST_USE = "first_use"
    DIRECT_RECOVERY = "direct_recovery"
    REJECT = "reject"
    REVOKE = "revoke"


def utc_now() -> datetime:
    """Return one timezone-aware UTC instant for production session issuance."""

    return datetime.now(UTC)


class CredentialAuthenticationService:
    """Verify login credentials while minimizing account-enumeration signals."""

    def __init__(
        self,
        *,
        password_hasher: PasswordHasher,
        credential_reader: CredentialReader,
        dummy_password_hash: PasswordHash,
    ) -> None:
        self._password_hasher = password_hasher
        self._credential_reader = credential_reader
        self._dummy_password_hash = dummy_password_hash

    async def authenticate(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> VerifiedCredentials:
        """Verify exactly one hash and expose one generic rejection boundary."""

        try:
            normalized_email = normalize_email_address(command.email)
        except InvalidEmailAddressError:
            normalized_email = None

        stored_credentials = (
            await self._credential_reader.find_by_email(normalized_email)
            if normalized_email is not None
            else None
        )
        password_hash = (
            stored_credentials.password_hash
            if stored_credentials is not None
            else self._dummy_password_hash
        )
        password_matches = await self._password_hasher.verify(
            password_hash,
            command.password,
        )

        if (
            stored_credentials is None
            or stored_credentials.status != "active"
            or not password_matches
        ):
            raise InvalidCredentialsError

        password_rehash_required = await self._password_hasher.needs_rehash(
            stored_credentials.password_hash
        )

        return VerifiedCredentials(
            user_id=stored_credentials.user_id,
            email=stored_credentials.email,
            expected_password_hash=stored_credentials.password_hash,
            password_rehash_required=password_rehash_required,
        )


class LoginSessionService:
    """Authenticate, prepare credentials, then atomically commit one login session."""

    def __init__(
        self,
        *,
        authentication_service: CredentialAuthenticationUseCase,
        password_rehasher: VerifiedPasswordRehasher,
        session_token_service: LoginSessionTokenService,
        access_token_codec: AccessTokenCodec,
        transaction_factory: LoginSessionTransactionFactory,
        clock: UtcClock = utc_now,
    ) -> None:
        self._authentication_service = authentication_service
        self._password_rehasher = password_rehasher
        self._session_token_service = session_token_service
        self._access_token_codec = access_token_codec
        self._transaction_factory = transaction_factory
        self._clock = clock

    async def login(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> EstablishedLoginSession:
        """Expose plaintext tokens only after the complete transaction commits."""

        verified = await self._authentication_service.authenticate(command)
        replacement_password_hash = (
            await self._password_rehasher.rehash_verified(command.password)
            if verified.password_rehash_required
            else None
        )
        issued_tokens = self._session_token_service.issue()
        issued_at = self._clock().replace(microsecond=0)
        create_command = CreateLoginSessionCommand(
            user_id=verified.user_id,
            expected_password_hash=verified.expected_password_hash,
            replacement_password_hash=replacement_password_hash,
            refresh_token_hash=issued_tokens.refresh_token_hash,
            csrf_token_hash=issued_tokens.csrf_token_hash,
            device_token_hash=issued_tokens.device_token_hash,
            issued_at=issued_at,
            idle_expires_at=issued_at + REFRESH_SESSION_IDLE_TTL,
            absolute_expires_at=issued_at + REFRESH_SESSION_ABSOLUTE_TTL,
            trace_id=command.trace_id,
        )

        async with self._transaction_factory() as writer:
            session = await writer.create_login_session(create_command)
            issued_access_token = self._access_token_codec.issue(
                IssueAccessTokenCommand(
                    user_id=session.user_id,
                    session_id=session.session_id,
                    issued_at=session.issued_at,
                )
            )
            result = EstablishedLoginSession(
                email=verified.email,
                session=session,
                access_token=issued_access_token.token,
                access_token_expires_at=issued_access_token.claims.expires_at,
                refresh_token=issued_tokens.refresh_token,
                csrf_token=issued_tokens.csrf_token,
                device_token=issued_tokens.device_token,
            )

        return result


class RefreshSessionService:
    """Rotate once, recover one direct successor, or commit family revocation."""

    def __init__(
        self,
        *,
        session_token_service: RefreshSessionTokenService,
        access_token_codec: AccessTokenCodec,
        browser_request_guard: BrowserSessionRequestGuard,
        recovery_codec: RefreshRecoveryCodec,
        transaction_factory: RefreshSessionTransactionFactory,
        session_id_source: RefreshSessionIdSource = uuid4,
    ) -> None:
        self._session_token_service = session_token_service
        self._access_token_codec = access_token_codec
        self._browser_request_guard = browser_request_guard
        self._recovery_codec = recovery_codec
        self._transaction_factory = transaction_factory
        self._session_id_source = session_id_source

    async def refresh(self, command: RefreshSessionCommand) -> RefreshedSession:
        """Return credentials only after rotate, recover, or revoke commits."""

        try:
            presented_hash = self._session_token_service.digest_refresh(command.refresh_token)
        except InvalidSessionTokenError:
            raise InvalidRefreshSessionError from None

        presented_device_hash: DeviceTokenHash | None
        try:
            presented_device_hash = self._session_token_service.digest_device(command.device_token)
        except InvalidSessionTokenError:
            presented_device_hash = None

        result: RefreshedSession | None = None
        reject_after_commit = False

        async with self._transaction_factory() as writer:
            rotation = await writer.lock_rotation(presented_hash)
            mode = self._classify(rotation)

            if mode is _RefreshMode.REJECT:
                raise InvalidRefreshSessionError

            proof_is_valid = self._proof_is_valid(
                command,
                rotation,
                presented_device_hash=presented_device_hash,
                include_current_device=(mode is _RefreshMode.DIRECT_RECOVERY),
            )

            if mode is _RefreshMode.FIRST_USE:
                if not proof_is_valid:
                    raise InvalidRefreshSessionError
                result = await self._rotate_first_use(
                    command,
                    rotation,
                    writer,
                )
            elif mode is _RefreshMode.DIRECT_RECOVERY and proof_is_valid:
                result = await self._recover_direct_successor(
                    command,
                    rotation,
                    writer,
                )
                if result is None:
                    await self._revoke(rotation, command, writer)
                    reject_after_commit = True
            else:
                await self._revoke(rotation, command, writer)
                reject_after_commit = True

        if reject_after_commit:
            raise InvalidRefreshSessionError
        if result is None:
            raise RuntimeError("Refresh transaction completed without a result")

        return result

    async def _rotate_first_use(
        self,
        command: RefreshSessionCommand,
        rotation: LockedRefreshRotation,
        writer: RefreshSessionWriter,
    ) -> RefreshedSession:
        issued = self._session_token_service.issue_refresh_successor()
        if hmac.compare_digest(
            bytes(issued.refresh_token_hash),
            bytes(rotation.presented.refresh_token_hash),
        ) or hmac.compare_digest(
            bytes(issued.csrf_token_hash),
            bytes(rotation.presented.csrf_token_hash),
        ):
            raise SessionTokenGenerationError

        successor_id = self._session_id_source()
        if successor_id.int == 0 or successor_id == rotation.presented.session_id:
            raise SessionTokenGenerationError

        idle_expires_at = min(
            rotation.checked_at + REFRESH_SESSION_IDLE_TTL,
            rotation.family_absolute_expires_at,
        )
        recovery_expires_at = min(
            rotation.checked_at + REFRESH_RECOVERY_GRACE,
            rotation.family_absolute_expires_at,
        )
        context = RefreshRecoveryContext(
            predecessor_session_id=rotation.presented.session_id,
            successor_session_id=successor_id,
            rotation_family_id=rotation.family_id,
            user_id=rotation.presented.user_id,
            device_token_hash=rotation.presented.device_token_hash,
        )
        envelope = self._recovery_codec.seal(
            RefreshSuccessorTokens(
                refresh_token=issued.refresh_token,
                csrf_token=issued.csrf_token,
            ),
            context=context,
        )
        session = await writer.persist_successor(
            PersistRefreshSuccessorCommand(
                user_id=rotation.presented.user_id,
                rotation_family_id=rotation.family_id,
                predecessor_session_id=rotation.presented.session_id,
                successor_session_id=successor_id,
                refresh_token_hash=issued.refresh_token_hash,
                csrf_token_hash=issued.csrf_token_hash,
                device_token_hash=rotation.presented.device_token_hash,
                issued_at=rotation.checked_at,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=rotation.family_absolute_expires_at,
                recovery_envelope=envelope,
                recovery_expires_at=recovery_expires_at,
                trace_id=command.trace_id,
            )
        )
        return self._build_result(
            session=session,
            refresh_token=issued.refresh_token,
            csrf_token=issued.csrf_token,
            recovered=False,
        )

    async def _recover_direct_successor(
        self,
        command: RefreshSessionCommand,
        rotation: LockedRefreshRotation,
        writer: RefreshSessionWriter,
    ) -> RefreshedSession | None:
        envelope = rotation.presented.recovery_envelope
        if envelope is None:
            return None

        context = RefreshRecoveryContext(
            predecessor_session_id=rotation.presented.session_id,
            successor_session_id=rotation.current.session_id,
            rotation_family_id=rotation.family_id,
            user_id=rotation.presented.user_id,
            device_token_hash=rotation.presented.device_token_hash,
        )
        try:
            recovered = self._recovery_codec.open(envelope, context=context)
            recovered_refresh_hash = self._session_token_service.digest_refresh(
                recovered.refresh_token
            )
            recovered_csrf_hash = self._session_token_service.digest_csrf(recovered.csrf_token)
        except (InvalidSessionTokenError, RefreshRecoveryError):
            return None

        if not hmac.compare_digest(
            bytes(recovered_refresh_hash),
            bytes(rotation.current.refresh_token_hash),
        ) or not hmac.compare_digest(
            bytes(recovered_csrf_hash),
            bytes(rotation.current.csrf_token_hash),
        ):
            return None

        await writer.record_recovery(
            RecordRefreshRecoveryCommand(
                user_id=rotation.presented.user_id,
                rotation_family_id=rotation.family_id,
                session_id=rotation.current.session_id,
                recovered_at=rotation.checked_at,
                trace_id=command.trace_id,
            )
        )
        session = LoginSessionRecord(
            user_id=rotation.presented.user_id,
            rotation_family_id=rotation.family_id,
            session_id=rotation.current.session_id,
            issued_at=rotation.checked_at,
            idle_expires_at=rotation.current.idle_expires_at,
            absolute_expires_at=rotation.family_absolute_expires_at,
        )
        return self._build_result(
            session=session,
            refresh_token=recovered.refresh_token,
            csrf_token=recovered.csrf_token,
            recovered=True,
        )

    def _build_result(
        self,
        *,
        session: LoginSessionRecord,
        refresh_token: RefreshToken,
        csrf_token: CsrfToken,
        recovered: bool,
    ) -> RefreshedSession:
        issued_access_token = self._access_token_codec.issue(
            IssueAccessTokenCommand(
                user_id=session.user_id,
                session_id=session.session_id,
                issued_at=session.issued_at,
            )
        )
        return RefreshedSession(
            session=session,
            access_token=issued_access_token.token,
            access_token_expires_at=issued_access_token.claims.expires_at,
            refresh_token=refresh_token,
            csrf_token=csrf_token,
            recovered=recovered,
        )

    def _proof_is_valid(
        self,
        command: RefreshSessionCommand,
        rotation: LockedRefreshRotation,
        *,
        presented_device_hash: DeviceTokenHash | None,
        include_current_device: bool,
    ) -> bool:
        if presented_device_hash is None:
            return False

        try:
            self._browser_request_guard.validate_origin(command.origin)
            self._browser_request_guard.validate_csrf(
                cookie_value=command.csrf_cookie_value,
                header_value=command.csrf_header_value,
                expected_hash=rotation.presented.csrf_token_hash,
            )
        except InvalidBrowserSessionRequestError:
            return False

        device_hash = bytes(presented_device_hash)

        if not hmac.compare_digest(
            device_hash,
            bytes(rotation.presented.device_token_hash),
        ):
            return False

        return not include_current_device or hmac.compare_digest(
            device_hash,
            bytes(rotation.current.device_token_hash),
        )

    @staticmethod
    def _classify(rotation: LockedRefreshRotation) -> _RefreshMode:
        presented = rotation.presented
        current = rotation.current

        if rotation.family_revoked_at is not None:
            return _RefreshMode.REJECT
        if rotation.checked_at >= rotation.family_absolute_expires_at:
            return _RefreshMode.REJECT
        if rotation.user_status != "active":
            return _RefreshMode.REVOKE
        if presented.revoked_at is not None or current.revoked_at is not None:
            return _RefreshMode.REVOKE

        is_first_use = (
            rotation.family_current_session_id == presented.session_id
            and presented.used_at is None
            and presented.replaced_by_session_id is None
        )
        if is_first_use:
            if (
                rotation.checked_at >= presented.idle_expires_at
                or rotation.checked_at >= presented.absolute_expires_at
            ):
                return _RefreshMode.REJECT
            return _RefreshMode.FIRST_USE

        is_direct_recovery = (
            presented.used_at is not None
            and presented.replaced_by_session_id == rotation.family_current_session_id
            and current.session_id == rotation.family_current_session_id
            and current.previous_session_id == presented.session_id
            and current.used_at is None
            and current.replaced_by_session_id is None
            and presented.recovery_envelope is not None
            and presented.recovery_expires_at is not None
            and rotation.checked_at < presented.recovery_expires_at
            and rotation.checked_at < rotation.family_absolute_expires_at
            and rotation.checked_at < current.idle_expires_at
            and rotation.checked_at < current.absolute_expires_at
        )
        return _RefreshMode.DIRECT_RECOVERY if is_direct_recovery else _RefreshMode.REVOKE

    @staticmethod
    async def _revoke(
        rotation: LockedRefreshRotation,
        command: RefreshSessionCommand,
        writer: RefreshSessionWriter,
    ) -> None:
        reason: RefreshRevocationReason = (
            "account_unavailable" if rotation.user_status != "active" else "refresh_replay_detected"
        )
        await writer.revoke_family(
            RevokeRefreshFamilyCommand(
                user_id=rotation.presented.user_id,
                rotation_family_id=rotation.family_id,
                detected_session_id=rotation.presented.session_id,
                revoked_at=rotation.checked_at,
                reason=reason,
                trace_id=command.trace_id,
            )
        )


class RegistrationService:
    """Register one user and their first workspace as an atomic business action."""

    def __init__(
        self,
        *,
        password_hasher: PasswordHasher,
        transaction_factory: RegistrationTransactionFactory,
    ) -> None:
        self._password_hasher = password_hasher
        self._transaction_factory = transaction_factory

    async def register(self, command: RegisterUserCommand) -> RegistrationRecord:
        """Validate, hash, then persist all registration records in one transaction."""

        normalized_email = normalize_email_address(command.email)
        validated_password = ValidatedPassword.from_secret(command.password)

        # Finish the expensive hash before borrowing a database connection.
        password_hash = await self._password_hasher.hash(validated_password)

        async with self._transaction_factory() as writer:
            return await writer.create_registration(
                email=normalized_email,
                password_hash=password_hash,
                workspace_name=DEFAULT_WORKSPACE_NAME,
                trace_id=command.trace_id,
            )
