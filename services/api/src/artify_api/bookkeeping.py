"""Daily bookkeeping service (Phase 10), part 1: policy, checklists, tasks, the day's facts and validation.

The closing workflow, snapshots, exports and readiness are in `bookkeeping_closing`. Everything here is company-owned: nothing
is shared between companies and templates are copied. Rules enforced here AND in the database (triggers/CHECKs):
a closed day cannot change, a task follows its status graph, and the person who asked for a review cannot give it.

Business dates are calendar days in the COMPANY's own time zone (`companies.timezone`). A document uploaded at 23:30 UTC belongs
to tomorrow in Muscat; the day is never taken from the server's clock zone."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from artify_api import bk_core as core
from artify_api import bk_templates_data as tpl
from artify_api import outbox
from artify_api.accounting import audit_event, must, one, rows, ser, user_perms
from artify_api.acct_core import D
from artify_api.context import RequestCtx
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.permissions import SYSTEM_ROLES

U = uuid.UUID
FORBIDDEN_CODES = {
    "independence_required",
    "review_forbidden",
    "cancel_forbidden",
    "assign_forbidden",
    "not_your_task",
    "role_not_allowed",
}
UNPROCESSABLE_CODES = {
    "explanation_required",
    "reason_required",
    "unknown_check",
    "unknown_severity",
    "hard_check",
    "unknown_rule",
}


def fail(e: core.RuleError) -> Exception:
    """Translate a pure-core refusal into the API's error model, keeping the stable code."""
    extra = {"code": e.code}
    if e.code in FORBIDDEN_CODES:
        return Forbidden(e.message, extra=extra)
    if e.code in UNPROCESSABLE_CODES:
        return Unprocessable(e.message, extra=extra)
    return Conflict(e.message, extra=extra)


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


def _j(v: Any) -> str:
    return json.dumps(ser(v), default=str)


def my_roles(ctx: RequestCtx) -> set[str]:
    p = ctx.principal
    assert p is not None
    roles = {p.org_role} if p.org_role else set()
    cr = p.company_roles.get(ctx.company_id) if ctx.company_id else None
    if cr:
        roles.add(cr)
    return roles


def has(ctx: RequestCtx, perm: str) -> bool:
    return perm in user_perms(ctx)


# ------------------------------------------------------------------ time
def company_tz(ctx: RequestCtx) -> str:
    """The zone in which this company's business days are counted: its own setting, else the organization's, else UTC."""
    _, co = _co(ctx)
    r = one(
        ctx,
        "SELECT COALESCE(p.timezone, o.timezone, 'UTC') AS tz FROM companies c JOIN organizations o ON o.id = c.organization_id"
        " LEFT JOIN bk_policies p ON p.company_id = c.id WHERE c.id = :c",
        c=co,
    )
    return (r or {}).get("tz") or "UTC"


def today(ctx: RequestCtx) -> date:
    return core.company_today(datetime.now(UTC), company_tz(ctx))


# ------------------------------------------------------------------ audit trail
def event(
    ctx: RequestCtx,
    entity_type: str,
    entity_id: Any,
    action: str,
    *,
    business_date: date | None = None,
    reason: str | None = None,
    summary: dict[str, Any] | None = None,
    actor_type: str = "user",
) -> None:
    """One domain history row (append-only) and one platform audit record. Both in the caller's transaction."""
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO bk_audit_events (id, organization_id, company_id, entity_type, entity_id, business_date, action, actor_type, actor_id, reason, summary)"
            " VALUES (:id, :o, :c, :et, :ei, :d, :a, :at, :u, :r, CAST(:s AS jsonb))"
        ),
        {
            "id": new_id(),
            "o": org,
            "c": co,
            "et": entity_type,
            "ei": U(str(entity_id)) if entity_id else None,
            "d": business_date,
            "a": action,
            "at": actor_type,
            "u": ctx.user_id if actor_type == "user" else None,
            "r": reason,
            "s": _j(summary or {}),
        },
    )
    audit_event(
        ctx,
        f"bookkeeping.{action}",
        f"bk_{entity_type}",
        entity_id,
        {"date": str(business_date) if business_date else None, **(summary or {})},
    )


def notify(ctx: RequestCtx, event_type: str, payload: dict[str, Any]) -> None:
    """Notifications use the existing transactional outbox (no new third-party service). Payloads carry ids only, never amounts."""
    org, co = _co(ctx)
    outbox.enqueue(
        ctx.conn, event_type=event_type, org_id=org, company_id=co, payload={"actor": str(ctx.user_id), **payload}
    )


# ------------------------------------------------------------------ policy
POLICY_COLS = "company_id, require_independent_approval, require_independent_task_review, action_roles, severity_overrides, override_min_chars, timezone, updated_at, row_version"
DEFAULT_POLICY: dict[str, Any] = {
    "require_independent_approval": True,
    "require_independent_task_review": True,
    "action_roles": {},
    "severity_overrides": {},
    "override_min_chars": 10,
    "timezone": None,
}


def get_policy(ctx: RequestCtx) -> dict[str, Any]:
    _, co = _co(ctx)
    r = one(ctx, f"SELECT {POLICY_COLS} FROM bk_policies WHERE company_id = :c", c=co)
    if r is None:
        return {**DEFAULT_POLICY, "company_id": str(co), "is_default": True, "row_version": 0}
    return {**r, "is_default": False}


def policy_view(ctx: RequestCtx) -> dict[str, Any]:
    p = get_policy(ctx)
    p["checks"] = [
        {
            "code": c,
            "title": s.title,
            "default_severity": s.severity,
            "hard": s.hard,
            "severity": core.effective_severity(c, p["severity_overrides"]),
        }
        for c, s in core.CODES.items()
    ]
    p["auto_rules"] = [{"key": k, "description": v} for k, v in core.AUTO_RULES.items()]
    p["actions"] = list(core.ACTIONS)
    return p


