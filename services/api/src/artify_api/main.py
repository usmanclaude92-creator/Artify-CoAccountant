"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from artify_api.errors import install_error_handlers
from artify_api.routers import auth, companies, health, me, orgs, platform
from artify_api.settings import get_settings

log = logging.getLogger("artify")
API_PREFIX = "/api/v1"


def _operation_id(route: APIRoute) -> str:
    if route.operation_id:
        return route.operation_id
    return f"{route.tags[0] if route.tags else 'api'}_{route.name}"


def create_app() -> FastAPI:
    settings = get_settings()
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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if settings.environment in ("staging", "production"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    for r in (health.router, auth.router, orgs.router, companies.router, me.router, platform.router):
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
