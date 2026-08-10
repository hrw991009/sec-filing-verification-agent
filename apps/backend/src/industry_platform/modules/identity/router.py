"""HTTP delivery adapter for identity use cases."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from industry_platform.core.http import get_trace_id, problem_openapi_response
from industry_platform.modules.identity.domain import RegisterUserCommand, TraceId
from industry_platform.modules.identity.ports import RegistrationUseCase
from industry_platform.modules.identity.resources import (
    IdentityResources,
    get_identity_resources,
)
from industry_platform.modules.identity.schemas import (
    RegisterRequest,
    RegistrationResponse,
)

router = APIRouter(prefix="/auth", tags=["identity"])


def get_registration_service(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> RegistrationUseCase:
    """Select the registration use case from process-wide identity resources."""

    return resources.registration_service


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