def update_policy(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    cur = get_policy(ctx)
    base = body.get("base_row_version")
    if base is not None and base != cur["row_version"]:
        raise Conflict("The policy was changed by someone else. Reload and try again.", extra={"code": "stale_version"})
    vals = {k: body[k] for k in DEFAULT_POLICY if body.get(k) is not None}
    if "severity_overrides" in vals:
        try:
            vals["severity_overrides"] = core.validate_severity_overrides(vals["severity_overrides"])
        except core.RuleError as e:
            raise fail(e) from e
    if "action_roles" in vals:
        ar: dict[str, list[str]] = {}
        for action, roles in vals["action_roles"].items():
            if action not in core.ACTIONS:
                raise Unprocessable(f"Unknown action {action}.", extra={"code": "unknown_action"})
            for r in roles:
                if r not in SYSTEM_ROLES:
                    raise Unprocessable(f"Unknown role {r}.", extra={"code": "unknown_role"})
            ar[action] = sorted(set(roles))
        vals["action_roles"] = ar
    if "timezone" in vals:
        if vals["timezone"] == "":
            vals["timezone"] = None  # back to the organization's zone
        else:
            try:
                ZoneInfo(vals["timezone"])
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise Unprocessable(f"Unknown time zone {vals['timezone']}.", extra={"code": "unknown_timezone"}) from e
    merged = {**{k: cur[k] for k in DEFAULT_POLICY}, **vals}
    ctx.conn.execute(
        text(
            "INSERT INTO bk_policies (company_id, organization_id, require_independent_approval, require_independent_task_review, action_roles, severity_overrides, override_min_chars, timezone, updated_by)"
            " VALUES (:c, :o, :a, :t, CAST(:ar AS jsonb), CAST(:so AS jsonb), :m, :tz, :u)"
            " ON CONFLICT (company_id) DO UPDATE SET require_independent_approval = EXCLUDED.require_independent_approval,"
            " require_independent_task_review = EXCLUDED.require_independent_task_review, action_roles = EXCLUDED.action_roles,"
            " severity_overrides = EXCLUDED.severity_overrides, override_min_chars = EXCLUDED.override_min_chars, timezone = EXCLUDED.timezone, updated_by = EXCLUDED.updated_by,"
            " updated_at = now(), row_version = bk_policies.row_version + 1"
        ),
        {
            "c": co,
            "o": org,
            "a": merged["require_independent_approval"],
            "t": merged["require_independent_task_review"],
            "ar": _j(merged["action_roles"]),
            "so": _j(merged["severity_overrides"]),
            "m": merged["override_min_chars"],
            "tz": merged["timezone"],
            "u": ctx.user_id,
        },
    )
    event(ctx, "policy", co, "policy_updated", summary={"changed": sorted(vals)})
    return policy_view(ctx)


def require_action(ctx: RequestCtx, action: str) -> None:
    """The per-company role restriction on top of the permission (configurable: who may prepare/review/approve/reopen/override)."""
    pol = get_policy(ctx)
    if not core.role_allows(pol["action_roles"], action, my_roles(ctx)):
        raise Forbidden(
            f"This company only lets specific roles {action.replace('_', ' ')}. Your role is not one of them.",
            extra={"code": "role_not_allowed"},
        )


# ------------------------------------------------------------------ checklists
def list_templates() -> list[dict[str, Any]]:
    return [
        {"key": k, "name": v["name"], "description": v["description"], "items": [i["title"] for i in v["items"]]}
        for k, v in tpl.TEMPLATES.items()
    ]


def _next_pos(ctx: RequestCtx, table: str, where: str, **p: Any) -> int:
    r = one(ctx, f"SELECT COALESCE(max(position), -1) + 1 AS n FROM {table} WHERE {where}", **p)
    return int((r or {"n": 0})["n"])


def _check_role_key(role: str | None) -> None:
    if role is not None and role not in SYSTEM_ROLES:
        raise Unprocessable(f"Unknown role {role}.", extra={"code": "unknown_role"})


def _check_rule(rule: str | None) -> None:
    if rule is not None and rule not in core.AUTO_RULES:
        raise Unprocessable(f"Unknown automatic rule {rule}.", extra={"code": "unknown_rule"})


def _insert_item(ctx: RequestCtx, checklist_id: uuid.UUID, it: dict[str, Any], pos: int) -> str:
    org, co = _co(ctx)
    _check_role_key(it.get("assigned_role"))
    _check_rule(it.get("auto_rule"))
    iid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_checklist_items (id, organization_id, company_id, checklist_id, title, description, position, mandatory, assigned_role, auto_rule)"
            " VALUES (:i, :o, :c, :l, :t, :d, :p, :m, :r, :a)"
        ),
        {
            "i": iid,
            "o": org,
            "c": co,
            "l": checklist_id,
            "t": it["title"].strip(),
            "d": it.get("description"),
            "p": pos,
            "m": bool(it.get("mandatory", False)),
            "r": it.get("assigned_role"),
            "a": it.get("auto_rule"),
        },
    )
    return str(iid)


