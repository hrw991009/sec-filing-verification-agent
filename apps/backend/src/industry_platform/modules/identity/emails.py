"""Reusable email validation and normalization for identity workflows."""

from email_validator import EmailNotValidError, validate_email

from industry_platform.modules.identity.domain import (
    InvalidEmailAddressError,
    NormalizedEmail,
)

MAX_STORED_EMAIL_LENGTH = 320


def normalize_email_address(raw_email: str) -> NormalizedEmail:
    """Return the single canonical email representation stored by identity."""

    try:
        validated = validate_email(
            raw_email.strip(),
            allow_smtputf8=False,
            check_deliverability=False,
        )
    except EmailNotValidError:
        raise InvalidEmailAddressError from None

    if validated.ascii_email is None:
        raise InvalidEmailAddressError

    normalized = validated.ascii_email.lower()

    if len(normalized) > MAX_STORED_EMAIL_LENGTH:
        raise InvalidEmailAddressError

    return NormalizedEmail(normalized)
