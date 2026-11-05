"""Daily closing service (Phase 10), part 2: the six-stage closing workflow, immutable snapshots, daily reports through the
Excel engine, cash/bank summaries, period readiness and the organization overview.

A closing moves: prepare -> validate -> review exceptions -> closing summary -> submit for review -> approve and close.
Every step is a person's action (invariant 6), is audited, and is refused when the pure rules in `bk_core` or the database
say so. A closed day is protected in the database; reopening keeps the original closing and snapshot and starts a new,
linked closing. Nothing here locks an accounting period, prepares a VAT return or reconciles a bank."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from artify_api import bk_core as core
from artify_api import bookkeeping as bk
from artify_api import excel as X
from artify_api.accounting import must, one, rows, ser
from artify_api.acct_core import D
from artify_api.context import RequestCtx
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id

U = uuid.UUID
SESSION_COLS = (
    "s.id, s.business_date, s.period_id, s.status, s.stage, s.validation_status, s.independence_required, s.summary, s.prepared_by, s.prepared_at,"
    " s.submitted_by, s.submitted_at, s.review_note, s.closed_by, s.closed_at, s.reopened_by, s.reopened_at, s.reopen_reason, s.cancelled_at,"
    " s.cancel_reason, s.reopened_from_id, s.created_at, s.updated_at, s.row_version,"
    " (SELECT u.display_name FROM users u WHERE u.id = s.prepared_by) AS prepared_by_name,"
    " (SELECT u.display_name FROM users u WHERE u.id = s.submitted_by) AS submitted_by_name,"
    " (SELECT u.display_name FROM users u WHERE u.id = s.closed_by) AS closed_by_name,"
    " (SELECT u.display_name FROM users u WHERE u.id = s.reopened_by) AS reopened_by_name"
)
MAX_RANGE_DAYS = 93


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


def _j(v: Any) -> str:
    return json.dumps(ser(v), default=str)


# ------------------------------------------------------------------ reading
def load_session(ctx: RequestCtx, sid: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    return must(
        ctx,
        f"SELECT {SESSION_COLS} FROM bk_closing_sessions s WHERE s.id = :i AND s.company_id = :c"
        + (" FOR UPDATE OF s" if lock else ""),
        i=sid,
        c=co,
    )


def latest_run(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any] | None:
    _, co = _co(ctx)
    return one(
        ctx,
        "SELECT r.id, r.result, r.counts, r.issues, r.facts, r.created_at, (SELECT u.display_name FROM users u WHERE u.id = r.run_by) AS run_by_name"
        " FROM bk_validation_runs r WHERE r.session_id = :s AND r.company_id = :c ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        s=sid,
        c=co,
    )


def session_view(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    s = load_session(ctx, sid)
    s["approvals"] = rows(
        ctx,
        "SELECT a.action, a.from_status, a.to_status, a.note, a.actor_type, a.created_at, (SELECT u.display_name FROM users u WHERE u.id = a.actor_id) AS actor_name"
        " FROM bk_closing_approvals a WHERE a.session_id = :s ORDER BY a.created_at, a.id",
        s=sid,
    )
    run = latest_run(ctx, sid)
    s["validation"] = (
        {
            "run_id": run["id"],
            "result": run["result"],
            "counts": run["counts"],
            "created_at": run["created_at"],
            "run_by_name": run["run_by_name"],
        }
        if run
        else None
    )
    s["exceptions"] = bk.list_exceptions(ctx, s["business_date"])
    snap = one(
        ctx,
        "SELECT id, snapshot_no, sha256, created_at, previous_snapshot_id FROM bk_closing_snapshots WHERE session_id = :s",
        s=sid,
    )
    s["snapshot"] = snap
    s["exports"] = list_closing_exports(ctx, sid)
    s["stale"] = _is_stale(ctx, s, run)
    return ser(s)


def _is_stale(ctx: RequestCtx, s: dict[str, Any], run: dict[str, Any] | None) -> bool:
    if s["status"] not in ("draft", "in_progress", "ready_for_review") or run is None:
        return False
    ev = bk.evaluate_day(ctx, s["business_date"])
    return bool(bk.facts_digest(ev["facts"], ev["issues"]) != run["facts"].get("digest"))


def list_sessions(
    ctx: RequestCtx, *, date_from: date | None, date_to: date | None, status: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            f"SELECT {SESSION_COLS} FROM bk_closing_sessions s WHERE s.company_id = :c AND (CAST(:f AS date) IS NULL OR s.business_date >= :f)"
            " AND (CAST(:t AS date) IS NULL OR s.business_date <= :t) AND (CAST(:s AS text) IS NULL OR s.status = :s)"
            " ORDER BY s.business_date DESC, s.created_at DESC LIMIT :l OFFSET :o",
            c=co,
            f=date_from,
            t=date_to,
            s=status,
            l=limit,
            o=offset,
        )
    )


def live_session(ctx: RequestCtx, d: date) -> dict[str, Any] | None:
    _, co = _co(ctx)
    r = one(
        ctx,
        "SELECT s.id FROM bk_closing_sessions s WHERE s.company_id = :c AND s.business_date = :d AND s.status IN ('draft', 'in_progress', 'ready_for_review', 'closed')",
        c=co,
        d=d,
    )
    return load_session(ctx, U(r["id"])) if r else None


# ------------------------------------------------------------------ transitions
def _trail(
    ctx: RequestCtx, sid: uuid.UUID, action: str, frm: str | None, to: str | None, note: str | None = None
) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO bk_closing_approvals (id, organization_id, company_id, session_id, action, from_status, to_status, actor_type, actor_id, note)"
            " VALUES (:i, :o, :c, :s, :a, :f, :t, 'user', :u, :n)"
        ),
        {
            "i": new_id(),
            "o": org,
            "c": co,
            "s": sid,
            "a": action,
            "f": frm,
            "t": to,
            "u": ctx.user_id,
            "n": (note or None) and note[:500],
        },
    )


def _set(ctx: RequestCtx, sid: uuid.UUID, **cols: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    ctx.conn.execute(
        text(
            f"UPDATE bk_closing_sessions SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :sid"
        ),
        {**cols, "sid": sid},
    )


def _period_for(ctx: RequestCtx, d: date) -> dict[str, Any] | None:
    _, co = _co(ctx)
    return one(
        ctx,
        "SELECT id, status, name FROM acct_periods WHERE company_id = :c AND start_date <= :d AND end_date >= :d ORDER BY start_date DESC LIMIT 1",
        c=co,
        d=d,
    )


def create_session(ctx: RequestCtx, d: date) -> dict[str, Any]:
    org, co = _co(ctx)
    bk.require_action(ctx, "prepare")
    if d > bk.today(ctx):
        raise Conflict("A day that has not happened yet cannot be closed.", extra={"code": "future_date"})
    existing = live_session(ctx, d)
    if existing is not None:
        raise Conflict(
            f"This day already has a closing ({existing['status'].replace('_', ' ')}).",
            extra={"code": "closing_exists", "closing_id": existing["id"]},
        )
    pol = bk.get_policy(ctx)
    per = _period_for(ctx, d)
    cfg = one(ctx, "SELECT require_open_period FROM acct_configurations WHERE company_id = :c", c=co)
    if per is not None and per["status"] == "closed":
        raise Conflict(
            f"The accounting period {per['name']} is closed, so its days cannot be closed. Reopen the period first.",
            extra={"code": "period_closed"},
        )
    if per is None and cfg is not None and cfg["require_open_period"]:
        raise Conflict(
            "No accounting period covers this day and the company requires one. Create the period first.",
            extra={"code": "period_missing"},
        )
    sid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_closing_sessions (id, organization_id, company_id, business_date, period_id, status, stage, independence_required, prepared_by, prepared_at, created_by)"
            " VALUES (:i, :o, :c, :d, :p, 'draft', 1, :ind, :u, now(), :u)"
        ),
        {
            "i": sid,
            "o": org,
            "c": co,
            "d": d,
            "p": U(per["id"]) if per else None,
            "ind": bool(pol["require_independent_approval"]),
            "u": ctx.user_id,
        },
    )
    _trail(ctx, sid, "prepare", None, "draft")
    bk.event(ctx, "closing", sid, "closing_started", business_date=d)
    return session_view(ctx, sid)


def _active(s: dict[str, Any]) -> None:
    if s["status"] not in ("draft", "in_progress"):
        raise Conflict(
            f"A closing that is {s['status'].replace('_', ' ')} cannot be worked on.", extra={"code": "invalid_status"}
        )


def validate(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    """Stage 2: run the deterministic validation, store the run and reconcile the day's exceptions."""
    org, co = _co(ctx)
    bk.require_action(ctx, "prepare")
    s = load_session(ctx, sid, lock=True)
    _active(s)
    d = s["business_date"]
    ev = bk.evaluate_day(ctx, d)
    statuses = bk.sync_exceptions(ctx, d, ev["issues"])
    result, counts = core.evaluate(ev["issues"], statuses)
    digest = bk.facts_digest(ev["facts"], ev["issues"])
    run_id = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_validation_runs (id, organization_id, company_id, session_id, business_date, result, counts, issues, facts, run_by)"
            " VALUES (:i, :o, :c, :s, :d, :r, CAST(:cn AS jsonb), CAST(:is AS jsonb), CAST(:f AS jsonb), :u)"
        ),
        {
            "i": run_id,
            "o": org,
            "c": co,
            "s": sid,
            "d": d,
            "r": result,
            "cn": _j(counts),
            "is": _j([i.as_dict() for i in ev["issues"]]),
            "f": _j(
                {
                    "digest": digest,
                    "docs": ev["facts"].docs,
                    "txn_counts": ev["facts"].txn_counts,
                    "tax_configured": ev["facts"].tax_configured,
                }
            ),
            "u": ctx.user_id,
        },
    )
    new_status = "in_progress"
    _set(ctx, sid, status=new_status, stage=max(s["stage"], 2), validation_status=result)
    _trail(ctx, sid, "validate", s["status"], new_status, f"result: {result}")
    bk.event(ctx, "closing", sid, "closing_validated", business_date=d, summary={"result": result, "counts": counts})
    return session_view(ctx, sid)


