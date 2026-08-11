"""Public HTTP request and response schemas for identity endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from industry_platform.modules.identity.emails import MAX_STORED_EMAIL_LENGTH
from industry_platform.modules.identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    ValidatedPassword,
)

BEARER_SCHEME: Literal["Bearer"] = "Bearer"


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


class LoginRequest(BaseModel):
    """Untrusted credentials accepted without applying the new-password policy."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=MAX_STORED_EMAIL_LENGTH)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class AuthenticatedUser(BaseModel):
    """Safe user identity returned by registration and login."""

    id: UUID
    email: EmailStr


class RegisteredWorkspace(BaseModel):
    """Safe initial-workspace fields returned after registration."""

    id: UUID
    name: str
    role: Literal["owner"]


class RegistrationResponse(BaseModel):
    """Public representation of a completed registration."""

    user: AuthenticatedUser
    workspace: RegisteredWorkspace


class LoginResponse(BaseModel):
    """Short-lived Access Token and safe identity returned after login."""

    user: AuthenticatedUser
    access_token: str = Field(min_length=1, repr=False)
    token_type: Literal["Bearer"] = BEARER_SCHEME
    expires_at: datetime
