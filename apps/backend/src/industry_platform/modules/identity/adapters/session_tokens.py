"""CSPRNG session tokens and purpose-separated HMAC-SHA-256 digests."""

import hmac
import re
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64DecodeError
from secrets import token_bytes

from pydantic import SecretBytes

from industry_platform.modules.identity.domain import (
    CsrfToken,
    CsrfTokenHash,
    DeviceToken,
    DeviceTokenHash,
    InvalidSessionTokenError,
    InvalidSessionTokenKeyError,
    IssuedLoginSessionTokens,
    RefreshToken,
    RefreshTokenHash,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.ports import RandomBytesSource

SESSION_TOKEN_BYTES = 32
SESSION_TOKEN_TEXT_LENGTH = 43
SESSION_HMAC_KEY_BYTES = 32

_CANONICAL_TOKEN_PATTERN = re.compile(rf"^[A-Za-z0-9_-]{{{SESSION_TOKEN_TEXT_LENGTH}}}$")
_REFRESH_TOKEN_DOMAIN = b"iip.identity.refresh-token-hash.v1\x00"
_CSRF_TOKEN_DOMAIN = b"iip.identity.csrf-token-hash.v1\x00"
_DEVICE_TOKEN_DOMAIN = b"iip.identity.device-token-hash.v1\x00"


class HmacSessionTokenService:
    """Issue strict opaque tokens and digest each purpose with an independent key."""

    def __init__(
        self,
        *,
        refresh_hmac_key: SecretBytes,
        csrf_hmac_key: SecretBytes,
        device_hmac_key: SecretBytes,
        random_bytes: RandomBytesSource = token_bytes,
    ) -> None:
        key_values = (
            refresh_hmac_key.get_secret_value(),
            csrf_hmac_key.get_secret_value(),
            device_hmac_key.get_secret_value(),
        )

        if any(len(key_value) != SESSION_HMAC_KEY_BYTES for key_value in key_values):
            raise InvalidSessionTokenKeyError

        if len(set(key_values)) != len(key_values):
            raise InvalidSessionTokenKeyError

        self._refresh_hmac_key, self._csrf_hmac_key, self._device_hmac_key = key_values
        self._random_bytes = random_bytes

    def issue(self) -> IssuedLoginSessionTokens:
        """Create three distinct 256-bit tokens before exposing any result."""

        raw_values = (
            self._new_random_value(),
            self._new_random_value(),
            self._new_random_value(),
        )

        if len(set(raw_values)) != len(raw_values):
            raise SessionTokenGenerationError

        refresh_raw, csrf_raw, device_raw = raw_values
        refresh_token = RefreshToken.from_transport(self._encode(refresh_raw))
        csrf_token = CsrfToken.from_transport(self._encode(csrf_raw))
        device_token = DeviceToken.from_transport(self._encode(device_raw))

        return IssuedLoginSessionTokens(
            refresh_token=refresh_token,
            csrf_token=csrf_token,
            device_token=device_token,
            refresh_token_hash=RefreshTokenHash(
                self._digest(self._refresh_hmac_key, _REFRESH_TOKEN_DOMAIN, refresh_raw)
            ),
            csrf_token_hash=CsrfTokenHash(
                self._digest(self._csrf_hmac_key, _CSRF_TOKEN_DOMAIN, csrf_raw)
            ),
            device_token_hash=DeviceTokenHash(
                self._digest(self._device_hmac_key, _DEVICE_TOKEN_DOMAIN, device_raw)
            ),
        )

    def digest_refresh(self, token: RefreshToken) -> RefreshTokenHash:
        """Strictly decode and digest one untrusted refresh-token value."""

        return RefreshTokenHash(
            self._digest(
                self._refresh_hmac_key,
                _REFRESH_TOKEN_DOMAIN,
                self._decode(token.reveal_for_transport()),
            )
        )

    def digest_csrf(self, token: CsrfToken) -> CsrfTokenHash:
        """Strictly decode and digest one untrusted CSRF-token value."""

        return CsrfTokenHash(
            self._digest(
                self._csrf_hmac_key,
                _CSRF_TOKEN_DOMAIN,
                self._decode(token.reveal_for_transport()),
            )
        )

    def digest_device(self, token: DeviceToken) -> DeviceTokenHash:
        """Strictly decode and digest one untrusted device-token value."""

        return DeviceTokenHash(
            self._digest(
                self._device_hmac_key,
                _DEVICE_TOKEN_DOMAIN,
                self._decode(token.reveal_for_transport()),
            )
        )

    def _new_random_value(self) -> bytes:
        try:
            raw_value = self._random_bytes(SESSION_TOKEN_BYTES)
        except Exception:
            raise SessionTokenGenerationError from None

        if not isinstance(raw_value, bytes) or len(raw_value) != SESSION_TOKEN_BYTES:
            raise SessionTokenGenerationError

        return raw_value

    @staticmethod
    def _encode(raw_value: bytes) -> str:
        return urlsafe_b64encode(raw_value).rstrip(b"=").decode("ascii")

    @classmethod
    def _decode(cls, encoded_value: str) -> bytes:
        if not _CANONICAL_TOKEN_PATTERN.fullmatch(encoded_value):
            raise InvalidSessionTokenError

        try:
            raw_value = b64decode(
                encoded_value + "=",
                altchars=b"-_",
                validate=True,
            )
        except (Base64DecodeError, ValueError):
            raise InvalidSessionTokenError from None

        if len(raw_value) != SESSION_TOKEN_BYTES or not hmac.compare_digest(
            cls._encode(raw_value), encoded_value
        ):
            raise InvalidSessionTokenError

        return raw_value

    @staticmethod
    def _digest(key: bytes, domain: bytes, raw_value: bytes) -> bytes:
        return hmac.digest(key, domain + raw_value, "sha256")