def set_stage(ctx: RequestCtx, sid: uuid.UUID, stage: int) -> dict[str, Any]:
    """Move between stages 1-4 (prepare, validate, review exceptions, closing summary). 5 and 6 are only reached by submitting and closing."""
    bk.require_action(ctx, "prepare")
    s = load_session(ctx, sid, lock=True)
    _active(s)
    if not 1 <= stage <= 4:
        raise Unprocessable(
            "Only stages 1 to 4 can be selected; submitting and closing move the later stages.",
            extra={"code": "invalid_stage"},
        )
    if stage >= 3 and s["validation_status"] is None:
        raise Conflict("Validate the day first.", extra={"code": "validation_required"})
    _set(ctx, sid, stage=stage, status="in_progress" if s["status"] == "draft" else s["status"])
    bk.event(
        ctx,
        "closing",
        sid,
        "closing_stage",
        business_date=s["business_date"],
        summary={"stage": core.STAGES[stage - 1]},
    )
    return session_view(ctx, sid)


def refresh_result(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    """Recompute the session's result from its latest run and the CURRENT exception decisions (after an acknowledge/override)."""
    s = load_session(ctx, sid, lock=True)
    run = latest_run(ctx, sid)
    if run is None or s["status"] not in ("draft", "in_progress", "ready_for_review"):
        return s
    issues = [
        core.Issue(i["code"], i["severity"], i["message"], i["subject_type"], i["subject_id"], i.get("data") or {})
        for i in run["issues"]
    ]
    result, _ = core.evaluate(issues, bk.exception_statuses(ctx, s["business_date"]))
    if result != s["validation_status"]:
        _set(ctx, sid, validation_status=result)
    return load_session(ctx, sid)


def summary(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    """Stage 4: the closing summary shown to the preparer and reviewer. The same builder produces the snapshot at close."""
    s = load_session(ctx, sid)
    ev = bk.evaluate_day(ctx, s["business_date"])
    return ser(_payload(ctx, s, ev, bk.exception_statuses(ctx, s["business_date"]), snapshot_no=None))


def submit(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    bk.require_action(ctx, "prepare")
    s = refresh_result(ctx, sid)
    s = load_session(ctx, sid, lock=True)
    run = latest_run(ctx, sid)
    if run is None or s["validation_status"] is None:
        raise Conflict("Validate the day before submitting it.", extra={"code": "validation_required"})
    stale = _is_stale(ctx, s, run)
    try:
        core.check_submit(status=s["status"], result=s["validation_status"], stale=stale)
    except core.RuleError as e:
        raise bk.fail(e) from e
    pol = bk.get_policy(ctx)
    _set(
        ctx,
        sid,
        status="ready_for_review",
        stage=5,
        submitted_by=ctx.user_id,
        submitted_at=datetime.now(UTC),
        independence_required=bool(pol["require_independent_approval"]),
        summary=_j({"result": s["validation_status"], "counts": run["counts"], "submitted_run": run["id"]}),
        review_note=None,
    )
    _trail(ctx, sid, "submit", s["status"], "ready_for_review")
    bk.event(
        ctx,
        "closing",
        sid,
        "closing_submitted",
        business_date=s["business_date"],
        summary={"result": s["validation_status"]},
    )
    bk.notify(
        ctx, "bookkeeping.closing.submitted", {"closing_id": str(sid), "business_date": s["business_date"].isoformat()}
    )
    return session_view(ctx, sid)


def send_back(ctx: RequestCtx, sid: uuid.UUID, note: str) -> dict[str, Any]:
    bk.require_action(ctx, "review")
    s = load_session(ctx, sid, lock=True)
    if s["status"] != "ready_for_review":
        raise Conflict("Only a submitted closing can be sent back.", extra={"code": "invalid_status"})
    if len((note or "").strip()) < 5:
        raise Unprocessable("Tell the preparer what needs another look.", extra={"code": "reason_required"})
    _set(ctx, sid, status="in_progress", stage=3, review_note=note.strip())
    _trail(ctx, sid, "send_back", "ready_for_review", "in_progress", note)
    bk.event(ctx, "closing", sid, "closing_sent_back", business_date=s["business_date"], reason=note.strip())
    bk.notify(ctx, "bookkeeping.closing.sent_back", {"closing_id": str(sid)})
    return session_view(ctx, sid)


def cancel(ctx: RequestCtx, sid: uuid.UUID, reason: str) -> dict[str, Any]:
    bk.require_action(ctx, "prepare")
    s = load_session(ctx, sid, lock=True)
    if s["status"] not in ("draft", "in_progress", "ready_for_review"):
        raise Conflict(
            "Only a closing that is not closed can be cancelled; a closed day is reopened instead.",
            extra={"code": "invalid_status"},
        )
    if len((reason or "").strip()) < 5:
        raise Unprocessable("Please give a reason.", extra={"code": "reason_required"})
    ctx.conn.execute(
        text(
            "UPDATE bk_closing_sessions SET status = 'cancelled', cancelled_by = :u, cancelled_at = now(), cancel_reason = :r, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "r": reason.strip(), "i": sid},
    )
    _trail(ctx, sid, "cancel", s["status"], "cancelled", reason)
    bk.event(ctx, "closing", sid, "closing_cancelled", business_date=s["business_date"], reason=reason.strip())
    return session_view(ctx, sid)


# ------------------------------------------------------------------ closing and the snapshot
def _cash_bank(ctx: RequestCtx, d: date) -> dict[str, Any]:
    _, co = _co(ctx)
    base = ("approved", "posted", "reversed")
    r = rows(
        ctx,
        "SELECT a.id, a.code, a.name, a.account_type,"
        " COALESCE(sum(l.base_debit - l.base_credit) FILTER (WHERE j.journal_date < :d), 0) AS opening,"
        " COALESCE(sum(l.base_debit) FILTER (WHERE j.journal_date = :d), 0) AS receipts,"
        " COALESCE(sum(l.base_credit) FILTER (WHERE j.journal_date = :d), 0) AS payments,"
        " count(l.id) FILTER (WHERE j.journal_date = :d) AS lines"
        " FROM acct_accounts a LEFT JOIN acct_journal_lines l ON l.account_id = a.id"
        " LEFT JOIN acct_journals j ON j.id = l.journal_id AND j.status = ANY(CAST(:st AS text[])) AND j.journal_date <= :d"
        " WHERE a.company_id = :c AND a.account_type IN ('cash', 'bank') AND a.archived_at IS NULL AND a.is_active"
        " GROUP BY a.id, a.code, a.name, a.account_type HAVING count(l.id) FILTER (WHERE j.id IS NOT NULL) > 0 OR TRUE ORDER BY a.code",
        c=co,
        d=d,
        st=list(base),
    )
    q = Decimal("0.000001")  # journal amounts carry six decimals; every figure here is shown with the same scale
    rs = [
        core.CashRow(
            x["id"],
            x["code"],
            x["name"],
            x["account_type"],
            D(x["opening"]).quantize(q),
            D(x["receipts"]).quantize(q),
            D(x["payments"]).quantize(q),
            int(x["lines"]),
        )
        for x in r
    ]
    out = core.cash_bank_summary(rs)
    pending = one(
        ctx,
        "SELECT count(*) AS n FROM acct_transactions WHERE company_id = :c AND transaction_date = :d AND reversal_of_id IS NULL"
        " AND txn_type IN ('bank_deposit', 'bank_withdrawal', 'bank_charges', 'bank_transfer', 'cash_expense', 'bank_expense', 'customer_receipt', 'supplier_payment')"
        " AND status IN ('draft', 'awaiting_review', 'awaiting_approval')",
        c=co,
        d=d,
    )
    out["unapproved_cash_bank_transactions"] = int((pending or {"n": 0})["n"])
    out["base_currency"] = (
        one(ctx, "SELECT base_currency FROM acct_configurations WHERE company_id = :c", c=co) or {}
    ).get("base_currency")
    out["note"] = "Books only. Not a bank reconciliation: nothing here is compared with a bank statement."
    return out


def cash_bank(ctx: RequestCtx, d: date) -> dict[str, Any]:
    return ser(_cash_bank(ctx, d))


def _txn_summary(ctx: RequestCtx, d: date) -> dict[str, Any]:
    _, co = _co(ctx)
    by_status = rows(
        ctx,
        "SELECT status, count(*) AS n FROM acct_transactions WHERE company_id = :c AND transaction_date = :d AND reversal_of_id IS NULL GROUP BY status ORDER BY status",
        c=co,
        d=d,
    )
    approved = rows(
        ctx,
        "SELECT txn_type, currency, count(*) AS n, sum(net_amount) AS net, sum(tax_amount) AS tax, sum(gross_amount) AS gross FROM acct_transactions"
        " WHERE company_id = :c AND transaction_date = :d AND reversal_of_id IS NULL AND status IN ('approved', 'posted') GROUP BY txn_type, currency ORDER BY txn_type, currency",
        c=co,
        d=d,
    )
    j = one(
        ctx,
        "SELECT COALESCE(sum(l.base_debit), 0) AS debit, COALESCE(sum(l.base_credit), 0) AS credit, count(DISTINCT j.id) AS journals FROM acct_journals j"
        " JOIN acct_journal_lines l ON l.journal_id = j.id WHERE j.company_id = :c AND j.journal_date = :d AND j.status IN ('approved', 'posted', 'reversed')",
        c=co,
        d=d,
    )
    return {
        "by_status": by_status,
        "approved_totals": approved,
        "journal_totals": j,
        "basis": "Approved and posted transactions only; drafts and rejected ones are counted but not totalled.",
    }


def _payload(
    ctx: RequestCtx, s: dict[str, Any], ev: dict[str, Any], statuses: dict[str, str], *, snapshot_no: int | None
) -> dict[str, Any]:
    _, co = _co(ctx)
    d = s["business_date"]
    result, counts = core.evaluate(ev["issues"], statuses)
    pol = ev["policy"]
    exc = bk.list_exceptions(ctx, d)
    tasks = rows(
        ctx,
        "SELECT status, count(*) AS n FROM bk_tasks WHERE company_id = :c AND business_date = :d GROUP BY status ORDER BY status",
        c=co,
        d=d,
    )
    f: core.DayFacts = ev["facts"]
    return {
        "schema": 1,
        "company_id": str(co),
        "business_date": d,
        "closing_id": s["id"],
        "snapshot_no": snapshot_no,
        "timezone": bk.company_tz(ctx),
        "period": one(
            ctx,
            "SELECT name, status, start_date, end_date FROM acct_periods WHERE company_id = :c AND start_date <= :d AND end_date >= :d ORDER BY start_date DESC LIMIT 1",
            c=co,
            d=d,
        ),
        "validation": {"result": result, "counts": counts, "issues": [i.as_dict() for i in ev["issues"]]},
        "exceptions": [
            {
                k: e[k]
                for k in ("code", "severity", "subject_type", "subject_id", "status", "message", "resolution_note")
            }
            for e in exc
        ],
        "transactions": _txn_summary(ctx, d),
        "documents": {"counts": f.docs},
        "cash_bank": _cash_bank(ctx, d),
        "checklists": [
            {
                "name": c["name"],
                "items": [
                    {"title": i["title"], "mandatory": i["mandatory"], "done": i["done"], "by": i["done_by"]}
                    for i in c["items"]
                ],
            }
            for c in ev["checklists"]
        ],
        "tasks": tasks,
        "sync_declared_by_clients": [
            {k: r[k] for k in ("client_id", "pending_uploads", "failed_uploads", "pending_changes", "conflicts")}
            for r in f.sync_reports
        ],
        "policy": {"independent_approval": s["independence_required"], "severity_overrides": pol["severity_overrides"]},
        "prepared_by": s["prepared_by"],
        "submitted_by": s["submitted_by"],
    }


def close(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    """Stage 6: approve and close. The day is validated again right now; the snapshot records exactly what was approved."""
    org, co = _co(ctx)
    bk.require_action(ctx, "approve")
    s = load_session(ctx, sid, lock=True)
    if s["status"] != "ready_for_review":
        raise Conflict(
            "Only a closing that was submitted for review can be approved and closed.", extra={"code": "invalid_status"}
        )
    d = s["business_date"]
    ev = bk.evaluate_day(ctx, d)
    statuses = bk.sync_exceptions(ctx, d, ev["issues"])
    result, counts = core.evaluate(ev["issues"], statuses)
    try:
        core.check_close(
            status=s["status"],
            result=result,
            submitted_by=str(s["submitted_by"]) if s["submitted_by"] else None,
            closer=str(ctx.user_id),
            independence_required=bool(s["independence_required"]),
        )
    except core.RuleError as e:
        raise bk.fail(e) from e
    prev = one(
        ctx,
        "SELECT id, snapshot_no FROM bk_closing_snapshots WHERE company_id = :c AND business_date = :d ORDER BY snapshot_no DESC LIMIT 1",
        c=co,
        d=d,
    )
    no = (prev["snapshot_no"] + 1) if prev else 1
    payload = _payload(ctx, s, ev, statuses, snapshot_no=no)
    snap_id = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_closing_snapshots (id, organization_id, company_id, session_id, business_date, snapshot_no, previous_snapshot_id, payload, sha256, created_by)"
            " VALUES (:i, :o, :c, :s, :d, :n, :p, CAST(:pl AS jsonb), 'pending', :u)"
        ),
        {
            "i": snap_id,
            "o": org,
            "c": co,
            "s": sid,
            "d": d,
            "n": no,
            "p": U(prev["id"]) if prev else None,
            "pl": core.canonical_json(ser(payload)),
            "u": ctx.user_id,
        },
    )
    run_id = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_validation_runs (id, organization_id, company_id, session_id, business_date, result, counts, issues, facts, run_by)"
            " VALUES (:i, :o, :c, :s, :d, :r, CAST(:cn AS jsonb), CAST(:is AS jsonb), CAST(:f AS jsonb), :u)"
        ),
        {
            "i": run_id,
            "o": org,
            "c": co,
            "s": sid,
            "d": d,
            "r": result,
            "cn": _j(counts),
            "is": _j([i.as_dict() for i in ev["issues"]]),
            "f": _j({"digest": bk.facts_digest(ev["facts"], ev["issues"]), "at": "close"}),
            "u": ctx.user_id,
        },
    )
    ctx.conn.execute(
        text(
            "UPDATE bk_closing_sessions SET status = 'closed', stage = 6, validation_status = :r, closed_by = :u, closed_at = now(), updated_at = now(), row_version = row_version + 1,"
            " summary = CAST(:sm AS jsonb) WHERE id = :i"
        ),
        {"r": result, "u": ctx.user_id, "i": sid, "sm": _j({"result": result, "counts": counts, "snapshot_no": no})},
    )
    _trail(ctx, sid, "approve_close", "ready_for_review", "closed", f"result: {result}")
    bk.event(
        ctx,
        "closing",
        sid,
        "closing_closed",
        business_date=d,
        summary={"result": result, "snapshot_no": no, "snapshot_id": str(snap_id)},
    )
    bk.notify(ctx, "bookkeeping.closing.closed", {"closing_id": str(sid), "business_date": d.isoformat()})
    return session_view(ctx, sid)


def reopen(ctx: RequestCtx, sid: uuid.UUID, reason: str) -> dict[str, Any]:
    """Reopening keeps the original closing (status 'reopened') and its snapshot, and starts a NEW closing linked to it."""
    org, co = _co(ctx)
    bk.require_action(ctx, "reopen")
    s = load_session(ctx, sid, lock=True)
    per = _period_for(ctx, s["business_date"])
    try:
        core.check_reopen(status=s["status"], reason=reason, period_closed=bool(per and per["status"] == "closed"))
    except core.RuleError as e:
        raise bk.fail(e) from e
    ctx.conn.execute(
        text(
            "UPDATE bk_closing_sessions SET status = 'reopened', reopened_by = :u, reopened_at = now(), reopen_reason = :r, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "r": reason.strip(), "i": sid},
    )
    _trail(ctx, sid, "reopen", "closed", "reopened", reason)
    pol = bk.get_policy(ctx)
    nid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO bk_closing_sessions (id, organization_id, company_id, business_date, period_id, status, stage, independence_required, prepared_by, prepared_at, created_by, reopened_from_id)"
            " VALUES (:i, :o, :c, :d, :p, 'in_progress', 2, :ind, :u, now(), :u, :f)"
        ),
        {
            "i": nid,
            "o": org,
            "c": co,
            "d": s["business_date"],
            "p": U(per["id"]) if per else None,
            "ind": bool(pol["require_independent_approval"]),
            "u": ctx.user_id,
            "f": sid,
        },
    )
    _trail(ctx, nid, "prepare", None, "in_progress", f"reopened from {sid}")
    bk.event(
        ctx,
        "closing",
        sid,
        "closing_reopened",
        business_date=s["business_date"],
        reason=reason.strip(),
        summary={"new_closing": str(nid)},
    )
    bk.notify(
        ctx,
        "bookkeeping.closing.reopened",
        {"closing_id": str(sid), "new_closing_id": str(nid), "business_date": s["business_date"].isoformat()},
    )
    return session_view(ctx, nid)


