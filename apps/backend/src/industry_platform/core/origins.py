"""Canonical HTTPS origins used by browser security boundaries."""

import json
import re
from ipaddress import IPv6Address, ip_address
from urllib.parse import urlsplit

_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_INVALID_ORIGIN_MESSAGE = "Browser trusted origins must be unique canonical HTTPS origins"


def canonicalize_https_origin(value: object) -> str:
    """Return one comparable origin without accepting URL-only components."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(_INVALID_ORIGIN_MESSAGE)

    parsed = urlsplit(value)

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(_INVALID_ORIGIN_MESSAGE) from None

    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or "%" in hostname
    ):
        raise ValueError(_INVALID_ORIGIN_MESSAGE)

    try:
        address = ip_address(hostname)
    except ValueError:
        try:
            canonical_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError(_INVALID_ORIGIN_MESSAGE) from None

        labels = canonical_hostname.split(".")
        if len(canonical_hostname) > 253 or any(
            not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels
        ):
            raise ValueError(_INVALID_ORIGIN_MESSAGE) from None
    else:
        canonical_hostname = address.compressed
        if isinstance(address, IPv6Address):
            canonical_hostname = f"[{canonical_hostname}]"

    port_suffix = "" if port in (None, 443) else f":{port}"
    return f"https://{canonical_hostname}{port_suffix}"


def decode_browser_trusted_origins(value: object) -> tuple[str, ...]:
    """Decode a required JSON array or controlled Python sequence of origins."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise ValueError(_INVALID_ORIGIN_MESSAGE) from None
    elif isinstance(value, (list, tuple)):
        parsed = value
    else:
        raise ValueError(_INVALID_ORIGIN_MESSAGE)

    if not parsed or not isinstance(parsed, (list, tuple)):
        raise ValueError(_INVALID_ORIGIN_MESSAGE)

    origins = tuple(canonicalize_https_origin(item) for item in parsed)
    if len(set(origins)) != len(origins):
        raise ValueError(_INVALID_ORIGIN_MESSAGE)

    return origins
