"""Prometheus-format metrics for any scraper (Phase 12). Provider-independent: plain text, no client library. Disabled (404) unless
`metrics_token` is configured; the scraper must send `Authorization: Bearer <token>`. Contains counts and ages only - no tenant
names, ids, or business data."""

from __future__ import annotations

import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import text

from artify_api import metrics
from artify_api.context import Public
from artify_api.db import DbContext, platform_engine, transaction
from artify_api.errors import NotFound, Unauthenticated
from artify_api.settings import get_settings

router = APIRouter(tags=["health"])


@router.get(
    "/metrics",
    dependencies=[Depends(Public("metrics scrape; guarded by its own bearer token"))],
    include_in_schema=False,
)
def scrape(authorization: Annotated[str | None, Header()] = None) -> Response:
    token = get_settings().metrics_token
    if token is None:
        raise NotFound("Not found.")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), token.get_secret_value().encode()):
        raise Unauthenticated("A valid metrics token is required.")
    lines = [
        "# HELP artify_http_responses_total HTTP responses by status class since this process started.",
        "# TYPE artify_http_responses_total counter",
        *[f'artify_http_responses_total{{class="{k}"}} {v}' for k, v in metrics.snapshot().items()],
        "# HELP artify_process_uptime_seconds Seconds since this API process started.",
        "# TYPE artify_process_uptime_seconds gauge",
        f"artify_process_uptime_seconds {int(time.time() - metrics.STARTED_AT)}",
    ]
    try:
        with transaction(platform_engine(), DbContext()) as conn:
            t0 = time.perf_counter()
            conn.execute(text("SELECT 1"))
            db_ms = (time.perf_counter() - t0) * 1000
            by = {r.state: r.n for r in conn.execute(text("SELECT state, n FROM platform_outbox_overview"))}
            oldest = conn.execute(
                text(
                    "SELECT COALESCE(extract(epoch FROM now() - oldest), 0) FROM platform_outbox_overview WHERE state = 'pending'"
                )
            ).scalar()
            seal = conn.execute(text("SELECT * FROM app.audit_seal_status()")).mappings().one()
        lines += [
            "# TYPE artify_database_up gauge", "artify_database_up 1",
            "# TYPE artify_database_ping_milliseconds gauge", f"artify_database_ping_milliseconds {db_ms:.2f}",
            "# HELP artify_outbox_events Outbox events by state (pending events are waiting for the dispatcher).",
            "# TYPE artify_outbox_events gauge",
            *[f'artify_outbox_events{{state="{s}"}} {by.get(s, 0)}' for s in ("pending", "failed", "dispatched")],
            "# TYPE artify_outbox_oldest_pending_seconds gauge", f"artify_outbox_oldest_pending_seconds {int(oldest or 0)}",
            "# HELP artify_audit_unsealed_events Audit events of the last 7 days the sealer has not chained yet.",
            "# TYPE artify_audit_unsealed_events gauge",
            f'artify_audit_unsealed_events{{log="tenant"}} {seal["tenant_unsealed"]}',
            f'artify_audit_unsealed_events{{log="platform"}} {seal["platform_unsealed"]}',
            "# TYPE artify_audit_oldest_unsealed_seconds gauge",
            f"artify_audit_oldest_unsealed_seconds {int(seal['oldest_unsealed_seconds'] or 0)}",
        ]  # fmt: skip
    except Exception:
        lines += ["# TYPE artify_database_up gauge", "artify_database_up 0"]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
