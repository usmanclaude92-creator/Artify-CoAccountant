"""Accounting reporting foundation (Phase 8): read-only queries over APPROVED journals.

Only journals whose transaction a person approved (status approved, posted or reversed) are counted; a reversed journal and
its reversal both stay in, so every figure nets out and can be reproduced. All sums are base-currency NUMERIC computed in
the database and returned as strings. There is no frontend arithmetic and no invented balance: an account without
approved lines simply does not appear.

These are company-level aggregates behind the `accounting.reports.read` permission; they are not narrowed by workspace
section (a restricted document's journal still counts toward the company's trial balance)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from artify_api import accounting as acc
from artify_api import acct_core as core
from artify_api.accounting import one, rows, ser
from artify_api.context import RequestCtx
from artify_api.errors import Unprocessable

LEDGER = "('approved', 'posted', 'reversed')"


def _co(ctx: RequestCtx) -> uuid.UUID:
    assert ctx.company_id is not None
    return ctx.company_id


def _scale(ctx: RequestCtx) -> int:
    return int(acc.require_config(ctx)["amount_scale"])


def trial_balance(ctx: RequestCtx, as_of: date | None = None) -> dict[str, Any]:
    co = _co(ctx)
    scale = _scale(ctx)
    items = rows(
        ctx,
        "SELECT a.id, a.code, a.name, a.account_type, a.normal_balance, COALESCE(sum(l.base_debit), 0) AS debit, COALESCE(sum(l.base_credit), 0) AS credit"
        " FROM acct_accounts a JOIN acct_journal_lines l ON l.account_id = a.id JOIN acct_journals j ON j.id = l.journal_id"
        f" WHERE a.company_id = :c AND j.company_id = :c AND j.status IN {LEDGER} AND (CAST(:d AS date) IS NULL OR j.journal_date <= :d)"
        " GROUP BY a.id, a.code, a.name, a.account_type, a.normal_balance ORDER BY a.code",
        c=co,
        d=as_of,
    )
    out, total_dr, total_cr = [], core.ZERO, core.ZERO
    for r in items:
        net = r["debit"] - r["credit"]
        dr, cr = (net, core.ZERO) if net >= 0 else (core.ZERO, -net)
        total_dr += dr
        total_cr += cr
        out.append(
            {
                "account_id": r["id"],
                "code": r["code"],
                "name": r["name"],
                "account_type": r["account_type"],
                "total_debit": r["debit"],
                "total_credit": r["credit"],
                "debit": dr,
                "credit": cr,
            }
        )
    return ser(
        {
            "as_of": as_of,
            "scale": scale,
            "rows": out,
            "debit": total_dr,
            "credit": total_cr,
            "balanced": total_dr == total_cr,
        }
    )


def account_balances(ctx: RequestCtx, as_of: date | None = None) -> dict[str, Any]:
    tb = trial_balance(ctx, as_of)
    out = []
    types = {
        r["id"]: r["normal_balance"]
        for r in rows(ctx, "SELECT id, normal_balance FROM acct_accounts WHERE company_id = :c", c=_co(ctx))
    }
    for r in tb["rows"]:
        d, c = Decimal(r["total_debit"]), Decimal(r["total_credit"])
        nb = types.get(r["account_id"], "debit")
        out.append(
            {
                "account_id": r["account_id"],
                "code": r["code"],
                "name": r["name"],
                "account_type": r["account_type"],
                "normal_balance": nb,
                "balance": (d - c) if nb == "debit" else (c - d),
            }
        )
    return ser({"as_of": as_of, "rows": out})


def ledger(
    ctx: RequestCtx, account_id: uuid.UUID, date_from: date | None, date_to: date | None, limit: int, offset: int
) -> dict[str, Any]:
    co = _co(ctx)
    a = acc.get_account(ctx, account_id)  # 404 for another company's account
    opening = one(
        ctx,
        f"SELECT COALESCE(sum(l.base_debit - l.base_credit), 0) AS bal FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id WHERE l.account_id = :a AND j.company_id = :c AND j.status IN {LEDGER}"
        " AND CAST(:f AS date) IS NOT NULL AND j.journal_date < :f",
        a=account_id,
        c=co,
        f=date_from,
    )
    opening_bal = opening["bal"] if opening else core.ZERO
    base = (
        f"FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id JOIN acct_transactions t ON t.id = j.transaction_id WHERE l.account_id = :a AND j.company_id = :c AND j.status IN {LEDGER}"
        " AND (CAST(:f AS date) IS NULL OR j.journal_date >= :f) AND (CAST(:t AS date) IS NULL OR j.journal_date <= :t)"
    )
    p: dict[str, Any] = {"a": account_id, "c": co, "f": date_from, "t": date_to}
    total = one(ctx, f"SELECT count(*) AS n {base}", **p)
    items = rows(
        ctx,
        "SELECT * FROM (SELECT j.journal_date, j.number AS journal_number, t.number AS transaction_number, t.id AS transaction_id, l.description, l.reference, l.debit, l.credit, l.currency, l.base_debit, l.base_credit,"
        " :o + sum(l.base_debit - l.base_credit) OVER (ORDER BY j.journal_date, j.number, l.line_no ROWS UNBOUNDED PRECEDING) AS running"
        f" {base}) q ORDER BY journal_date, journal_number LIMIT :l OFFSET :off",
        **p,
        o=opening_bal,
        l=limit,
        off=offset,
    )
    nb = a["normal_balance"]
    for r in items:
        r["balance"] = r.pop("running") if nb == "debit" else -r.pop("running")
    return ser(
        {
            "account": {"id": a["id"], "code": a["code"], "name": a["name"], "normal_balance": nb},
            "opening_balance": opening_bal if nb == "debit" else -opening_bal,
            "items": items,
            "total": total["n"] if total else 0,
            "limit": limit,
            "offset": offset,
        }
    )


def contact_balances(ctx: RequestCtx, kind: str, as_of: date | None = None) -> dict[str, Any]:
    if kind not in ("customer", "supplier"):
        raise Unprocessable("kind must be customer or supplier.")
    co = _co(ctx)
    sign = "l.base_debit - l.base_credit" if kind == "customer" else "l.base_credit - l.base_debit"
    items = rows(
        ctx,
        f"SELECT c.id, c.display_name, c.tax_number, c.currency, sum({sign}) AS balance FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id"
        " JOIN acct_accounts a ON a.id = l.account_id JOIN acct_contacts c ON c.id = l.contact_id"
        f" WHERE j.company_id = :c AND c.kind = :k AND a.account_type IN ('receivable', 'payable') AND j.status IN {LEDGER} AND (CAST(:d AS date) IS NULL OR j.journal_date <= :d)"
        " GROUP BY c.id, c.display_name, c.tax_number, c.currency HAVING sum("
        + sign
        + ") <> 0 ORDER BY c.display_name",
        c=co,
        k=kind,
        d=as_of,
    )
    return ser({"kind": kind, "as_of": as_of, "rows": items, "total": sum((r["balance"] for r in items), core.ZERO)})


SUMMARY_TYPES = {
    "sales": (("sales_invoice",), ("sales_credit_note",)),
    "purchases": (("purchase_invoice",), ("purchase_credit_note",)),
    "expenses": (("cash_expense", "bank_expense", "employee_expense", "general_expense", "bank_charges"), ()),
}


def summary(ctx: RequestCtx, kind: str, date_from: date | None, date_to: date | None) -> dict[str, Any]:
    if kind not in SUMMARY_TYPES:
        raise Unprocessable("kind must be sales, purchases or expenses.")
    plus, minus = SUMMARY_TYPES[kind]
    scale = _scale(ctx)
    items = rows(
        ctx,
        "SELECT to_char(t.transaction_date, 'YYYY-MM') AS month, count(*) AS count,"
        " round(sum(CASE WHEN t.txn_type = ANY(:minus) THEN -1 ELSE 1 END * t.net_amount * COALESCE(t.exchange_rate, 1)), :s) AS net,"
        " round(sum(CASE WHEN t.txn_type = ANY(:minus) THEN -1 ELSE 1 END * t.tax_amount * COALESCE(t.exchange_rate, 1)), :s) AS tax,"
        " round(sum(CASE WHEN t.txn_type = ANY(:minus) THEN -1 ELSE 1 END * t.gross_amount * COALESCE(t.exchange_rate, 1)), :s) AS gross"
        " FROM acct_transactions t WHERE t.company_id = :c AND t.status IN ('approved', 'posted') AND t.txn_type = ANY(:all)"
        " AND (CAST(:f AS date) IS NULL OR t.transaction_date >= :f) AND (CAST(:t AS date) IS NULL OR t.transaction_date <= :t) GROUP BY 1 ORDER BY 1",
        c=_co(ctx),
        minus=list(minus),
        all=list(plus + minus),
        s=scale,
        f=date_from,
        t=date_to,
    )
    tot = {k: sum((r[k] for r in items), core.ZERO) for k in ("net", "tax", "gross")}
    return ser(
        {
            "kind": kind,
            "from": date_from,
            "to": date_to,
            "rows": items,
            "totals": {**tot, "count": sum(r["count"] for r in items)},
        }
    )


def tax_summary(ctx: RequestCtx, date_from: date | None, date_to: date | None) -> dict[str, Any]:
    """Input and output tax by tax code from approved journal lines. Input = movement on the code's input account (debit minus
    credit); output = movement on its output account (credit minus debit); the company's input/output roles stand in when a
    code names no account. A working summary only: it is NOT a VAT return."""
    co = _co(ctx)
    roles = acc.role_accounts(ctx)
    items = rows(
        ctx,
        "SELECT t.id, t.code, t.name, t.treatment,"
        " COALESCE(sum(CASE WHEN l.account_id = COALESCE(t.input_account_id, CAST(:vi AS uuid)) THEN l.base_debit - l.base_credit ELSE 0 END), 0) AS input_tax,"
        " COALESCE(sum(CASE WHEN l.account_id = COALESCE(t.output_account_id, CAST(:vo AS uuid)) THEN l.base_credit - l.base_debit ELSE 0 END), 0) AS output_tax"
        " FROM acct_tax_codes t JOIN acct_journal_lines l ON l.tax_code_id = t.id JOIN acct_journals j ON j.id = l.journal_id"
        f" WHERE t.company_id = :c AND j.company_id = :c AND j.status IN {LEDGER} AND (CAST(:f AS date) IS NULL OR j.journal_date >= :f) AND (CAST(:t AS date) IS NULL OR j.journal_date <= :t)"
        " GROUP BY t.id, t.code, t.name, t.treatment ORDER BY t.code",
        c=co,
        vi=roles.get("vat_input"),
        vo=roles.get("vat_output"),
        f=date_from,
        t=date_to,
    )
    ti = sum((r["input_tax"] for r in items), core.ZERO)
    to = sum((r["output_tax"] for r in items), core.ZERO)
    return ser(
        {
            "from": date_from,
            "to": date_to,
            "rows": items,
            "input_tax": ti,
            "output_tax": to,
            "net_payable": to - ti,
            "note": "Working summary from approved journals; not a VAT return.",
        }
    )
