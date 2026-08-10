"""Argon2id password adapter with bounded worker-thread concurrency."""

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher as Argon2Engine
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from pydantic import SecretStr

from industry_platform.core.config import Settings
from industry_platform.modules.identity.domain import PasswordHash
from industry_platform.modules.identity.passwords import ValidatedPassword


class Argon2idPasswordHasher:
    """Run memory-hard Argon2id operations away from the async event loop."""

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: CapacityLimiter,
    ) -> None:
        self._engine = Argon2Engine(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=settings.argon2_hash_length,
            salt_len=settings.argon2_salt_length,
            type=Type.ID,
        )
        self._limiter = limiter

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        """Hash a new password with a fresh random salt."""

        encoded_hash = await to_thread.run_sync(
            self._engine.hash,
            password.reveal(),
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
