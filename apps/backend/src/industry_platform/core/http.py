"""Safe, traceable HTTP error responses shared by API modules."""

import logging
from collections.abc import Mapping
from uuid import uuid4

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PROBLEM_MEDIA_TYPE = "application/problem+json"
TRACE_ID_HEADER = "X-Trace-ID"
TRACE_ID_STATE_KEY = "trace_id"
CACHE_CONTROL_HEADER = "Cache-Control"
PRAGMA_HEADER = "Pragma"

logger = logging.getLogger(__name__)


class ProblemDetails(BaseModel):
    """Stable, non-sensitive error contract returned by the API."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    code: str
    trace_id: str


def _new_trace_id() -> str:
    return uuid4().hex


def _trace_id_from_scope(scope: Scope) -> str:
    state = scope.get("state")

    if isinstance(state, dict):
        trace_id = state.get(TRACE_ID_STATE_KEY)
        if isinstance(trace_id, str) and trace_id:
            return trace_id

    trace_id = _new_trace_id()
    scope.setdefault("state", {})[TRACE_ID_STATE_KEY] = trace_id
    return trace_id


def get_trace_id(request: Request) -> str:
    """Read the server-generated trace ID attached to an HTTP request."""

    return _trace_id_from_scope(request.scope)


def set_no_store_headers(response: Response) -> None:
    """Prevent browsers and intermediaries from caching authentication data."""

    response.headers[CACHE_CONTROL_HEADER] = "no-store"
    response.headers[PRAGMA_HEADER] = "no-cache"


def problem_response(
    *,
    trace_id: str,
    status_code: int,
    title: str,
    code: str,
    detail: str,
    problem_type: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Serialize one sanitized error and retain only safe caller headers."""

    forwarded_headers = {
        "allow",
        "retry-after",
        "www-authenticate",
    }
    safe_headers: dict[str, str] = {
        name: value for name, value in (headers or {}).items() if name.lower() in forwarded_headers
    }
    safe_headers[TRACE_ID_HEADER] = trace_id
    safe_headers[CACHE_CONTROL_HEADER] = "no-store"
    safe_headers[PRAGMA_HEADER] = "no-cache"

    problem = ProblemDetails(
        type=problem_type,
        title=title,
        status=status_code,
        detail=detail,
        code=code,
        trace_id=trace_id,
    )

    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json"),
        headers=safe_headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def problem_openapi_response(description: str) -> dict[str, object]:
    """Document an error response with the same media type used at runtime."""

    return {
        "description": description,
        "content": {
            PROBLEM_MEDIA_TYPE: {
                "schema": ProblemDetails.model_json_schema(mode="serialization"),
            }
        },
    }


class TraceIdMiddleware:
    """Create a trusted trace ID for every HTTP request and response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        trace_id = _new_trace_id()
        scope.setdefault("state", {})[TRACE_ID_STATE_KEY] = trace_id

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[TRACE_ID_HEADER] = trace_id

            await send(message)

        await self._app(scope, receive, send_with_trace_id)


class SafeUnhandledExceptionMiddleware:
    """Convert unhandled HTTP failures without exposing exception details."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        response_started = False

        async def track_response_start(message: Message) -> None:
            nonlocal response_started

            if message["type"] == "http.response.start":
                response_started = True

            await send(message)

        try:
            await self._app(scope, receive, track_response_start)
        except Exception as error:
            if response_started:
                raise

            trace_id = _trace_id_from_scope(scope)
            logger.error(
                "Unhandled HTTP failure trace_id=%s exception_type=%s",
                trace_id,
                type(error).__name__,
            )
            response = problem_response(
                trace_id=trace_id,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                title="Internal server error",
                code="INTERNAL_SERVER_ERROR",
                detail="The server could not complete the request.",
                problem_type="urn:iip:problem:internal-server-error",
            )
            await response(scope, receive, send)


async def http_exception_handler(
    request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    """Convert framework 404/405 errors without returning raw details."""

    known_errors = {
        status.HTTP_404_NOT_FOUND: (
            "Resource not found",
            "RESOURCE_NOT_FOUND",
            "The requested resource does not exist.",
            "urn:iip:problem:resource-not-found",
        ),
        status.HTTP_405_METHOD_NOT_ALLOWED: (
            "Method not allowed",
            "METHOD_NOT_ALLOWED",
            "This HTTP method is not allowed for the requested resource.",
            "urn:iip:problem:method-not-allowed",
        ),
    }
    title, code, detail, problem_type = known_errors.get(
        error.status_code,
        (
            "HTTP request failed",
            "HTTP_REQUEST_FAILED",
            "The requested operation could not be completed.",
            "urn:iip:problem:http-request-failed",
        ),
    )

    return problem_response(
        trace_id=get_trace_id(request),
        status_code=error.status_code,
        title=title,
        code=code,
        detail=detail,
        problem_type=problem_type,
        headers=error.headers,
    )


async def request_validation_exception_handler(
    request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    """Return a generic validation failure without echoing submitted values."""

    return problem_response(
        trace_id=get_trace_id(request),
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Request validation failed",
        code="REQUEST_VALIDATION_FAILED",
        detail="One or more request fields are invalid.",
        problem_type="urn:iip:problem:request-validation-failed",
    )
