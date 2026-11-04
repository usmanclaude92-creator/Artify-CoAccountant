"""Idempotency-Key support for retried mutating requests (per user, 24h)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from artify_api.context import RequestCtx
from artify_api.errors import Unprocessable
from artify_api.ids import new_id


def run_idempotent(
    ctx: RequestCtx, key: str | None, payload: Any, fn: Callable[[], tuple[int, dict[str, Any]]]
) -> tuple[int, dict[str, Any]]:
    """Execute `fn` once per (user, key). A replay with the same payload returns the stored
    response; the same key with a different payload is rejected. No key => plain execution."""
    if key is None:
        return fn()
    if not 8 <= len(key) <= 128:
        raise Unprocessable("Idempotency-Key must be 8-128 characters.")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).digest()
    inserted = ctx.conn.execute(
        text(
            "INSERT INTO idempotency_keys (id, user_id, key, request_hash, expires_at)"
            " VALUES (:id, :u, :k, :h, now() + interval '24 hours')"
            " ON CONFLICT (user_id, key) DO NOTHING RETURNING id"
        ),
        {"id": new_id(), "u": ctx.user_id, "k": key, "h": digest},
    ).first()
    if inserted is None:
        row = ctx.conn.execute(
            text(
                "SELECT request_hash, response_status, response_body FROM idempotency_keys"
                " WHERE user_id = :u AND key = :k"
            ),
            {"u": ctx.user_id, "k": key},
        ).one()
        if bytes(row.request_hash) != digest:
            raise Unprocessable("Idempotency-Key was already used with a different request.")
        if row.response_status is None:
            raise Unprocessable("A request with this Idempotency-Key is still being processed.")
        return row.response_status, row.response_body
    status, body = fn()
    ctx.conn.execute(
        text(
            "UPDATE idempotency_keys SET response_status = :s, response_body = CAST(:b AS jsonb)"
            " WHERE user_id = :u AND key = :k"
        ),
        {"s": status, "b": json.dumps(body, default=str), "u": ctx.user_id, "k": key},
    )
    return status, body
