"""Public HTTP request and response schemas for identity endpoints."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from industry_platform.modules.identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    ValidatedPassword,
)


class RegisterRequest(BaseModel):
    """Untrusted JSON accepted by the public registration endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("password")
    @classmethod
    def validate_password_policy(cls, password: SecretStr) -> SecretStr:
        """Keep the HTTP boundary aligned with the domain password policy."""

        ValidatedPassword.from_secret(password)
        return password


class RegisteredUser(BaseModel):
    """Safe user fields returned after registration."""

    id: UUID
    email: EmailStr


class RegisteredWorkspace(BaseModel):
    """Safe initial-workspace fields returned after registration."""

    id: UUID
    name: str
    role: Literal["owner"]


class RegistrationResponse(BaseModel):
    """Public representation of a completed registration."""

    user: RegisteredUser
    workspace: RegisteredWorkspace
