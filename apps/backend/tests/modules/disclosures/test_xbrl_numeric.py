"""Real inline numeric conventions remain deterministic and fail closed."""

import pytest

from industry_platform.modules.disclosures.adapters.xbrl import parse_raw_xbrl
from industry_platform.modules.disclosures.domain import SecXbrlSourceKind
from industry_platform.modules.disclosures.xbrl_numeric import normalize_xbrl_number

from .test_xbrl_adapter import filing, source


@pytest.mark.parametrize(
    ("value", "format_name", "sign", "expected"),
    [
        ("281,724", "ixt:num-dot-decimal", None, "281724"),
        ("1,234.50", "ixt:numcommadot", "-", "-1234.50"),
        ("-", "ixt:zerodash", None, "0"),
        ("123.40", None, None, "123.40"),
    ],
)
def test_inline_numeric_mantissa(
    value: str, format_name: str | None, sign: str | None, expected: str
) -> None:
    assert normalize_xbrl_number(value, transform=format_name, sign=sign) == expected


@pytest.mark.parametrize("value", ["12,34", "NaN", "1e9", "1,000,00", ""])
def test_invalid_numeric_value_is_not_guessed(value: str) -> None:
    with pytest.raises(ValueError, match="XBRL"):
        normalize_xbrl_number(value, transform="ixt:num-dot-decimal", sign=None)


def test_nested_numeric_inside_large_textblock_is_still_extracted() -> None:
    body = (
        """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2023"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
      <ix:resources><xbrli:context id="I"><xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">0000320193</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:instant>2023-09-30</xbrli:instant>
      </xbrli:period></xbrli:context><xbrli:unit id="USD">
      <xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources>
      <ix:nonNumeric name="us-gaap:AccountingPoliciesTextBlock" contextRef="I">
      """
        + "narrative " * 3000
        + """
      <ix:nonFraction name="us-gaap:Assets" contextRef="I" unitRef="USD"
        scale="6" sign="-" format="ixt:num-dot-decimal">281,724</ix:nonFraction>
      </ix:nonNumeric></html>"""
    ).encode()
    batch = parse_raw_xbrl(source(body, SecXbrlSourceKind.RAW_INLINE), filing())
    assert len(batch.facts) == 1
    assert batch.facts[0].value == "-281724"
    assert batch.facts[0].scale == 6
    assert batch.facts[0].concept == "Assets"
