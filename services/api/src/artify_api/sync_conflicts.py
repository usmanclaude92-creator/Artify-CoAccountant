"""Synchronization conflicts (Phase 12): record, list and resolve them. A conflict is a client write that was based on an
older revision (stale) or aimed at a record that is now locked (approved, exported, closed). The server checks the claim
itself with `sync.check` - a client cannot invent a conflict or hide one - stores what it held at that moment, and keeps
the decision (who, what, why) in an immutable row plus the audit log.

Resolving does NOT write the record. `apply_local` and `merged` only record the decision and return the revision the client
must now send as its base; the write itself goes through the normal endpoint with its own permission, concurrency and
idempotency checks. `keep_server` and `discarded` mean the local change is dropped."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from artify_api import audit
from artify_api import sync as S
from artify_api import workspace as ws
from artify_api.accounting import ser, user_perms
from artify_api.context import RequestCtx
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id

RESOLUTIONS = ("keep_server", "apply_local", "merged", "discarded")
NOTE_REQUIRED = ("apply_local", "merged")
COLS = (
    "id, item_kind, item_id, client_id, conflict_type, base_revision, server_revision, local_summary, server_summary, state,"
    " resolution, resolution_note, detected_by, detected_at, resolved_by, resolved_at, row_version"
)


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


def _view(r: dict[str, Any]) -> dict[str, Any]:
    return ser(r)


def _single(ctx: RequestCtx, kind: str, ident: str, base: int | None) -> dict[str, Any]:
    return S.check(ctx, [{"kind": kind, "id": ident, "base_revision": base}])[0]


def record(ctx: RequestCtx, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """The client says: I hold <kind,id> at <base_revision> and could not write it. The server verifies."""
    org, co = _co(ctx)
    kind, ident, client = body["kind"], str(body["id"]), body["client_id"]
    base = body.get("base_revision")
    local = body.get("local_summary") or {}
    if len(json.dumps(local)) > 4096:
        raise Unprocessable("local_summary is limited to 4 KB.", extra={"code": "summary_too_large"})
    res = _single(ctx, kind, ident, base)
    if res["state"] == "missing":
        raise NotFound("That item was not found.")
    if res["state"] == "current":
        raise Unprocessable(
            "The server copy is still at your revision: there is no conflict.", extra={"code": "no_conflict"}
        )
    ctype = res["state"]  # stale | locked
    existing = (
        ctx.conn.execute(
            text(
                f"SELECT {COLS} FROM sync_conflicts WHERE company_id = :c AND item_kind = :k AND item_id = :i"
                " AND client_id = :d AND state = 'open'"
            ),
            {"c": co, "k": kind, "i": ident, "d": client},
        )
        .mappings()
        .first()
    )
    if existing is not None:
        return 200, _view(dict(existing))
    cid = new_id()
    server = {"state": res["state"], "reason": res["reason"], "current_revision": res["current_revision"]}
    ctx.conn.execute(
        text(
            "INSERT INTO sync_conflicts (id, organization_id, company_id, item_kind, item_id, client_id, conflict_type,"
            " base_revision, server_revision, local_summary, server_summary, detected_by)"
            " VALUES (:id, :o, :c, :k, :i, :d, :t, :b, :r, CAST(:ls AS jsonb), CAST(:ss AS jsonb), :u)"
        ),
        {
            "id": cid, "o": org, "c": co, "k": kind, "i": ident, "d": client, "t": ctype, "b": base,
            "r": res["current_revision"], "ls": json.dumps(local), "ss": json.dumps(server), "u": ctx.user_id,
        },
    )  # fmt: skip
    _audit(ctx, "sync.conflict.detected", cid, {"kind": kind, "type": ctype, "client_id": client})
    return 201, get(ctx, cid)


def _audit(ctx: RequestCtx, action: str, cid: Any, summary: dict[str, Any]) -> None:
    audit.write_audit(
        ctx.conn, action=action, actor_type="user", actor_id=ctx.user_id, org_id=ctx.org_id, company_id=ctx.company_id,
        entity_type="sync_conflict", entity_id=cid, summary=summary, meta=ctx.meta,
    )  # fmt: skip


def _visible(ctx: RequestCtx, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only conflicts about records the caller may see now (kind permission + folder visibility)."""
    if not rows:
        return rows
    checked = S.check(ctx, [{"kind": r["item_kind"], "id": r["item_id"]} for r in rows])
    return [r for r, c in zip(rows, checked, strict=True) if c["state"] != "missing"]


