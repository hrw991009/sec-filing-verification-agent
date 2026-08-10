"""Technology-independent values and failures used by identity workflows."""

from dataclasses import dataclass, field
from typing import Literal, NewType
from uuid import UUID

from pydantic import SecretStr

NormalizedEmail = NewType("NormalizedEmail", str)
PasswordHash = NewType("PasswordHash", str)
TraceId = NewType("TraceId", str)


class EmailAlreadyRegisteredError(RuntimeError):
    """Raised when a normalized email already belongs to an account."""


class InvalidEmailAddressError(ValueError):
    """Raised when an application caller supplies an invalid email address."""


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
class RegistrationRecord:
    """Non-sensitive registration result safe to return from the service."""

    user_id: UUID
    email: NormalizedEmail
    workspace_id: UUID
    workspace_name: str
    workspace_role: Literal["owner"] = "owner"
