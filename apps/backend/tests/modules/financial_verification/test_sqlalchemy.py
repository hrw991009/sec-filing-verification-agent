"""Unit normalization at the SEC XBRL to financial verification boundary."""

import pytest

from industry_platform.modules.financial_verification.adapters.sqlalchemy import _financial_unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("USD", "USD"),
        ("iso4217:USD", "USD"),
        ("iso4217:USD/xbrli:shares", "USD/SHARES"),
        ("xbrli:pure", "PURE"),
    ],
)
def test_standard_xbrl_units_are_normalized_for_financial_calculation(
    raw: str | None,
    expected: str | None,
) -> None:
    assert _financial_unit(raw) == expected


def test_custom_xbrl_unit_is_not_silently_reinterpreted() -> None:
    with pytest.raises(ValueError, match="cannot be normalized"):
        _financial_unit("custom:widgets")
