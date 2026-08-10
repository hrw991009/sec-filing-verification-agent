"""Technology-independent interfaces owned by the identity application layer."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    NormalizedEmail,
    PasswordHash,
    RegisterUserCommand,
    RegistrationRecord,
    TraceId,
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
