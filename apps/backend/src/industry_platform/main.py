"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
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
    TRACE_ID_HEADER,
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
from industry_platform.modules.conversations.resources import (
    create_conversation_resources,
)
from industry_platform.modules.conversations.router import router as conversation_router
from industry_platform.modules.conversations.schemas import InvalidConversationCursorError
from industry_platform.modules.conversations.service import (
    ConversationNotFoundError,
    ConversationPersistenceError,
)
from industry_platform.modules.conversations.submission import (
    ConversationIdempotencyConflictError,
    ConversationModeNotReadyError,
)
from industry_platform.modules.identity.domain import (
    AccessTokenGenerationError,
    AuthenticatedSessionPersistenceError,
    AuthenticationPersistenceError,
    EmailAlreadyRegisteredError,
    InvalidAuthenticatedSessionError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    InvalidEmailAddressError,
    InvalidLogoutSessionError,
    InvalidPasswordChangeError,
    InvalidRefreshSessionError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
    LoginSessionPersistenceError,
    LogoutSessionUnavailableError,
    NewPasswordMatchesCurrentError,
    PasswordChangeConflictError,
    PasswordChangePersistenceError,
    PasswordChangeRateLimitExceededError,
    PasswordChangeRateLimitUnavailableError,
    RefreshSessionUnavailableError,
    RegistrationPersistenceError,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.http_cookies import (
    CSRF_PROOF_HEADER_NAME,
    clear_session_cookies,
)
from industry_platform.modules.identity.passwords import PasswordPolicyError
from industry_platform.modules.identity.resources import create_identity_resources
from industry_platform.modules.identity.router import router as identity_router
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.workspaces.domain import (
    LastWorkspaceOwnerError,
    WorkspaceAccessDeniedError,
    WorkspaceMembershipConflictError,
    WorkspaceMembershipNotFoundError,
    WorkspacePersistenceError,
)
from industry_platform.modules.workspaces.resources import create_workspace_resources
from industry_platform.modules.workspaces.router import router as workspace_router

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

    active_settings = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
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
            workspace_resources = create_workspace_resources(database_session_factory)
            conversation_resources = create_conversation_resources(database_session_factory)
            job_resources = create_job_resources(
                active_settings,
                database_session_factory,
            )

            application.state.resources = ApplicationResources(
                settings=active_settings,
                database_engine=database_engine,
                redis_client=redis_client,
            )
            application.state.identity_resources = identity_resources
            application.state.workspace_resources = workspace_resources
            application.state.conversation_resources = conversation_resources
            application.state.job_resources = job_resources

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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.browser_trusted_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            CSRF_PROOF_HEADER_NAME,
        ],
        expose_headers=[TRACE_ID_HEADER],
        max_age=600,
    )
    application.include_router(identity_router, prefix="/api/v1")
    application.include_router(workspace_router, prefix="/api/v1")
    application.include_router(conversation_router, prefix="/api/v1")

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

    @application.exception_handler(InvalidRefreshSessionError)
    async def handle_invalid_refresh_session(
        request: Request,
        _error: InvalidRefreshSessionError,
    ) -> JSONResponse:
        response = problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Invalid refresh session",
            code="INVALID_REFRESH_SESSION",
            detail="A valid browser session could not be refreshed.",
            problem_type="urn:iip:problem:invalid-refresh-session",
        )
        clear_session_cookies(response)
        return response

    @application.exception_handler(InvalidAuthenticatedSessionError)
    async def handle_invalid_authenticated_session(
        request: Request,
        _error: InvalidAuthenticatedSessionError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Invalid authenticated session",
            code="INVALID_AUTHENTICATED_SESSION",
            detail="A valid authenticated session is required.",
            problem_type="urn:iip:problem:invalid-authenticated-session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(AuthenticatedSessionPersistenceError)
    async def handle_authenticated_session_persistence_failure(
        request: Request,
        error: AuthenticatedSessionPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Authenticated session unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Identity unavailable",
            code="IDENTITY_UNAVAILABLE",
            detail="Identity verification is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:identity-unavailable",
        )

    @application.exception_handler(RefreshSessionUnavailableError)
    async def handle_refresh_unavailable(
        request: Request,
        error: RefreshSessionUnavailableError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Refresh unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "not-applicable",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Refresh unavailable",
            code="REFRESH_UNAVAILABLE",
            detail="The browser session could not be refreshed. Please try again.",
            problem_type="urn:iip:problem:refresh-unavailable",
        )

    @application.exception_handler(InvalidLogoutSessionError)
    async def handle_invalid_logout_session(
        request: Request,
        _error: InvalidLogoutSessionError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Invalid logout session",
            code="INVALID_LOGOUT_SESSION",
            detail="A valid browser session is required to log out.",
            problem_type="urn:iip:problem:invalid-logout-session",
        )

    @application.exception_handler(LogoutSessionUnavailableError)
    async def handle_logout_unavailable(
        request: Request,
        error: LogoutSessionUnavailableError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Logout unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Logout unavailable",
            code="LOGOUT_UNAVAILABLE",
            detail="Logout is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:logout-unavailable",
        )

    @application.exception_handler(InvalidCurrentPasswordError)
    async def handle_invalid_current_password(
        request: Request,
        _error: InvalidCurrentPasswordError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid current password",
            code="INVALID_CURRENT_PASSWORD",
            detail="The current password is incorrect.",
            problem_type="urn:iip:problem:invalid-current-password",
        )

    @application.exception_handler(InvalidPasswordChangeError)
    async def handle_invalid_password_change(
        request: Request,
        _error: InvalidPasswordChangeError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_403_FORBIDDEN,
            title="Password change rejected",
            code="PASSWORD_CHANGE_REJECTED",
            detail="The password change request could not be trusted.",
            problem_type="urn:iip:problem:password-change-rejected",
        )

    @application.exception_handler(NewPasswordMatchesCurrentError)
    async def handle_reused_password(
        request: Request,
        _error: NewPasswordMatchesCurrentError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Request validation failed",
            code="REQUEST_VALIDATION_FAILED",
            detail="The new password must differ from the current password.",
            problem_type="urn:iip:problem:request-validation-failed",
        )

    @application.exception_handler(PasswordChangeConflictError)
    async def handle_password_change_conflict(
        request: Request,
        _error: PasswordChangeConflictError,
    ) -> JSONResponse:
        response = problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Password change conflict",
            code="PASSWORD_CHANGE_CONFLICT",
            detail="The password changed concurrently. Please sign in again.",
            problem_type="urn:iip:problem:password-change-conflict",
        )
        clear_session_cookies(response)
        return response

    @application.exception_handler(PasswordChangeRateLimitExceededError)
    async def handle_password_change_rate_limit(
        request: Request,
        error: PasswordChangeRateLimitExceededError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            title="Too many password change attempts",
            code="PASSWORD_CHANGE_RATE_LIMITED",
            detail="Too many attempts. Please try again later.",
            problem_type="urn:iip:problem:password-change-rate-limited",
            headers={"Retry-After": str(error.retry_after_seconds)},
        )

    @application.exception_handler(PasswordChangePersistenceError)
    @application.exception_handler(PasswordChangeRateLimitUnavailableError)
    async def handle_password_change_unavailable(
        request: Request,
        error: PasswordChangePersistenceError | PasswordChangeRateLimitUnavailableError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        sqlstate = error.sqlstate if isinstance(error, PasswordChangePersistenceError) else None
        logger.error(
            "Password change unavailable trace_id=%s failure_type=%s sqlstate=%s",
            trace_id,
            type(error).__name__,
            sqlstate or "not-applicable",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Password change unavailable",
            code="PASSWORD_CHANGE_UNAVAILABLE",
            detail="The password could not be changed. Please try again.",
            problem_type="urn:iip:problem:password-change-unavailable",
        )

    @application.exception_handler(InvalidConversationCursorError)
    async def handle_invalid_conversation_cursor(
        request: Request,
        _error: InvalidConversationCursorError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid conversation cursor",
            code="INVALID_CONVERSATION_CURSOR",
            detail="The conversation page cursor is invalid or no longer supported.",
            problem_type="urn:iip:problem:invalid-conversation-cursor",
        )

    @application.exception_handler(ConversationNotFoundError)
    async def handle_conversation_not_found(
        request: Request,
        _error: ConversationNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Conversation not found",
            code="CONVERSATION_NOT_FOUND",
            detail="The requested conversation does not exist in this workspace.",
            problem_type="urn:iip:problem:conversation-not-found",
        )

    @application.exception_handler(ConversationModeNotReadyError)
    async def handle_conversation_mode_not_ready(
        request: Request,
        _error: ConversationModeNotReadyError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Conversation mode not ready",
            code="CONVERSATION_MODE_NOT_READY",
            detail="Only direct answers without search are available right now.",
            problem_type="urn:iip:problem:conversation-mode-not-ready",
        )

    @application.exception_handler(ConversationIdempotencyConflictError)
    async def handle_conversation_idempotency_conflict(
        request: Request,
        _error: ConversationIdempotencyConflictError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Conversation request conflict",
            code="CONVERSATION_IDEMPOTENCY_CONFLICT",
            detail="This idempotency key was already used for a different request.",
            problem_type="urn:iip:problem:conversation-idempotency-conflict",
        )

    @application.exception_handler(ConversationPersistenceError)
    async def handle_conversation_unavailable(
        request: Request,
        error: ConversationPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Conversation persistence unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Conversation service unavailable",
            code="CONVERSATION_UNAVAILABLE",
            detail="Conversation data is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:conversation-unavailable",
        )

    @application.exception_handler(WorkspaceAccessDeniedError)
    async def handle_workspace_access_denied(
        request: Request,
        _error: WorkspaceAccessDeniedError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_403_FORBIDDEN,
            title="Workspace access denied",
            code="WORKSPACE_ACCESS_DENIED",
            detail="You do not have permission to perform this workspace operation.",
            problem_type="urn:iip:problem:workspace-access-denied",
        )

    @application.exception_handler(WorkspaceMembershipNotFoundError)
    async def handle_workspace_member_not_found(
        request: Request,
        _error: WorkspaceMembershipNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Workspace member not found",
            code="WORKSPACE_MEMBER_NOT_FOUND",
            detail="The requested workspace member does not exist.",
            problem_type="urn:iip:problem:workspace-member-not-found",
        )

    @application.exception_handler(WorkspaceMembershipConflictError)
    @application.exception_handler(LastWorkspaceOwnerError)
    async def handle_workspace_membership_conflict(
        request: Request,
        error: WorkspaceMembershipConflictError | LastWorkspaceOwnerError,
    ) -> JSONResponse:
        last_owner = isinstance(error, LastWorkspaceOwnerError)
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title=(
                "Last workspace owner required" if last_owner else "Workspace membership conflict"
            ),
            code=("LAST_WORKSPACE_OWNER" if last_owner else "WORKSPACE_MEMBERSHIP_CONFLICT"),
            detail=(
                "A workspace must retain at least one active owner."
                if last_owner
                else "The requested workspace membership already exists."
            ),
            problem_type=(
                "urn:iip:problem:last-workspace-owner"
                if last_owner
                else "urn:iip:problem:workspace-membership-conflict"
            ),
        )

    @application.exception_handler(WorkspacePersistenceError)
    async def handle_workspace_unavailable(
        request: Request,
        error: WorkspacePersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Workspace persistence unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Workspace service unavailable",
            code="WORKSPACE_UNAVAILABLE",
            detail="Workspace data is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:workspace-unavailable",
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
