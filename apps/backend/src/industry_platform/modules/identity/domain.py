"""Technology-independent values and failures used by identity workflows."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, NewType, Self
from uuid import UUID

from pydantic import SecretStr

NormalizedEmail = NewType("NormalizedEmail", str)
PasswordHash = NewType("PasswordHash", str)
RefreshTokenHash = NewType("RefreshTokenHash", bytes)
CsrfTokenHash = NewType("CsrfTokenHash", bytes)
DeviceTokenHash = NewType("DeviceTokenHash", bytes)
RefreshRecoveryEnvelope = NewType("RefreshRecoveryEnvelope", bytes)
TraceId = NewType("TraceId", str)
type AccountStatus = Literal["active", "disabled", "deleting", "deleted"]
type RefreshRevocationReason = Literal[
    "account_unavailable",
    "refresh_replay_detected",
]


def _is_utc_timestamp(value: datetime) -> bool:
    """Return whether a timestamp is timezone-aware and normalized to UTC."""

    return (
        value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0)
    )


class EmailAlreadyRegisteredError(RuntimeError):
    """Raised when a normalized email already belongs to an account."""


class InvalidEmailAddressError(ValueError):
    """Raised when an application caller supplies an invalid email address."""


class InvalidCredentialsError(RuntimeError):
    """Reject login without revealing which credential was incorrect."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password")


class LoginRateLimitConfigurationError(ValueError):
    """Reject an unsafe limiter configuration without exposing key material."""

    def __init__(self) -> None:
        super().__init__("Invalid login rate limit configuration")


class LoginRateLimitExceededError(RuntimeError):
    """Tell HTTP delivery when one generic login retry may be attempted."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("Login rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class LoginRateLimitUnavailableError(RuntimeError):
    """Fail closed when the shared limiter cannot make a safe decision."""

    def __init__(self) -> None:
        super().__init__("Login rate limiter unavailable")


class InvalidAccessTokenError(ValueError):
    """Reject an untrusted Access Token without echoing any part of it."""

    def __init__(self) -> None:
        super().__init__("Invalid access token")


class AccessTokenConfigurationError(ValueError):
    """Reject an unsafe local signing or verification key configuration."""

    def __init__(self) -> None:
        super().__init__("Invalid access token configuration")


class AccessTokenGenerationError(RuntimeError):
    """Report signing failure without exposing keys, claims, or partial output."""

    def __init__(self) -> None:
        super().__init__("Access token generation failed")


class InvalidSessionTokenError(ValueError):
    """Reject malformed browser token input without echoing it."""

    def __init__(self) -> None:
        super().__init__("Invalid session token")


class InvalidSessionTokenKeyError(ValueError):
    """Reject unsafe HMAC key configuration without echoing key material."""

    def __init__(self) -> None:
        super().__init__("Invalid session token HMAC key configuration")


class SessionTokenGenerationError(RuntimeError):
    """Report random-source failure without exposing partial token material."""

    def __init__(self) -> None:
        super().__init__("Session token generation failed")


class BrowserRequestSecurityConfigurationError(ValueError):
    """Reject an unusable trusted-origin policy without echoing its input."""

    def __init__(self) -> None:
        super().__init__("Invalid browser request security configuration")


class InvalidBrowserSessionRequestError(RuntimeError):
    """Reject Origin or CSRF proof without revealing which check failed."""

    def __init__(self) -> None:
        super().__init__("Invalid browser session request")


class RefreshRecoveryConfigurationError(ValueError):
    """Reject an unsafe successor-recovery encryption configuration."""

    def __init__(self) -> None:
        super().__init__("Invalid refresh recovery configuration")


class RefreshRecoveryError(RuntimeError):
    """Reject unsafe recovery data without exposing tokens or key material."""

    def __init__(self) -> None:
        super().__init__("Refresh recovery failed")


class InvalidRefreshSessionError(RuntimeError):
    """Reject every invalid refresh attempt without revealing its failed check."""

    def __init__(self) -> None:
        super().__init__("Refresh session rejected")


class RefreshSessionPersistenceError(RuntimeError):
    """Carry a safe refresh-transaction failure classification beyond an adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Refresh session persistence failed")
        self.sqlstate = sqlstate


