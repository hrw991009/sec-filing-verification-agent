"""Password-policy values shared by identity workflows."""

from dataclasses import dataclass, field

from pydantic import SecretStr

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class PasswordPolicyError(ValueError):
    """Raised when a new password violates the accepted policy."""


@dataclass(frozen=True, slots=True, init=False)
class ValidatedPassword:
    """A secret that can exist only after passing the password policy."""

    _secret: SecretStr = field(repr=False)

    def __init__(self, password: SecretStr) -> None:
        plaintext = password.get_secret_value()
        password_length = len(plaintext)

        if password_length < MIN_PASSWORD_LENGTH or password_length > MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError(
                f"Password must contain between {MIN_PASSWORD_LENGTH} "
                f"and {MAX_PASSWORD_LENGTH} characters"
            )

        object.__setattr__(self, "_secret", password)

    @classmethod
    def from_secret(cls, password: SecretStr) -> "ValidatedPassword":
        """Create a validated secret without trimming or normalizing it."""

        return cls(password)

    def reveal(self) -> str:
        """Reveal plaintext only to a password-hashing adapter."""

        return self._secret.get_secret_value()
