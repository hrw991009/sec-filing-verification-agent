"""Tests for the shared identity email normalization rule."""

import pytest

from industry_platform.modules.identity.domain import (
    InvalidEmailAddressError,
    NormalizedEmail,
)
from industry_platform.modules.identity.emails import normalize_email_address


def test_normalize_email_trims_outer_whitespace_and_lowercases() -> None:
    normalized = normalize_email_address(" \tUser.Name+TAG@Example.COM\r\n")

    assert normalized == NormalizedEmail("user.name+tag@example.com")
    assert normalize_email_address(str(normalized)) == normalized


def test_normalize_email_converts_an_international_domain_to_ascii_idna() -> None:
    normalized = normalize_email_address("User@例子.测试")

    assert normalized == NormalizedEmail("user@xn--fsqu00a.xn--0zwm56d")


def test_normalize_email_preserves_provider_specific_local_part_semantics() -> None:
    normalized = normalize_email_address("User.Name+TAG@gmail.com")

    assert normalized == NormalizedEmail("user.name+tag@gmail.com")


@pytest.mark.parametrize(
    "raw_email",
    [
        "not-an-email",
        "missing-domain@",
        "@missing-local.test",
        "用户@example.com",
    ],
)
def test_normalize_email_rejects_invalid_values_without_echoing_them(
    raw_email: str,
) -> None:
    with pytest.raises(InvalidEmailAddressError) as exc_info:
        normalize_email_address(raw_email)

    assert raw_email not in str(exc_info.value)