class AuthenticationPersistenceError(RuntimeError):
    """Carry a safe credential-read failure classification beyond an adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Authentication persistence failed")
        self.sqlstate = sqlstate


class LoginSessionPersistenceError(RuntimeError):
    """Carry a safe login-transaction failure classification beyond an adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Login session persistence failed")
        self.sqlstate = sqlstate


class RegistrationPersistenceError(RuntimeError):
    """Carry a safe database failure classification beyond an adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Registration persistence failed")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class RegisterUserCommand:
    """Input required by the registration application service."""

    email: str
    password: SecretStr = field(repr=False)
    trace_id: TraceId


@dataclass(frozen=True, slots=True)
class AuthenticateCredentialsCommand:
    """Untrusted credentials supplied to the authentication use case."""

    email: str = field(repr=False)
    password: SecretStr = field(repr=False)
    trace_id: TraceId


@dataclass(frozen=True, slots=True)
class StoredCredentials:
    """Minimal persistence snapshot required to verify one account."""

    user_id: UUID
    email: NormalizedEmail
    password_hash: PasswordHash = field(repr=False)
    status: AccountStatus


@dataclass(frozen=True, slots=True)
class VerifiedCredentials:
    """Internal proof passed to the later session-creation transaction."""

    user_id: UUID
    email: NormalizedEmail
    expected_password_hash: PasswordHash = field(repr=False)
    password_rehash_required: bool


@dataclass(frozen=True, slots=True)
class AccessToken:
    """Short-lived signed credential that delivery code may reveal once."""

    _value: SecretStr = field(repr=False)

    @classmethod
    def from_transport(cls, raw_value: str) -> Self:
        """Wrap an encoded JWT without putting it into normal object output."""

        return cls(SecretStr(raw_value))

    def reveal_for_transport(self) -> str:
        """Reveal the compact JWT only at an HTTP or verification boundary."""

        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True)
class IssueAccessTokenCommand:
    """Trusted identifiers and time used to create one short-lived JWT."""

    user_id: UUID
    session_id: UUID
    issued_at: datetime

    def __post_init__(self) -> None:
        """Require an unambiguous UTC issuance time."""

        if self.user_id.int == 0 or self.session_id.int == 0:
            raise ValueError("Access token identifiers must not be nil UUIDs")

        if not _is_utc_timestamp(self.issued_at):
            raise ValueError("Access token issuance time must use timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Verified internal meaning of the fixed Access Token claim set."""

    user_id: UUID
    session_id: UUID
    jwt_id: UUID
    issued_at: datetime
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Reject ambiguous timestamps or internally inconsistent validity windows."""

        if self.user_id.int == 0 or self.session_id.int == 0 or self.jwt_id.int == 0:
            raise ValueError("Access token claim identifiers must not be nil UUIDs")

        timestamps = (self.issued_at, self.not_before, self.expires_at)

        if any(not _is_utc_timestamp(timestamp) for timestamp in timestamps):
            raise ValueError("Access token claim timestamps must use timezone-aware UTC")

        if self.issued_at != self.not_before or self.expires_at <= self.issued_at:
            raise ValueError("Access token validity window is inconsistent")


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    """Signed transport value paired with the exact claims used to create it."""

    token: AccessToken = field(repr=False)
    claims: AccessTokenClaims


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Opaque browser credential accepted only by the refresh-token path."""

    _value: SecretStr = field(repr=False)

    @classmethod
    def from_transport(cls, raw_value: str) -> Self:
        """Wrap untrusted HTTP input without logging or validating it yet."""

        return cls(SecretStr(raw_value))

    def reveal_for_transport(self) -> str:
        """Reveal plaintext only when setting a Cookie or calculating its digest."""

        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True)
class CsrfToken:
    """Opaque browser value accepted only by the CSRF-token path."""

    _value: SecretStr = field(repr=False)

    @classmethod
    def from_transport(cls, raw_value: str) -> Self:
        """Wrap untrusted HTTP input without logging or validating it yet."""

        return cls(SecretStr(raw_value))

    def reveal_for_transport(self) -> str:
        """Reveal plaintext only when setting a Cookie or calculating its digest."""

        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True)