# ------------------------------------------------------------------ snapshots
def get_snapshot(ctx: RequestCtx, sid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    load_session(ctx, sid)
    snap = must(
        ctx,
        "SELECT id, session_id, business_date, snapshot_no, previous_snapshot_id, payload, sha256, created_at, (SELECT u.display_name FROM users u WHERE u.id = created_by) AS created_by_name FROM bk_closing_snapshots WHERE session_id = :s AND company_id = :c",
        s=sid,
        c=co,
    )
    recomputed = one(
        ctx,
        "SELECT encode(sha256(convert_to(payload::text, 'UTF8')), 'hex') AS h FROM bk_closing_snapshots WHERE id = :i",
        i=U(snap["id"]),
    )
    snap["verified"] = bool(recomputed and recomputed["h"] == snap["sha256"])
    return ser(snap)


def snapshot_history(ctx: RequestCtx, d: date) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            "SELECT n.id, n.session_id, n.snapshot_no, n.previous_snapshot_id, n.sha256, n.created_at, s.status AS closing_status, s.reopened_at, s.reopen_reason"
            " FROM bk_closing_snapshots n JOIN bk_closing_sessions s ON s.id = n.session_id WHERE n.company_id = :c AND n.business_date = :d ORDER BY n.snapshot_no",
            c=co,
            d=d,
        )
    )


