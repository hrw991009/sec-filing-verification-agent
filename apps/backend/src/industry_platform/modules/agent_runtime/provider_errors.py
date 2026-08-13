"""Sanitized Provider failure contracts owned by the Agent Runtime."""

import re
from enum import StrEnum
from typing import Final

from industry_platform.modules.agent_runtime.domain import RunStopReason
from industry_platform.modules.agent_runtime.model import ModelUsage

MAX_PROVIDER_RETRY_AFTER_SECONDS: Final = 86_400

_PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ModelProviderErrorCode(StrEnum):
    """Stable failure categories independent of an HTTP client or vendor SDK."""

    NOT_CONFIGURED = "provider_not_configured"
    CONFIGURATION = "provider_configuration_error"
    AUTHENTICATION_FAILED = "provider_authentication_failed"
    PERMISSION_DENIED = "provider_permission_denied"
    REQUEST_INVALID = "provider_request_invalid"
    TIMEOUT = "provider_timeout"
    RATE_LIMITED = "provider_rate_limited"
    UNAVAILABLE = "provider_unavailable"
    REJECTED = "provider_rejected"
    INVALID_RESPONSE = "invalid_provider_response"
    INCOMPLETE_RESPONSE = "incomplete_provider_response"


_RETRYABLE_PROVIDER_ERRORS: Final = frozenset(
    {
        ModelProviderErrorCode.TIMEOUT,
        ModelProviderErrorCode.RATE_LIMITED,
        ModelProviderErrorCode.UNAVAILABLE,
        ModelProviderErrorCode.INCOMPLETE_RESPONSE,
    }
)

_ERROR_STOP_REASONS: Final = {
    ModelProviderErrorCode.TIMEOUT: RunStopReason.PROVIDER_TIMEOUT,
    ModelProviderErrorCode.RATE_LIMITED: RunStopReason.PROVIDER_RATE_LIMITED,
    ModelProviderErrorCode.INVALID_RESPONSE: RunStopReason.INVALID_PROVIDER_RESPONSE,
    ModelProviderErrorCode.INCOMPLETE_RESPONSE: RunStopReason.INCOMPLETE_PROVIDER_RESPONSE,
}


class ModelProviderError(RuntimeError):
    """One safe failure that never retains prompts, secrets, headers, or raw bodies."""

    __slots__ = (
        "code",
        "http_status",
        "partial_response",
        "provider_request_id",
        "retry_after_seconds",
        "usage",
    )

    def __init__(
        self,
        code: ModelProviderErrorCode,
        *,
        provider_request_id: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
        partial_response: bool = False,
        usage: ModelUsage | None = None,
    ) -> None:
        if provider_request_id is not None and not _PROVIDER_REQUEST_ID_PATTERN.fullmatch(
            provider_request_id
        ):
            raise ValueError("Provider request ID is invalid")
        if http_status is not None and (
            isinstance(http_status, bool) or not 100 <= http_status <= 599
        ):
            raise ValueError("Provider HTTP status is invalid")
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not 0 <= retry_after_seconds <= MAX_PROVIDER_RETRY_AFTER_SECONDS
        ):
            raise ValueError("Provider retry-after value is invalid")
        if retry_after_seconds is not None and code is not ModelProviderErrorCode.RATE_LIMITED:
            raise ValueError("Only rate-limit errors may carry retry-after")
        if not isinstance(partial_response, bool):
            raise ValueError("Provider partial-response flag is invalid")

        super().__init__(code.value)
        self.code = code
        self.provider_request_id = provider_request_id
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.partial_response = partial_response
        self.usage = usage

    @property
    def retryable(self) -> bool:
        """Describe retry eligibility without performing an implicit retry."""

        response_observed = self.partial_response or self.usage is not None
        return not response_observed and self.code in _RETRYABLE_PROVIDER_ERRORS

    @property
    def stop_reason(self) -> RunStopReason:
        """Map this boundary failure to the Runtime's stable terminal reason."""

        return _ERROR_STOP_REASONS.get(self.code, RunStopReason.PROVIDER_ERROR)