class DeviceToken:
    """Opaque browser credential accepted only by the device-token path."""

    _value: SecretStr = field(repr=False)

    @classmethod
    def from_transport(cls, raw_value: str) -> Self:
        """Wrap untrusted HTTP input without logging or validating it yet."""

        return cls(SecretStr(raw_value))

    def reveal_for_transport(self) -> str:
        """Reveal plaintext only when setting a Cookie or calculating its digest."""

        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True)
class IssuedLoginSessionTokens:
    """Three browser tokens paired with the only values persistence may receive."""

    refresh_token: RefreshToken = field(repr=False)
    csrf_token: CsrfToken = field(repr=False)
    device_token: DeviceToken = field(repr=False)
    refresh_token_hash: RefreshTokenHash = field(repr=False)
    csrf_token_hash: CsrfTokenHash = field(repr=False)
    device_token_hash: DeviceTokenHash = field(repr=False)


@dataclass(frozen=True, slots=True)
class IssuedRefreshSuccessorTokens:
    """New Refresh and CSRF values issued together while the device stays stable."""

    refresh_token: RefreshToken = field(repr=False)
    csrf_token: CsrfToken = field(repr=False)
    refresh_token_hash: RefreshTokenHash = field(repr=False)
    csrf_token_hash: CsrfTokenHash = field(repr=False)


@dataclass(frozen=True, slots=True)
class RefreshSuccessorTokens:
    """The two rotating browser values recoverable after a lost response."""

    refresh_token: RefreshToken = field(repr=False)
    csrf_token: CsrfToken = field(repr=False)


@dataclass(frozen=True, slots=True)
class RefreshRecoveryContext:
    """Stable identifiers cryptographically bound to one successor response."""

    predecessor_session_id: UUID
    successor_session_id: UUID
    rotation_family_id: UUID
    user_id: UUID
    device_token_hash: DeviceTokenHash = field(repr=False)

    def __post_init__(self) -> None:
        identifiers = (
            self.predecessor_session_id,
            self.successor_session_id,
            self.rotation_family_id,
            self.user_id,
        )

        if any(identifier.int == 0 for identifier in identifiers):
            raise ValueError("Refresh recovery identifiers must not be nil UUIDs")

        if len(self.device_token_hash) != 32:
            raise ValueError("Refresh recovery device hash must contain exactly 32 bytes")


@dataclass(frozen=True, slots=True)
class RefreshSessionCommand:
    """Untrusted browser values required for one refresh attempt."""

    origin: str = field(repr=False)
    refresh_token: RefreshToken = field(repr=False)
    csrf_cookie_value: str = field(repr=False)
    csrf_header_value: str = field(repr=False)
    device_token: DeviceToken = field(repr=False)
    trace_id: TraceId


@dataclass(frozen=True, slots=True)
class LockedRefreshSessionState:
    """Technology-independent snapshot of one refresh row held under a DB lock."""

    user_id: UUID
    rotation_family_id: UUID
    session_id: UUID
    previous_session_id: UUID | None
    replaced_by_session_id: UUID | None
    refresh_token_hash: RefreshTokenHash = field(repr=False)
    csrf_token_hash: CsrfTokenHash = field(repr=False)
    device_token_hash: DeviceTokenHash = field(repr=False)
    idle_expires_at: datetime
    absolute_expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None
    recovery_envelope: RefreshRecoveryEnvelope | None = field(repr=False)
    recovery_expires_at: datetime | None

    def __post_init__(self) -> None:
        identifiers = (
            self.user_id,
            self.rotation_family_id,
            self.session_id,
        )
        digests = (
            self.refresh_token_hash,
            self.csrf_token_hash,
            self.device_token_hash,
        )
        timestamps = (
            self.idle_expires_at,
            self.absolute_expires_at,
            self.used_at,
            self.revoked_at,
            self.recovery_expires_at,
        )

        if any(identifier.int == 0 for identifier in identifiers):
            raise ValueError("Locked refresh identifiers must not be nil UUIDs")
        if any(len(digest) != 32 for digest in digests):
            raise ValueError("Locked refresh token hashes must contain exactly 32 bytes")
        if any(
            timestamp is not None and not _is_utc_timestamp(timestamp) for timestamp in timestamps
        ):
            raise ValueError("Locked refresh timestamps must use timezone-aware UTC")
        if self.idle_expires_at > self.absolute_expires_at:
            raise ValueError("Locked refresh expiration order is invalid")
        if (self.used_at is None) != (self.replaced_by_session_id is None):
            raise ValueError("Locked refresh rotation state is inconsistent")
        if (self.recovery_envelope is None) != (self.recovery_expires_at is None):
            raise ValueError("Locked refresh recovery state is inconsistent")


