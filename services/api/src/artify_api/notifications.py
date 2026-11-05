"""Notification delivery (Phase 12). The transactional outbox already carries domain events; this module turns an event into
per-person notification rows (in-app) and, if the person allows it, an e-mail.

Rules
* Notifications hold generic text and an entity reference only - never names, amounts or document content - so an e-mail
  or a lock-screen preview cannot leak business data. The app fetches the details with the person's own permissions.
* One row per (event, person): redelivery of an event never duplicates a notification or an e-mail.
* The actor is never notified of their own action.
* E-mail is attempted once per notification; the outcome (`sent` / `failed` / `skipped`) is stored. A failed e-mail does not
  fail the event and is visible in the row - there is no hidden retry queue.
* Push notifications to phones are NOT implemented (they need a provider account); the phone app reads this same list."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer

log = logging.getLogger("artify.notifications")


@dataclass(frozen=True)
class Spec:
    title: str
    body: str
    entity_type: str
    entity_key: str


SPECS: dict[str, Spec] = {
    "bookkeeping.task.assigned": Spec("A bookkeeping task was assigned to you", "Open Daily Bookkeeping to see it.", "bk_task", "task_id"),
    "bookkeeping.task.review_requested": Spec("A bookkeeping task is waiting for your review", "Open Daily Bookkeeping to review it.", "bk_task", "task_id"),
    "bookkeeping.closing.sent_back": Spec("A daily closing was sent back", "Open the closing to see what needs attention.", "bk_closing", "closing_id"),
    "bookkeeping.closing.closed": Spec("A daily closing you prepared was closed", "Open the closing to see the result.", "bk_closing", "closing_id"),
}  # fmt: skip


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def recipients(conn: Connection, event: dict[str, Any]) -> list[uuid.UUID]:
    """Who should hear about this event. Empty when the event has no human recipient or the record is gone."""
    et, p = event["event_type"], event.get("payload") or {}
    co = _uuid(event.get("company_id"))
    out: list[uuid.UUID] = []
    if co is None:
        return out
    if et == "bookkeeping.task.assigned":
        if (u := _uuid(p.get("assignee_id"))) is not None:
            out.append(u)
    elif et == "bookkeeping.task.review_requested":
        r = conn.execute(
            text("SELECT reviewer_id FROM bk_tasks WHERE company_id = :c AND id = :t"),
            {"c": co, "t": _uuid(p.get("task_id"))},
        ).first()
        if r and r.reviewer_id:
            out.append(r.reviewer_id)
    elif et in ("bookkeeping.closing.sent_back", "bookkeeping.closing.closed"):
        r = conn.execute(
            text("SELECT prepared_by, submitted_by FROM bk_closing_sessions WHERE company_id = :c AND id = :s"),
            {"c": co, "s": _uuid(p.get("closing_id"))},
        ).first()
        if r:
            out += [u for u in (r.prepared_by, r.submitted_by) if u is not None]
    actor = _uuid(p.get("actor"))
    unique: list[uuid.UUID] = []
    for u in out:
        if u != actor and u not in unique:
            unique.append(u)
    return unique


def handle_event(conn: Connection, event: dict[str, Any]) -> dict[str, int]:
    """Create the notifications for one outbox event. Runs in the tenant context (organization, and company when given)."""
    spec = SPECS.get(event["event_type"])
    result = {"created": 0, "emailed": 0, "email_failed": 0, "skipped": 0}
    if spec is None:
        return result
    org, co = _uuid(event.get("organization_id")), _uuid(event.get("company_id"))
    p = event.get("payload") or {}
    for user in recipients(conn, event):
        u = conn.execute(
            text(
                "SELECT u.email, u.status, u.email_verified_at, COALESCE(np.email_enabled, true) AS email_enabled"
                " FROM users u LEFT JOIN notification_prefs np ON np.user_id = u.id WHERE u.id = :u"
            ),
            {"u": user},
        ).first()
        if u is None or u.status != "active":
            result["skipped"] += 1
            continue
        nid = new_id()
        ins = conn.execute(
            text(
                "INSERT INTO notifications (id, organization_id, company_id, user_id, event_id, event_type, title, body, entity_type, entity_id)"
                " VALUES (:id, :o, :c, :u, :e, :t, :ti, :b, :et, :ei) ON CONFLICT (event_id, user_id) DO NOTHING"
            ),
            {
                "id": nid, "o": org, "c": co, "u": user, "e": _uuid(event["id"]), "t": event["event_type"], "ti": spec.title,
                "b": spec.body, "et": spec.entity_type, "ei": str(p.get(spec.entity_key) or "") or None,
            },
        )  # fmt: skip
        if ins.rowcount != 1:
            continue  # redelivered event: already notified
        result["created"] += 1
        if not (u.email_enabled and u.email_verified_at):
            conn.execute(text("UPDATE notifications SET email_state = 'skipped' WHERE id = :i"), {"i": nid})
            continue
        try:
            get_mailer().send(Message(to=str(u.email), template="notification", context={"title": spec.title}))
            conn.execute(text("UPDATE notifications SET email_state = 'sent' WHERE id = :i"), {"i": nid})
            result["emailed"] += 1
        except Exception as exc:
            log.error("notification e-mail failed type=%s error=%s", event["event_type"], type(exc).__name__)
            conn.execute(
                text("UPDATE notifications SET email_state = 'failed', email_error = :e WHERE id = :i"),
                {"i": nid, "e": type(exc).__name__[:200]},
            )
            result["email_failed"] += 1
    return result