def create_checklist(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    cid = new_id()
    name = body["name"].strip()
    if one(
        ctx,
        "SELECT 1 AS x FROM bk_checklists WHERE company_id = :c AND lower(name) = lower(:n) AND archived_at IS NULL",
        c=co,
        n=name,
    ):
        raise Conflict("A checklist with this name already exists.", extra={"code": "name_taken"})
    ctx.conn.execute(
        text(
            "INSERT INTO bk_checklists (id, organization_id, company_id, name, description, position, source_template_key, created_by)"
            " VALUES (:i, :o, :c, :n, :d, :p, :s, :u)"
        ),
        {
            "i": cid,
            "o": org,
            "c": co,
            "n": name,
            "d": body.get("description"),
            "p": _next_pos(ctx, "bk_checklists", "company_id = :c", c=co),
            "s": body.get("source_template_key"),
            "u": ctx.user_id,
        },
    )
    for n, it in enumerate(body.get("items") or []):
        _insert_item(ctx, cid, it, n)
    event(
        ctx, "checklist", cid, "checklist_created", summary={"name": name, "template": body.get("source_template_key")}
    )
    return get_checklist(ctx, cid)


def create_from_template(ctx: RequestCtx, key: str) -> dict[str, Any]:
    t = tpl.TEMPLATES.get(key)
    if t is None:
        raise NotFound("Template not found.")
    _, co = _co(ctx)
    name = t["name"]
    # a second copy gets a numbered name instead of failing: the company owns the copy and may rename it
    n = 1
    while one(
        ctx,
        "SELECT 1 AS x FROM bk_checklists WHERE company_id = :c AND lower(name) = lower(:n) AND archived_at IS NULL",
        c=co,
        n=name,
    ):
        n += 1
        name = f"{t['name']} ({n})"
    return create_checklist(
        ctx, {"name": name, "description": t["description"], "items": t["items"], "source_template_key": key}
    )


def get_checklist(ctx: RequestCtx, cid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    c = must(
        ctx,
        "SELECT id, name, description, position, source_template_key, archived_at, row_version FROM bk_checklists WHERE id = :i AND company_id = :c",
        i=cid,
        c=co,
    )
    c["items"] = rows(
        ctx,
        "SELECT id, title, description, position, mandatory, assigned_role, auto_rule, archived_at FROM bk_checklist_items WHERE checklist_id = :l ORDER BY position, created_at",
        l=cid,
    )
    return c


def list_checklists(ctx: RequestCtx, include_archived: bool = False) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    ids = rows(
        ctx,
        "SELECT id FROM bk_checklists WHERE company_id = :c AND (:a OR archived_at IS NULL) ORDER BY position, created_at",
        c=co,
        a=include_archived,
    )
    return [get_checklist(ctx, U(r["id"])) for r in ids]


def update_checklist(ctx: RequestCtx, cid: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    _, co = _co(ctx)
    cur = must(
        ctx, "SELECT id, name, row_version FROM bk_checklists WHERE id = :i AND company_id = :c FOR UPDATE", i=cid, c=co
    )
    if body.get("base_row_version") is not None and body["base_row_version"] != cur["row_version"]:
        raise Conflict(
            "The checklist was changed by someone else. Reload and try again.", extra={"code": "stale_version"}
        )
    sets, p = [], {"i": cid}
    if "name" in body and body["name"] is not None:
        nm = body["name"].strip()
        if one(
            ctx,
            "SELECT 1 AS x FROM bk_checklists WHERE company_id = :c AND lower(name) = lower(:n) AND archived_at IS NULL AND id <> :i",
            c=co,
            n=nm,
            i=cid,
        ):
            raise Conflict("A checklist with this name already exists.", extra={"code": "name_taken"})
        sets.append("name = :n")
        p["n"] = nm
    if "description" in body:
        sets.append("description = :d")
        p["d"] = body["description"]
    if body.get("position") is not None:
        sets.append("position = :p")
        p["p"] = body["position"]
    if body.get("archived") is True:
        sets.append("archived_at = COALESCE(archived_at, now())")
    elif body.get("archived") is False:
        sets.append("archived_at = NULL")
    if sets:
        ctx.conn.execute(
            text(
                f"UPDATE bk_checklists SET {', '.join(sets)}, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            p,
        )
        event(
            ctx,
            "checklist",
            cid,
            "checklist_updated",
            summary={"fields": sorted(k for k in body if k != "base_row_version")},
        )
    return get_checklist(ctx, cid)


def add_item(ctx: RequestCtx, cid: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    _, co = _co(ctx)
    must(
        ctx,
        "SELECT id FROM bk_checklists WHERE id = :i AND company_id = :c AND archived_at IS NULL FOR UPDATE",
        i=cid,
        c=co,
    )
    pos = body.get("position")
    iid = _insert_item(
        ctx, cid, body, pos if pos is not None else _next_pos(ctx, "bk_checklist_items", "checklist_id = :l", l=cid)
    )
    event(ctx, "checklist_item", iid, "checklist_item_added", summary={"checklist": str(cid), "title": body["title"]})
    return get_checklist(ctx, cid)


def update_item(ctx: RequestCtx, item_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    _, co = _co(ctx)
    it = must(
        ctx,
        "SELECT id, checklist_id FROM bk_checklist_items WHERE id = :i AND company_id = :c FOR UPDATE",
        i=item_id,
        c=co,
    )
    sets, p = [], {"i": item_id}
    for col in ("title", "description", "mandatory", "position"):
        if col in body and (body[col] is not None or col == "description"):
            sets.append(f"{col} = :{col}")
            p[col] = body[col].strip() if col == "title" else body[col]
    for col, check in (("assigned_role", _check_role_key), ("auto_rule", _check_rule)):
        if col in body:
            check(body[col])
            sets.append(f"{col} = :{col}")
            p[col] = body[col]
    if body.get("archived") is True:
        sets.append("archived_at = COALESCE(archived_at, now())")
    elif body.get("archived") is False:
        sets.append("archived_at = NULL")
    if sets:
        ctx.conn.execute(text(f"UPDATE bk_checklist_items SET {', '.join(sets)}, updated_at = now() WHERE id = :i"), p)
        event(ctx, "checklist_item", item_id, "checklist_item_updated", summary={"fields": sorted(body)})
    return get_checklist(ctx, U(it["checklist_id"]))


def reorder_items(ctx: RequestCtx, cid: uuid.UUID, ordered_ids: list[uuid.UUID]) -> dict[str, Any]:
    _, co = _co(ctx)
    cur = {
        r["id"]
        for r in rows(ctx, "SELECT id FROM bk_checklist_items WHERE checklist_id = :l AND company_id = :c", l=cid, c=co)
    }
    if not cur:
        must(ctx, "SELECT id FROM bk_checklists WHERE id = :i AND company_id = :c", i=cid, c=co)
    if {str(i) for i in ordered_ids} != cur or len(set(ordered_ids)) != len(ordered_ids):
        raise Unprocessable("Send every item of the checklist exactly once.", extra={"code": "order_mismatch"})
    for n, i in enumerate(ordered_ids):
        ctx.conn.execute(text("UPDATE bk_checklist_items SET position = :p WHERE id = :i"), {"p": n, "i": i})
    event(ctx, "checklist", cid, "checklist_reordered")
    return get_checklist(ctx, cid)


def assert_day_open(ctx: RequestCtx, d: date) -> None:
    _, co = _co(ctx)
    r = one(ctx, "SELECT app.bk_day_closed(:c, :d) AS closed", c=co, d=d)
    if r and r["closed"]:
        raise Conflict(
            f"The day {d.isoformat()} is closed. Reopen it before changing it.", extra={"code": "day_closed"}
        )


def complete_item(ctx: RequestCtx, item_id: uuid.UUID, d: date, note: str | None) -> dict[str, Any]:
    org, co = _co(ctx)
    it = must(
        ctx,
        "SELECT i.id, i.title, i.assigned_role, i.auto_rule, i.archived_at, l.archived_at AS list_archived FROM bk_checklist_items i"
        " JOIN bk_checklists l ON l.id = i.checklist_id WHERE i.id = :i AND i.company_id = :c",
        i=item_id,
        c=co,
    )
    if it["archived_at"] or it["list_archived"]:
        raise Conflict("This checklist item is archived.", extra={"code": "archived"})
    if it["auto_rule"]:
        raise Conflict(
            "This item completes itself when its rule is met; it cannot be ticked by hand.", extra={"code": "auto_item"}
        )
    if d > today(ctx):
        raise Conflict("A future day cannot be checked off.", extra={"code": "future_date"})
    if (
        it["assigned_role"]
        and it["assigned_role"] not in my_roles(ctx)
        and not has(ctx, "bookkeeping.checklists.manage")
    ):
        raise Forbidden(
            f"This item is assigned to the {it['assigned_role'].replace('_', ' ')} role.",
            extra={"code": "role_not_allowed"},
        )
    assert_day_open(ctx, d)
    ctx.conn.execute(
        text(
            "INSERT INTO bk_checklist_completions (id, organization_id, company_id, item_id, business_date, completed_by, note)"
            " VALUES (:i, :o, :c, :it, :d, :u, :n) ON CONFLICT (item_id, business_date) DO NOTHING"
        ),
        {"i": new_id(), "o": org, "c": co, "it": item_id, "d": d, "u": ctx.user_id, "n": note},
    )
    event(ctx, "checklist_item", item_id, "checklist_item_completed", business_date=d, summary={"title": it["title"]})
    return {"item_id": str(item_id), "business_date": d.isoformat(), "done": True}


def uncomplete_item(ctx: RequestCtx, item_id: uuid.UUID, d: date) -> dict[str, Any]:
    _, co = _co(ctx)
    must(ctx, "SELECT id FROM bk_checklist_items WHERE id = :i AND company_id = :c", i=item_id, c=co)
    assert_day_open(ctx, d)
    done = one(
        ctx,
        "SELECT completed_by FROM bk_checklist_completions WHERE item_id = :i AND business_date = :d",
        i=item_id,
        d=d,
    )
    if done is None:
        raise NotFound("This item was not ticked for that day.")
    if (
        str(done["completed_by"]) != str(ctx.user_id)
        and not has(ctx, "bookkeeping.checklists.manage")
        and not has(ctx, "bookkeeping.close.review")
    ):
        raise Forbidden("Only the person who ticked an item, or a reviewer, can undo it.", extra={"code": "not_yours"})
    ctx.conn.execute(
        text("DELETE FROM bk_checklist_completions WHERE item_id = :i AND business_date = :d"), {"i": item_id, "d": d}
    )
    event(ctx, "checklist_item", item_id, "checklist_item_unticked", business_date=d)
    return {"item_id": str(item_id), "business_date": d.isoformat(), "done": False}


# ------------------------------------------------------------------ the day's facts
# Artify-defined transaction types that normally rest on a source document (this is accounting vocabulary, not customer structure)
DOCUMENT_EXPECTED = (
    "sales_invoice", "sales_credit_note", "customer_receipt", "customer_refund", "purchase_invoice", "purchase_credit_note",
    "supplier_payment", "supplier_refund", "cash_expense", "bank_expense", "employee_expense", "general_expense",
)  # fmt: skip
IN_FLIGHT = ("queued", "preparing_pages", "ocr_processing", "classifying", "extracting", "validating", "suggesting")


def _facts_transactions(ctx: RequestCtx, f: core.DayFacts, d: date) -> None:
    _, co = _co(ctx)
    cfg = one(ctx, "SELECT tolerance FROM acct_configurations WHERE company_id = :c", c=co)
    tol = D((cfg or {}).get("tolerance") or 0)
    txns = rows(
        ctx,
        "SELECT id, number, status, duplicate_status, validation, tax_amount FROM acct_transactions"
        " WHERE company_id = :c AND transaction_date = :d AND reversal_of_id IS NULL ORDER BY number",
        c=co,
        d=d,
    )
    for t in txns:
        f.txn_counts[t["status"]] = f.txn_counts.get(t["status"], 0) + 1
    live = [t for t in txns if t["status"] not in ("rejected",)]
    f.awaiting = [
        {"id": t["id"], "number": t["number"], "status": t["status"]}
        for t in live
        if t["status"] in ("awaiting_review", "awaiting_approval")
    ]
    f.drafts = [{"id": t["id"], "number": t["number"], "status": t["status"]} for t in live if t["status"] == "draft"]
    for t in live:
        if t["status"] in ("draft", "awaiting_review", "awaiting_approval"):
            codes = [i.get("code", "?") for i in (t["validation"] or []) if i.get("blocking")]
            if codes:
                f.blocking_validation.append({"id": t["id"], "number": t["number"], "codes": codes})
    undecided = {
        r["transaction_id"]
        for r in rows(
            ctx,
            "SELECT DISTINCT transaction_id FROM acct_duplicate_matches WHERE company_id = :c AND decision IS NULL",
            c=co,
        )
    }
    f.duplicates = [
        {"id": t["id"], "number": t["number"], "level": t["duplicate_status"]}
        for t in live
        if t["duplicate_status"] != "none" and t["status"] != "reversed" and t["id"] in undecided
    ]
    f.tax_recorded = any(D(t["tax_amount"]) > 0 for t in live)
    # approved records: the journal exists and balances (recomputed from its lines, not from the stored header)
    for r in rows(
        ctx,
        "SELECT t.id, t.number, j.id AS journal_id,"
        " COALESCE((SELECT sum(l.debit) FROM acct_journal_lines l WHERE l.journal_id = j.id), 0) AS debit,"
        " COALESCE((SELECT sum(l.credit) FROM acct_journal_lines l WHERE l.journal_id = j.id), 0) AS credit"
        " FROM acct_transactions t LEFT JOIN acct_journals j ON j.transaction_id = t.id"
        " WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status IN ('approved', 'posted') ORDER BY t.number",
        c=co,
        d=d,
    ):
        if r["journal_id"] is None:
            f.no_journal.append({"id": r["id"], "number": r["number"]})
        elif D(r["debit"]) != D(r["credit"]):
            f.unbalanced.append(
                {
                    "id": r["id"],
                    "number": r["number"],
                    "debit": format(D(r["debit"]), "f"),
                    "credit": format(D(r["credit"]), "f"),
                }
            )
    for r in rows(
        ctx,
        "SELECT t.id, t.number, t.tax_amount AS header, COALESCE((SELECT sum(l.tax_amount) FROM acct_transaction_lines l WHERE l.transaction_id = t.id), 0) AS lines"
        " FROM acct_transactions t WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status <> 'rejected' ORDER BY t.number",
        c=co,
        d=d,
    ):
        if abs(D(r["header"]) - D(r["lines"])) > tol:
            f.tax_mismatch.append(
                {
                    "id": r["id"],
                    "number": r["number"],
                    "header": format(D(r["header"]), "f"),
                    "lines": format(D(r["lines"]), "f"),
                }
            )
    for r in rows(
        ctx,
        "SELECT t.id, t.number, c.code AS tax_code, c.id AS tax_code_id, c.is_active, c.verification_status"
        " FROM acct_transaction_lines l JOIN acct_transactions t ON t.id = l.transaction_id JOIN acct_tax_codes c ON c.id = l.tax_code_id"
        " WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status <> 'rejected' AND l.tax_amount > 0 ORDER BY t.number",
        c=co,
        d=d,
    ):
        if not r["is_active"]:
            f.inactive_tax.append({"id": r["id"], "number": r["number"], "tax_code": r["tax_code"]})
    for r in rows(
        ctx,
        "SELECT DISTINCT t.id, t.number, a.code AS account FROM acct_transactions t"
        " JOIN (SELECT transaction_id, account_id FROM acct_transaction_lines WHERE account_id IS NOT NULL"
        "       UNION SELECT j.transaction_id, jl.account_id FROM acct_journals j JOIN acct_journal_lines jl ON jl.journal_id = j.id) x ON x.transaction_id = t.id"
        " JOIN acct_accounts a ON a.id = x.account_id"
        " WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status <> 'rejected' AND NOT a.is_active ORDER BY t.number",
        c=co,
        d=d,
    ):
        f.inactive_account.append({"id": r["id"], "number": r["number"], "account": r["account"]})
    for r in rows(
        ctx,
        "SELECT id, number FROM acct_transactions WHERE company_id = :c AND transaction_date = :d AND reversal_of_id IS NULL AND status <> 'rejected'"
        " AND source_document_id IS NULL AND txn_type = ANY(CAST(:ty AS text[])) ORDER BY number",
        c=co,
        d=d,
        ty=list(DOCUMENT_EXPECTED),
    ):
        f.no_source_document.append({"id": r["id"], "number": r["number"]})
    unv: dict[str, dict[str, Any]] = {}
    for r in rows(
        ctx,
        "SELECT c.id AS tax_code_id, c.code AS tax_code, count(DISTINCT t.id) AS n FROM acct_transaction_lines l"
        " JOIN acct_transactions t ON t.id = l.transaction_id JOIN acct_tax_codes c ON c.id = l.tax_code_id"
        " WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status <> 'rejected' AND l.tax_amount > 0"
        " AND c.verification_status = 'unverified' GROUP BY c.id, c.code ORDER BY c.code",
        c=co,
        d=d,
    ):
        unv[r["tax_code_id"]] = {"id": r["tax_code_id"], "tax_code": r["tax_code"], "count": int(r["n"])}
    f.unverified_tax = list(unv.values())


def _facts_documents(ctx: RequestCtx, f: core.DayFacts, d: date, tz: str) -> None:
    """Company-wide COUNTS, so a closing never depends on who runs it. No document id or name is stored in an exception:
    persisted exceptions are read by people who may not see every folder (the queue lists documents folder by folder)."""
    _, co = _co(ctx)
    docs = rows(
        ctx,
        "SELECT d.id, d.status,"
        " (SELECT j.status FROM processing_jobs j WHERE j.document_id = d.id ORDER BY j.created_at DESC LIMIT 1) AS job_status,"
        " (SELECT v.scan_status FROM document_versions v WHERE v.document_id = d.id AND v.version_no = d.current_version_no) AS scan"
        " FROM documents d WHERE d.company_id = :c AND d.status IN ('active', 'quarantined') AND (d.created_at AT TIME ZONE :tz)::date = :d",
        c=co,
        tz=tz,
        d=d,
    )
    c = {
        "total": 0,
        "infected": 0,
        "quarantined": 0,
        "unprocessed": 0,
        "failed": 0,
        "needs_manual": 0,
        "awaiting_review": 0,
    }
    for doc in docs:
        c["total"] += 1
        if doc["scan"] == "infected":
            c["infected"] += 1
        if doc["status"] == "quarantined":
            c["quarantined"] += 1
            continue
        js = doc["job_status"]
        if js is None or js in IN_FLIGHT:
            c["unprocessed"] += 1
        elif js == "failed":
            c["failed"] += 1
        elif js == "needs_manual":
            c["needs_manual"] += 1
        elif js == "awaiting_review":
            c["awaiting_review"] += 1
    f.docs = c


def build_facts(ctx: RequestCtx, d: date) -> core.DayFacts:
    _, co = _co(ctx)
    tz = company_tz(ctx)
    cfg = one(
        ctx,
        "SELECT tax_registered, jurisdiction_code, require_open_period FROM acct_configurations WHERE company_id = :c",
        c=co,
    )
    f = core.DayFacts(
        business_date=d,
        today=core.company_today(datetime.now(UTC), tz),
        accounting_configured=cfg is not None,
        tax_configured=bool(cfg and (cfg["tax_registered"] or cfg["jurisdiction_code"])),
    )
    if cfg is not None:
        _facts_transactions(ctx, f, d)
        already_closed = bool((one(ctx, "SELECT app.bk_day_closed(:c, :d) AS x", c=co, d=d) or {}).get("x"))
        per = one(
            ctx,
            "SELECT name, status FROM acct_periods WHERE company_id = :c AND start_date <= :d AND end_date >= :d ORDER BY start_date DESC LIMIT 1",
            c=co,
            d=d,
        )
        f.has_period = per is not None
        f.period_required = bool(cfg["require_open_period"]) and not already_closed
        if per is not None:
            f.period_name = per["name"]
            f.period_closed = per["status"] == "closed" and not already_closed
    _facts_documents(ctx, f, d, tz)
    f.sync_reports = rows(
        ctx,
        "SELECT client_id, pending_uploads, failed_uploads, pending_changes, conflicts, last_sync_at, reported_at FROM bk_sync_reports WHERE company_id = :c AND business_date = :d ORDER BY client_id",
        c=co,
        d=d,
    )
    f.blocking_tasks = rows(
        ctx,
        "SELECT id, title, status FROM bk_tasks WHERE company_id = :c AND business_date = :d AND blocks_closing AND status NOT IN ('completed', 'cancelled') ORDER BY created_at",
        c=co,
        d=d,
    )
    f.overdue_tasks = rows(
        ctx,
        "SELECT id, title, status, due_date FROM bk_tasks WHERE company_id = :c AND due_date <= :d AND status NOT IN ('completed', 'cancelled') ORDER BY due_date, created_at",
        c=co,
        d=d,
    )
    return f


def checklist_for_day(ctx: RequestCtx, d: date, facts: core.DayFacts, issues: list[core.Issue]) -> list[dict[str, Any]]:
    """Active checklists with each item's state for the day: ticked by a person, or its automatic rule is met right now."""
    _, co = _co(ctx)
    done = {
        r["item_id"]
        for r in rows(
            ctx, "SELECT item_id FROM bk_checklist_completions WHERE company_id = :c AND business_date = :d", c=co, d=d
        )
    }
    lists = []
    for c in rows(
        ctx,
        "SELECT id, name FROM bk_checklists WHERE company_id = :c AND archived_at IS NULL ORDER BY position, created_at",
        c=co,
    ):
        items = rows(
            ctx,
            "SELECT id, title, description, mandatory, assigned_role, auto_rule FROM bk_checklist_items WHERE checklist_id = :l AND archived_at IS NULL ORDER BY position, created_at",
            l=U(c["id"]),
        )
        lists.append(
            {
                "id": c["id"],
                "name": c["name"],
                "items": core.checklist_state(items, done, lambda rule: core.auto_rule_met(rule, facts, issues)),
            }
        )
    return lists


def evaluate_day(ctx: RequestCtx, d: date) -> dict[str, Any]:
    """The whole deterministic evaluation of a day: facts -> issues -> checklist -> issues again (mandatory items) -> result.
    Pure reads; nothing is stored here."""
    pol = get_policy(ctx)
    facts = build_facts(ctx, d)
    base = core.validate_day(facts, pol["severity_overrides"])
    lists = checklist_for_day(ctx, d, facts, base)
    facts.checklist_open = [
        {"id": i["id"], "title": i["title"], "list": cl["name"]}
        for cl in lists
        for i in cl["items"]
        if i["mandatory"] and not i["done"]
    ]
    issues = core.validate_day(facts, pol["severity_overrides"])
    lists = checklist_for_day(ctx, d, facts, issues)  # auto rules may depend on the final issue list
    return {"facts": facts, "issues": issues, "checklists": lists, "policy": pol}


def facts_digest(facts: core.DayFacts, issues: list[core.Issue]) -> str:
    """Fingerprint of what validation looked at; a changed day gives a different digest (used to detect a stale validation)."""
    return core.payload_digest(
        {
            "txn": facts.txn_counts,
            "awaiting": [x["id"] for x in facts.awaiting],
            "drafts": [x["id"] for x in facts.drafts],
            "docs": facts.docs,
            "sync": [
                (r["client_id"], r["pending_uploads"], r["failed_uploads"], r["pending_changes"], r["conflicts"])
                for r in facts.sync_reports
            ],
            "issues": sorted(i.fingerprint + ":" + i.severity for i in issues),
        }
    )


# ------------------------------------------------------------------ exceptions
def sync_exceptions(ctx: RequestCtx, d: date, issues: list[core.Issue]) -> dict[str, str]:
    """Reconcile stored exceptions with the issues found now. Returns fingerprint -> status for the evaluation.

    New issue -> open. Still present -> keep the person's decision unless the severity got worse. Gone -> resolved."""
    org, co = _co(ctx)
    existing = {
        r["fingerprint"]: r
        for r in rows(
            ctx,
            "SELECT id, fingerprint, severity, status FROM bk_exceptions WHERE company_id = :c AND business_date = :d",
            c=co,
            d=d,
        )
    }
    now_fp = set()
    out: dict[str, str] = {}
    rank = {s: n for n, s in enumerate(core.SEVERITIES)}
    for i in issues:
        now_fp.add(i.fingerprint)
        ex = existing.get(i.fingerprint)
        if ex is None:
            ctx.conn.execute(
                text(
                    "INSERT INTO bk_exceptions (id, organization_id, company_id, business_date, code, severity, subject_type, subject_id, fingerprint, message, data)"
                    " VALUES (:id, :o, :c, :d, :code, :sev, :st, :si, :fp, :m, CAST(:data AS jsonb))"
                ),
                {
                    "id": new_id(),
                    "o": org,
                    "c": co,
                    "d": d,
                    "code": i.code,
                    "sev": i.severity,
                    "st": i.subject_type,
                    "si": i.subject_id,
                    "fp": i.fingerprint,
                    "m": i.message[:500],
                    "data": _j(i.data),
                },
            )
            out[i.fingerprint] = "open"
            continue
        status = ex["status"]
        reopen = status == "resolved" or (
            status in ("acknowledged", "overridden") and rank[i.severity] > rank[ex["severity"]]
        )
        if reopen:
            status = "open"
        ctx.conn.execute(
            text(
                "UPDATE bk_exceptions SET severity = :sev, message = :m, data = CAST(:data AS jsonb), status = :s, last_seen_at = now(),"
                " resolved_by = CASE WHEN :s = 'open' THEN NULL ELSE resolved_by END, resolved_at = CASE WHEN :s = 'open' THEN NULL ELSE resolved_at END,"
                " resolution_note = CASE WHEN :s = 'open' THEN NULL ELSE resolution_note END WHERE id = :i"
            ),
            {"sev": i.severity, "m": i.message[:500], "data": _j(i.data), "s": status, "i": U(ex["id"])},
        )
        out[i.fingerprint] = status
    for fp, ex in existing.items():
        if fp not in now_fp and ex["status"] != "resolved":
            ctx.conn.execute(
                text(
                    "UPDATE bk_exceptions SET status = 'resolved', resolved_at = now(), last_seen_at = now() WHERE id = :i"
                ),
                {"i": U(ex["id"])},
            )
    return out


def exception_statuses(ctx: RequestCtx, d: date) -> dict[str, str]:
    _, co = _co(ctx)
    return {
        r["fingerprint"]: r["status"]
        for r in rows(
            ctx, "SELECT fingerprint, status FROM bk_exceptions WHERE company_id = :c AND business_date = :d", c=co, d=d
        )
    }


def list_exceptions(ctx: RequestCtx, d: date, *, status: str | None = None) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return rows(
        ctx,
        "SELECT e.id, e.business_date, e.code, e.severity, e.subject_type, e.subject_id, e.message, e.data, e.status, e.resolution_note, e.resolved_at,"
        " e.first_seen_at, e.last_seen_at, (SELECT u.display_name FROM users u WHERE u.id = e.resolved_by) AS resolved_by_name FROM bk_exceptions e"
        " WHERE e.company_id = :c AND e.business_date = :d AND (CAST(:s AS text) IS NULL OR e.status = :s)"
        " ORDER BY CASE e.severity WHEN 'critical' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, e.code, e.subject_id",
        c=co,
        d=d,
        s=status,
    )


def decide_exception(ctx: RequestCtx, exception_id: uuid.UUID, action: str, note: str | None) -> dict[str, Any]:
    """Acknowledge (warning/error; reviewer) or override (critical but not an integrity check; authorised person). Always explained, always audited."""
    _, co = _co(ctx)
    ex = must(
        ctx,
        "SELECT id, business_date, code, severity, status, fingerprint FROM bk_exceptions WHERE id = :i AND company_id = :c FOR UPDATE",
        i=exception_id,
        c=co,
    )
    d = ex["business_date"]
    assert_day_open(ctx, d)
    if ex["status"] != "open":
        raise Conflict(f"This exception is already {ex['status']}.", extra={"code": "not_open"})
    need = "bookkeeping.close.override" if action == "override" else "bookkeeping.close.review"
    if not has(ctx, need):
        raise Forbidden("You do not have permission for this decision.", extra={"code": "permission"})
    require_action(ctx, "override" if action == "override" else "review")
    pol = get_policy(ctx)
    try:
        core.can_resolve(ex["severity"], ex["code"], action, note=note, min_chars=pol["override_min_chars"])
    except core.RuleError as e:
        raise fail(e) from e
    new = "overridden" if action == "override" else "acknowledged"
    ctx.conn.execute(
        text(
            "UPDATE bk_exceptions SET status = :s, resolved_by = :u, resolved_at = now(), resolution_note = :n WHERE id = :i"
        ),
        {"s": new, "u": ctx.user_id, "n": (note or "").strip() or None, "i": exception_id},
    )
    sess = one(
        ctx,
        "SELECT id, status FROM bk_closing_sessions WHERE company_id = :c AND business_date = :d AND status IN ('draft', 'in_progress', 'ready_for_review')",
        c=co,
        d=d,
    )
    if sess is not None:
        ctx.conn.execute(
            text(
                "INSERT INTO bk_closing_approvals (id, organization_id, company_id, session_id, action, from_status, to_status, actor_type, actor_id, note)"
                " VALUES (:i, :o, :c, :s, 'override', :f, :f, 'user', :u, :n)"
            ),
            {
                "i": new_id(),
                "o": ctx.org_id,
                "c": co,
                "s": U(sess["id"]),
                "f": sess["status"],
                "u": ctx.user_id,
                "n": f"{ex['code']} {new}: {(note or '').strip()}"[:500],
            },
        )
    event(
        ctx,
        "exception",
        exception_id,
        f"exception_{new}",
        business_date=d,
        reason=(note or "").strip() or None,
        summary={"code": ex["code"], "severity": ex["severity"]},
    )
    return must(
        ctx,
        "SELECT id, code, severity, status, message, resolution_note FROM bk_exceptions WHERE id = :i",
        i=exception_id,
    )


# ------------------------------------------------------------------ tasks
TASK_COLS = (
    "t.id, t.title, t.description, t.status, t.priority, t.category, t.reviewer_id, t.business_date, t.due_date, t.requires_review, t.blocks_closing, t.subject_type, t.subject_id,"
    " t.assignee_id, t.review_requested_by, t.approved_by, t.rejection_reason, t.created_at, t.updated_at, t.completed_at, t.row_version,"
    " (SELECT u.display_name FROM users u WHERE u.id = t.assignee_id) AS assignee_name,"
    " (SELECT u.display_name FROM users u WHERE u.id = t.reviewer_id) AS reviewer_name,"
    " (SELECT u.display_name FROM users u WHERE u.id = t.created_by) AS created_by_name"
)


def _can_be_assigned(ctx: RequestCtx, user_id: uuid.UUID) -> bool:
    org, co = _co(ctx)
    r = one(
        ctx,
        "SELECT 1 AS x FROM organization_memberships m JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o AND m.user_id = :u AND m.status = 'active'"
        " AND (r.key IN ('firm_owner', 'practice_admin', 'practice_manager') OR EXISTS (SELECT 1 FROM accountant_assignments a WHERE a.company_id = :c AND a.user_id = m.user_id))"
        " UNION SELECT 1 FROM company_memberships WHERE company_id = :c AND user_id = :u AND status = 'active'",
        o=org,
        c=co,
        u=user_id,
    )
    return r is not None


def _assignment(ctx: RequestCtx, tid: uuid.UUID, assignee: uuid.UUID | None) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO bk_task_assignments (id, organization_id, company_id, task_id, assignee_id, assigned_by) VALUES (:i, :o, :c, :t, :a, :u)"
        ),
        {"i": new_id(), "o": org, "c": co, "t": tid, "a": assignee, "u": ctx.user_id},
    )


def create_task(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    tid = new_id()
    assignee = body.get("assignee_id")
    if assignee is not None and not _can_be_assigned(ctx, U(str(assignee))):
        raise Unprocessable("That person cannot be given work in this company.", extra={"code": "invalid_assignee"})
    reviewer = body.get("reviewer_id")
    if reviewer is not None and not _can_be_assigned(ctx, U(str(reviewer))):
        raise Unprocessable("That person cannot review work in this company.", extra={"code": "invalid_reviewer"})
    if body.get("subject_type") and body.get("subject_id"):
        _check_subject(ctx, body["subject_type"], U(str(body["subject_id"])))
    ctx.conn.execute(
        text(
            "INSERT INTO bk_tasks (id, organization_id, company_id, title, description, status, priority, category, reviewer_id, business_date, due_date, requires_review,"
            " blocks_closing, subject_type, subject_id, assignee_id, created_by)"
            " VALUES (:i, :o, :c, :t, :d, :s, :p, :cat, :rv, :bd, :dd, :rr, :bc, :st, :si, :a, :u)"
        ),
        {
            "i": tid,
            "o": org,
            "c": co,
            "t": body["title"].strip(),
            "d": body.get("description"),
            "s": "assigned" if assignee else "new",
            "p": body.get("priority", "normal"),
            "cat": body.get("category"),
            "rv": reviewer,
            "bd": body.get("business_date"),
            "dd": body.get("due_date"),
            "rr": bool(body.get("requires_review", False)),
            "bc": bool(body.get("blocks_closing", False)),
            "st": body.get("subject_type"),
            "si": body.get("subject_id"),
            "a": assignee,
            "u": ctx.user_id,
        },
    )
    if assignee:
        _assignment(ctx, tid, U(str(assignee)))
        notify(ctx, "bookkeeping.task.assigned", {"task_id": str(tid), "assignee_id": str(assignee)})
    event(
        ctx,
        "task",
        tid,
        "task_created",
        business_date=body.get("business_date"),
        summary={"title": body["title"], "assignee": str(assignee) if assignee else None},
    )
    return get_task(ctx, tid)


def _check_subject(ctx: RequestCtx, kind: str, sid: uuid.UUID) -> None:
    _, co = _co(ctx)
    table = {
        "document": "documents",
        "transaction": "acct_transactions",
        "exception": "bk_exceptions",
        "closing": "bk_closing_sessions",
    }[kind]
    if one(ctx, f"SELECT 1 AS x FROM {table} WHERE id = :i AND company_id = :c", i=sid, c=co) is None:
        raise NotFound("The object this task refers to was not found.")


def get_task(ctx: RequestCtx, tid: uuid.UUID, *, detail: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    t = must(ctx, f"SELECT {TASK_COLS} FROM bk_tasks t WHERE t.id = :i AND t.company_id = :c", i=tid, c=co)
    t["overdue"] = bool(t["due_date"] and t["due_date"] < today(ctx) and t["status"] not in ("completed", "cancelled"))
    if detail:
        t["comments"] = rows(
            ctx,
            "SELECT c.id, c.body, c.created_at, (SELECT u.display_name FROM users u WHERE u.id = c.author_id) AS author_name FROM bk_task_comments c WHERE c.task_id = :t ORDER BY c.created_at",
            t=tid,
        )
        t["assignments"] = rows(
            ctx,
            "SELECT a.assigned_at, (SELECT u.display_name FROM users u WHERE u.id = a.assignee_id) AS assignee_name,"
            " (SELECT u.display_name FROM users u WHERE u.id = a.assigned_by) AS assigned_by_name FROM bk_task_assignments a WHERE a.task_id = :t ORDER BY a.assigned_at",
            t=tid,
        )
        t["history"] = rows(
            ctx,
            "SELECT e.action, e.reason, e.summary, e.created_at, (SELECT u.display_name FROM users u WHERE u.id = e.actor_id) AS actor_name FROM bk_audit_events e"
            " WHERE e.company_id = :c AND e.entity_type = 'task' AND e.entity_id = :t ORDER BY e.created_at",
            c=co,
            t=tid,
        )
    return t


def list_tasks(
    ctx: RequestCtx,
    *,
    status: str | None,
    mine: bool,
    business_date: date | None,
    open_only: bool,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    items = rows(
        ctx,
        f"SELECT {TASK_COLS} FROM bk_tasks t WHERE t.company_id = :c AND (CAST(:s AS text) IS NULL OR t.status = :s)"
        " AND (NOT :m OR t.assignee_id = :u) AND (CAST(:d AS date) IS NULL OR t.business_date = :d)"
        " AND (NOT :o OR t.status NOT IN ('completed', 'cancelled'))"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, t.due_date NULLS LAST, t.created_at DESC LIMIT :l OFFSET :f",
        c=co,
        s=status,
        m=mine,
        u=ctx.user_id,
        d=business_date,
        o=open_only,
        l=limit,
        f=offset,
    )
    td = today(ctx)
    for t in items:
        t["overdue"] = bool(t["due_date"] and t["due_date"] < td and t["status"] not in ("completed", "cancelled"))
    return items


def update_task(ctx: RequestCtx, tid: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    _, co = _co(ctx)
    cur = must(
        ctx, "SELECT id, status, row_version FROM bk_tasks WHERE id = :i AND company_id = :c FOR UPDATE", i=tid, c=co
    )
    if cur["status"] in ("completed", "cancelled"):
        raise Conflict("A completed or cancelled task cannot be edited.", extra={"code": "task_closed"})
    if body.get("base_row_version") is not None and body["base_row_version"] != cur["row_version"]:
        raise Conflict("The task was changed by someone else. Reload and try again.", extra={"code": "stale_version"})
    sets, p = [], {"i": tid}
    if body.get("reviewer_id") is not None and not _can_be_assigned(ctx, U(str(body["reviewer_id"]))):
        raise Unprocessable("That person cannot review work in this company.", extra={"code": "invalid_reviewer"})
    for col in (
        "title",
        "description",
        "priority",
        "category",
        "reviewer_id",
        "business_date",
        "due_date",
        "requires_review",
        "blocks_closing",
    ):
        if col in body and (body[col] is not None or col in ("description", "category", "business_date", "due_date")):
            sets.append(f"{col} = :{col}")
            p[col] = body[col].strip() if col == "title" else body[col]
    if sets:
        ctx.conn.execute(
            text(
                f"UPDATE bk_tasks SET {', '.join(sets)}, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            p,
        )
        event(ctx, "task", tid, "task_updated", summary={"fields": sorted(k for k in body if k != "base_row_version")})
    return get_task(ctx, tid, detail=True)


def assign_task(ctx: RequestCtx, tid: uuid.UUID, assignee: uuid.UUID | None) -> dict[str, Any]:
    _, co = _co(ctx)
    cur = must(ctx, "SELECT id, status FROM bk_tasks WHERE id = :i AND company_id = :c FOR UPDATE", i=tid, c=co)
    if cur["status"] not in ("new", "assigned", "in_progress", "awaiting_information", "rejected"):
        raise Conflict(
            f"A task that is {cur['status'].replace('_', ' ')} cannot be reassigned.", extra={"code": "invalid_status"}
        )
    if assignee is not None and not _can_be_assigned(ctx, assignee):
        raise Unprocessable("That person cannot be given work in this company.", extra={"code": "invalid_assignee"})
    new_status = cur["status"]
    if cur["status"] == "new" and assignee:
        new_status = "assigned"
    elif cur["status"] == "assigned" and assignee is None:
        new_status = "new"
    ctx.conn.execute(
        text(
            "UPDATE bk_tasks SET assignee_id = :a, status = :s, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"a": assignee, "s": new_status, "i": tid},
    )
    _assignment(ctx, tid, assignee)
    if assignee:
        notify(ctx, "bookkeeping.task.assigned", {"task_id": str(tid), "assignee_id": str(assignee)})
    event(ctx, "task", tid, "task_assigned", summary={"assignee": str(assignee) if assignee else None})
    return get_task(ctx, tid, detail=True)


def transition_task(ctx: RequestCtx, tid: uuid.UUID, target: str, *, reason: str | None) -> dict[str, Any]:
    _, co = _co(ctx)
    t = must(
        ctx,
        "SELECT id, status, requires_review, assignee_id, review_requested_by, independence_required, title FROM bk_tasks WHERE id = :i AND company_id = :c FOR UPDATE",
        i=tid,
        c=co,
    )
    pol = get_policy(ctx)
    me = str(ctx.user_id)
    try:
        core.check_task_transition(
            t["status"],
            target,
            requires_review=t["requires_review"],
            actor=me,
            review_requested_by=str(t["review_requested_by"]) if t["review_requested_by"] else None,
            independence_required=bool(t["independence_required"]),
            has_review_perm=has(ctx, "bookkeeping.close.review"),
            is_assignee=str(t["assignee_id"]) == me if t["assignee_id"] else False,
            can_manage=has(ctx, "bookkeeping.tasks.manage"),
        )
    except core.RuleError as e:
        raise fail(e) from e
    if target in ("rejected", "cancelled", "awaiting_information") and not (reason or "").strip():
        raise Unprocessable("Please give a short reason.", extra={"code": "reason_required"})
    sets = ["status = :s"]
    p: dict[str, Any] = {"s": target, "i": tid}
    if target == "awaiting_review":
        sets += ["review_requested_by = :u", "independence_required = :ind"]
        p.update(u=ctx.user_id, ind=bool(pol["require_independent_task_review"]))
    elif target == "approved":
        sets.append("approved_by = :u")
        p["u"] = ctx.user_id
    elif target == "rejected":
        sets.append("rejection_reason = :r")
        p["r"] = reason.strip() if reason else None
    if target in ("completed", "cancelled"):
        sets.append("completed_at = now()")
    if target == "in_progress" and t["status"] == "rejected":
        sets += ["approved_by = NULL", "review_requested_by = NULL"]
    ctx.conn.execute(
        text(f"UPDATE bk_tasks SET {', '.join(sets)}, updated_at = now(), row_version = row_version + 1 WHERE id = :i"),
        p,
    )
    event(
        ctx,
        "task",
        tid,
        f"task_{target}",
        reason=(reason or "").strip() or None,
        summary={"from": t["status"], "to": target, "title": t["title"]},
    )
    if target == "awaiting_review":
        notify(ctx, "bookkeeping.task.review_requested", {"task_id": str(tid)})
    return get_task(ctx, tid, detail=True)


def add_comment(ctx: RequestCtx, tid: uuid.UUID, body: str) -> dict[str, Any]:
    org, co = _co(ctx)
    must(ctx, "SELECT id FROM bk_tasks WHERE id = :i AND company_id = :c", i=tid, c=co)
    ctx.conn.execute(
        text(
            "INSERT INTO bk_task_comments (id, organization_id, company_id, task_id, body, author_id) VALUES (:i, :o, :c, :t, :b, :u)"
        ),
        {"i": new_id(), "o": org, "c": co, "t": tid, "b": body.strip(), "u": ctx.user_id},
    )
    event(ctx, "task", tid, "task_commented")
    return get_task(ctx, tid, detail=True)


# ------------------------------------------------------------------ sync declarations
def report_sync(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    if body["business_date"] > today(ctx):
        raise Conflict("A future day cannot be reported on.", extra={"code": "future_date"})
    assert_day_open(ctx, body["business_date"])
    ctx.conn.execute(
        text(
            "INSERT INTO bk_sync_reports (id, organization_id, company_id, client_id, business_date, pending_uploads, failed_uploads, pending_changes, conflicts, last_sync_at, reported_by)"
            " VALUES (:i, :o, :c, :cl, :d, :pu, :fu, :pc, :cf, :ls, :u)"
            " ON CONFLICT (company_id, client_id, business_date) DO UPDATE SET pending_uploads = EXCLUDED.pending_uploads, failed_uploads = EXCLUDED.failed_uploads,"
            " pending_changes = EXCLUDED.pending_changes, conflicts = EXCLUDED.conflicts, last_sync_at = EXCLUDED.last_sync_at, reported_by = EXCLUDED.reported_by, reported_at = now()"
        ),
        {
            "i": new_id(),
            "o": org,
            "c": co,
            "cl": body["client_id"],
            "d": body["business_date"],
            "pu": body.get("pending_uploads", 0),
            "fu": body.get("failed_uploads", 0),
            "pc": body.get("pending_changes", 0),
            "cf": body.get("conflicts", 0),
            "ls": body.get("last_sync_at"),
            "u": ctx.user_id,
        },
    )
    return {"recorded": True, "declared_by_client": True}


def money_str(v: Any) -> str:
    return format(D(v if v is not None else 0), "f") if not isinstance(v, Decimal) else format(v, "f")
