"""Process-wide identity services assembled from concrete adapters."""

from dataclasses import dataclass

from anyio import CapacityLimiter
from fastapi import Request
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.argon2 import Argon2idPasswordHasher
from industry_platform.modules.identity.adapters.login_rate_limits import (
    RedisLoginAttemptRateLimiter,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    SqlAlchemyCredentialReader,
    SqlAlchemyLoginSessionTransactionFactory,
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.ports import (
    AccessTokenCodec,
    LoginAttemptRateLimiter,
    LoginSessionTokenService,
    LoginSessionUseCase,
    RegistrationUseCase,
)
from industry_platform.modules.identity.service import (
    CredentialAuthenticationService,
    LoginSessionService,
    RegistrationService,
)


@dataclass(frozen=True, slots=True)
class IdentityResources:
    """Long-lived, stateless identity application services."""

    registration_service: RegistrationUseCase
    login_service: LoginSessionUseCase
    session_token_service: LoginSessionTokenService
    access_token_codec: AccessTokenCodec
    login_rate_limiter: LoginAttemptRateLimiter


async def create_identity_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    redis_client: Redis,
) -> IdentityResources:
    """Create identity adapters and share one Argon2 concurrency limiter."""

    argon2_limiter = CapacityLimiter(settings.argon2_max_concurrency)
    password_hasher = Argon2idPasswordHasher(
        settings,
        limiter=argon2_limiter,
    )
    dummy_password_hash = await password_hasher.get_dummy_password_hash()
    session_token_service = HmacSessionTokenService(
        refresh_hmac_key=settings.refresh_token_hmac_key,
        csrf_hmac_key=settings.csrf_token_hmac_key,
        device_hmac_key=settings.device_token_hmac_key,
    )
    access_token_codec = Ed25519AccessTokenCodec(
        current_kid=settings.access_token_current_kid,
        private_key=settings.access_token_private_key,
        public_keys=dict(settings.access_token_public_keys),
    )
    authentication_service = CredentialAuthenticationService(
        password_hasher=password_hasher,
        credential_reader=SqlAlchemyCredentialReader(session_factory),
        dummy_password_hash=dummy_password_hash,
    )
    login_rate_limiter = RedisLoginAttemptRateLimiter(
        redis_client,
        hmac_key=settings.login_rate_limit_hmac_key,
        ip_max_attempts=settings.login_rate_limit_ip_max_attempts,
        ip_window_seconds=settings.login_rate_limit_ip_window_seconds,
        account_max_attempts=settings.login_rate_limit_account_max_attempts,
        account_window_seconds=settings.login_rate_limit_account_window_seconds,
    )

    return IdentityResources(
        registration_service=RegistrationService(
            password_hasher=password_hasher,
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        ),
        login_service=LoginSessionService(
            authentication_service=authentication_service,
            password_rehasher=password_hasher,
            session_token_service=session_token_service,
            access_token_codec=access_token_codec,
            transaction_factory=SqlAlchemyLoginSessionTransactionFactory(session_factory),
        ),
        session_token_service=session_token_service,
        access_token_codec=access_token_codec,
        login_rate_limiter=login_rate_limiter,
    )


def get_identity_resources(request: Request) -> IdentityResources:
    """Return identity resources initialized by the application lifespan."""

    resources = getattr(request.app.state, "identity_resources", None)

    if not isinstance(resources, IdentityResources):
        raise RuntimeError("Application lifespan has not initialized identity resources")

    return resources
