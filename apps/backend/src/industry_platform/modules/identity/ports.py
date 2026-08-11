"""Technology-independent interfaces owned by the identity application layer."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenClaims,
    AuthenticateCredentialsCommand,
    CreateLoginSessionCommand,
    CsrfToken,
    CsrfTokenHash,
    DeviceToken,
    DeviceTokenHash,
    EstablishedLoginSession,
    IssueAccessTokenCommand,
    IssuedAccessToken,
    IssuedLoginSessionTokens,
    IssuedRefreshSuccessorTokens,
    LockedRefreshRotation,
    LoginSessionRecord,
    NormalizedEmail,
    PasswordHash,
    PersistRefreshSuccessorCommand,
    RecordRefreshRecoveryCommand,
    RefreshedSession,
    RefreshRecoveryContext,
    RefreshRecoveryEnvelope,
    RefreshSessionCommand,
    RefreshSuccessorTokens,
    RefreshToken,
    RefreshTokenHash,
    RegisterUserCommand,
    RegistrationRecord,
    RevokeRefreshFamilyCommand,
    StoredCredentials,
    TraceId,
    VerifiedCredentials,
)
from industry_platform.modules.identity.passwords import ValidatedPassword


class PasswordHasher(Protocol):
    """Asynchronous password hashing boundary used by identity services."""

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        """Hash one policy-compliant new password."""

        ...

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        """Verify an existing hash without applying the new-password policy."""

        ...

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        """Return whether a valid stored hash uses obsolete parameters."""

        ...


class VerifiedPasswordRehasher(Protocol):
    """Upgrade a password only after credential verification has succeeded."""

    async def rehash_verified(self, password: SecretStr) -> PasswordHash:
        """Hash the verified raw value without applying new-password policy."""

        ...


class RandomBytesSource(Protocol):
    """Supply cryptographically secure bytes without hiding test injection."""

    def __call__(self, byte_count: int, /) -> bytes:
        """Return exactly the requested number of unpredictable bytes."""

        ...


class JwtIdSource(Protocol):
    """Supply an unpredictable unique identifier for each signed Access Token."""

    def __call__(self) -> UUID:
        """Return one UUID suitable for the standard JWT `jti` claim."""

        ...


class RefreshSessionIdSource(Protocol):
    """Supply one unpredictable identifier for a refresh-session successor."""

    def __call__(self) -> UUID:
        """Return one non-nil UUID for a new persisted successor."""

        ...


class AccessTokenCodec(Protocol):
    """Issue and verify only the platform's fixed Ed25519 Access Token format."""

    def issue(self, command: IssueAccessTokenCommand) -> IssuedAccessToken:
        """Sign one short-lived Access Token from trusted identifiers."""

        ...

    def verify(
        self,
        token: AccessToken,
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        """Verify signature, fixed metadata, claims, and validity window."""

        ...


class LoginSessionTokenService(Protocol):
    """Issue and digest each browser session-token purpose explicitly."""

    def issue(self) -> IssuedLoginSessionTokens:
        """Return three new tokens and their persistence-safe hashes."""

        ...

    def digest_refresh(self, token: RefreshToken) -> RefreshTokenHash:
        """Validate and digest one received refresh token."""

        ...

    def digest_csrf(self, token: CsrfToken) -> CsrfTokenHash:
        """Validate and digest one received CSRF token."""

        ...

    def digest_device(self, token: DeviceToken) -> DeviceTokenHash:
        """Validate and digest one received device token."""

        ...


class RefreshSessionTokenService(Protocol):
    """Rotate Refresh/CSRF values while keeping the device token stable."""

    def issue_refresh_successor(self) -> IssuedRefreshSuccessorTokens:
        """Issue exactly one new Refresh value and one new CSRF value."""

        ...

    def digest_refresh(self, token: RefreshToken) -> RefreshTokenHash:
        """Validate and digest one received refresh token."""

        ...

    def digest_csrf(self, token: CsrfToken) -> CsrfTokenHash:
        """Validate and digest one received CSRF token."""

        ...

    def digest_device(self, token: DeviceToken) -> DeviceTokenHash:
        """Validate and digest one received stable device token."""

        ...


class BrowserSessionRequestGuard(Protocol):
    """Validate exact Origin and the bound double-submit CSRF proof."""

    def validate_origin(self, origin: str | None) -> None:
        """Reject a missing, malformed, insecure, or untrusted Origin."""

        ...

    def validate_csrf(
        self,
        *,
        cookie_value: str | None,
        header_value: str | None,
        expected_hash: CsrfTokenHash,
    ) -> CsrfToken:
        """Return the proven token or raise one generic browser error."""

        ...


class RefreshRecoveryCodec(Protocol):
    """Seal and recover one exact Refresh/CSRF successor pair."""

    def seal(
        self,
        tokens: RefreshSuccessorTokens,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshRecoveryEnvelope:
        """Encrypt one successor and authenticate all stable bindings."""

        ...

    def open(
        self,
        envelope: RefreshRecoveryEnvelope,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshSuccessorTokens:
        """Recover values only when ciphertext and bindings are authentic."""

        ...


class CredentialReader(Protocol):
    """Read the minimal account snapshot required for password verification."""

    async def find_by_email(
        self,
        email: NormalizedEmail,
    ) -> StoredCredentials | None:
        """Return canonical stored credentials, or None for an unknown email."""

        ...


class CredentialAuthenticationUseCase(Protocol):
    """Verify credentials without yet creating a browser session."""

    async def authenticate(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> VerifiedCredentials:
        """Return internal proof or raise the same error for every rejection."""

        ...


class LoginAttemptRateLimiter(Protocol):
    """Gate credential work using shared source and account attempt windows."""

    async def acquire(self, *, source_ip: str, raw_email: str) -> None:
        """Consume one attempt or raise a safe rejection."""

        ...


class LoginSessionUseCase(Protocol):
    """Authenticate credentials and atomically establish a browser session."""

    async def login(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> EstablishedLoginSession:
        """Return browser credentials only after persistence commits."""

        ...


class LoginSessionWriter(Protocol):
    """Persistence operations available inside one login transaction."""

    async def create_login_session(
        self,
        command: CreateLoginSessionCommand,
    ) -> LoginSessionRecord:
        """Recheck the user and atomically create their refresh session."""

        ...


class LoginSessionTransactionFactory(Protocol):
    """Open a new atomic login-session transaction on demand."""

    def __call__(self) -> AbstractAsyncContextManager[LoginSessionWriter]:
        """Return a context manager that commits or rolls back as one unit."""

        ...


class RefreshSessionUseCase(Protocol):
    """Rotate or safely recover one browser refresh session."""

    async def refresh(self, command: RefreshSessionCommand) -> RefreshedSession:
        """Return credentials only after the refresh transaction commits."""

        ...


class RefreshSessionWriter(Protocol):
    """Persistence operations available inside one refresh transaction."""

    async def lock_rotation(
        self,
        refresh_token_hash: RefreshTokenHash,
    ) -> LockedRefreshRotation:
        """Lock user, family, and relevant sessions in the global order."""

        ...

    async def persist_successor(
        self,
        command: PersistRefreshSuccessorCommand,
    ) -> LoginSessionRecord:
        """Insert and link exactly one successor under the existing locks."""

        ...

    async def record_recovery(
        self,
        command: RecordRefreshRecoveryCommand,
    ) -> None:
        """Write one sanitized audit row for successful response recovery."""

        ...

    async def revoke_family(
        self,
        command: RevokeRefreshFamilyCommand,
    ) -> None:
        """Revoke every session and clear all recovery envelopes."""

        ...


class RefreshSessionTransactionFactory(Protocol):
    """Open a new atomic refresh-session transaction on demand."""

    def __call__(self) -> AbstractAsyncContextManager[RefreshSessionWriter]:
        """Return a context manager that commits or rolls back as one unit."""

        ...


class RegistrationWriter(Protocol):
    """Persistence operations available inside one registration transaction."""

    async def create_registration(
        self,
        *,
        email: NormalizedEmail,
        password_hash: PasswordHash,
        workspace_name: str,
        trace_id: TraceId,
    ) -> RegistrationRecord:
        """Create the account, default workspace, owner membership, and audit."""

        ...


class RegistrationTransactionFactory(Protocol):
    """Open a new atomic registration transaction on demand."""

    def __call__(self) -> AbstractAsyncContextManager[RegistrationWriter]:
        """Return a context manager that commits or rolls back as one unit."""

        ...


class RegistrationUseCase(Protocol):
    """Registration operation exposed to delivery adapters such as HTTP."""

    async def register(self, command: RegisterUserCommand) -> RegistrationRecord:
        """Register one account and its initial owner workspace."""

        ...
