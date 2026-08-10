"""Registration application service."""

from industry_platform.modules.identity.domain import (
    RegisterUserCommand,
    RegistrationRecord,
)
from industry_platform.modules.identity.emails import normalize_email_address
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.ports import (
    PasswordHasher,
    RegistrationTransactionFactory,
)

DEFAULT_WORKSPACE_NAME = "My Workspace"


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
