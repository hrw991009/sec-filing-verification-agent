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


class ChangePasswordRequest(BaseModel):
    """Current proof and policy-compliant replacement accepted from one user."""

    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: SecretStr = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("new_password")
    @classmethod
    def validate_new_password_policy(cls, value: SecretStr) -> SecretStr:
        """Apply the same domain policy used by registration."""

        ValidatedPassword.from_secret(value)
        return value


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


class RefreshResponse(BaseModel):
    """New short-lived Access Token returned after safe session rotation."""

    access_token: str = Field(min_length=1, repr=False)
    token_type: Literal["Bearer"] = BEARER_SCHEME
    expires_at: datetime


class CurrentWorkspace(BaseModel):
    """One live Workspace membership returned by the current-user endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    role: Literal["owner", "admin", "member", "viewer"]


class CurrentUserResponse(BaseModel):
    """Current server-verified account and its live Workspace memberships."""

    model_config = ConfigDict(extra="forbid")

    user: AuthenticatedUser
    workspaces: list[CurrentWorkspace]
