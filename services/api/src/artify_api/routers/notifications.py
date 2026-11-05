"""The caller's own notifications (Phase 12): list, mark read, and choose whether e-mail is also sent. Organization-scoped
through the X-Organization-Id header like the other member-level routes; row-level security limits every query to the
caller's own rows in that organization."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from artify_api import workspace as ws
from artify_api.accounting import ser
from artify_api.context import RequestCtx, Requires
from artify_api.errors import NotFound

router = APIRouter(tags=["notifications"])
Own = Annotated[RequestCtx, Depends(Requires("notifications.read_own", org_source="header"))]
COLS = "id, company_id, event_type, title, body, entity_type, entity_id, created_at, read_at, email_state"


class PrefsIn(BaseModel):
    email_enabled: bool


@router.get("/notifications", operation_id="notifications_list")
def list_notifications(
    ctx: Own,
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    where = "user_id = :u AND organization_id = :o" + (" AND read_at IS NULL" if unread else "")
    p: dict[str, Any] = {"u": ctx.user_id, "o": ctx.org_id, "n": limit, "off": offset}
    items = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(f"SELECT {COLS} FROM notifications WHERE {where} ORDER BY created_at DESC, id LIMIT :n OFFSET :off"), p
        )
    ]
    total = ctx.conn.execute(text(f"SELECT count(*) FROM notifications WHERE {where}"), p).scalar_one()
    unread_count = ctx.conn.execute(
        text("SELECT count(*) FROM notifications WHERE user_id = :u AND organization_id = :o AND read_at IS NULL"), p
    ).scalar_one()
    return ser({"items": items, "total": total, "unread": unread_count})


@router.post("/notifications/read-all", operation_id="notifications_read_all")
def read_all(ctx: Own) -> Any:
    r = ctx.conn.execute(
        text(
            "UPDATE notifications SET read_at = now() WHERE user_id = :u AND organization_id = :o AND read_at IS NULL"
        ),
        {"u": ctx.user_id, "o": ctx.org_id},
    )
    return {"marked": r.rowcount}


@router.get("/notifications/preferences", operation_id="notifications_preferences")
def get_prefs(ctx: Own) -> Any:
    r = ctx.conn.execute(
        text("SELECT email_enabled FROM notification_prefs WHERE user_id = :u"), {"u": ctx.user_id}
    ).first()
    return {"email_enabled": True if r is None else r.email_enabled}


@router.put("/notifications/preferences", operation_id="notifications_preferences_set")
def set_prefs(body: PrefsIn, ctx: Own) -> Any:
    ctx.conn.execute(
        text(
            "INSERT INTO notification_prefs (user_id, email_enabled) VALUES (:u, :e)"
            " ON CONFLICT (user_id) DO UPDATE SET email_enabled = :e, updated_at = now()"
        ),
        {"u": ctx.user_id, "e": body.email_enabled},
    )
    return {"email_enabled": body.email_enabled}


@router.post("/notifications/{notification_id}/read", operation_id="notifications_read")
def mark_read(notification_id: uuid.UUID, ctx: Own) -> Any:
    r = (
        ctx.conn.execute(
            text(
                "UPDATE notifications SET read_at = COALESCE(read_at, now()) WHERE id = :i AND user_id = :u AND organization_id = :o"
                f" RETURNING {COLS}"
            ),
            {"i": notification_id, "u": ctx.user_id, "o": ctx.org_id},
        )
        .mappings()
        .first()
    )
    if r is None:
        raise NotFound("Notification not found.")
    return ser(dict(r))