# ------------------------------------------------------------------ daily Excel reports (Phase 9 engine)
def list_closing_exports(ctx: RequestCtx, sid: uuid.UUID) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return rows(
        ctx,
        "SELECT ce.export_id AS id, ce.snapshot_id, ce.created_at, e.status, e.filename, e.sha256, e.include_unapproved, e.repeat_acknowledged, e.source_export_id,"
        " t.name AS template_name, v.version_no AS template_version, m.mapping_no, e.error_message,"
        " (SELECT u.display_name FROM users u WHERE u.id = ce.created_by) AS created_by_name"
        " FROM bk_closing_exports ce JOIN xl_exports e ON e.id = ce.export_id JOIN xl_templates t ON t.id = e.template_id"
        " JOIN xl_template_versions v ON v.id = e.version_id JOIN xl_mappings m ON m.id = e.mapping_id WHERE ce.session_id = :s AND ce.company_id = :c ORDER BY ce.created_at DESC",
        s=sid,
        c=co,
    )


def _link_export(ctx: RequestCtx, s: dict[str, Any], export_id: str) -> None:
    org, co = _co(ctx)
    snap = one(ctx, "SELECT id FROM bk_closing_snapshots WHERE session_id = :s", s=U(s["id"]))
    ctx.conn.execute(
        text(
            "INSERT INTO bk_closing_exports (id, organization_id, company_id, session_id, snapshot_id, export_id, created_by) VALUES (:i, :o, :c, :s, :n, :e, :u)"
            " ON CONFLICT (export_id) DO NOTHING"
        ),
        {
            "i": new_id(),
            "o": org,
            "c": co,
            "s": U(s["id"]),
            "n": U(snap["id"]) if snap else None,
            "e": U(export_id),
            "u": ctx.user_id,
        },
    )


