"""Synchronisation foundation (Phase 12): a server-authoritative, permission-aware change feed and a preflight check for
queued client work.

This is NOT a complete synchronisation engine. It gives every client (Windows, mobile, Control Center) the same two things,
computed by the cloud database and never by a device clock:

* `changes`  - what changed in a company since a cursor: documents, accounting transactions, daily closings, Excel exports
               and the workspace configuration, each with its server `revision`, `status` and a stable id.
* `check`    - for items a client holds locally (with the revision it last saw), whether each is current, stale, locked or
               gone, and why. A locked item (approved transaction, closed day, closed period, archived document) is never
               silently overwritten: the client must use the normal correction workflow.

Everything is read-only. Writes still go through the existing endpoints with their own optimistic-concurrency checks
(`base_row_version`, `base_version`) and idempotency keys. Visibility follows the existing rules: folder permissions for
documents and the permission catalogue for the other kinds; anything a user may not see is reported as `missing`."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from artify_api import documents as docs
from artify_api import workspace as ws
from artify_api.accounting import ser, user_perms
from artify_api.context import RequestCtx
from artify_api.errors import Unprocessable

KINDS = ("workspace", "document", "transaction", "closing", "export")
NEEDS = {
    "workspace": "workspace.read",
    "document": "documents.read",
    "transaction": "accounting.read",
    "closing": "bookkeeping.read",
    "export": "excel.read",
}
LOCKED_TXN = ("approved", "posted", "reversed")


def encode_cursor(ts: datetime, kind: str, ident: str) -> str:
    return base64.urlsafe_b64encode(f"{ts.astimezone(UTC).isoformat()}|{kind}|{ident}".encode()).decode().rstrip("=")


def decode_cursor(raw: str | None) -> tuple[datetime, str, str]:
    if not raw:
        return datetime(1970, 1, 1, tzinfo=UTC), "", ""
    try:
        s = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        ts, kind, ident = s.split("|", 2)
        if kind and kind not in KINDS:
            raise ValueError("kind")
        return datetime.fromisoformat(ts), kind, ident
    except (ValueError, UnicodeDecodeError) as e:
        raise Unprocessable(
            "That cursor is not valid. Start again from the beginning.", extra={"code": "invalid_cursor"}
        ) from e


def _allowed_kinds(ctx: RequestCtx, wanted: list[str] | None) -> list[str]:
    perms = user_perms(ctx)
    out = [k for k in KINDS if NEEDS[k] in perms and (not wanted or k in wanted)]
    if wanted:
        bad = [k for k in wanted if k not in KINDS]
        if bad:
            raise Unprocessable(f"Unknown kind {bad[0]}.", extra={"code": "unknown_kind"})
    return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def changes(
    ctx: RequestCtx, *, cursor: str | None, limit: int, kinds: list[str] | None, overlap_seconds: int = 0
) -> dict[str, Any]:
    """Items changed after [cursor], oldest first. Pass `next_cursor` back to continue. A client that wants to be safe
    against a slow transaction committing late can ask for an `overlap_seconds` window; items are idempotent by
    (kind, id, revision), so seeing one twice is harmless."""
    assert ctx.company_id is not None
    co = ctx.company_id
    ts, ckind, cid = decode_cursor(cursor)
    ts = ts - timedelta(seconds=max(0, min(overlap_seconds, 600)))
    allowed = _allowed_kinds(ctx, kinds)
    parts: list[str] = []
    params: dict[str, Any] = {"c": co, "ts": ts, "ck": ckind, "ci": cid, "lim": limit + 1}
    if "document" in allowed:
        vis = [str(n) for n in docs.visible_node_ids(docs.doc_access(ctx))]
        params["vis"] = "{" + ",".join(vis) + "}"
        parts.append(
            "SELECT 'document' AS kind, d.id::text AS id, d.updated_at AS ts, d.row_version AS revision, d.status AS status,"
            " jsonb_build_object('node_id', d.node_id, 'category_id', d.category_id, 'current_version_no', d.current_version_no,"
            " 'sha256', d.content_sha256, 'size_bytes', d.size_bytes) AS data FROM documents d"
            " WHERE d.company_id = :c AND d.purged_at IS NULL AND d.node_id = ANY(CAST(:vis AS uuid[]))"
        )
    if "transaction" in allowed:
        parts.append(
            "SELECT 'transaction', t.id::text, t.updated_at, t.row_version, t.status,"
            " jsonb_build_object('number', t.number, 'transaction_date', t.transaction_date, 'source_document_id', t.source_document_id)"
            " FROM acct_transactions t WHERE t.company_id = :c"
        )
    if "closing" in allowed:
        parts.append(
            "SELECT 'closing', s.id::text, s.updated_at, s.row_version, s.status,"
            " jsonb_build_object('business_date', s.business_date, 'validation_status', s.validation_status, 'reopened_from_id', s.reopened_from_id)"
            " FROM bk_closing_sessions s WHERE s.company_id = :c"
        )
    if "export" in allowed:
        parts.append(
            "SELECT 'export', e.id::text, COALESCE(e.finished_at, e.created_at), 1, e.status,"
            " jsonb_build_object('template_id', e.template_id, 'version_id', e.version_id, 'mapping_id', e.mapping_id, 'sha256', e.sha256)"
            " FROM xl_exports e WHERE e.company_id = :c"
        )
    if "workspace" in allowed:
        parts.append(
            "SELECT 'workspace', r.company_id::text, r.created_at, r.version, 'active', jsonb_build_object('action', r.action)"
            " FROM workspace_revisions r WHERE r.company_id = :c AND r.version = (SELECT max(version) FROM workspace_revisions WHERE company_id = :c)"
        )
    if not parts:
        return {
            "items": [],
            "next_cursor": cursor,
            "has_more": False,
            "server_time": datetime.now(UTC).isoformat(),
            "kinds": [],
        }
    sql = (
        "SELECT * FROM ("
        + " UNION ALL ".join(parts)
        + ") x WHERE (ts, kind, id) > (:ts, :ck, :ci) ORDER BY ts, kind, id LIMIT :lim"
    )
    rows = [ws.row_dict(r) for r in ctx.conn.execute(text(sql), params)]
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "kind": r["kind"],
            "id": r["id"],
            "revision": int(r["revision"]),
            "status": r["status"],
            "changed_at": r["ts"],
            "data": r["data"],
        }
        for r in rows
    ]
    nxt = encode_cursor(rows[-1]["ts"], rows[-1]["kind"], rows[-1]["id"]) if rows else cursor
    return ser(
        {
            "items": items,
            "next_cursor": nxt,
            "has_more": more,
            "server_time": datetime.now(UTC).isoformat(),
            "kinds": allowed,
        }
    )


def check(ctx: RequestCtx, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each (kind, id, base_revision): current | stale | locked | missing. Never discloses what a user may not see."""
    assert ctx.company_id is not None
    co = ctx.company_id
    perms = user_perms(ctx)
    acc = docs.doc_access(ctx) if "documents.read" in perms else None
    vis = {str(n) for n in docs.visible_node_ids(acc)} if acc else set()
    out: list[dict[str, Any]] = []
    for it in items:
        kind, ident, base = it["kind"], str(it["id"]), it.get("base_revision")
        res: dict[str, Any] = {
            "kind": kind,
            "id": ident,
            "state": "missing",
            "current_revision": None,
            "reason": "This item is not available to you.",
        }
        out.append(res)
        if kind not in KINDS or NEEDS[kind] not in perms:
            continue
        try:
            uid = uuid.UUID(ident)
        except ValueError:
            continue
        if kind == "document":
            r = ctx.conn.execute(
                text(
                    "SELECT row_version, status, node_id FROM documents WHERE company_id = :c AND id = :i AND purged_at IS NULL"
                ),
                {"c": co, "i": uid},
            ).first()
            if r is None or str(r.node_id) not in vis:
                continue
            rev, locked = int(r.row_version), r.status in ("deleted", "archived", "quarantined")
            why = f"The document is {r.status}." if locked else None
        elif kind == "transaction":
            r = ctx.conn.execute(
                text(
                    "SELECT row_version, status, transaction_date FROM acct_transactions WHERE company_id = :c AND id = :i"
                ),
                {"c": co, "i": uid},
            ).first()
            if r is None:
                continue
            rev = int(r.row_version)
            day = ctx.conn.execute(
                text("SELECT app.bk_day_closed(:c, :d)"), {"c": co, "d": r.transaction_date}
            ).scalar()
            per = ctx.conn.execute(
                text(
                    "SELECT status FROM acct_periods WHERE company_id = :c AND start_date <= :d AND end_date >= :d ORDER BY start_date DESC LIMIT 1"
                ),
                {"c": co, "d": r.transaction_date},
            ).scalar()
            locked = r.status in LOCKED_TXN or bool(day) or per == "closed"
            why = (
                ("The transaction is " + r.status + ". Use a correction or reversal, not an overwrite.")
                if r.status in LOCKED_TXN
                else (
                    "The day is closed." if day else ("The accounting period is closed." if per == "closed" else None)
                )
            )
        elif kind == "closing":
            r = ctx.conn.execute(
                text("SELECT row_version, status FROM bk_closing_sessions WHERE company_id = :c AND id = :i"),
                {"c": co, "i": uid},
            ).first()
            if r is None:
                continue
            rev, locked = int(r.row_version), r.status in ("closed", "reopened", "cancelled")
            why = f"The closing is {r.status}; a closed day changes only by reopening it." if locked else None
        elif kind == "export":
            r = ctx.conn.execute(
                text("SELECT status FROM xl_exports WHERE company_id = :c AND id = :i"), {"c": co, "i": uid}
            ).first()
            if r is None:
                continue
            rev, locked, why = 1, True, "Exports are historical records and never change."
        else:  # workspace
            if uid != co:
                continue
            rev, locked, why = ws.current_version(ctx.conn, co), False, None
        res["current_revision"] = rev
        if locked:
            res.update(state="locked", reason=why)
        elif base is not None and int(base) != rev:
            res.update(state="stale", reason="Someone changed this after your copy. Refresh before changing it.")
        else:
            res.update(state="current", reason=None)
    return out
