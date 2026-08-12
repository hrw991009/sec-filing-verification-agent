"""Tests for Access Token verification plus live session resolution."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenClaims,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    InvalidAccessTokenError,
    InvalidAuthenticatedSessionError,
    IssueAccessTokenCommand,
    IssuedAccessToken,
    NormalizedEmail,
)
from industry_platform.modules.identity.service import AuthenticatedPrincipalService

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
JWT_ID = UUID("44444444-4444-4444-8444-444444444444")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


def valid_claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        user_id=USER_ID,
        session_id=SESSION_ID,
        jwt_id=JWT_ID,
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def valid_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("learner@example.com"),
        workspaces=(
            AuthenticatedWorkspace(
                workspace_id=WORKSPACE_ID,
                name="My Workspace",
                role="owner",
            ),
        ),
    )


class RecordingAccessCodec:
    def __init__(self, *, claims: AccessTokenClaims | None = None) -> None:
        self.claims = claims
        self.verification_calls: list[tuple[AccessToken, datetime]] = []

    def issue(self, command: IssueAccessTokenCommand) -> IssuedAccessToken:
        raise AssertionError(f"Unexpected access issuance for {command.user_id}")

    def verify(self, token: AccessToken, *, now: datetime) -> AccessTokenClaims:
        self.verification_calls.append((token, now))
        if self.claims is None:
            raise InvalidAccessTokenError
        return self.claims


class RecordingSessionReader:
    def __init__(self, principal: AuthenticatedPrincipal | None) -> None:
        self.principal = principal
        self.calls: list[tuple[AccessTokenClaims, datetime]] = []

    async def find_active(
        self,
        claims: AccessTokenClaims,
        *,
        now: datetime,
    ) -> AuthenticatedPrincipal | None:
        self.calls.append((claims, now))
        return self.principal


@pytest.mark.asyncio
async def test_resolve_verifies_jwt_then_rechecks_server_session() -> None:
    claims = valid_claims()
    principal = valid_principal()
    codec = RecordingAccessCodec(claims=claims)
    reader = RecordingSessionReader(principal)
    service = AuthenticatedPrincipalService(
        access_token_codec=codec,
        session_reader=reader,
        clock=lambda: NOW,
    )
    presented = AccessToken.from_transport(RAW_ACCESS_VALUE)

    resolved = await service.resolve(presented)

    assert resolved == principal
    assert codec.verification_calls == [(presented, NOW)]
    assert reader.calls == [(claims, NOW)]


@pytest.mark.asyncio
async def test_invalid_jwt_is_rejected_without_querying_postgres() -> None:
    codec = RecordingAccessCodec()
    reader = RecordingSessionReader(valid_principal())
    service = AuthenticatedPrincipalService(
        access_token_codec=codec,
        session_reader=reader,
        clock=lambda: NOW,
    )

    with pytest.raises(InvalidAuthenticatedSessionError):
        await service.resolve(AccessToken.from_transport(RAW_ACCESS_VALUE))

    assert reader.calls == []


@pytest.mark.asyncio
async def test_revoked_or_expired_server_session_rejects_valid_jwt() -> None:
    codec = RecordingAccessCodec(claims=valid_claims())
    reader = RecordingSessionReader(None)
    service = AuthenticatedPrincipalService(
        access_token_codec=codec,
        session_reader=reader,
        clock=lambda: NOW,
    )

    with pytest.raises(InvalidAuthenticatedSessionError):
        await service.resolve(AccessToken.from_transport(RAW_ACCESS_VALUE))

    assert len(reader.calls) == 1