def create_closing_export(ctx: RequestCtx, sid: uuid.UUID, body: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """A daily report for the closing's day, generated by the Phase 9 engine: the template version and mapping are recorded by
    the export, a repeat of the same report must be acknowledged, and unapproved data is excluded unless explicitly requested
    by someone allowed to include it. The caller still needs the Excel export permission."""
    s = load_session(ctx, sid)
    if s["status"] == "cancelled":
        raise Conflict("A cancelled closing has no reports.", extra={"code": "invalid_status"})
    d = s["business_date"]
    payload = {
        "template_id": body["template_id"],
        "params": {"date_from": d, "date_to": d, "include_unapproved": bool(body.get("include_unapproved", False))},
        "idempotency_key": body["idempotency_key"],
        "acknowledge_repeat": bool(body.get("acknowledge_repeat", False)),
        "reason": body.get("reason"),
    }
    created, exp = X.create_export(ctx, payload)
    _link_export(ctx, s, exp["id"])
    if created:
        bk.event(
            ctx,
            "closing",
            sid,
            "closing_report_requested",
            business_date=d,
            summary={"export": exp["id"], "template": str(body["template_id"]), "closed": s["status"] == "closed"},
        )
    return created, exp


def regenerate_closing_export(
    ctx: RequestCtx, sid: uuid.UUID, export_id: uuid.UUID, body: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    s = load_session(ctx, sid)
    if (
        one(ctx, "SELECT 1 AS x FROM bk_closing_exports WHERE session_id = :s AND export_id = :e", s=sid, e=export_id)
        is None
    ):
        raise NotFound("This report does not belong to this closing.")
    created, exp = X.regenerate_export(ctx, export_id, body)
    _link_export(ctx, s, exp["id"])
    if created:
        bk.event(
            ctx,
            "closing",
            sid,
            "closing_report_regenerated",
            business_date=s["business_date"],
            summary={"export": exp["id"], "from": str(export_id)},
        )
    return created, exp


# ------------------------------------------------------------------ the daily workbench
def day_overview(ctx: RequestCtx, d: date) -> dict[str, Any]:
    _, co = _co(ctx)
    ev = bk.evaluate_day(ctx, d)
    f: core.DayFacts = ev["facts"]
    statuses = bk.exception_statuses(ctx, d)
    result, counts = core.evaluate(ev["issues"], statuses)
    live = live_session(ctx, d)
    mine = one(
        ctx,
        "SELECT count(*) FILTER (WHERE status NOT IN ('completed', 'cancelled')) AS open, count(*) FILTER (WHERE status NOT IN ('completed', 'cancelled') AND due_date < :t) AS overdue"
        " FROM bk_tasks WHERE company_id = :c AND assignee_id = :u",
        c=co,
        u=ctx.user_id,
        t=bk.today(ctx),
    )
    exported = one(
        ctx,
        "SELECT count(*) AS n FROM acct_transactions t WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL AND t.status IN ('approved', 'posted')"
        " AND NOT EXISTS (SELECT 1 FROM xl_export_rows r JOIN xl_exports e ON e.id = r.export_id AND e.status = 'completed' WHERE r.transaction_id = t.id)",
        c=co,
        d=d,
    )
    items = [i for c in ev["checklists"] for i in c["items"]]
    return ser(
        {
            "business_date": d,
            "timezone": bk.company_tz(ctx),
            "today": f.today,
            "is_future": d > f.today,
            "day_closed": bool(live and live["status"] == "closed"),
            "closing": {
                k: live[k]
                for k in (
                    "id",
                    "status",
                    "stage",
                    "validation_status",
                    "submitted_by_name",
                    "closed_by_name",
                    "closed_at",
                )
            }
            if live
            else None,
            "documents": f.docs,
            "transactions": f.txn_counts,
            "approved_not_exported": int((exported or {"n": 0})["n"]),
            "validation": {"result": result, "counts": counts, "issue_count": len(ev["issues"])},
            "checklist": {
                "total": len(items),
                "done": sum(1 for i in items if i["done"]),
                "mandatory_open": len(f.checklist_open),
            },
            "tasks_mine": mine,
            "sync_reports": len(f.sync_reports),
            "has_activity": f.has_activity(),
            "scope_note": "Document counts here and in validation cover the whole company; the queue lists only the folders you can see.",
        }
    )


def preview_validation(ctx: RequestCtx, d: date) -> dict[str, Any]:
    """Validation WITHOUT storing anything: what a closing would find right now."""
    ev = bk.evaluate_day(ctx, d)
    statuses = bk.exception_statuses(ctx, d)
    result, counts = core.evaluate(ev["issues"], statuses)
    return ser(
        {
            "business_date": d,
            "result": result,
            "counts": counts,
            "issues": [{**i.as_dict(), "status": statuses.get(i.fingerprint, "open")} for i in ev["issues"]],
            "checklists": ev["checklists"],
        }
    )


def document_queue(ctx: RequestCtx, d: date, visible: list[str], limit: int, offset: int) -> list[dict[str, Any]]:
    """Documents received on the day, in folders the caller can see (the workbench is not a way around folder permissions)."""
    _, co = _co(ctx)
    tz = bk.company_tz(ctx)
    return ser(
        rows(
            ctx,
            "SELECT d.id, d.display_name, d.node_id, d.category_id, d.status, d.source, d.created_at,"
            " (SELECT j.status FROM processing_jobs j WHERE j.document_id = d.id ORDER BY j.created_at DESC LIMIT 1) AS processing_status,"
            " (SELECT v.scan_status FROM document_versions v WHERE v.document_id = d.id AND v.version_no = d.current_version_no) AS scan_status,"
            " (SELECT count(*) FROM acct_transactions t WHERE t.source_document_id = d.id AND t.status <> 'rejected') AS transactions"
            " FROM documents d WHERE d.company_id = :c AND d.status IN ('active', 'quarantined') AND (d.created_at AT TIME ZONE :tz)::date = :d"
            " AND d.node_id = ANY(CAST(:v AS uuid[])) ORDER BY d.created_at DESC LIMIT :l OFFSET :o",
            c=co,
            tz=tz,
            d=d,
            v="{" + ",".join(visible) + "}",
            l=limit,
            o=offset,
        )
    )


def transaction_list(ctx: RequestCtx, d: date, limit: int, offset: int) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            "SELECT t.id, t.number, t.txn_type, t.status, t.currency, t.net_amount, t.tax_amount, t.gross_amount, t.narration, t.duplicate_status, t.source_document_id,"
            " (SELECT count(*) FROM jsonb_array_elements(t.validation) i WHERE (i->>'blocking')::boolean) AS blocking_issues"
            " FROM acct_transactions t WHERE t.company_id = :c AND t.transaction_date = :d AND t.reversal_of_id IS NULL ORDER BY t.number LIMIT :l OFFSET :o",
            c=co,
            d=d,
            l=limit,
            o=offset,
        )
    )


