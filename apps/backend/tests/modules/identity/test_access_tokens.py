"""Security and contract tests for the fixed Ed25519 Access Token profile."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import SecretBytes

from industry_platform.modules.identity.adapters.access_tokens import (
    ACCESS_TOKEN_CLOCK_SKEW,
    ACCESS_TOKEN_TTL,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_TYPE,
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenConfigurationError,
    AccessTokenGenerationError,
    InvalidAccessTokenError,
    IssueAccessTokenCommand,
)
from industry_platform.modules.identity.ports import JwtIdSource

CURRENT_KEY_ID = "test-current-key"
PREVIOUS_KEY_ID = "test-previous-key"
CURRENT_SEED = bytes(range(32))
PREVIOUS_SEED = bytes(range(32, 64))
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
JWT_ID = UUID("33333333-3333-4333-8333-333333333333")
ISSUED_AT = datetime(2026, 8, 11, 8, 30, tzinfo=UTC)


def public_key_for(seed: bytes) -> bytes:
    """Derive deterministic raw public bytes for a test-only private seed."""

    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def fixed_jti() -> UUID:
    """Return a stable unique identifier without patching global UUID behavior."""

    return JWT_ID


def access_codec(
    *,
    current_key_id: str = CURRENT_KEY_ID,
    signing_seed: bytes = CURRENT_SEED,
    public_keys: Mapping[str, bytes] | None = None,
    jti_source: JwtIdSource = fixed_jti,
) -> Ed25519AccessTokenCodec:
    """Construct one codec from explicit local keys and an injectable JTI source."""

    configured_public_keys = (
        {CURRENT_KEY_ID: public_key_for(CURRENT_SEED)} if public_keys is None else public_keys
    )
    return Ed25519AccessTokenCodec(
        current_kid=current_key_id,
        private_key=SecretBytes(signing_seed),
        public_keys=configured_public_keys,
        jti_source=jti_source,
    )


def issue_command() -> IssueAccessTokenCommand:
    """Build the trusted identifiers used by signing tests."""

    return IssueAccessTokenCommand(
        user_id=USER_ID,
        session_id=SESSION_ID,
        issued_at=ISSUED_AT,
    )


def valid_payload() -> dict[str, object]:
    """Return the exact claims accepted by the platform profile."""

    issued_timestamp = int(ISSUED_AT.timestamp())
    return {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "jti": str(JWT_ID),
        "iat": issued_timestamp,
        "nbf": issued_timestamp,
        "exp": int((ISSUED_AT + ACCESS_TOKEN_TTL).timestamp()),
    }


def sign_payload(
    payload: dict[str, object],
    *,
    seed: bytes = CURRENT_SEED,
    key_id: str | None = CURRENT_KEY_ID,
    extra_headers: Mapping[str, object] | None = None,
) -> str:
    """Sign a controlled payload, including deliberately invalid profiles."""

    headers: dict[str, object] = {"typ": JWT_TYPE}
    if key_id is not None:
        headers["kid"] = key_id
    if extra_headers is not None:
        headers.update(extra_headers)

    return jwt.encode(
        payload,
        Ed25519PrivateKey.from_private_bytes(seed),
        algorithm=JWT_ALGORITHM,
        headers=headers,
    )


def test_issue_uses_exact_header_claims_and_ten_minute_lifetime() -> None:
    codec = access_codec()

    issued = codec.issue(issue_command())
    encoded_value = issued.token.reveal_for_transport()
    header = jwt.get_unverified_header(encoded_value)
    payload = jwt.decode(
        encoded_value,
        Ed25519PrivateKey.from_private_bytes(CURRENT_SEED).public_key(),
        algorithms=[JWT_ALGORITHM],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={
            "verify_exp": False,
            "verify_iat": False,
            "verify_nbf": False,
        },
    )

    assert header == {
        "alg": JWT_ALGORITHM,
        "kid": CURRENT_KEY_ID,
        "typ": JWT_TYPE,
    }
    assert payload == valid_payload()
    assert issued.claims.user_id == USER_ID
    assert issued.claims.session_id == SESSION_ID
    assert issued.claims.jwt_id == JWT_ID
    assert issued.claims.expires_at - issued.claims.issued_at == timedelta(minutes=10)
    assert encoded_value not in repr(issued)


def test_verification_accepts_a_retained_previous_public_key() -> None:
    previous_codec = access_codec(
        current_key_id=PREVIOUS_KEY_ID,
        signing_seed=PREVIOUS_SEED,
        public_keys={PREVIOUS_KEY_ID: public_key_for(PREVIOUS_SEED)},
    )
    previous_value = previous_codec.issue(issue_command()).token
    rotating_codec = access_codec(
        public_keys={
            CURRENT_KEY_ID: public_key_for(CURRENT_SEED),
            PREVIOUS_KEY_ID: public_key_for(PREVIOUS_SEED),
        }
    )

    claims = rotating_codec.verify(previous_value, now=ISSUED_AT)

    assert claims.user_id == USER_ID
    assert claims.session_id == SESSION_ID
    assert claims.jwt_id == JWT_ID

    with pytest.raises(InvalidAccessTokenError):
        access_codec().verify(previous_value, now=ISSUED_AT)


@pytest.mark.parametrize(
    ("user_id", "session_id"),
    [
        (UUID(int=0), SESSION_ID),
        (USER_ID, UUID(int=0)),
    ],
)
def test_issue_command_rejects_nil_user_or_session_identifiers(
    user_id: UUID,
    session_id: UUID,
) -> None:
    with pytest.raises(ValueError, match="must not be nil UUIDs"):
        IssueAccessTokenCommand(
            user_id=user_id,
            session_id=session_id,
            issued_at=ISSUED_AT,
        )


def test_verification_rejects_untrusted_algorithms_keys_and_headers() -> None:
    codec = access_codec()
    unsigned_value = jwt.encode(
        valid_payload(),
        "",
        algorithm="none",
        headers={"typ": JWT_TYPE, "kid": CURRENT_KEY_ID},
    )
    symmetric_value = jwt.encode(
        valid_payload(),
        b"symmetric-test-material-with-32-bytes",
        algorithm="HS256",
        headers={"typ": JWT_TYPE, "kid": CURRENT_KEY_ID},
    )
    invalid_values = (
        unsigned_value,
        symmetric_value,
        sign_payload(valid_payload(), seed=PREVIOUS_SEED),
        sign_payload(valid_payload(), key_id="unknown-key"),
        sign_payload(
            valid_payload(),
            extra_headers={"jku": "https://attacker.invalid/keys.json"},
        ),
        sign_payload(
            valid_payload(),
            extra_headers={"x5u": "https://attacker.invalid/certificate.pem"},
        ),
        sign_payload(valid_payload(), extra_headers={"x5c": ["untrusted-certificate"]}),
        sign_payload(valid_payload(), extra_headers={"crit": ["custom"]}),
        sign_payload(valid_payload(), extra_headers={"typ": "JWT"}),
        sign_payload(valid_payload(), key_id=None),
    )

    for encoded_value in invalid_values:
        with pytest.raises(InvalidAccessTokenError):
            codec.verify(AccessToken.from_transport(encoded_value), now=ISSUED_AT)


def test_verification_rejects_invalid_or_extended_claim_sets() -> None:
    codec = access_codec()
    wrong_issuer = valid_payload()
    wrong_issuer["iss"] = "unexpected-issuer"
    wrong_audience = valid_payload()
    wrong_audience["aud"] = "unexpected-audience"
    missing_expiration = valid_payload()
    del missing_expiration["exp"]
    invalid_subject = valid_payload()
    invalid_subject["sub"] = "not-a-uuid"
    extended_claims = valid_payload()
    extended_claims["role"] = "owner"
    excessive_lifetime = valid_payload()
    excessive_lifetime["exp"] = int((ISSUED_AT + timedelta(hours=1)).timestamp())
    mismatched_not_before = valid_payload()
    mismatched_not_before["nbf"] = int((ISSUED_AT + timedelta(seconds=1)).timestamp())
    non_integer_expiration = valid_payload()
    non_integer_expiration["exp"] = (ISSUED_AT + ACCESS_TOKEN_TTL).timestamp()
    non_canonical_subject = valid_payload()
    non_canonical_subject["sub"] = f"{{{USER_ID}}}"

    for payload in (
        wrong_issuer,
        wrong_audience,
        missing_expiration,
        invalid_subject,
        extended_claims,
        excessive_lifetime,
        mismatched_not_before,
        non_integer_expiration,
        non_canonical_subject,
    ):
        with pytest.raises(InvalidAccessTokenError):
            codec.verify(
                AccessToken.from_transport(sign_payload(payload)),
                now=ISSUED_AT,
            )


def test_verification_enforces_the_exact_thirty_second_clock_skew() -> None:
    codec = access_codec()
    issued = codec.issue(issue_command())

    codec.verify(
        issued.token,
        now=ISSUED_AT - ACCESS_TOKEN_CLOCK_SKEW,
    )
    codec.verify(
        issued.token,
        now=issued.claims.expires_at + ACCESS_TOKEN_CLOCK_SKEW - timedelta(seconds=1),
    )

    with pytest.raises(InvalidAccessTokenError):
        codec.verify(
            issued.token,
            now=ISSUED_AT - ACCESS_TOKEN_CLOCK_SKEW - timedelta(seconds=1),
        )

    with pytest.raises(InvalidAccessTokenError):
        codec.verify(
            issued.token,
            now=issued.claims.expires_at + ACCESS_TOKEN_CLOCK_SKEW,
        )


def test_verification_requires_a_timezone_aware_utc_clock() -> None:
    codec = access_codec()
    issued = codec.issue(issue_command())

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        codec.verify(issued.token, now=ISSUED_AT.replace(tzinfo=None))


def test_configuration_rejects_a_private_key_that_does_not_match_current_public_key() -> None:
    with pytest.raises(AccessTokenConfigurationError) as exc_info:
        access_codec(
            public_keys={CURRENT_KEY_ID: public_key_for(PREVIOUS_SEED)},
        )

    rendered = str(exc_info.value)
    assert CURRENT_SEED.hex() not in rendered
    assert PREVIOUS_SEED.hex() not in rendered


def test_generation_wraps_jti_source_failure_without_exposing_details() -> None:
    sensitive_detail = "random source leaked internal signing detail"

    def failing_source() -> UUID:
        raise RuntimeError(sensitive_detail)

    codec = access_codec(jti_source=failing_source)

    with pytest.raises(AccessTokenGenerationError) as exc_info:
        codec.issue(issue_command())

    assert sensitive_detail not in str(exc_info.value)


def test_invalid_input_never_appears_in_repr_or_verification_errors() -> None:
    sensitive_value = "header.payload.signature-sensitive-material"
    wrapped = AccessToken.from_transport(sensitive_value)

    with pytest.raises(InvalidAccessTokenError) as exc_info:
        access_codec().verify(wrapped, now=ISSUED_AT)

    assert sensitive_value not in repr(wrapped)
    assert sensitive_value not in str(exc_info.value)
