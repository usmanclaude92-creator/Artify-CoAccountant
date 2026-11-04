"""RFC 9457 problem+json errors with stable machine-readable codes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ApiError(Exception):
    """Base for all expected, client-visible errors."""

    status = 400
    code = "bad_request"
    title = "Bad request"

    def __init__(
        self, detail: str | None = None, *, extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra or {}
        self.headers = headers or {}


class Unauthenticated(ApiError):
    status, code, title = 401, "unauthenticated", "Authentication required"


class Forbidden(ApiError):
    status, code, title = 403, "forbidden", "Not permitted"


class MfaRequired(Forbidden):
    code, title = "mfa_required", "Multi-factor authentication is required"


class NotFound(ApiError):
    # Also used for objects outside the caller's scope: no existence disclosure (404, not 403).
    status, code, title = 404, "not_found", "Not found"


class Conflict(ApiError):
    status, code, title = 409, "conflict", "Conflict"


class Unprocessable(ApiError):
    status, code, title = 422, "unprocessable", "Unprocessable request"


class RateLimited(ApiError):
    status, code, title = 429, "rate_limited", "Too many requests"


class EntitlementExceeded(ApiError):
    """Structured denial so clients can show actionable, localizable messages."""

    status, code, title = 402, "entitlement_exceeded", "Subscription limit reached"

    def __init__(self, *, key: str, limit: int, current: int, requested: int) -> None:
        super().__init__(
            f"Limit {key} reached ({current} of {limit} in use).",
            extra={"key": key, "limit": limit, "current": current, "requested": requested},
        )


def _problem(
    request: Request,
    status: int,
    code: str,
    title: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://api.artify.example/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "instance": request.url.path,
    }
    cid = getattr(request.state, "correlation_id", None)
    if cid:
        body["correlation_id"] = cid
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _problem(request, exc.status, exc.code, exc.title, exc.detail, exc.extra, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _problem(
            request,
            422,
            "validation_error",
            "Validation failed",
            "The request did not match the expected schema.",
            {"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _problem(request, exc.status_code, code, "Request failed", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals; the correlation id lets operators find the logged traceback.
        logging.getLogger("artify.errors").error(
            "unhandled error cid=%s path=%s",
            getattr(request.state, "correlation_id", None),
            request.url.path,
            exc_info=exc,
        )
        return _problem(request, 500, "internal_error", "Internal error", "An unexpected error occurred.")