@dataclass(frozen=True, slots=True)
class LockedRefreshRotation:
    """User, family, presented row, and current row locked in one order."""

    user_status: AccountStatus
    family_id: UUID
    family_current_session_id: UUID
    family_absolute_expires_at: datetime
    family_revoked_at: datetime | None
    checked_at: datetime
    presented: LockedRefreshSessionState
    current: LockedRefreshSessionState

    def __post_init__(self) -> None:
        timestamps = (
            self.family_absolute_expires_at,
            self.family_revoked_at,
            self.checked_at,
        )

        if self.family_id.int == 0 or self.family_current_session_id.int == 0:
            raise ValueError("Locked refresh family identifiers must not be nil UUIDs")
        if any(
            timestamp is not None and not _is_utc_timestamp(timestamp) for timestamp in timestamps
        ):
            raise ValueError("Locked refresh family timestamps must use timezone-aware UTC")
        if self.presented.rotation_family_id != self.family_id:
            raise ValueError("Presented refresh session belongs to another family")
        if self.current.rotation_family_id != self.family_id:
            raise ValueError("Current refresh session belongs to another family")
        if self.current.session_id != self.family_current_session_id:
            raise ValueError("Locked refresh current pointer is inconsistent")
        if self.presented.user_id != self.current.user_id:
            raise ValueError("Locked refresh sessions belong to different users")


@dataclass(frozen=True, slots=True)
class PersistRefreshSuccessorCommand:
    """Persistence-safe values for one first-use refresh rotation."""

    user_id: UUID
    rotation_family_id: UUID
    predecessor_session_id: UUID
    successor_session_id: UUID
    refresh_token_hash: RefreshTokenHash = field(repr=False)
    csrf_token_hash: CsrfTokenHash = field(repr=False)
    device_token_hash: DeviceTokenHash = field(repr=False)
    issued_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    recovery_envelope: RefreshRecoveryEnvelope = field(repr=False)
    recovery_expires_at: datetime
    trace_id: TraceId

    def __post_init__(self) -> None:
        identifiers = (
            self.user_id,
            self.rotation_family_id,
            self.predecessor_session_id,
            self.successor_session_id,
        )
        digests = (
            self.refresh_token_hash,
            self.csrf_token_hash,
            self.device_token_hash,
        )
        timestamps = (
            self.issued_at,
            self.idle_expires_at,
            self.absolute_expires_at,
            self.recovery_expires_at,
        )

        if any(identifier.int == 0 for identifier in identifiers):
            raise ValueError("Refresh successor identifiers must not be nil UUIDs")
        if self.predecessor_session_id == self.successor_session_id:
            raise ValueError("Refresh successor must differ from its predecessor")
        if any(len(digest) != 32 for digest in digests):
            raise ValueError("Refresh successor token hashes must contain exactly 32 bytes")
        if any(not _is_utc_timestamp(timestamp) for timestamp in timestamps):
            raise ValueError("Refresh successor timestamps must use timezone-aware UTC")
        if not self.issued_at < self.idle_expires_at <= self.absolute_expires_at:
            raise ValueError("Refresh successor expiration order is invalid")
        if not self.issued_at < self.recovery_expires_at <= self.absolute_expires_at:
            raise ValueError("Refresh recovery expiration order is invalid")
        if not self.recovery_envelope:
            raise ValueError("Refresh recovery envelope must not be empty")


