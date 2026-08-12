"""Strict Ed25519 Access Token signing and verification adapter."""

import hmac
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jwt.exceptions import PyJWTError
from jwt.types import Options
from pydantic import SecretBytes

from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenClaims,
    AccessTokenConfigurationError,
    AccessTokenGenerationError,
    InvalidAccessTokenError,
    IssueAccessTokenCommand,
    IssuedAccessToken,
)
from industry_platform.modules.identity.ports import JwtIdSource

JWT_ALGORITHM = "EdDSA"
JWT_TYPE = "at+jwt"
JWT_ISSUER = "industry-intelligence-platform"
JWT_AUDIENCE = "industry-platform-api"
ACCESS_TOKEN_TTL = timedelta(minutes=10)
ACCESS_TOKEN_CLOCK_SKEW = timedelta(seconds=30)

_ED25519_KEY_BYTES = 32
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HEADER_FIELDS = frozenset({"alg", "typ", "kid"})
_CLAIM_FIELDS = frozenset({"iss", "aud", "sub", "sid", "jti", "iat", "nbf", "exp"})
_DECODE_OPTIONS: Options = {
    "require": list(_CLAIM_FIELDS),
    "strict_aud": True,
    "verify_aud": True,
    "verify_exp": False,
    "verify_iat": False,
    "verify_iss": True,
    "verify_jti": True,
    "verify_nbf": False,
    "verify_signature": True,
    "verify_sub": True,
}


class Ed25519AccessTokenCodec:
    """Enforce one local-key, fixed-algorithm JWT profile from ADR 0006."""

    def __init__(
        self,
        *,
        current_kid: str,
        private_key: SecretBytes,
        public_keys: Mapping[str, bytes],
        jti_source: JwtIdSource = uuid4,
    ) -> None:
        try:
            private_key_bytes = private_key.get_secret_value()
            configured_public_keys = dict(public_keys)

            if len(private_key_bytes) != _ED25519_KEY_BYTES or not configured_public_keys:
                raise ValueError

            signing_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
            verification_keys = {
                key_id: Ed25519PublicKey.from_public_bytes(key_bytes)
                for key_id, key_bytes in configured_public_keys.items()
                if isinstance(key_id, str)
                and _KEY_ID_PATTERN.fullmatch(key_id)
                and isinstance(key_bytes, bytes)
                and len(key_bytes) == _ED25519_KEY_BYTES
            }

            if (
                len(verification_keys) != len(configured_public_keys)
                or current_kid not in verification_keys
            ):
                raise ValueError

            derived_public_key = signing_key.public_key().public_bytes(
                Encoding.Raw,
                PublicFormat.Raw,
            )
            configured_public_key = configured_public_keys[current_kid]

            if not hmac.compare_digest(derived_public_key, configured_public_key):
                raise ValueError
        except Exception:
            raise AccessTokenConfigurationError from None

        self._current_kid = current_kid
        self._signing_key = signing_key
        self._verification_keys = verification_keys
        self._jti_source = jti_source

    def issue(self, command: IssueAccessTokenCommand) -> IssuedAccessToken:
        """Sign the exact claim set and fixed JOSE header accepted by verification."""

        try:
            jwt_id = self._jti_source()

            if not isinstance(jwt_id, UUID) or jwt_id.int == 0:
                raise ValueError

            issued_at = command.issued_at.replace(microsecond=0)
            claims = AccessTokenClaims(
                user_id=command.user_id,
                session_id=command.session_id,
                jwt_id=jwt_id,
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=issued_at + ACCESS_TOKEN_TTL,
            )
            encoded_value = jwt.encode(
                {
                    "iss": JWT_ISSUER,
                    "aud": JWT_AUDIENCE,
                    "sub": str(claims.user_id),
                    "sid": str(claims.session_id),
                    "jti": str(claims.jwt_id),
                    "iat": int(claims.issued_at.timestamp()),
                    "nbf": int(claims.not_before.timestamp()),
                    "exp": int(claims.expires_at.timestamp()),
                },
                self._signing_key,
                algorithm=JWT_ALGORITHM,
                headers={
                    "typ": JWT_TYPE,
                    "kid": self._current_kid,
                },
            )
        except Exception:
            raise AccessTokenGenerationError from None

        return IssuedAccessToken(
            token=AccessToken.from_transport(encoded_value),
            claims=claims,
        )

    def verify(
        self,
        token: AccessToken,
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        """Verify local key selection, signature, claims, and injected current time."""

        self._validate_verification_time(now)
        encoded_value = token.reveal_for_transport()

        try:
            header = jwt.get_unverified_header(encoded_value)
        except (PyJWTError, TypeError, ValueError):
            raise InvalidAccessTokenError from None

        if (
            set(header) != _HEADER_FIELDS
            or header.get("alg") != JWT_ALGORITHM
            or header.get("typ") != JWT_TYPE
            or not isinstance(header.get("kid"), str)
        ):
            raise InvalidAccessTokenError

        key_id = header["kid"]
        verification_key = self._verification_keys.get(key_id)

        if verification_key is None:
            raise InvalidAccessTokenError

        try:
            payload = jwt.decode(
                encoded_value,
                verification_key,
                algorithms=[JWT_ALGORITHM],
                audience=JWT_AUDIENCE,
                issuer=JWT_ISSUER,
                options=_DECODE_OPTIONS,
            )
        except (PyJWTError, TypeError, ValueError):
            raise InvalidAccessTokenError from None

        try:
            return self._parse_and_validate_claims(payload, now=now)
        except (KeyError, OSError, OverflowError, TypeError, ValueError):
            raise InvalidAccessTokenError from None

    @staticmethod
    def _validate_verification_time(now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None or now.utcoffset() != timedelta(0):
            raise ValueError("Access token verification time must use timezone-aware UTC")

    @classmethod
    def _parse_and_validate_claims(
        cls,
        payload: dict[str, object],
        *,
        now: datetime,
    ) -> AccessTokenClaims:
        if set(payload) != _CLAIM_FIELDS:
            raise ValueError

        claims = AccessTokenClaims(
            user_id=cls._decode_uuid(payload["sub"]),
            session_id=cls._decode_uuid(payload["sid"]),
            jwt_id=cls._decode_uuid(payload["jti"]),
            issued_at=cls._decode_numeric_date(payload["iat"]),
            not_before=cls._decode_numeric_date(payload["nbf"]),
            expires_at=cls._decode_numeric_date(payload["exp"]),
        )

        if claims.expires_at - claims.issued_at != ACCESS_TOKEN_TTL:
            raise ValueError

        if claims.issued_at > now + ACCESS_TOKEN_CLOCK_SKEW:
            raise ValueError

        if claims.not_before > now + ACCESS_TOKEN_CLOCK_SKEW:
            raise ValueError

        if claims.expires_at <= now - ACCESS_TOKEN_CLOCK_SKEW:
            raise ValueError

        return claims

    @staticmethod
    def _decode_uuid(value: object) -> UUID:
        if not isinstance(value, str):
            raise TypeError

        decoded = UUID(value)

        if str(decoded) != value or decoded.int == 0:
            raise ValueError

        return decoded

    @staticmethod
    def _decode_numeric_date(value: object) -> datetime:
        if type(value) is not int:
            raise TypeError

        return datetime.fromtimestamp(value, tz=UTC)
