"""Tests for process-wide identity adapter composition."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.core.redis_client import create_redis_client
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.login_rate_limits import (
    RedisLoginAttemptRateLimiter,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.domain import IssueAccessTokenCommand
from industry_platform.modules.identity.resources import create_identity_resources
from industry_platform.modules.identity.service import (
    LoginSessionService,
    RefreshSessionService,
    RegistrationService,
)


@pytest.mark.asyncio
async def test_identity_resources_wire_the_complete_login_runtime(
    test_settings: Settings,
) -> None:
    engine = create_database_engine(test_settings)
    redis_client = create_redis_client(test_settings)

    try:
        resources = await create_identity_resources(
            test_settings,
            create_database_session_factory(engine),
            redis_client,
        )
        issued = resources.session_token_service.issue()
        access_issued_at = datetime(2026, 8, 11, 6, 0, tzinfo=UTC)
        issued_access = resources.access_token_codec.issue(
            IssueAccessTokenCommand(
                user_id=UUID("11111111-1111-4111-8111-111111111111"),
                session_id=UUID("22222222-2222-4222-8222-222222222222"),
                issued_at=access_issued_at,
            )
        )
        verified_access = resources.access_token_codec.verify(
            issued_access.token,
            now=access_issued_at,
        )

        assert isinstance(resources.registration_service, RegistrationService)
        assert isinstance(resources.login_service, LoginSessionService)
        assert isinstance(resources.refresh_service, RefreshSessionService)
        assert isinstance(resources.session_token_service, HmacSessionTokenService)
        assert isinstance(resources.access_token_codec, Ed25519AccessTokenCodec)
        assert isinstance(resources.login_rate_limiter, RedisLoginAttemptRateLimiter)
        assert verified_access == issued_access.claims
        assert (
            resources.session_token_service.digest_refresh(issued.refresh_token)
            == issued.refresh_token_hash
        )
        assert (
            resources.session_token_service.digest_csrf(issued.csrf_token) == issued.csrf_token_hash
        )
        assert (
            resources.session_token_service.digest_device(issued.device_token)
            == issued.device_token_hash
        )
    finally:
        await redis_client.aclose()
        await engine.dispose()
