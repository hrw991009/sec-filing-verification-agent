"""Tests for the credential-authentication application service."""

from uuid import UUID

import pytest
from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    AccountStatus,
    AuthenticateCredentialsCommand,
    InvalidCredentialsError,
    NormalizedEmail,
    PasswordHash,
    StoredCredentials,
    TraceId,
)
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import CredentialAuthenticationService

USER_ID = UUID("33333333-3333-4333-8333-333333333333")
STORED_HASH = PasswordHash("$argon2id$stored-test-value")
DUMMY_HASH = PasswordHash("$argon2id$dummy-test-value")
RAW_VALUE = "correct horse battery staple"


class RecordingCredentialReader:
    """Return one configured snapshot and record canonical lookup values."""

    def __init__(self, result: StoredCredentials | None) -> None:
        self.result = result
        self.calls: list[NormalizedEmail] = []

    async def find_by_email(
        self,
        email: NormalizedEmail,
    ) -> StoredCredentials | None:
        self.calls.append(email)
        return self.result


class RecordingPasswordHasher:
    """Record verification behavior without performing expensive Argon2 work."""

    def __init__(self, *, matches: bool, needs_rehash: bool = False) -> None:
        self.matches = matches
        self.rehash_result = needs_rehash
        self.verify_calls: list[tuple[PasswordHash, str]] = []
        self.rehash_calls: list[PasswordHash] = []

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        del password
        raise AssertionError("Authentication must not hash a new password")

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        self.verify_calls.append((password_hash, password.get_secret_value()))
        return self.matches

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        self.rehash_calls.append(password_hash)
        return self.rehash_result


def stored_credentials(*, status: AccountStatus = "active") -> StoredCredentials:
    """Build a persistence snapshot without exposing its hash in test output."""

    return StoredCredentials(
        user_id=USER_ID,
        email=NormalizedEmail("learner@example.com"),
        password_hash=STORED_HASH,
        status=status,
    )


def authentication_command(
    *,
    email: str = "  Learner@Example.COM ",
) -> AuthenticateCredentialsCommand:
    """Build untrusted input while keeping the raw credential out of repr."""

    return AuthenticateCredentialsCommand(
        email=email,
        password=SecretStr(RAW_VALUE),
        trace_id=TraceId("authentication-test-trace"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rehash_required", [False, True])
async def test_authentication_normalizes_email_and_returns_internal_proof(
    rehash_required: bool,
) -> None:
    reader = RecordingCredentialReader(stored_credentials())
    hasher = RecordingPasswordHasher(
        matches=True,
        needs_rehash=rehash_required,
    )
    service = CredentialAuthenticationService(
        password_hasher=hasher,
        credential_reader=reader,
        dummy_password_hash=DUMMY_HASH,
    )

    result = await service.authenticate(authentication_command())

    assert reader.calls == [NormalizedEmail("learner@example.com")]
    assert hasher.verify_calls == [(STORED_HASH, RAW_VALUE)]
    assert hasher.rehash_calls == [STORED_HASH]
    assert result.user_id == USER_ID
    assert result.email == NormalizedEmail("learner@example.com")
    assert result.expected_password_hash == STORED_HASH
    assert result.password_rehash_required is rehash_required
    assert RAW_VALUE not in repr(result)
    assert str(STORED_HASH) not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record", "matches", "expected_hash"),
    [
        (stored_credentials(), False, STORED_HASH),
        (None, False, DUMMY_HASH),
    ],
)
async def test_wrong_and_unknown_credentials_share_one_rejection(
    record: StoredCredentials | None,
    matches: bool,
    expected_hash: PasswordHash,
) -> None:
    reader = RecordingCredentialReader(record)
    hasher = RecordingPasswordHasher(matches=matches)
    service = CredentialAuthenticationService(
        password_hasher=hasher,
        credential_reader=reader,
        dummy_password_hash=DUMMY_HASH,
    )

    with pytest.raises(InvalidCredentialsError) as exc_info:
        await service.authenticate(authentication_command())

    assert str(exc_info.value) == "Invalid email or password"
    assert hasher.verify_calls == [(expected_hash, RAW_VALUE)]
    assert hasher.rehash_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["disabled", "deleting", "deleted"])
async def test_non_active_accounts_verify_once_then_use_the_generic_rejection(
    status: AccountStatus,
) -> None:
    reader = RecordingCredentialReader(stored_credentials(status=status))
    hasher = RecordingPasswordHasher(matches=True)
    service = CredentialAuthenticationService(
        password_hasher=hasher,
        credential_reader=reader,
        dummy_password_hash=DUMMY_HASH,
    )

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(authentication_command())

    assert hasher.verify_calls == [(STORED_HASH, RAW_VALUE)]
    assert hasher.rehash_calls == []


@pytest.mark.asyncio
async def test_invalid_email_still_runs_the_dummy_password_path() -> None:
    reader = RecordingCredentialReader(stored_credentials())
    hasher = RecordingPasswordHasher(matches=False)
    service = CredentialAuthenticationService(
        password_hasher=hasher,
        credential_reader=reader,
        dummy_password_hash=DUMMY_HASH,
    )
    command = authentication_command(email="not-an-email")

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(command)

    assert reader.calls == []
    assert hasher.verify_calls == [(DUMMY_HASH, RAW_VALUE)]
    assert hasher.rehash_calls == []
    assert RAW_VALUE not in repr(command)
    assert command.email not in repr(command)
