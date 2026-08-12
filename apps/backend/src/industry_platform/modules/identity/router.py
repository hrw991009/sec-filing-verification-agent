"""HTTP delivery adapter for identity use cases."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.identity.domain import (
    AccessTokenGenerationError,
    AuthenticateCredentialsCommand,
    AuthenticatedPrincipal,
    ChangePasswordCommand,
    DeviceToken,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
    LogoutSessionCommand,
    PasswordChangeRateLimitExceededError,
    PasswordChangeRateLimitUnavailableError,
    RefreshRecoveryError,
    RefreshSessionCommand,
    RefreshSessionPersistenceError,
    RefreshSessionUnavailableError,
    RefreshToken,
    RegisterUserCommand,
    SessionTokenGenerationError,
    TraceId,
)
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    CSRF_PROOF_HEADER_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_session_cookies,
    set_login_cookies,
    set_refresh_cookies,
)
from industry_platform.modules.identity.ports import (
    LoginAttemptRateLimiter,
    LoginSessionUseCase,
    LogoutSessionUseCase,
    PasswordChangeUseCase,
    RefreshSessionUseCase,
    RegistrationUseCase,
)
from industry_platform.modules.identity.resources import (
    IdentityResources,
    get_identity_resources,
)
from industry_platform.modules.identity.schemas import (
    ChangePasswordRequest,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
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


def get_refresh_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> RefreshSessionUseCase:
    """Select the refresh use case from process-wide identity resources."""

    return resources.refresh_service


def get_logout_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> LogoutSessionUseCase:
    """Select the browser logout use case from process-wide resources."""

    return resources.logout_service


def get_password_change_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> PasswordChangeUseCase:
    """Select the atomic password replacement use case."""

    return resources.password_change_service


def get_password_change_rate_limiter(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> LoginAttemptRateLimiter:
    """Select the independent user-and-source password-change limiter."""

    return resources.password_change_rate_limiter


def _single_header_value(request: Request, name: str) -> str:
    """Return one exact header value or an invalid sentinel for missing/duplicates."""

    values = request.headers.getlist(name)

    return values[0] if len(values) == 1 else ""


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


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid refresh session"),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Refresh temporarily unavailable"
        ),
    },
)
async def refresh_session(
    request: Request,
    response: Response,
    refresh_service: Annotated[RefreshSessionUseCase, Depends(get_refresh_service)],
) -> RefreshResponse:
    """Rotate browser session credentials and return one new Access Token."""

    try:
        result = await refresh_service.refresh(
            RefreshSessionCommand(
                origin=_single_header_value(request, "Origin"),
                refresh_token=RefreshToken.from_transport(
                    request.cookies.get(REFRESH_COOKIE_NAME, "")
                ),
                csrf_cookie_value=request.cookies.get(CSRF_COOKIE_NAME, ""),
                csrf_header_value=_single_header_value(
                    request,
                    CSRF_PROOF_HEADER_NAME,
                ),
                device_token=DeviceToken.from_transport(
                    request.cookies.get(DEVICE_COOKIE_NAME, "")
                ),
                trace_id=TraceId(get_trace_id(request)),
            )
        )
    except RefreshSessionPersistenceError as error:
        raise RefreshSessionUnavailableError(sqlstate=error.sqlstate) from error
    except (
        AccessTokenGenerationError,
        RefreshRecoveryError,
        SessionTokenGenerationError,
    ) as error:
        raise RefreshSessionUnavailableError from error
    set_no_store_headers(response)
    set_refresh_cookies(response, result)

    return RefreshResponse.model_validate(
        {
            "access_token": result.access_token.reveal_for_transport(),
            "expires_at": result.access_token_expires_at,
        }
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Identity temporarily unavailable"
        ),
    },
)
async def current_user(
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
) -> CurrentUserResponse:
    """Return only identity and memberships revalidated for this request."""

    set_no_store_headers(response)
    return CurrentUserResponse.model_validate(
        {
            "user": {
                "id": principal.user_id,
                "email": principal.email,
            },
            "workspaces": [
                {
                    "id": workspace.workspace_id,
                    "name": workspace.name,
                    "role": workspace.role,
                }
                for workspace in principal.workspaces
            ],
        }
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid logout session"),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Logout temporarily unavailable"
        ),
    },
)
async def logout(
    request: Request,
    response: Response,
    logout_service: Annotated[LogoutSessionUseCase, Depends(get_logout_service)],
) -> Response:
    """Revoke one browser family and clear its cookies after commit."""

    await logout_service.logout(
        LogoutSessionCommand(
            origin=_single_header_value(request, "Origin"),
            refresh_token=RefreshToken.from_transport(request.cookies.get(REFRESH_COOKIE_NAME, "")),
            csrf_cookie_value=request.cookies.get(CSRF_COOKIE_NAME, ""),
            csrf_header_value=_single_header_value(
                request,
                CSRF_PROOF_HEADER_NAME,
            ),
            device_token=DeviceToken.from_transport(request.cookies.get(DEVICE_COOKIE_NAME, "")),
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    set_no_store_headers(response)
    clear_session_cookies(response)
    return response


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_400_BAD_REQUEST: problem_openapi_response("Current password is invalid"),
        status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
        status.HTTP_403_FORBIDDEN: problem_openapi_response("Password change request rejected"),
        status.HTTP_409_CONFLICT: problem_openapi_response("Password changed concurrently"),
        status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response(
            "Request validation failed"
        ),
        status.HTTP_429_TOO_MANY_REQUESTS: problem_openapi_response(
            "Password change rate limit exceeded"
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
        status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
            "Password change temporarily unavailable"
        ),
    },
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    password_change_service: Annotated[
        PasswordChangeUseCase,
        Depends(get_password_change_service),
    ],
    rate_limiter: Annotated[
        LoginAttemptRateLimiter,
        Depends(get_password_change_rate_limiter),
    ],
) -> Response:
    """Replace the password, revoke all sessions, then clear browser state."""

    client = request.client
    if client is None:
        raise PasswordChangeRateLimitUnavailableError

    try:
        await rate_limiter.acquire(
            source_ip=client.host,
            raw_email=str(principal.email),
        )
    except LoginRateLimitExceededError as error:
        raise PasswordChangeRateLimitExceededError(
            retry_after_seconds=error.retry_after_seconds,
        ) from None
    except LoginRateLimitUnavailableError:
        raise PasswordChangeRateLimitUnavailableError from None

    await password_change_service.change_password(
        ChangePasswordCommand(
            user_id=principal.user_id,
            session_id=principal.session_id,
            email=principal.email,
            origin=_single_header_value(request, "Origin"),
            current_password=payload.current_password,
            new_password=payload.new_password,
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    set_no_store_headers(response)
    clear_session_cookies(response)
    return response
