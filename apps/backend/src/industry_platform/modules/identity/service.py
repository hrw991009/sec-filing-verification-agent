"""Identity application services."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    CreateLoginSessionCommand,
    EstablishedLoginSession,
    InvalidCredentialsError,
    InvalidEmailAddressError,
    IssueAccessTokenCommand,
    PasswordHash,
    RegisterUserCommand,
    RegistrationRecord,
    VerifiedCredentials,
)
from industry_platform.modules.identity.emails import normalize_email_address
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.ports import (
    AccessTokenCodec,
    CredentialAuthenticationUseCase,
    CredentialReader,
    LoginSessionTokenService,
    LoginSessionTransactionFactory,
    PasswordHasher,
    RegistrationTransactionFactory,
    VerifiedPasswordRehasher,
)

DEFAULT_WORKSPACE_NAME = "My Workspace"
REFRESH_SESSION_IDLE_TTL = timedelta(days=7)
REFRESH_SESSION_ABSOLUTE_TTL = timedelta(days=30)

type UtcClock = Callable[[], datetime]


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
