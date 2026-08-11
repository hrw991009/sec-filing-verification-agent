"""Security contracts shared by refresh and logout delivery paths."""

from base64 import urlsafe_b64encode
from uuid import UUID

import pytest
from pydantic import SecretBytes

from industry_platform.modules.identity.adapters.browser_requests import (
    ExactBrowserSessionRequestGuard,
)
from industry_platform.modules.identity.adapters.refresh_recovery import (
    AesGcmRefreshRecoveryCodec,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.domain import (
    BrowserRequestSecurityConfigurationError,
    CsrfToken,
    CsrfTokenHash,
    DeviceTokenHash,
    InvalidBrowserSessionRequestError,
    RefreshRecoveryConfigurationError,
    RefreshRecoveryContext,
    RefreshRecoveryEnvelope,
    RefreshRecoveryError,
    RefreshSuccessorTokens,
    RefreshToken,
)

PREDECESSOR_ID = UUID("11111111-1111-4111-8111-111111111111")
SUCCESSOR_ID = UUID("22222222-2222-4222-8222-222222222222")
FAMILY_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
REFRESH_VALUE = urlsafe_b64encode(b"r" * 32).rstrip(b"=").decode("ascii")
CSRF_VALUE = urlsafe_b64encode(b"c" * 32).rstrip(b"=").decode("ascii")
RECOVERY_KEY_BYTES = b"e" * 32


def token_service() -> HmacSessionTokenService:
    return HmacSessionTokenService(
        refresh_hmac_key=SecretBytes(b"r" * 32),
        csrf_hmac_key=SecretBytes(b"c" * 32),
        device_hmac_key=SecretBytes(b"d" * 32),
    )


def recovery_context(
    *,
    predecessor_session_id: UUID = PREDECESSOR_ID,
    successor_session_id: UUID = SUCCESSOR_ID,
    rotation_family_id: UUID = FAMILY_ID,
    user_id: UUID = USER_ID,
    device_token_hash: DeviceTokenHash | None = None,
) -> RefreshRecoveryContext:
    return RefreshRecoveryContext(
        predecessor_session_id=predecessor_session_id,
        successor_session_id=successor_session_id,
        rotation_family_id=rotation_family_id,
        user_id=user_id,
        device_token_hash=(
            DeviceTokenHash(b"d" * 32) if device_token_hash is None else device_token_hash
        ),
    )


def successor_tokens() -> RefreshSuccessorTokens:
    return RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport(REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(CSRF_VALUE),
    )


def recovery_codec(
    *,
    key: bytes = RECOVERY_KEY_BYTES,
) -> AesGcmRefreshRecoveryCodec:
    return AesGcmRefreshRecoveryCodec(
        SecretBytes(key),
        random_bytes=lambda byte_count: b"n" * byte_count,
    )


def test_recovery_round_trip_hides_plaintext_and_preserves_exact_successor() -> None:
    codec = recovery_codec()
    tokens = successor_tokens()

    envelope = codec.seal(tokens, context=recovery_context())
    recovered = codec.open(envelope, context=recovery_context())

    assert recovered == tokens
    assert REFRESH_VALUE.encode() not in bytes(envelope)
    assert CSRF_VALUE.encode() not in bytes(envelope)
    assert REFRESH_VALUE not in repr(recovered)
    assert CSRF_VALUE not in repr(recovered)


@pytest.mark.parametrize(
    "changed_context",
    [
        recovery_context(predecessor_session_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")),
        recovery_context(successor_session_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")),
        recovery_context(rotation_family_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")),
        recovery_context(user_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")),
        recovery_context(device_token_hash=DeviceTokenHash(b"x" * 32)),
    ],
)
def test_recovery_rejects_every_changed_context_binding(
    changed_context: RefreshRecoveryContext,
) -> None:
    codec = recovery_codec()
    context = recovery_context()
    envelope = codec.seal(successor_tokens(), context=context)
    with pytest.raises(RefreshRecoveryError):
        codec.open(envelope, context=changed_context)


def test_recovery_rejects_wrong_key_tampering_version_and_truncation() -> None:
    codec = recovery_codec()
    context = recovery_context()
    envelope = codec.seal(successor_tokens(), context=context)
    tampered = bytearray(envelope)
    tampered[-1] ^= 1
    invalid_values = (
        RefreshRecoveryEnvelope(bytes(tampered)),
        RefreshRecoveryEnvelope(b"\x02" + bytes(envelope)[1:]),
        RefreshRecoveryEnvelope(bytes(envelope)[:-1]),
    )

    with pytest.raises(RefreshRecoveryError):
        recovery_codec(key=b"x" * 32).open(envelope, context=context)

    for invalid_value in invalid_values:
        with pytest.raises(RefreshRecoveryError):
            codec.open(invalid_value, context=context)


def test_recovery_failures_do_not_reveal_tokens_or_key_material() -> None:
    sensitive_detail = "random-source-sensitive-detail"

    def failing_random_bytes(_byte_count: int) -> bytes:
        raise RuntimeError(sensitive_detail)

    codec = AesGcmRefreshRecoveryCodec(
        SecretBytes(RECOVERY_KEY_BYTES),
        random_bytes=failing_random_bytes,
    )

    with pytest.raises(RefreshRecoveryError) as exc_info:
        codec.seal(successor_tokens(), context=recovery_context())

    rendered = str(exc_info.value)
    assert sensitive_detail not in rendered
    assert REFRESH_VALUE not in rendered
    assert CSRF_VALUE not in rendered
    assert RECOVERY_KEY_BYTES.hex() not in rendered


def test_recovery_rejects_invalid_key_and_noncanonical_successor() -> None:
    with pytest.raises(RefreshRecoveryConfigurationError):
        recovery_codec(key=b"short")

    invalid_tokens = RefreshSuccessorTokens(
        refresh_token=RefreshToken.from_transport("r" * 43),
        csrf_token=CsrfToken.from_transport(CSRF_VALUE),
    )

    with pytest.raises(RefreshRecoveryError):
        recovery_codec().seal(invalid_tokens, context=recovery_context())


def test_recovery_uses_a_new_nonce_for_every_envelope() -> None:
    nonces = iter((b"a" * 12, b"b" * 12))
    codec = AesGcmRefreshRecoveryCodec(
        SecretBytes(RECOVERY_KEY_BYTES),
        random_bytes=lambda _byte_count: next(nonces),
    )

    first = codec.seal(successor_tokens(), context=recovery_context())
    second = codec.seal(successor_tokens(), context=recovery_context())

    assert first != second


def test_browser_guard_accepts_only_exact_origin_and_complete_csrf_proof() -> None:
    service = token_service()
    issued = service.issue()
    guard = ExactBrowserSessionRequestGuard(
        trusted_origins=("https://LOCALHOST:443", "https://localhost:5173"),
        token_service=service,
    )

    guard.validate_origin("https://localhost")
    guard.validate_origin("https://localhost:5173")
    validated = guard.validate_csrf(
        cookie_value=issued.csrf_token.reveal_for_transport(),
        header_value=issued.csrf_token.reveal_for_transport(),
        expected_hash=issued.csrf_token_hash,
    )

    assert validated == issued.csrf_token


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://localhost:5173",
        "https://localhost:4173",
        "https://localhost:5173/",
        "https://localhost:5173/path",
        "https://localhost:5173.attacker.invalid",
        "https://user@localhost:5173",
    ],
)
def test_browser_guard_rejects_untrusted_or_non_origin_values(origin: str | None) -> None:
    guard = ExactBrowserSessionRequestGuard(
        trusted_origins=("https://localhost:5173",),
        token_service=token_service(),
    )

    with pytest.raises(InvalidBrowserSessionRequestError):
        guard.validate_origin(origin)


@pytest.mark.parametrize(
    ("cookie_value", "header_value", "expected_hash"),
    [
        (None, CSRF_VALUE, CsrfTokenHash(b"c" * 32)),
        (CSRF_VALUE, None, CsrfTokenHash(b"c" * 32)),
        (CSRF_VALUE, "x" * 43, CsrfTokenHash(b"c" * 32)),
        ("!" * 43, "!" * 43, CsrfTokenHash(b"c" * 32)),
        ("令牌" * 22, "令牌" * 22, CsrfTokenHash(b"c" * 32)),
        (CSRF_VALUE, CSRF_VALUE, CsrfTokenHash(b"short")),
        (CSRF_VALUE, CSRF_VALUE, CsrfTokenHash(b"x" * 32)),
    ],
)
def test_browser_guard_rejects_all_incomplete_or_invalid_csrf_proofs(
    cookie_value: str | None,
    header_value: str | None,
    expected_hash: CsrfTokenHash,
) -> None:
    guard = ExactBrowserSessionRequestGuard(
        trusted_origins=("https://localhost:5173",),
        token_service=token_service(),
    )

    with pytest.raises(InvalidBrowserSessionRequestError):
        guard.validate_csrf(
            cookie_value=cookie_value,
            header_value=header_value,
            expected_hash=expected_hash,
        )


@pytest.mark.parametrize(
    "trusted_origins",
    [(), ("http://localhost:5173",), ("https://localhost:5173",) * 2],
)
def test_browser_guard_rejects_unsafe_configuration(
    trusted_origins: tuple[str, ...],
) -> None:
    with pytest.raises(BrowserRequestSecurityConfigurationError):
        ExactBrowserSessionRequestGuard(
            trusted_origins=trusted_origins,
            token_service=token_service(),
        )