def list_(ctx: RequestCtx, *, state: str | None, kind: str | None, limit: int) -> dict[str, Any]:
    _, co = _co(ctx)
    where, p = ["company_id = :c"], {"c": co, "n": limit * 3}
    if state:
        where.append("state = :s")
        p["s"] = state
    if kind:
        where.append("item_kind = :k")
        p["k"] = kind
    rows = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                f"SELECT {COLS} FROM sync_conflicts WHERE {' AND '.join(where)} ORDER BY detected_at DESC, id LIMIT :n"
            ),
            p,
        )
    ]
    vis = _visible(ctx, rows)[:limit]
    return {"items": [_view(r) for r in vis], "total": len(vis)}


def get(ctx: RequestCtx, cid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    r = (
        ctx.conn.execute(
            text(f"SELECT {COLS} FROM sync_conflicts WHERE company_id = :c AND id = :i"), {"c": co, "i": cid}
        )
        .mappings()
        .first()
    )
    if r is None or not _visible(ctx, [dict(r)]):
        raise NotFound("Conflict not found.")
    out = _view(dict(r))
    cur = _single(ctx, out["item_kind"], out["item_id"], None)
    out["current"] = {"state": cur["state"], "revision": cur["current_revision"], "reason": cur["reason"]}
    return out


def status(ctx: RequestCtx) -> dict[str, Any]:
    opened = list_(ctx, state="open", kind=None, limit=500)["items"]
    by: dict[str, int] = {}
    for r in opened:
        by[r["item_kind"]] = by.get(r["item_kind"], 0) + 1
    return {"open_conflicts": len(opened), "by_kind": by, "server_time": S.now_iso()}


def resolve(ctx: RequestCtx, cid: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    _, co = _co(ctx)
    cur = get(ctx, cid)  # also proves the caller may see the record
    if cur["state"] != "open":
        raise Conflict("This conflict is already resolved.", extra={"code": "already_resolved"})
    if cur["detected_by"] != str(ctx.user_id) and "sync.resolve" not in user_perms(ctx):
        raise Forbidden("Only the person who found the conflict, or someone who may resolve conflicts, can decide it.")
    resolution, note = body["resolution"], (body.get("note") or "").strip()
    if resolution not in RESOLUTIONS:
        raise Unprocessable("Unknown resolution.", extra={"code": "unknown_resolution"})
    if resolution in NOTE_REQUIRED and len(note) < 5:
        raise Unprocessable("Explain the decision (at least 5 characters).", extra={"code": "note_required"})
    if resolution in ("apply_local", "merged") and (
        cur["conflict_type"] == "locked" or cur["current"]["state"] == "locked"
    ):
        raise Conflict(
            "The record is locked; local changes cannot be applied. Use the correction workflow or keep the server copy.",
            extra={"code": "locked"},
        )
    r = ctx.conn.execute(
        text(
            "UPDATE sync_conflicts SET state = 'resolved', resolution = :r, resolution_note = :n, resolved_by = :u,"
            " resolved_at = now(), row_version = row_version + 1 WHERE id = :i AND company_id = :c AND state = 'open'"
        ),
        {"r": resolution, "n": note or None, "u": ctx.user_id, "i": cid, "c": co},
    )
    if r.rowcount != 1:
        raise Conflict("This conflict was resolved by someone else.", extra={"code": "already_resolved"})
    _audit(ctx, "sync.conflict.resolved", cid, {"resolution": resolution, "kind": cur["item_kind"]})
    out = get(ctx, cid)
    out["next_base_revision"] = out["current"]["revision"] if resolution in ("apply_local", "merged") else None
    return out
