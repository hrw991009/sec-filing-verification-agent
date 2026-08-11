"""HTTP delivery adapter for identity use cases."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    LoginRateLimitUnavailableError,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.http_cookies import set_login_cookies
from industry_platform.modules.identity.ports import (
    LoginAttemptRateLimiter,
    LoginSessionUseCase,
    RegistrationUseCase,
)
from industry_platform.modules.identity.resources import (
    IdentityResources,
    get_identity_resources,
)
from industry_platform.modules.identity.schemas import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegistrationResponse,
)

router = APIRouter(prefix="/auth", tags=["identity"])


def get_registration_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> RegistrationUseCase:
    """Select the registration use case from process-wide identity resources."""

    return resources.registration_service


def get_login_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> LoginSessionUseCase:
    """Select the complete login use case from process-wide resources."""

    return resources.login_service


def get_login_rate_limiter(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> LoginAttemptRateLimiter:
    """Select the shared Redis login gate from process-wide resources."""

    return resources.login_rate_limiter


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: problem_openapi_response("Email already registered"),
        status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response(
            "Request validation failed"
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Registration temporarily unavailable"
        ),
    },
)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    registration_service: Annotated[
        RegistrationUseCase,
        Depends(get_registration_service),
    ],
) -> RegistrationResponse:
    """Register an account and its initial owner workspace atomically."""

    record = await registration_service.register(
        RegisterUserCommand(
            email=str(payload.email),
            password=payload.password,
            trace_id=TraceId(get_trace_id(request)),
        )
    )

    return RegistrationResponse.model_validate(
        {
            "user": {
                "id": record.user_id,
                "email": record.email,
            },
            "workspace": {
                "id": record.workspace_id,
                "name": record.workspace_name,
                "role": record.workspace_role,
            },
        }
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid credentials"),
        status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response(
            "Request validation failed"
        ),
        status.HTTP_429_TOO_MANY_REQUESTS: problem_openapi_response("Login rate limit exceeded"),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Login temporarily unavailable"
        ),
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    login_service: Annotated[LoginSessionUseCase, Depends(get_login_service)],
    rate_limiter: Annotated[
        LoginAttemptRateLimiter,
        Depends(get_login_rate_limiter),
    ],
) -> LoginResponse:
    """Rate-limit credentials, commit one session, then deliver browser tokens."""

    client = request.client
    if client is None:
        raise LoginRateLimitUnavailableError

    await rate_limiter.acquire(
        source_ip=client.host,
        raw_email=payload.email,
    )
    result = await login_service.login(
        AuthenticateCredentialsCommand(
            email=payload.email,
            password=payload.password,
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    set_no_store_headers(response)
    set_login_cookies(response, result)

    return LoginResponse.model_validate(
        {
            "user": {
                "id": result.session.user_id,
                "email": result.email,
            },
            "access_token": result.access_token.reveal_for_transport(),
            "expires_at": result.access_token_expires_at,
        }
    )
