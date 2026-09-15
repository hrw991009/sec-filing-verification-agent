"""Small explicit numeric transformation surface; unsupported formats stay non-calculable."""

import re
from decimal import Decimal, InvalidOperation

_DOT_FORMATS = {"ixt:num-dot-decimal", "ixt:numcommadot"}
_ZERO_FORMATS = {"ixt:fixed-zero", "ixt:zerodash", "ixt:numdash"}
_DECIMAL = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
_GROUPED = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?$")


def supported_numeric_format(value: str | None) -> bool:
    return value is None or value in _DOT_FORMATS | _ZERO_FORMATS


def normalize_xbrl_number(value: str, *, transform: str | None, sign: str | None) -> str:
    """Return the mantissa; the separate XBRL scale is applied exactly once by finance."""
    if not supported_numeric_format(transform) or sign not in {None, "-"}:
        raise ValueError("Unsupported XBRL numeric transformation")
    if transform in _ZERO_FORMATS:
        if transform != "ixt:fixed-zero" and value.strip() not in {"-", "\u2013", "\u2014"}:
            raise ValueError("Invalid XBRL zero transformation")
        return "0"
    cleaned = value.strip()
    if transform in _DOT_FORMATS:
        if not _GROUPED.fullmatch(cleaned):
            raise ValueError("Invalid XBRL grouped decimal")
        cleaned = cleaned.replace(",", "")
    elif not _DECIMAL.fullmatch(cleaned):
        raise ValueError("Invalid XBRL decimal")
    try:
        parsed = Decimal(cleaned)
        if not parsed.is_finite() or (sign is not None and parsed < 0):
            raise ValueError("Invalid XBRL numeric sign")
        return format(-parsed if sign == "-" else parsed, "f")
    except InvalidOperation:
        raise ValueError("Invalid XBRL numeric value") from None