@dataclass(frozen=True, slots=True)
class RevokeRefreshFamilyCommand:
    """Sanitized instruction that must commit before refresh rejection escapes."""

    user_id: UUID
    rotation_family_id: UUID
    detected_session_id: UUID
    revoked_at: datetime
    reason: RefreshRevocationReason
    trace_id: TraceId

    def __post_init__(self) -> None:
        identifiers = (
            self.user_id,
            self.rotation_family_id,
            self.detected_session_id,
        )
        if any(identifier.int == 0 for identifier in identifiers):
            raise ValueError("Refresh revocation identifiers must not be nil UUIDs")
        if not _is_utc_timestamp(self.revoked_at):
            raise ValueError("Refresh revocation timestamp must use timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class RecordRefreshRecoveryCommand:
    """Sanitized audit instruction for a successful lost-response recovery."""

    user_id: UUID
    rotation_family_id: UUID
    session_id: UUID
    recovered_at: datetime
    trace_id: TraceId

    def __post_init__(self) -> None:
        if any(
            identifier.int == 0
            for identifier in (
                self.user_id,
                self.rotation_family_id,
                self.session_id,
            )
        ):
            raise ValueError("Refresh recovery audit identifiers must not be nil UUIDs")
        if not _is_utc_timestamp(self.recovered_at):
            raise ValueError("Refresh recovery audit timestamp must use timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class CreateLoginSessionCommand:
    """Persistence-safe inputs required to atomically establish a login session."""

    user_id: UUID
    expected_password_hash: PasswordHash = field(repr=False)
    refresh_token_hash: RefreshTokenHash = field(repr=False)
    csrf_token_hash: CsrfTokenHash = field(repr=False)
    device_token_hash: DeviceTokenHash = field(repr=False)
    issued_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    trace_id: TraceId
    replacement_password_hash: PasswordHash | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Reject malformed hashes and ambiguous or unsafe expiration windows."""

        digests = (
            self.refresh_token_hash,
            self.csrf_token_hash,
            self.device_token_hash,
        )

        if any(len(digest) != 32 for digest in digests):
            raise ValueError("Login session token hashes must contain exactly 32 bytes")

        timestamps = (
            self.issued_at,
            self.idle_expires_at,
            self.absolute_expires_at,
        )

        if any(
            timestamp.tzinfo is None or timestamp.utcoffset() is None for timestamp in timestamps
        ):
            raise ValueError("Login session timestamps must be timezone-aware")

        if any(timestamp.utcoffset() != timedelta(0) for timestamp in timestamps):
            raise ValueError("Login session timestamps must use UTC")

        if not self.issued_at < self.idle_expires_at <= self.absolute_expires_at:
            raise ValueError("Login session expiration order is invalid")


@dataclass(frozen=True, slots=True)
class LoginSessionRecord:
    """Identifiers and expiration boundaries committed for one login."""

    user_id: UUID
    rotation_family_id: UUID
    session_id: UUID
    issued_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class EstablishedLoginSession:
    """Committed session metadata plus plaintext values needed by HTTP delivery."""

    email: NormalizedEmail = field(repr=False)
    session: LoginSessionRecord
    access_token: AccessToken = field(repr=False)
    access_token_expires_at: datetime
    refresh_token: RefreshToken = field(repr=False)
    csrf_token: CsrfToken = field(repr=False)
    device_token: DeviceToken = field(repr=False)

    def __post_init__(self) -> None:
        """Require one usable UTC Access Token window for the committed session."""

        if not _is_utc_timestamp(self.access_token_expires_at):
            raise ValueError("Access token expiration must use timezone-aware UTC")

        if self.access_token_expires_at <= self.session.issued_at:
            raise ValueError("Access token must expire after session issuance")


@dataclass(frozen=True, slots=True)
class RefreshedSession:
    """Committed rotation result containing only values required by delivery."""

    session: LoginSessionRecord
    access_token: AccessToken = field(repr=False)
    access_token_expires_at: datetime
    refresh_token: RefreshToken = field(repr=False)
    csrf_token: CsrfToken = field(repr=False)
    recovered: bool

    def __post_init__(self) -> None:
        if not _is_utc_timestamp(self.access_token_expires_at):
            raise ValueError("Access token expiration must use timezone-aware UTC")
        if self.access_token_expires_at <= self.session.issued_at:
            raise ValueError("Access token must expire after refresh issuance")


@dataclass(frozen=True, slots=True)
class RegistrationRecord:
    """Non-sensitive registration result safe to return from the service."""

    user_id: UUID
    email: NormalizedEmail
    workspace_id: UUID
    workspace_name: str
    workspace_role: Literal["owner"] = "owner"
