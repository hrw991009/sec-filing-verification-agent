"""Strict HTTP bearer parsing for every protected identity endpoint."""

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    InvalidAuthenticatedSessionError,
)
from industry_platform.modules.identity.ports import AuthenticatedPrincipalResolver
from industry_platform.modules.identity.resources import (
    IdentityResources,
    get_identity_resources,
)

AUTHORIZATION_HEADER_NAME = "Authorization"
BEARER_AUTH_SCHEME = "Bearer"
_ACCESS_TOKEN_DOCUMENTATION = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
)


def get_principal_resolver(
    resources: Annotated[IdentityResources, Depends(get_identity_resources)],
) -> AuthenticatedPrincipalResolver:
    """Select the one runtime resolver shared by all protected APIs."""

    return resources.principal_resolver


def _parse_bearer_value(request: Request) -> AccessToken:
    values = request.headers.getlist(AUTHORIZATION_HEADER_NAME)

    if len(values) != 1:
        raise InvalidAuthenticatedSessionError

    value = values[0]
    scheme, separator, credentials = value.partition(" ")

    if (
        separator != " "
        or scheme.casefold() != BEARER_AUTH_SCHEME.casefold()
        or not credentials
        or credentials.strip() != credentials
        or any(character.isspace() for character in credentials)
        or "," in credentials
    ):
        raise InvalidAuthenticatedSessionError

    return AccessToken.from_transport(credentials)


async def require_authenticated_principal(
    request: Request,
    _documented_credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_ACCESS_TOKEN_DOCUMENTATION),
    ],
    resolver: Annotated[
        AuthenticatedPrincipalResolver,
        Depends(get_principal_resolver),
    ],
) -> AuthenticatedPrincipal:
    """Resolve one server-verified principal or raise the uniform 401 error."""

    return await resolver.resolve(_parse_bearer_value(request))
