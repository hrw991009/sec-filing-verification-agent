"""AES-GCM successor recovery for refresh responses lost after commit."""

import hmac
import re
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64DecodeError
from secrets import token_bytes

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes

from industry_platform.modules.identity.domain import (
    CsrfToken,
    RefreshRecoveryConfigurationError,
    RefreshRecoveryContext,
    RefreshRecoveryEnvelope,
    RefreshRecoveryError,
    RefreshSuccessorTokens,
    RefreshToken,
)
from industry_platform.modules.identity.ports import RandomBytesSource

RECOVERY_KEY_BYTES = 32
RECOVERY_NONCE_BYTES = 12
RECOVERY_TAG_BYTES = 16
RECOVERY_VERSION = b"\x01"
SESSION_TOKEN_TEXT_BYTES = 43
SESSION_TOKEN_BYTES = 32

_TOKEN_PATTERN = re.compile(rb"^[A-Za-z0-9_-]{43}$")
_PLAINTEXT_BYTES = SESSION_TOKEN_TEXT_BYTES * 2
_ENVELOPE_BYTES = (
    len(RECOVERY_VERSION) + RECOVERY_NONCE_BYTES + _PLAINTEXT_BYTES + RECOVERY_TAG_BYTES
)
_AAD_DOMAIN = b"iip.identity.refresh-successor-recovery.v1\x00"


class AesGcmRefreshRecoveryCodec:
    """Encrypt only the exact successor values and bind all session identities."""

    def __init__(
        self,
        key: SecretBytes,
        *,
        random_bytes: RandomBytesSource = token_bytes,
    ) -> None:
        key_value = key.get_secret_value()
        if len(key_value) != RECOVERY_KEY_BYTES:
            raise RefreshRecoveryConfigurationError

        self._cipher = AESGCM(key_value)
        self._random_bytes = random_bytes

    def seal(
        self,
        tokens: RefreshSuccessorTokens,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshRecoveryEnvelope:
        """Seal one successor with a fresh nonce and fixed binary representation."""

        try:
            plaintext = self._encode_tokens(tokens)
            nonce = self._random_bytes(RECOVERY_NONCE_BYTES)
            if not isinstance(nonce, bytes) or len(nonce) != RECOVERY_NONCE_BYTES:
                raise ValueError

            ciphertext = self._cipher.encrypt(
                nonce,
                plaintext,
                self._associated_data(context),
            )
        except Exception:
            raise RefreshRecoveryError from None

        return RefreshRecoveryEnvelope(RECOVERY_VERSION + nonce + ciphertext)

    def open(
        self,
        envelope: RefreshRecoveryEnvelope,
        *,
        context: RefreshRecoveryContext,
    ) -> RefreshSuccessorTokens:
        """Return original values only when ciphertext and every binding match."""

        try:
            envelope_bytes = bytes(envelope)
        except (TypeError, ValueError):
            raise RefreshRecoveryError from None

        if len(envelope_bytes) != _ENVELOPE_BYTES or envelope_bytes[:1] != RECOVERY_VERSION:
            raise RefreshRecoveryError

        nonce_end = len(RECOVERY_VERSION) + RECOVERY_NONCE_BYTES
        nonce = envelope_bytes[1:nonce_end]
        ciphertext = envelope_bytes[nonce_end:]

        try:
            plaintext = self._cipher.decrypt(
                nonce,
                ciphertext,
                self._associated_data(context),
            )
            return self._decode_tokens(plaintext)
        except (InvalidTag, TypeError, ValueError):
            raise RefreshRecoveryError from None

    @staticmethod
    def _associated_data(context: RefreshRecoveryContext) -> bytes:
        return b"".join(
            (
                _AAD_DOMAIN,
                RECOVERY_VERSION,
                context.predecessor_session_id.bytes,
                context.successor_session_id.bytes,
                context.rotation_family_id.bytes,
                context.user_id.bytes,
                bytes(context.device_token_hash),
            )
        )

    @staticmethod
    def _encode_tokens(tokens: RefreshSuccessorTokens) -> bytes:
        try:
            refresh_value = tokens.refresh_token.reveal_for_transport().encode("ascii")
            csrf_value = tokens.csrf_token.reveal_for_transport().encode("ascii")
        except UnicodeEncodeError:
            raise ValueError from None

        return AesGcmRefreshRecoveryCodec._validate_canonical_token(
            refresh_value
        ) + AesGcmRefreshRecoveryCodec._validate_canonical_token(csrf_value)

    @staticmethod
    def _decode_tokens(plaintext: bytes) -> RefreshSuccessorTokens:
        if len(plaintext) != _PLAINTEXT_BYTES:
            raise ValueError

        refresh_value = plaintext[:SESSION_TOKEN_TEXT_BYTES]
        csrf_value = plaintext[SESSION_TOKEN_TEXT_BYTES:]

        refresh_value = AesGcmRefreshRecoveryCodec._validate_canonical_token(refresh_value)
        csrf_value = AesGcmRefreshRecoveryCodec._validate_canonical_token(csrf_value)

        return RefreshSuccessorTokens(
            refresh_token=RefreshToken.from_transport(refresh_value.decode("ascii")),
            csrf_token=CsrfToken.from_transport(csrf_value.decode("ascii")),
        )

    @staticmethod
    def _validate_canonical_token(encoded_value: bytes) -> bytes:
        if not _TOKEN_PATTERN.fullmatch(encoded_value):
            raise ValueError

        try:
            raw_value = b64decode(
                encoded_value + b"=",
                altchars=b"-_",
                validate=True,
            )
        except (Base64DecodeError, ValueError):
            raise ValueError from None

        canonical_value = urlsafe_b64encode(raw_value).rstrip(b"=")
        if len(raw_value) != SESSION_TOKEN_BYTES or not hmac.compare_digest(
            canonical_value,
            encoded_value,
        ):
            raise ValueError

        return encoded_value
