"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    check_database_connection,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.core.health import (
    LivenessResponse,
    ReadinessChecks,
    ReadinessResponse,
    ReadinessStatus,
    assess_readiness,
)
from industry_platform.core.http import (
    SafeUnhandledExceptionMiddleware,
    TraceIdMiddleware,
    get_trace_id,
    http_exception_handler,
    problem_response,
    request_validation_exception_handler,
)
from industry_platform.core.redis_client import (
    check_redis_connection,
    create_redis_client,
)
from industry_platform.modules.identity.domain import (
    AccessTokenGenerationError,
    AuthenticationPersistenceError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailAddressError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
    LoginSessionPersistenceError,
    RegistrationPersistenceError,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.passwords import PasswordPolicyError
from industry_platform.modules.identity.resources import create_identity_resources
from industry_platform.modules.identity.router import router as identity_router

type DatabaseHealthCheck = Callable[[AsyncEngine], Awaitable[None]]
type RedisHealthCheck = Callable[[Redis], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationResources:
    """Process-wide resources created and closed by FastAPI lifespan."""

    settings: Settings
    database_engine: AsyncEngine
    redis_client: Redis


def _get_resources(request: Request) -> ApplicationResources:
    resources = getattr(request.app.state, "resources", None)

    if not isinstance(resources, ApplicationResources):
        raise RuntimeError("Application lifespan has not initialized resources")

    return resources


def create_app(
    *,
    settings: Settings | None = None,
    database_health_check: DatabaseHealthCheck = check_database_connection,
    redis_health_check: RedisHealthCheck = check_redis_connection,
) -> FastAPI:
    """Create a configured FastAPI application."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_settings = settings if settings is not None else get_settings()
        database_engine = create_database_engine(active_settings)
        redis_client: Redis | None = None

        try:
            database_session_factory = create_database_session_factory(database_engine)
            redis_client = create_redis_client(active_settings)
            identity_resources = await create_identity_resources(
                active_settings,
                database_session_factory,
                redis_client,
            )

            application.state.resources = ApplicationResources(
                settings=active_settings,
                database_engine=database_engine,
                redis_client=redis_client,
            )
            application.state.identity_resources = identity_resources

            yield
        finally:
            try:
                if redis_client is not None:
                    await redis_client.aclose()
            finally:
                await database_engine.dispose()

    application = FastAPI(
        title="Industry Intelligence Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(TraceIdMiddleware)
    application.add_middleware(SafeUnhandledExceptionMiddleware)
    application.include_router(identity_router, prefix="/api/v1")

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        return await http_exception_handler(request, error)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return await request_validation_exception_handler(request, error)

    @application.exception_handler(EmailAlreadyRegisteredError)
    async def handle_duplicate_email(
        request: Request,
        _error: EmailAlreadyRegisteredError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Email already registered",
            code="EMAIL_ALREADY_REGISTERED",
            detail="An account with this email already exists.",
            problem_type="urn:iip:problem:email-already-registered",
        )

    @application.exception_handler(InvalidEmailAddressError)
    async def handle_invalid_email(
        request: Request,
        _error: InvalidEmailAddressError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Request validation failed",
            code="REQUEST_VALIDATION_FAILED",
            detail="One or more request fields are invalid.",
            problem_type="urn:iip:problem:request-validation-failed",
        )

    @application.exception_handler(PasswordPolicyError)
    async def handle_invalid_password(
        request: Request,
        _error: PasswordPolicyError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Request validation failed",
            code="REQUEST_VALIDATION_FAILED",
            detail="One or more request fields are invalid.",
            problem_type="urn:iip:problem:request-validation-failed",
        )

    @application.exception_handler(RegistrationPersistenceError)
    async def handle_registration_persistence_failure(
        request: Request,
        error: RegistrationPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Registration persistence failure trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )

        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Registration unavailable",
            code="REGISTRATION_UNAVAILABLE",
            detail="Registration is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:registration-unavailable",
        )

    @application.exception_handler(InvalidCredentialsError)
    async def handle_invalid_credentials(
        request: Request,
        _error: InvalidCredentialsError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Invalid credentials",
            code="INVALID_CREDENTIALS",
            detail="The email or password is incorrect.",
            problem_type="urn:iip:problem:invalid-credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(LoginRateLimitExceededError)
    async def handle_login_rate_limit(
        request: Request,
        error: LoginRateLimitExceededError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            title="Too many login attempts",
            code="LOGIN_RATE_LIMITED",
            detail="Too many login attempts. Please try again later.",
            problem_type="urn:iip:problem:login-rate-limited",
            headers={"Retry-After": str(error.retry_after_seconds)},
        )

    @application.exception_handler(LoginRateLimitUnavailableError)
    @application.exception_handler(AuthenticationPersistenceError)
    @application.exception_handler(LoginSessionPersistenceError)
    @application.exception_handler(SessionTokenGenerationError)
    @application.exception_handler(AccessTokenGenerationError)
    async def handle_login_unavailable(
        request: Request,
        error: LoginRateLimitUnavailableError
        | AuthenticationPersistenceError
        | LoginSessionPersistenceError
        | SessionTokenGenerationError
        | AccessTokenGenerationError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        sqlstate = (
            error.sqlstate
            if isinstance(error, AuthenticationPersistenceError | LoginSessionPersistenceError)
            else None
        )
        logger.error(
            "Login unavailable trace_id=%s failure_type=%s sqlstate=%s",
            trace_id,
            type(error).__name__,
            sqlstate or "not-applicable",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Login unavailable",
            code="LOGIN_UNAVAILABLE",
            detail="Login is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:login-unavailable",
        )

    @application.get(
        "/health/live",
        response_model=LivenessResponse,
        tags=["health"],
    )
    async def live() -> LivenessResponse:
        return LivenessResponse()

    @application.get(
        "/health/ready",
        response_model=ReadinessResponse,
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "model": ReadinessResponse,
            },
        },
        tags=["health"],
    )
    async def ready(
        request: Request,
        response: Response,
    ) -> ReadinessResponse:
        resources = _get_resources(request)

        async def postgres_probe() -> None:
            await database_health_check(resources.database_engine)

        async def redis_probe() -> None:
            await redis_health_check(resources.redis_client)

        report = await assess_readiness(
            postgres_check=postgres_probe,
            redis_check=redis_probe,
            timeout_seconds=resources.settings.health_check_timeout_seconds,
        )

        readiness_status = ReadinessStatus.READY if report.is_ready else ReadinessStatus.NOT_READY

        if not report.is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return ReadinessResponse(
            status=readiness_status,
            checks=ReadinessChecks(
                postgres=report.postgres,
                redis=report.redis,
            ),
        )

    return application


app = create_app()
