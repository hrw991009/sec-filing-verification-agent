"""Argon2id password adapter with bounded worker-thread concurrency."""

from dataclasses import dataclass
from functools import lru_cache
from secrets import token_urlsafe
from threading import Lock

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher as Argon2Engine
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from pydantic import SecretStr

from industry_platform.core.config import Settings
from industry_platform.modules.identity.domain import PasswordHash
from industry_platform.modules.identity.passwords import ValidatedPassword


@dataclass(frozen=True, slots=True)
class _Argon2Parameters:
    """Hashing parameters that safely identify one process-cached dummy hash."""

    time_cost: int
    memory_cost: int
    parallelism: int
    hash_len: int
    salt_len: int


def _create_argon2_engine(parameters: _Argon2Parameters) -> Argon2Engine:
    """Create one engine from the same explicit parameter set everywhere."""

    return Argon2Engine(
        time_cost=parameters.time_cost,
        memory_cost=parameters.memory_cost,
        parallelism=parameters.parallelism,
        hash_len=parameters.hash_len,
        salt_len=parameters.salt_len,
        type=Type.ID,
    )


@lru_cache(maxsize=8)
def _build_cached_dummy_hash(parameters: _Argon2Parameters) -> PasswordHash:
    """Build one unpredictable dummy hash per Argon2 parameter set."""

    engine = _create_argon2_engine(parameters)
    raw_dummy_value = token_urlsafe(32)
    return PasswordHash(engine.hash(raw_dummy_value))


_DUMMY_HASH_LOCK = Lock()


def _get_cached_dummy_hash(parameters: _Argon2Parameters) -> PasswordHash:
    """Prevent concurrent cold starts from repeating expensive Argon2 work."""

    with _DUMMY_HASH_LOCK:
        return _build_cached_dummy_hash(parameters)


class Argon2idPasswordHasher:
    """Run memory-hard Argon2id operations away from the async event loop."""

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: CapacityLimiter,
    ) -> None:
        self._parameters = _Argon2Parameters(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=settings.argon2_hash_length,
            salt_len=settings.argon2_salt_length,
        )
        self._engine = _create_argon2_engine(self._parameters)
        self._limiter = limiter

    async def get_dummy_password_hash(self) -> PasswordHash:
        """Return a process-cached hash used to equalize unknown-user login work."""

        return await to_thread.run_sync(
            _get_cached_dummy_hash,
            self._parameters,
            limiter=self._limiter,
        )

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        """Hash a new password with a fresh random salt."""

        return await self._hash_raw_value(password.reveal())

    async def rehash_verified(self, password: SecretStr) -> PasswordHash:
        """Upgrade a verified legacy password without treating it as a new choice."""

        return await self._hash_raw_value(password.get_secret_value())

    async def _hash_raw_value(self, raw_value: str) -> PasswordHash:
        """Run the shared Argon2 hashing operation in the bounded worker pool."""

        encoded_hash = await to_thread.run_sync(
            self._engine.hash,
            raw_value,
            limiter=self._limiter,
        )
        return PasswordHash(encoded_hash)

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        """Treat a mismatch or malformed stored hash as failed authentication."""

        def verify_in_worker() -> bool:
            try:
                return self._engine.verify(
                    password_hash,
                    password.get_secret_value(),
                )
            except (InvalidHashError, VerificationError):
                return False

        return await to_thread.run_sync(
            verify_in_worker,
            limiter=self._limiter,
        )

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        """Request replacement when a valid hash no longer matches settings."""

        def check_in_worker() -> bool:
            try:
                return self._engine.check_needs_rehash(password_hash)
            except InvalidHashError:
                return True

        return await to_thread.run_sync(
            check_in_worker,
            limiter=self._limiter,
        )
