"""Exact Origin and double-submit CSRF checks for browser session changes."""

import hmac
from collections.abc import Iterable

from industry_platform.core.origins import canonicalize_https_origin
from industry_platform.modules.identity.domain import (
    BrowserRequestSecurityConfigurationError,
    CsrfToken,
    CsrfTokenHash,
    InvalidBrowserSessionRequestError,
    InvalidSessionTokenError,
)
from industry_platform.modules.identity.ports import LoginSessionTokenService


class ExactBrowserSessionRequestGuard:
    """Apply one generic rejection boundary to Origin and CSRF failures."""

    def __init__(
        self,
        *,
        trusted_origins: Iterable[str],
        token_service: LoginSessionTokenService,
    ) -> None:
        try:
            configured_origins = tuple(
                canonicalize_https_origin(origin) for origin in trusted_origins
            )
        except ValueError:
            raise BrowserRequestSecurityConfigurationError from None

        if not configured_origins or len(set(configured_origins)) != len(configured_origins):
            raise BrowserRequestSecurityConfigurationError

        self._trusted_origins = frozenset(configured_origins)
        self._token_service = token_service

    def validate_origin(self, origin: str | None) -> None:
        """Reject missing, malformed, insecure, or untrusted origins identically."""

        try:
            canonical_origin = canonicalize_https_origin(origin)
        except ValueError:
            raise InvalidBrowserSessionRequestError from None

        if canonical_origin not in self._trusted_origins:
            raise InvalidBrowserSessionRequestError

    def validate_csrf(
        self,
        *,
        cookie_value: str | None,
        header_value: str | None,
        expected_hash: CsrfTokenHash,
    ) -> CsrfToken:
        """Require the same token in Cookie, header, and persisted HMAC digest."""

        if (
            not isinstance(cookie_value, str)
            or not isinstance(header_value, str)
            or not cookie_value
            or not header_value
            or len(expected_hash) != 32
        ):
            raise InvalidBrowserSessionRequestError

        try:
            cookie_bytes = cookie_value.encode("ascii")
            header_bytes = header_value.encode("ascii")
        except UnicodeEncodeError:
            raise InvalidBrowserSessionRequestError from None

        if not hmac.compare_digest(cookie_bytes, header_bytes):
            raise InvalidBrowserSessionRequestError

        csrf_token = CsrfToken.from_transport(cookie_value)

        try:
            actual_hash = self._token_service.digest_csrf(csrf_token)
        except InvalidSessionTokenError:
            raise InvalidBrowserSessionRequestError from None

        if not hmac.compare_digest(bytes(actual_hash), bytes(expected_hash)):
            raise InvalidBrowserSessionRequestError

        return csrf_token
