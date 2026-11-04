"""Transactional outbox (ADR-018): state change and job intent commit atomically; a dispatcher
moves pending rows to Celery. Worker tasks re-establish tenant context from the payload."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Connection, text

from artify_api.db import DbContext, dispatcher_engine, transaction
from artify_api.ids import new_id


def enqueue(
    conn: Connection,
    *,
    event_type: str,
    org_id: uuid.UUID | None,
    company_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    event_id = new_id()
    conn.execute(
        text(
            "INSERT INTO outbox_events (id, organization_id, company_id, event_type, payload)"
            " VALUES (:id, :org, :co, :t, CAST(:p AS jsonb))"
        ),
        {"id": event_id, "org": org_id, "co": company_id, "t": event_type, "p": json.dumps(payload or {})},
    )
    return event_id


def dispatch_pending(send: Any, batch: int = 100) -> int:
    """Dispatch up to `batch` pending events using `send(event_dict)`. Returns the number sent.

    Rows are claimed with FOR UPDATE SKIP LOCKED so multiple dispatchers can run safely."""
    sent = 0
    with transaction(dispatcher_engine(), DbContext()) as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT id, organization_id, company_id, event_type, payload FROM outbox_events"
                    " WHERE state = 'pending' AND available_at <= now()"
                    " ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED"
                ),
                {"n": batch},
            )
            .mappings()
            .all()
        )
        for row in rows:
            try:
                send(dict(row))
            except Exception:
                conn.execute(
                    text(
                        "UPDATE outbox_events SET attempts = attempts + 1,"
                        " available_at = now() + make_interval(secs => LEAST(3600, 5 * (attempts + 1) * (attempts + 1))),"
                        " state = CASE WHEN attempts + 1 >= 10 THEN 'failed' ELSE 'pending' END"
                        " WHERE id = :id"
                    ),
                    {"id": row["id"]},
                )
                continue
            conn.execute(
                text("UPDATE outbox_events SET state = 'dispatched', dispatched_at = now() WHERE id = :id"),
                {"id": row["id"]},
            )
            sent += 1
    return sent