def daily_report(ctx: RequestCtx, d: date) -> dict[str, Any]:
    """The day's figures from approved data, with the basis stated. Nothing is estimated and nothing missing is filled in."""
    return ser(
        {
            "business_date": d,
            "transactions": _txn_summary(ctx, d),
            "cash_bank": _cash_bank(ctx, d),
            "snapshots": snapshot_history(ctx, d),
        }
    )


# ------------------------------------------------------------------ period readiness
def readiness(
    ctx: RequestCtx, *, period_id: uuid.UUID | None, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    _, co = _co(ctx)
    per = None
    if period_id is not None:
        per = must(
            ctx,
            "SELECT id, name, start_date, end_date, status FROM acct_periods WHERE id = :i AND company_id = :c",
            i=period_id,
            c=co,
        )
        date_from, date_to = per["start_date"], per["end_date"]
    if date_from is None or date_to is None:
        raise Unprocessable("Give a period or a date range.", extra={"code": "range_required"})
    if date_to < date_from or (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise Unprocessable(f"The range must be 1 to {MAX_RANGE_DAYS} days.", extra={"code": "range_invalid"})
    tz = bk.company_tz(ctx)
    act = {
        r["day"]: r
        for r in rows(
            ctx,
            "SELECT d AS day, sum(txns) AS txns, sum(open_txns) AS open_txns, sum(docs) AS docs FROM ("
            " SELECT transaction_date AS d, count(*) AS txns, count(*) FILTER (WHERE status IN ('draft', 'awaiting_review', 'awaiting_approval')) AS open_txns, 0 AS docs"
            " FROM acct_transactions WHERE company_id = :c AND transaction_date BETWEEN :f AND :t AND reversal_of_id IS NULL GROUP BY 1"
            " UNION ALL SELECT (created_at AT TIME ZONE :tz)::date, 0, 0, count(*) FROM documents WHERE company_id = :c AND status IN ('active', 'quarantined')"
            " AND (created_at AT TIME ZONE :tz)::date BETWEEN :f AND :t GROUP BY 1) x GROUP BY d",
            c=co,
            f=date_from,
            t=date_to,
            tz=tz,
        )
    }
    sess = {
        r["business_date"]: r["status"]
        for r in rows(
            ctx,
            "SELECT business_date, status FROM bk_closing_sessions WHERE company_id = :c AND business_date BETWEEN :f AND :t AND status IN ('draft', 'in_progress', 'ready_for_review', 'closed')",
            c=co,
            f=date_from,
            t=date_to,
        )
    }
    exc = {
        r["business_date"]: int(r["n"])
        for r in rows(
            ctx,
            "SELECT business_date, count(*) AS n FROM bk_exceptions WHERE company_id = :c AND business_date BETWEEN :f AND :t AND status = 'open' AND severity IN ('error', 'critical') GROUP BY 1",
            c=co,
            f=date_from,
            t=date_to,
        )
    }
    days = []
    d = date_from
    while d <= date_to:
        a = act.get(d)
        days.append(
            {
                "date": d,
                "has_activity": bool(a and (a["txns"] or a["docs"])),
                "transactions": int(a["txns"]) if a else 0,
                "documents": int(a["docs"]) if a else 0,
                "open_transactions": int(a["open_txns"]) if a else 0,
                "open_exceptions": exc.get(d, 0),
                "closing_status": sess.get(d, "none"),
            }
        )
        d += timedelta(days=1)
    attention = one(
        ctx,
        "SELECT"
        " (SELECT count(*) FROM acct_transactions t WHERE t.company_id = :c AND t.transaction_date BETWEEN :f AND :t AND t.reversal_of_id IS NULL AND t.status IN ('approved', 'posted')"
        "   AND NOT EXISTS (SELECT 1 FROM xl_export_rows r JOIN xl_exports e ON e.id = r.export_id AND e.status = 'completed' WHERE r.transaction_id = t.id)) AS approved_not_exported,"
        " (SELECT count(*) FROM documents d WHERE d.company_id = :c AND d.status = 'active' AND (d.created_at AT TIME ZONE :tz)::date BETWEEN :f AND :t"
        "   AND COALESCE((SELECT j.status FROM processing_jobs j WHERE j.document_id = d.id ORDER BY j.created_at DESC LIMIT 1), 'queued') IN"
        "   ('queued', 'preparing_pages', 'ocr_processing', 'classifying', 'extracting', 'validating', 'suggesting', 'failed', 'needs_manual', 'awaiting_review')) AS documents_not_processed,"
        " (SELECT count(*) FROM acct_transactions t WHERE t.company_id = :c AND t.transaction_date BETWEEN :f AND :t AND t.reversal_of_id IS NULL AND t.status <> 'rejected'"
        "   AND t.source_document_id IS NULL AND t.txn_type = ANY(CAST(:ty AS text[]))) AS without_source_document",
        c=co,
        f=date_from,
        t=date_to,
        tz=tz,
        ty=list(bk.DOCUMENT_EXPECTED),
    )
    out = core.period_readiness(days, today=bk.today(ctx), period_status=per["status"] if per else None)
    out["period"] = per
    out["attention"] = {
        k: int(v) for k, v in (attention or {}).items()
    }  # worth a look before period end, not blockers on their own
    out["from"], out["to"] = date_from, date_to
    out["day_rows"] = days
    return ser(out)


# ------------------------------------------------------------------ organization overview (Firm Control Center)
def org_overview(ctx: RequestCtx, d: date | None) -> dict[str, Any]:
    """Closing status of every company the caller may see, for one day (default: each company's own today)."""
    r = rows(
        ctx.conn,
        "SELECT co.id AS company_id, co.name AS company_name, COALESCE(pol.timezone, org.timezone, 'UTC') AS timezone,"
        " COALESCE(CAST(:d AS date), (now() AT TIME ZONE COALESCE(pol.timezone, org.timezone, 'UTC'))::date) AS day,"
        " (SELECT s.status FROM bk_closing_sessions s WHERE s.company_id = co.id AND s.business_date = COALESCE(CAST(:d AS date), (now() AT TIME ZONE COALESCE(pol.timezone, org.timezone, 'UTC'))::date)"
        "   AND s.status IN ('draft', 'in_progress', 'ready_for_review', 'closed')) AS closing_status,"
        " (SELECT count(*) FROM bk_exceptions e WHERE e.company_id = co.id AND e.status = 'open' AND e.severity IN ('error', 'critical')) AS open_exceptions,"
        " (SELECT count(*) FROM bk_tasks t WHERE t.company_id = co.id AND t.status NOT IN ('completed', 'cancelled')) AS open_tasks,"
        " (SELECT count(*) FROM bk_tasks t WHERE t.company_id = co.id AND t.status NOT IN ('completed', 'cancelled') AND t.due_date < (now() AT TIME ZONE COALESCE(pol.timezone, org.timezone, 'UTC'))::date) AS overdue_tasks,"
        " (SELECT count(*) FROM generate_series(1, 14) g WHERE EXISTS (SELECT 1 FROM acct_transactions t WHERE t.company_id = co.id AND t.reversal_of_id IS NULL"
        "     AND t.transaction_date = (now() AT TIME ZONE COALESCE(pol.timezone, org.timezone, 'UTC'))::date - g)"
        "   AND NOT EXISTS (SELECT 1 FROM bk_closing_sessions s WHERE s.company_id = co.id AND s.status = 'closed' AND s.business_date = (now() AT TIME ZONE COALESCE(pol.timezone, org.timezone, 'UTC'))::date - g)) AS unclosed_recent_days"
        " FROM companies co JOIN organizations org ON org.id = co.organization_id LEFT JOIN bk_policies pol ON pol.company_id = co.id WHERE co.status <> 'archived' ORDER BY co.name LIMIT 500",
        d=d,
    )
    summary_ = {
        "companies": len(r),
        "closed": sum(1 for x in r if x["closing_status"] == "closed"),
        "ready_for_review": sum(1 for x in r if x["closing_status"] == "ready_for_review"),
        "not_started": sum(1 for x in r if x["closing_status"] is None),
        "open_exceptions": sum(int(x["open_exceptions"]) for x in r),
        "overdue_tasks": sum(int(x["overdue_tasks"]) for x in r),
    }
    return ser({"companies": r, "summary": summary_})
