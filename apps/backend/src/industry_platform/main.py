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

from industry_platform.adapters.public_egress import create_public_egress_http_client
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
from industry_platform.modules.agent_runtime.adapters.trace_query import (
    AgentTraceDataError,
    AgentTraceNotFoundError,
    AgentTraceQueryError,
)
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryStateError,
    AgentRunDeliveryUnavailableError,
    AgentRunNotFoundError,
)
from industry_platform.modules.agent_runtime.resources import (
    create_agent_run_delivery_resources,
    create_agent_trace_resources,
)
from industry_platform.modules.agent_runtime.router import router as agent_run_router
from industry_platform.modules.agent_runtime.streaming import (
    StreamContractError,
    StreamErrorCode,
)
from industry_platform.modules.conversations.resources import (
    create_conversation_resources,
)
from industry_platform.modules.conversations.router import router as conversation_router
from industry_platform.modules.conversations.schemas import InvalidConversationCursorError
from industry_platform.modules.conversations.service import (
    ConversationAttachmentNotReadyError,
    ConversationAttachmentNotSupportedError,
    ConversationNotFoundError,
    ConversationPersistenceError,
)
from industry_platform.modules.conversations.submission import (
    ConversationIdempotencyConflictError,
    ConversationModeNotReadyError,
)
from industry_platform.modules.data_explorer.domain import (
    DataConnectionNotFoundError,
    DataExplorerError,
    DataExplorerPersistenceError,
    QueryRunNotFoundError,
)
from industry_platform.modules.data_explorer.resources import (
    DataExplorerResources,
    create_data_explorer_resources,
)
from industry_platform.modules.data_explorer.router import router as data_explorer_router
from industry_platform.modules.evidence.domain import (
    ClaimNotFoundError,
    EvidenceConflictError,
    EvidenceNotFoundError,
    EvidencePersistenceError,
    EvidenceRequestRejectedError,
    ResearchRunNotFoundError,
)
from industry_platform.modules.evidence.resources import create_evidence_resources
from industry_platform.modules.evidence.router import router as evidence_router
from industry_platform.modules.files.resources import create_file_resources
from industry_platform.modules.files.router import router as file_router
from industry_platform.modules.files.service import (
    FileNotFoundError,
    FileServiceUnavailableError,
    FileStateConflictError,
    FileStorageConfigurationError,
    FileUploadExpiredError,
    FileValidationRejectedError,
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
from industry_platform.modules.industry.adapters.sqlalchemy import (
    industry_collection_occurrence_observer,
)
from industry_platform.modules.industry.domain import (
    IndustryCollectionNotFoundError,
    IndustryNotFoundError,
    IndustryPersistenceError,
)
from industry_platform.modules.industry.resources import create_industry_resources
from industry_platform.modules.industry.router import router as industry_router
from industry_platform.modules.jobs.domain import (
    ScheduleDefinitionConflictError,
    ScheduleNotFoundError,
    ScheduleTriggerConflictError,
)
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.memory.domain import (
    MemoryCandidateEditRequiredError,
    MemoryCandidateNotFoundError,
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryPersistenceError,
    MemoryRequestRejectedError,
    MemorySourceNotFoundError,
)
from industry_platform.modules.memory.resources import create_memory_resources
from industry_platform.modules.memory.router import router as memory_router
from industry_platform.modules.research.adapters.sqlalchemy import ResearchPersistenceError
from industry_platform.modules.research.resources import create_research_resources
from industry_platform.modules.research.router import router as research_router
from industry_platform.modules.research.service import ResearchNotFoundError
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
        data_explorer_resources: DataExplorerResources | None = None
        external_http_client = create_public_egress_http_client()

        try:
            database_session_factory = create_database_session_factory(database_engine)
            redis_client = create_redis_client(active_settings)
            identity_resources = await create_identity_resources(
                active_settings,
                database_session_factory,
                redis_client,
            )
            workspace_resources = create_workspace_resources(database_session_factory)
            conversation_resources = create_conversation_resources(
                database_session_factory,
                supports_image_input=(
                    active_settings.agent_model_route is not None
                    and active_settings.agent_model_route.supports_image_input
                ),
            )
            memory_resources = create_memory_resources(database_session_factory)
            evidence_resources = create_evidence_resources(database_session_factory)
            research_resources = create_research_resources(database_session_factory)
            file_resources = create_file_resources(
                active_settings,
                database_session_factory,
            )
            agent_run_delivery_resources = create_agent_run_delivery_resources(
                database_session_factory
            )
            agent_trace_resources = create_agent_trace_resources(database_session_factory)
            job_resources = create_job_resources(
                active_settings,
                database_session_factory,
                occurrence_observer=industry_collection_occurrence_observer,
            )
            industry_resources = create_industry_resources(
                active_settings,
                database_session_factory,
                external_http_client,
                job_resources.schedule_service,
            )
            data_explorer_resources = create_data_explorer_resources(
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
            application.state.memory_resources = memory_resources
            application.state.evidence_resources = evidence_resources
            application.state.research_resources = research_resources
            application.state.file_resources = file_resources
            application.state.agent_run_delivery_resources = agent_run_delivery_resources
            application.state.agent_trace_resources = agent_trace_resources
            application.state.job_resources = job_resources
            application.state.industry_resources = industry_resources
            application.state.data_explorer_resources = data_explorer_resources

            yield
        finally:
            try:
                if redis_client is not None:
                    await redis_client.aclose()
            finally:
                try:
                    if data_explorer_resources is not None:
                        await data_explorer_resources.close()
                finally:
                    try:
                        await external_http_client.aclose()
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
            "If-Match",
            "Last-Event-ID",
            CSRF_PROOF_HEADER_NAME,
        ],
        expose_headers=[TRACE_ID_HEADER],
        max_age=600,
    )
    application.include_router(identity_router, prefix="/api/v1")
    application.include_router(workspace_router, prefix="/api/v1")
    application.include_router(conversation_router, prefix="/api/v1")
    application.include_router(memory_router, prefix="/api/v1")
    application.include_router(agent_run_router, prefix="/api/v1")
    application.include_router(file_router, prefix="/api/v1")
    application.include_router(industry_router, prefix="/api/v1")
    application.include_router(data_explorer_router, prefix="/api/v1")
    application.include_router(evidence_router, prefix="/api/v1")
    application.include_router(research_router, prefix="/api/v1")

    @application.exception_handler(ResearchNotFoundError)
    async def handle_research_not_found(
        request: Request,
        _error: ResearchNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Research Run not found",
            code="RESEARCH_RUN_NOT_FOUND",
            detail="The requested Research Run does not exist or is unavailable.",
            problem_type="urn:iip:problem:research-run-not-found",
        )

    @application.exception_handler(ResearchPersistenceError)
    async def handle_research_unavailable(
        request: Request,
        _error: ResearchPersistenceError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Research service unavailable",
            code="RESEARCH_UNAVAILABLE",
            detail="Research facts could not be loaded safely.",
            problem_type="urn:iip:problem:research-unavailable",
        )

    @application.exception_handler(EvidenceNotFoundError)
    @application.exception_handler(ResearchRunNotFoundError)
    @application.exception_handler(ClaimNotFoundError)
    async def handle_evidence_not_found(
        request: Request,
        _error: EvidenceNotFoundError | ResearchRunNotFoundError | ClaimNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Evidence resource not found",
            code="EVIDENCE_RESOURCE_NOT_FOUND",
            detail="The requested Evidence resource does not exist or is unavailable.",
            problem_type="urn:iip:problem:evidence-resource-not-found",
        )

    @application.exception_handler(EvidenceConflictError)
    async def handle_evidence_conflict(
        request: Request,
        _error: EvidenceConflictError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Evidence revision conflict",
            code="EVIDENCE_REVISION_CONFLICT",
            detail="The Evidence ledger changed. Reload it before retrying.",
            problem_type="urn:iip:problem:evidence-revision-conflict",
        )

    @application.exception_handler(EvidenceRequestRejectedError)
    async def handle_evidence_request_rejected(
        request: Request,
        _error: EvidenceRequestRejectedError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Evidence request rejected",
            code="EVIDENCE_REQUEST_REJECTED",
            detail="The Evidence request violates a ledger contract.",
            problem_type="urn:iip:problem:evidence-request-rejected",
        )

    @application.exception_handler(EvidencePersistenceError)
    async def handle_evidence_persistence_error(
        request: Request,
        error: EvidencePersistenceError,
    ) -> JSONResponse:
        logger.exception(
            "Evidence persistence unavailable trace_id=%s sqlstate=%s",
            get_trace_id(request),
            error.sqlstate,
        )
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Evidence service unavailable",
            code="EVIDENCE_SERVICE_UNAVAILABLE",
            detail="Evidence is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:evidence-service-unavailable",
        )

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

    @application.exception_handler(IndustryNotFoundError)
    @application.exception_handler(IndustryCollectionNotFoundError)
    @application.exception_handler(ScheduleNotFoundError)
    async def handle_industry_not_found(
        request: Request,
        _error: IndustryNotFoundError | IndustryCollectionNotFoundError | ScheduleNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Industry resource not found",
            code="INDUSTRY_RESOURCE_NOT_FOUND",
            detail="The requested industry resource does not exist in this workspace.",
            problem_type="urn:iip:problem:industry-resource-not-found",
        )

    @application.exception_handler(DataConnectionNotFoundError)
    @application.exception_handler(QueryRunNotFoundError)
    async def handle_data_explorer_not_found(
        request: Request,
        _error: DataConnectionNotFoundError | QueryRunNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Data Explorer resource not found",
            code="DATA_EXPLORER_RESOURCE_NOT_FOUND",
            detail="The requested Data Explorer resource does not exist in this workspace.",
            problem_type="urn:iip:problem:data-explorer-resource-not-found",
        )

    @application.exception_handler(DataExplorerPersistenceError)
    async def handle_data_explorer_unavailable(
        request: Request,
        error: DataExplorerPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Data Explorer persistence unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Data Explorer unavailable",
            code="DATA_EXPLORER_UNAVAILABLE",
            detail="Database exploration is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:data-explorer-unavailable",
        )

    @application.exception_handler(DataExplorerError)
    async def handle_data_explorer_rejected(
        request: Request,
        error: DataExplorerError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Data Explorer request rejected",
            code="DATA_EXPLORER_REQUEST_REJECTED",
            detail=f"The database request was rejected ({error.code}).",
            problem_type="urn:iip:problem:data-explorer-request-rejected",
        )

    @application.exception_handler(ScheduleDefinitionConflictError)
    @application.exception_handler(ScheduleTriggerConflictError)
    async def handle_industry_schedule_conflict(
        request: Request,
        _error: ScheduleDefinitionConflictError | ScheduleTriggerConflictError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Industry schedule conflict",
            code="INDUSTRY_SCHEDULE_CONFLICT",
            detail="The schedule request conflicts with an existing durable definition.",
            problem_type="urn:iip:problem:industry-schedule-conflict",
        )

    @application.exception_handler(IndustryPersistenceError)
    async def handle_industry_unavailable(
        request: Request,
        error: IndustryPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Industry persistence unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Industry service unavailable",
            code="INDUSTRY_SERVICE_UNAVAILABLE",
            detail="Industry data is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:industry-service-unavailable",
        )

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

    @application.exception_handler(MemoryCandidateNotFoundError)
    @application.exception_handler(MemoryNotFoundError)
    async def handle_memory_not_found(
        request: Request,
        _error: MemoryCandidateNotFoundError | MemoryNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Memory resource not found",
            code="MEMORY_NOT_FOUND",
            detail="The requested Memory resource does not exist in this workspace.",
            problem_type="urn:iip:problem:memory-not-found",
        )

    @application.exception_handler(MemorySourceNotFoundError)
    async def handle_memory_source_not_found(
        request: Request,
        _error: MemorySourceNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Memory source not found",
            code="MEMORY_SOURCE_NOT_FOUND",
            detail="A selected message is unavailable in this conversation.",
            problem_type="urn:iip:problem:memory-source-not-found",
        )

    @application.exception_handler(MemoryConflictError)
    async def handle_memory_conflict(
        request: Request,
        _error: MemoryConflictError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Memory revision conflict",
            code="MEMORY_CONFLICT",
            detail="The Memory candidate or target changed. Reload it before deciding again.",
            problem_type="urn:iip:problem:memory-conflict",
        )

    @application.exception_handler(MemoryCandidateEditRequiredError)
    async def handle_memory_edit_required(
        request: Request,
        _error: MemoryCandidateEditRequiredError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Memory candidate requires editing",
            code="MEMORY_CANDIDATE_EDIT_REQUIRED",
            detail="Review and edit this assistant-only candidate before confirming it.",
            problem_type="urn:iip:problem:memory-candidate-edit-required",
        )

    @application.exception_handler(MemoryRequestRejectedError)
    async def handle_memory_request_rejected(
        request: Request,
        _error: MemoryRequestRejectedError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Memory request rejected",
            code="MEMORY_REQUEST_REJECTED",
            detail="The Memory request violates content, expiry, or revision requirements.",
            problem_type="urn:iip:problem:memory-request-rejected",
        )

    @application.exception_handler(MemoryPersistenceError)
    async def handle_memory_unavailable(
        request: Request,
        error: MemoryPersistenceError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Memory persistence unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Memory service unavailable",
            code="MEMORY_UNAVAILABLE",
            detail="Memory is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:memory-unavailable",
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

    @application.exception_handler(ConversationAttachmentNotReadyError)
    async def handle_conversation_attachment_not_ready(
        request: Request,
        _error: ConversationAttachmentNotReadyError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Conversation attachment not ready",
            code="CONVERSATION_ATTACHMENT_NOT_READY",
            detail="Every selected attachment must be ready and unused in this workspace.",
            problem_type="urn:iip:problem:conversation-attachment-not-ready",
        )

    @application.exception_handler(ConversationAttachmentNotSupportedError)
    async def handle_conversation_attachment_not_supported(
        request: Request,
        _error: ConversationAttachmentNotSupportedError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="Conversation attachment not supported",
            code="CONVERSATION_ATTACHMENT_NOT_SUPPORTED",
            detail="The active model route does not support the selected attachment type.",
            problem_type="urn:iip:problem:conversation-attachment-not-supported",
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

    @application.exception_handler(FileNotFoundError)
    async def handle_file_not_found(
        request: Request,
        _error: FileNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="File not found",
            code="FILE_NOT_FOUND",
            detail="The requested file does not exist in this workspace.",
            problem_type="urn:iip:problem:file-not-found",
        )

    @application.exception_handler(FileUploadExpiredError)
    async def handle_file_upload_expired(
        request: Request,
        _error: FileUploadExpiredError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="File upload expired",
            code="FILE_UPLOAD_EXPIRED",
            detail="Create a new upload before sending this attachment.",
            problem_type="urn:iip:problem:file-upload-expired",
        )

    @application.exception_handler(FileStateConflictError)
    async def handle_file_state_conflict(
        request: Request,
        _error: FileStateConflictError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_409_CONFLICT,
            title="File state conflict",
            code="FILE_STATE_CONFLICT",
            detail="The file cannot perform that operation in its current state.",
            problem_type="urn:iip:problem:file-state-conflict",
        )

    @application.exception_handler(FileValidationRejectedError)
    async def handle_file_validation_rejected(
        request: Request,
        error: FileValidationRejectedError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="File rejected",
            code=f"FILE_{error.code.value.upper()}",
            detail="The uploaded attachment did not pass the required safety checks.",
            problem_type="urn:iip:problem:file-validation-rejected",
        )

    @application.exception_handler(FileStorageConfigurationError)
    async def handle_file_storage_configuration_required(
        request: Request,
        _error: FileStorageConfigurationError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="File storage not configured",
            code="FILE_STORAGE_CONFIGURATION_REQUIRED",
            detail="Private file storage is not configured for this deployment.",
            problem_type="urn:iip:problem:file-storage-configuration-required",
        )

    @application.exception_handler(FileServiceUnavailableError)
    async def handle_file_service_unavailable(
        request: Request,
        error: FileServiceUnavailableError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "File service unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "not-applicable",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="File service unavailable",
            code="FILE_SERVICE_UNAVAILABLE",
            detail="File storage is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:file-service-unavailable",
        )

    @application.exception_handler(AgentTraceNotFoundError)
    @application.exception_handler(AgentRunNotFoundError)
    async def handle_agent_run_not_found(
        request: Request,
        _error: AgentRunNotFoundError | AgentTraceNotFoundError,
    ) -> JSONResponse:
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=status.HTTP_404_NOT_FOUND,
            title="Agent Run not found",
            code="AGENT_RUN_NOT_FOUND",
            detail="The requested Agent Run does not exist in this workspace.",
            problem_type="urn:iip:problem:agent-run-not-found",
        )

    @application.exception_handler(AgentTraceDataError)
    async def handle_agent_trace_data_error(
        request: Request,
        _error: AgentTraceDataError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error("Persisted Agent Trace is inconsistent trace_id=%s", trace_id)
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Agent Trace data invalid",
            code="AGENT_TRACE_DATA_INVALID",
            detail="The Agent Trace could not be reconstructed from persisted data.",
            problem_type="urn:iip:problem:agent-trace-data-invalid",
        )

    @application.exception_handler(AgentTraceQueryError)
    async def handle_agent_trace_unavailable(
        request: Request,
        error: AgentTraceQueryError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(
            "Agent Trace unavailable trace_id=%s sqlstate=%s",
            trace_id,
            error.sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Agent Trace unavailable",
            code="AGENT_TRACE_UNAVAILABLE",
            detail="Agent Trace data is temporarily unavailable. Please try again.",
            problem_type="urn:iip:problem:agent-trace-unavailable",
        )

    @application.exception_handler(StreamContractError)
    async def handle_agent_stream_contract_error(
        request: Request,
        error: StreamContractError,
    ) -> JSONResponse:
        invalid_cursor = error.code is StreamErrorCode.INVALID_CURSOR
        return problem_response(
            trace_id=get_trace_id(request),
            status_code=(
                status.HTTP_400_BAD_REQUEST if invalid_cursor else status.HTTP_409_CONFLICT
            ),
            title="Invalid Agent stream cursor"
            if invalid_cursor
            else "Agent stream reset required",
            code=error.code.value,
            detail=(
                "Last-Event-ID must be a non-negative decimal sequence."
                if invalid_cursor
                else "Reconnect using a cursor from this Agent Run's committed stream."
            ),
            problem_type=f"urn:iip:problem:{error.code.value.lower().replace('_', '-')}",
        )

    @application.exception_handler(AgentRunDeliveryStateError)
    @application.exception_handler(AgentRunDeliveryUnavailableError)
    async def handle_agent_delivery_unavailable(
        request: Request,
        error: AgentRunDeliveryUnavailableError | AgentRunDeliveryStateError,
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        sqlstate = error.sqlstate if isinstance(error, AgentRunDeliveryUnavailableError) else None
        logger.error(
            "Agent event delivery unavailable trace_id=%s sqlstate=%s",
            trace_id,
            sqlstate or "unknown",
        )
        return problem_response(
            trace_id=trace_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            title="Agent event delivery unavailable",
            code="AGENT_EVENT_DELIVERY_UNAVAILABLE",
            detail="Agent events are temporarily unavailable. Please reconnect shortly.",
            problem_type="urn:iip:problem:agent-event-delivery-unavailable",
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
