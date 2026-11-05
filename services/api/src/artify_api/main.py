"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from artify_api import metrics
from artify_api.errors import install_error_handlers
from artify_api.logging_config import configure_logging, correlation_id_var
from artify_api.routers import (
    auth,
    companies,
    control_center,
    documents,
    health,
    me,
    orgs,
    platform,
    platform_admins,
    platform_billing,
    platform_ops,
    platform_orgs,
    platform_plans,
    platform_subs,
    platform_support,
    processing,
    support_grants,
    workspace,
)
from artify_api.settings import get_settings

log = logging.getLogger("artify")
API_PREFIX = "/api/v1"
# Every router mounted under API_PREFIX. Tests iterate this tuple (a FastAPI app's `.routes` only exposes wrappers).
ROUTERS = (
    health.router,
    auth.router,
    orgs.router,
    companies.router,
    control_center.router,
    workspace.router,
    documents.router,
    processing.router,
    me.router,
    platform.router,
    platform_orgs.router,
    platform_plans.router,
    platform_subs.router,
    platform_billing.router,
    platform_admins.router,
    platform_support.router,
    platform_ops.router,
    support_grants.router,
)


def _operation_id(route: APIRoute) -> str:
    if route.operation_id:
        return route.operation_id
    return f"{route.tags[0] if route.tags else 'api'}_{route.name}"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.effective_log_format, settings.log_level)
    app = FastAPI(
        title="Artify Co-Accountant API",
        version="0.1.0",
        description="Shared cloud backend. Tenant realm: /api/v1/*. Platform realm: /api/v1/platform/*.",
        generate_unique_id_function=_operation_id,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Organization-Id"],
        )

    @app.middleware("http")
    async def _common(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.correlation_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        correlation_id_var.set(request.state.correlation_id)
        started = time.perf_counter()
        response = await call_next(request)
        metrics.record_response(response.status_code)
        # Path only (never the query string); no bodies, headers or identities beyond the correlation id.
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        response.headers["X-Request-ID"] = request.state.correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if settings.environment in ("staging", "production"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    for r in ROUTERS:
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
