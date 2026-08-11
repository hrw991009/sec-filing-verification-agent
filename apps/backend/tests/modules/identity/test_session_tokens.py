"""Tests for strict opaque session-token generation and keyed digests."""

import hmac
from base64 import b64decode, urlsafe_b64encode

import pytest
from pydantic import SecretBytes

from industry_platform.modules.identity.adapters.session_tokens import (
    SESSION_HMAC_KEY_BYTES,
    SESSION_TOKEN_BYTES,
    SESSION_TOKEN_TEXT_LENGTH,
    HmacSessionTokenService,
)
from industry_platform.modules.identity.domain import (
    InvalidSessionTokenError,
    InvalidSessionTokenKeyError,
    RefreshToken,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.ports import RandomBytesSource

REFRESH_KEY_VALUE = b"r" * SESSION_HMAC_KEY_BYTES
CSRF_KEY_VALUE = b"c" * SESSION_HMAC_KEY_BYTES
DEVICE_KEY_VALUE = b"d" * SESSION_HMAC_KEY_BYTES

REFRESH_DOMAIN = b"iip.identity.refresh-token-hash.v1\x00"
CSRF_DOMAIN = b"iip.identity.csrf-token-hash.v1\x00"
DEVICE_DOMAIN = b"iip.identity.device-token-hash.v1\x00"


class RecordingRandomBytes:
    """Return predetermined entropy blocks and record every size request."""

    def __init__(self, *values: bytes) -> None:
        self._values = iter(values)
        self.requests: list[int] = []

    def __call__(self, byte_count: int) -> bytes:
        self.requests.append(byte_count)
        return next(self._values)


def token_service(
    *,
    refresh_key: bytes = REFRESH_KEY_VALUE,
    csrf_key: bytes = CSRF_KEY_VALUE,
    device_key: bytes = DEVICE_KEY_VALUE,
    random_bytes: RandomBytesSource | None = None,
) -> HmacSessionTokenService:
    """Create a service with explicit, independent binary keys."""

    if random_bytes is None:
        return HmacSessionTokenService(
            refresh_hmac_key=SecretBytes(refresh_key),
            csrf_hmac_key=SecretBytes(csrf_key),
            device_hmac_key=SecretBytes(device_key),
        )

    return HmacSessionTokenService(
        refresh_hmac_key=SecretBytes(refresh_key),
        csrf_hmac_key=SecretBytes(csrf_key),
        device_hmac_key=SecretBytes(device_key),
        random_bytes=random_bytes,
    )


def encode_token(raw_value: bytes) -> str:
    """Encode one canonical unpadded base64url token for boundary tests."""

    return urlsafe_b64encode(raw_value).rstrip(b"=").decode("ascii")


def decode_token(encoded_value: str) -> bytes:
    """Strictly decode a known-valid token in test assertions."""

    return b64decode(encoded_value + "=", altchars=b"-_", validate=True)


def test_issue_uses_three_256_bit_blocks_and_matches_fixed_hmac_vectors() -> None:
    raw_values = (b"\x01" * 32, b"\x02" * 32, b"\x03" * 32)
    random_bytes = RecordingRandomBytes(*raw_values)
    service = token_service(random_bytes=random_bytes)

    issued = service.issue()

    encoded_values = (
        issued.refresh_token.reveal_for_transport(),
        issued.csrf_token.reveal_for_transport(),
        issued.device_token.reveal_for_transport(),
    )
    assert random_bytes.requests == [SESSION_TOKEN_BYTES] * 3
    assert len(set(encoded_values)) == 3
    assert all(len(value) == SESSION_TOKEN_TEXT_LENGTH for value in encoded_values)
    assert all("=" not in value for value in encoded_values)
    assert tuple(decode_token(value) for value in encoded_values) == raw_values
    assert bytes(issued.refresh_token_hash) == hmac.digest(
        REFRESH_KEY_VALUE,
        REFRESH_DOMAIN + raw_values[0],
        "sha256",
    )
    assert bytes(issued.csrf_token_hash) == hmac.digest(
        CSRF_KEY_VALUE,
        CSRF_DOMAIN + raw_values[1],
        "sha256",
    )
    assert bytes(issued.device_token_hash) == hmac.digest(
        DEVICE_KEY_VALUE,
        DEVICE_DOMAIN + raw_values[2],
        "sha256",
    )
    assert service.digest_refresh(issued.refresh_token) == issued.refresh_token_hash
    assert service.digest_csrf(issued.csrf_token) == issued.csrf_token_hash
    assert service.digest_device(issued.device_token) == issued.device_token_hash


def test_refresh_successor_rotates_only_refresh_and_csrf_values() -> None:
    raw_values = (b"\x07" * 32, b"\x08" * 32)
    random_bytes = RecordingRandomBytes(*raw_values)
    service = token_service(random_bytes=random_bytes)

    issued = service.issue_refresh_successor()

    refresh_value = issued.refresh_token.reveal_for_transport()
    csrf_value = issued.csrf_token.reveal_for_transport()
    assert random_bytes.requests == [SESSION_TOKEN_BYTES] * 2
    assert refresh_value != csrf_value
    assert decode_token(refresh_value) == raw_values[0]
    assert decode_token(csrf_value) == raw_values[1]
    assert service.digest_refresh(issued.refresh_token) == issued.refresh_token_hash
    assert service.digest_csrf(issued.csrf_token) == issued.csrf_token_hash
    assert not hasattr(issued, "device_token")
    assert refresh_value not in repr(issued)
    assert csrf_value not in repr(issued)


def test_changing_only_the_refresh_key_changes_only_the_refresh_digest() -> None:
    issued = token_service(
        random_bytes=RecordingRandomBytes(
            b"\x04" * 32,
            b"\x05" * 32,
            b"\x06" * 32,
        )
    ).issue()
    baseline = token_service()
    changed = token_service(refresh_key=b"x" * SESSION_HMAC_KEY_BYTES)

    assert changed.digest_refresh(issued.refresh_token) != baseline.digest_refresh(
        issued.refresh_token
    )
    assert changed.digest_csrf(issued.csrf_token) == baseline.digest_csrf(issued.csrf_token)
    assert changed.digest_device(issued.device_token) == baseline.digest_device(issued.device_token)


def test_default_csprng_does_not_repeat_across_two_issuances() -> None:
    service = token_service()
    first = service.issue()
    second = service.issue()
    plaintext_values = {
        first.refresh_token.reveal_for_transport(),
        first.csrf_token.reveal_for_transport(),
        first.device_token.reveal_for_transport(),
        second.refresh_token.reveal_for_transport(),
        second.csrf_token.reveal_for_transport(),
        second.device_token.reveal_for_transport(),
    }

    assert len(plaintext_values) == 6


def test_tokens_keys_and_hashes_do_not_appear_in_representations() -> None:
    service = token_service()
    issued = service.issue()
    rendered = repr(issued)

    assert issued.refresh_token.reveal_for_transport() not in repr(issued.refresh_token)
    assert issued.csrf_token.reveal_for_transport() not in repr(issued.csrf_token)
    assert issued.device_token.reveal_for_transport() not in repr(issued.device_token)
    assert issued.refresh_token.reveal_for_transport() not in rendered
    assert bytes(issued.refresh_token_hash).hex() not in rendered
    assert REFRESH_KEY_VALUE.hex() not in repr(service)


@pytest.mark.parametrize(
    ("refresh_key", "csrf_key", "device_key"),
    [
        (b"short", CSRF_KEY_VALUE, DEVICE_KEY_VALUE),
        (REFRESH_KEY_VALUE, REFRESH_KEY_VALUE, DEVICE_KEY_VALUE),
    ],
)
def test_service_rejects_wrong_length_or_reused_hmac_keys(
    refresh_key: bytes,
    csrf_key: bytes,
    device_key: bytes,
) -> None:
    with pytest.raises(InvalidSessionTokenKeyError) as exc_info:
        token_service(
            refresh_key=refresh_key,
            csrf_key=csrf_key,
            device_key=device_key,
        )

    assert refresh_key.hex() not in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_value",
    [
        "",
        "A" * (SESSION_TOKEN_TEXT_LENGTH - 1),
        "A" * SESSION_TOKEN_TEXT_LENGTH + "=",
        "*" + "A" * (SESSION_TOKEN_TEXT_LENGTH - 1),
        "伪" * SESSION_TOKEN_TEXT_LENGTH,
        encode_token(b"\x00" * 32)[:-1] + "B",
    ],
)
def test_digest_rejects_noncanonical_or_malformed_transport_values(
    invalid_value: str,
) -> None:
    with pytest.raises(InvalidSessionTokenError) as exc_info:
        token_service().digest_refresh(RefreshToken.from_transport(invalid_value))

    assert str(exc_info.value) == "Invalid session token"


def test_generation_rejects_short_or_duplicate_random_results() -> None:
    short_source = RecordingRandomBytes(b"short")
    duplicate_source = RecordingRandomBytes(*([b"x" * 32] * 3))

    with pytest.raises(SessionTokenGenerationError):
        token_service(random_bytes=short_source).issue()

    with pytest.raises(SessionTokenGenerationError):
        token_service(random_bytes=duplicate_source).issue()

    with pytest.raises(SessionTokenGenerationError):
        token_service(
            random_bytes=RecordingRandomBytes(*([b"y" * 32] * 2))
        ).issue_refresh_successor()


def test_generation_sanitizes_random_source_exceptions() -> None:
    sensitive_detail = "partial random bytes must never escape"

    def broken_source(_byte_count: int) -> bytes:
        raise RuntimeError(sensitive_detail)

    with pytest.raises(SessionTokenGenerationError) as exc_info:
        token_service(random_bytes=broken_source).issue()

    assert sensitive_detail not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
