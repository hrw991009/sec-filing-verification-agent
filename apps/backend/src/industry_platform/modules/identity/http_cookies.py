"""Secure browser Cookie delivery for identity session credentials."""

from datetime import UTC, datetime

from fastapi import Response

from industry_platform.modules.identity.domain import EstablishedLoginSession

REFRESH_COOKIE_NAME = "__Host-iip_refresh"
CSRF_COOKIE_NAME = "__Host-iip_csrf"
DEVICE_COOKIE_NAME = "__Host-iip_device"


def _max_age_seconds(*, issued_at: datetime, expires_at: datetime) -> int:
    max_age = int((expires_at - issued_at).total_seconds())

    if max_age <= 0:
        raise ValueError("Session Cookie expiration must be after issuance")

    return max_age


def _set_cookie(
    response: Response,
    *,
    name: str,
    value: str,
    expires_at: datetime,
    max_age: int,
    httponly: bool,
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        expires=expires_at.astimezone(UTC),
        path="/",
        secure=True,
        httponly=httponly,
        samesite="strict",
    )


def set_login_cookies(response: Response, result: EstablishedLoginSession) -> None:
    """Publish three purpose-specific values only after login commits."""

    idle_max_age = _max_age_seconds(
        issued_at=result.session.issued_at,
        expires_at=result.session.idle_expires_at,
    )
    absolute_max_age = _max_age_seconds(
        issued_at=result.session.issued_at,
        expires_at=result.session.absolute_expires_at,
    )
    _set_cookie(
        response,
        name=REFRESH_COOKIE_NAME,
        value=result.refresh_token.reveal_for_transport(),
        expires_at=result.session.idle_expires_at,
        max_age=idle_max_age,
        httponly=True,
    )
    _set_cookie(
        response,
        name=CSRF_COOKIE_NAME,
        value=result.csrf_token.reveal_for_transport(),
        expires_at=result.session.idle_expires_at,
        max_age=idle_max_age,
        httponly=False,
    )
    _set_cookie(
        response,
        name=DEVICE_COOKIE_NAME,
        value=result.device_token.reveal_for_transport(),
        expires_at=result.session.absolute_expires_at,
        max_age=absolute_max_age,
        httponly=True,
    )
