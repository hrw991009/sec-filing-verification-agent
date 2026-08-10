"""Tests for the identity new-password policy."""

import pytest
from anyio import CapacityLimiter
from argon2 import extract_parameters
from argon2.low_level import Type
from pydantic import SecretStr

from industry_platform.core.config import Settings
from industry_platform.modules.identity.adapters.argon2 import Argon2idPasswordHasher
from industry_platform.modules.identity.domain import PasswordHash
from industry_platform.modules.identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    ValidatedPassword,
)


@pytest.mark.parametrize(
    "raw_value",
    [
        "a" * MIN_PASSWORD_LENGTH,
        "界" * MAX_PASSWORD_LENGTH,
        "correct horse battery staple",
    ],
)
def test_password_policy_accepts_boundaries_unicode_and_password_manager_values(
    raw_value: str,
) -> None:
    validated = ValidatedPassword.from_secret(SecretStr(raw_value))

    assert validated.reveal() == raw_value
    assert raw_value not in repr(validated)


@pytest.mark.parametrize(
    "raw_value",
    [
        "a" * (MIN_PASSWORD_LENGTH - 1),
        "界" * (MAX_PASSWORD_LENGTH + 1),
    ],
)
def test_password_policy_rejects_values_outside_the_character_boundaries_without_leaking_them(
    raw_value: str,
) -> None:
    with pytest.raises(PasswordPolicyError) as exc_info:
        ValidatedPassword.from_secret(SecretStr(raw_value))

    assert raw_value not in str(exc_info.value)


def test_password_policy_preserves_leading_and_trailing_whitespace() -> None:
    raw_value = "  preserve me  "

    validated = ValidatedPassword.from_secret(SecretStr(raw_value))

    assert validated.reveal() == raw_value


@pytest.mark.asyncio
async def test_argon2id_hasher_uses_the_accepted_security_baseline(
    test_settings: Settings,
) -> None:
    raw_value = "unicode-口令-12345"
    validated = ValidatedPassword.from_secret(SecretStr(raw_value))
    hasher = Argon2idPasswordHasher(test_settings, limiter=CapacityLimiter(1))

    encoded_hash = await hasher.hash(validated)
    parameters = extract_parameters(str(encoded_hash))

    assert parameters.type is Type.ID
    assert parameters.version == 19
    assert parameters.memory_cost == 65_536
    assert parameters.time_cost == 3
    assert parameters.parallelism == 1
    assert parameters.salt_len == 16
    assert parameters.hash_len == 32
    assert raw_value not in str(encoded_hash)


@pytest.mark.asyncio
async def test_argon2id_hashes_use_random_salts_and_verify_safely(
    test_settings: Settings,
) -> None:
    raw_value = "another-valid-口令"
    validated = ValidatedPassword.from_secret(SecretStr(raw_value))
    hasher = Argon2idPasswordHasher(test_settings, limiter=CapacityLimiter(1))

    first_hash = await hasher.hash(validated)
    second_hash = await hasher.hash(validated)

    assert first_hash != second_hash
    assert await hasher.verify(first_hash, SecretStr(raw_value)) is True
    assert await hasher.verify(first_hash, SecretStr("different-long-value")) is False
    assert (
        await hasher.verify(
            PasswordHash("not-an-argon2-hash"),
            SecretStr(raw_value),
        )
        is False
    )
    assert await hasher.needs_rehash(first_hash) is False
    assert await hasher.needs_rehash(PasswordHash("not-an-argon2-hash")) is True
