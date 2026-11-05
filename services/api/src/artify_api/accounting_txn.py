"""Accounting transactions, double-entry journals and the approval workflow (Phase 8).

Flow: an accountant-APPROVED Phase 7 extraction (or a hand-made draft) becomes a transaction draft; the deterministic
core resolves accounts, tax and exchange rates, generates the journal and validates it; a person reviews, corrects and
approves. Nothing here approves on its own: approval writes the approving user, needs a permission, respects the
company's separation-of-duties policy, and is refused while any blocking validation issue exists. Approved records are
immutable (database triggers back this up): corrections are reversals or amendments."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from artify_api import accounting as acc
from artify_api import acct_core as core
from artify_api import entitlements
from artify_api.accounting import one, rows, ser
from artify_api.context import RequestCtx
from artify_api.documents import doc_access, visible_node_ids
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id

TXN_LABEL = {t: t.replace("_", " ").capitalize() for t in core.TXN_TYPES}
ACTIVE_STATES = ("draft", "awaiting_review", "awaiting_approval")
LEDGER_STATES = ("approved", "posted", "reversed")
LINE_ROLE = {
    "sales_invoice": "revenue_default",
    "sales_credit_note": "revenue_default",
    "purchase_invoice": "expense_default",
    "purchase_credit_note": "expense_default",
    "cash_expense": "expense_default",
    "bank_expense": "expense_default",
    "employee_expense": "expense_default",
    "general_expense": "expense_default",
    "bank_charges": "bank_charges",
    "bank_deposit": "suspense",
    "bank_withdrawal": "suspense",
}
CONTROL_ROLE = {
    "sales_invoice": "ar_control",
    "sales_credit_note": "ar_control",
    "customer_receipt": "ar_control",
    "customer_refund": "ar_control",
    "purchase_invoice": "ap_control",
    "purchase_credit_note": "ap_control",
    "supplier_payment": "ap_control",
    "supplier_refund": "ap_control",
    "employee_expense": "employee_payable",
    "general_expense": "ap_control",
}
CONTACT_KIND = {
    **dict.fromkeys(core.SALES_TYPES | {"customer_receipt", "customer_refund"}, "customer"),
    **dict.fromkeys(core.PURCHASE_TYPES | {"supplier_payment", "supplier_refund"}, "supplier"),
}
TAX_DIRECTION = {
    **dict.fromkeys(core.SALES_TYPES | {"bank_deposit"}, "output"),
    **dict.fromkeys(core.PURCHASE_TYPES | core.EXPENSE_TYPES | {"bank_withdrawal", "bank_charges"}, "input"),
}
HEADER_COLS = (
    "id, number, txn_type, status, source_document_id, extraction_id, source_ref, document_checksum, transaction_date, due_date, doc_number,"
    " contact_id, contact_suggestions, settlement_account_id, counter_account_id, currency, exchange_rate, rate_source, rate_date,"
    " prices_include_tax, net_amount, tax_amount, gross_amount, stated_amounts, base_gross_amount, narration, journal_mode, validation,"
    " validated_at, override_note, duplicate_status, correction_note, prepared_by, submitted_by, submitted_at, reviewed_by, reviewed_at,"
    " approved_by, approved_at, rejected_by, rejected_at, rejection_reason, reversal_of_id, reversed_by, reversed_at, amends_id,"
    " created_at, updated_at, row_version"
)


def U(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _u(value: Any) -> uuid.UUID | None:
    return None if value is None or value == "" else U(value)


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a) == str(b)


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


def _currency_scale(ctx: RequestCtx, code: str) -> int:
    r = one(ctx, "SELECT minor_unit FROM currencies WHERE code = :c", c=code)
    if r is None:
        raise Unprocessable(f"Unknown currency {code}.")
    return int(r["minor_unit"])


# ------------------------------------------------------------------ loading and visibility
def _visible_doc(ctx: RequestCtx, doc_id: Any) -> bool:
    """A transaction made from a document is only as visible as the document (restricted sections stay restricted).
    A permanently deleted document leaves nothing to protect."""
    if doc_id is None:
        return True
    d = one(
        ctx, "SELECT node_id, purged_at FROM documents WHERE id = :i AND company_id = :c", i=U(doc_id), c=ctx.company_id
    )
    if d is None or d["purged_at"] is not None:
        return True
    access = doc_access(ctx)
    return str(d["node_id"]) in access.nodes and access.visible(str(d["node_id"]))


def load_txn(ctx: RequestCtx, tid: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    t = one(
        ctx,
        f"SELECT {HEADER_COLS} FROM acct_transactions WHERE company_id = :c AND id = :i"
        + (" FOR UPDATE" if lock else ""),
        c=co,
        i=tid,
    )
    if t is None or not _visible_doc(ctx, t["source_document_id"]):
        raise NotFound("Transaction not found.")
    return t


def _lines(ctx: RequestCtx, tid: Any) -> list[dict[str, Any]]:
    return rows(
        ctx,
        "SELECT l.id, l.line_no, l.description, l.account_id, l.account_source, l.entry_side, l.quantity, l.unit_price, l.discount, l.net_amount,"
        " l.tax_amount, l.gross_amount, l.tax_code_id, l.dimensions, l.source_ref, l.calc, a.code AS account_code, a.name AS account_name,"
        " tc.code AS tax_code FROM acct_transaction_lines l LEFT JOIN acct_accounts a ON a.id = l.account_id"
        " LEFT JOIN acct_tax_codes tc ON tc.id = l.tax_code_id WHERE l.transaction_id = :t ORDER BY l.line_no",
        t=U(tid),
    )


def _journal(ctx: RequestCtx, tid: Any) -> dict[str, Any] | None:
    j = one(
        ctx,
        "SELECT id, number, journal_type, journal_date, period_id, narration, status, currency, base_currency, exchange_rate, rate_source, rate_date,"
        " total_debit, total_credit, base_total_debit, base_total_credit, approved_by, approved_at, reversal_of_id FROM acct_journals WHERE transaction_id = :t",
        t=U(tid),
    )
    if j is None:
        return None
    j["lines"] = rows(
        ctx,
        "SELECT l.id, l.line_no, l.account_id, a.code AS account_code, a.name AS account_name, l.debit, l.credit, l.base_debit, l.base_credit, l.currency,"
        " l.description, l.reference, l.tax_code_id, l.contact_id, l.dimensions, l.source FROM acct_journal_lines l JOIN acct_accounts a ON a.id = l.account_id"
        " WHERE l.journal_id = :j ORDER BY l.line_no",
        j=U(j["id"]),
    )
    return j


# ------------------------------------------------------------------ numbering
def _next_number(ctx: RequestCtx, key: str, cfg: dict[str, Any]) -> str:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO acct_counters (organization_id, company_id, key, value) VALUES (:o, :c, :k, 0) ON CONFLICT (company_id, key) DO NOTHING"
        ),
        {"o": org, "c": co, "k": key},
    )
    value = ctx.conn.execute(
        text("UPDATE acct_counters SET value = value + 1 WHERE company_id = :c AND key = :k RETURNING value"),
        {"c": co, "k": key},
    ).scalar_one()
    spec = (cfg["numbering"] or {}).get(key) or {}
    return core.next_number(
        spec.get("prefix", "TX-" if key == "transaction" else "JV-"), int(spec.get("padding", 6)), int(value)
    )


# ------------------------------------------------------------------ drafts
def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return core.D(value)
    except (TypeError, ValueError) as exc:
        raise Unprocessable(f"“{value}” is not a valid amount.") from exc


def _guard_creation(ctx: RequestCtx) -> None:
    org, _ = _co(ctx)
    entitlements.guard_quota(ctx.conn, org, "quota.accounting_transactions_per_month", 1)
    entitlements.guard_quota(ctx.conn, org, "quota.journals_per_month", 1)


def _trail(
    ctx: RequestCtx, tid: Any, action: str, from_status: str | None, to_status: str | None, note: str | None = None
) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO acct_approvals (id, organization_id, company_id, transaction_id, action, from_status, to_status, actor_type, actor_id, note)"
            " VALUES (:id, :o, :c, :t, :a, :f, :s, 'user', :u, :n)"
        ),
        {
            "id": new_id(),
            "o": org,
            "c": co,
            "t": U(tid),
            "a": action,
            "f": from_status,
            "s": to_status,
            "u": ctx.user_id,
            "n": note,
        },
    )


def _insert_txn(ctx: RequestCtx, cfg: dict[str, Any], h: dict[str, Any]) -> uuid.UUID:
    org, co = _co(ctx)
    tid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_transactions (id, organization_id, company_id, number, txn_type, status, source_document_id, extraction_id, source_ref, source_key,"
            " document_checksum, transaction_date, due_date, doc_number, contact_id, contact_suggestions, settlement_account_id, counter_account_id, currency,"
            " exchange_rate, rate_source, rate_date, prices_include_tax, stated_amounts, narration, journal_mode, prepared_by, amends_id)"
            " VALUES (:id, :o, :c, :num, :t, 'draft', :sd, :ex, :sr, :sk, :ck, :d, :due, :dn, :ct, CAST(:cs AS jsonb), :sa, :ca, :cur, :er, :rs, :rd, :inc,"
            " CAST(:st AS jsonb), :nar, :jm, :u, :am)"
        ),
        {
            "id": tid,
            "o": org,
            "c": co,
            "num": _next_number(ctx, "transaction", cfg),
            "t": h["txn_type"],
            "sd": _u(h.get("source_document_id")),
            "ex": _u(h.get("extraction_id")),
            "sr": _u(h.get("source_ref")),
            "sk": str(h["source_ref"]) if h.get("source_ref") else "doc",
            "ck": h.get("document_checksum"),
            "d": h["transaction_date"],
            "due": h.get("due_date"),
            "dn": h.get("doc_number"),
            "ct": _u(h.get("contact_id")),
            "cs": json.dumps(h.get("contact_suggestions") or []),
            "sa": _u(h.get("settlement_account_id")),
            "ca": _u(h.get("counter_account_id")),
            "cur": h["currency"],
            "er": h.get("exchange_rate"),
            "rs": h.get("rate_source"),
            "rd": h.get("rate_date"),
            "inc": bool(h.get("prices_include_tax", False)),
            "st": json.dumps(ser(h.get("stated_amounts") or {})),
            "nar": h.get("narration"),
            "jm": h.get("journal_mode", "generated"),
            "u": ctx.user_id,
            "am": _u(h.get("amends_id")),
        },
    )
    return tid


def _write_lines(ctx: RequestCtx, tid: uuid.UUID, lines: list[dict[str, Any]]) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(text("DELETE FROM acct_transaction_lines WHERE transaction_id = :t"), {"t": tid})
    for i, ln in enumerate(lines, 1):
        ctx.conn.execute(
            text(
                "INSERT INTO acct_transaction_lines (id, organization_id, company_id, transaction_id, line_no, description, account_id, account_source, entry_side,"
                " quantity, unit_price, discount, tax_code_id, dimensions, source_ref, calc) VALUES (:id, :o, :c, :t, :n, :d, :a, :as, :es, :q, :up, :dc, :tc,"
                " CAST(:dm AS jsonb), CAST(:sr AS jsonb), CAST(:calc AS jsonb))"
            ),
            {
                "id": new_id(),
                "o": org,
                "c": co,
                "t": tid,
                "n": i,
                "d": ln.get("description"),
                "a": _u(ln.get("account_id")),
                "as": "manual" if ln.get("account_id") else "unresolved",
                "es": ln.get("entry_side"),
                "q": _as_decimal(ln.get("quantity")),
                "up": _as_decimal(ln.get("unit_price")),
                "dc": _as_decimal(ln.get("discount")) or core.ZERO,
                "tc": _u(ln.get("tax_code_id")),
                "dm": json.dumps(ln.get("dimensions") or {}),
                "sr": json.dumps(ln.get("source_ref") or {}),
                "calc": json.dumps({"input": ser({"amount": ln.get("amount"), "tax_amount": ln.get("tax_amount")})}),
            },
        )


def _check_lines(ctx: RequestCtx, typ: str, lines: list[dict[str, Any]]) -> None:
    for ln in lines:
        for k, getter in (("account_id", acc.get_account), ("tax_code_id", acc.get_tax_code)):
            if ln.get(k):
                getter(ctx, U(ln[k]))  # 404 when it belongs to another company
        if ln.get("entry_side") and typ not in core.MANUAL_TYPES:
            raise Unprocessable("Debit/credit sides are only used by journal-type transactions.")
        if (ln.get("amount") is None) == (ln.get("unit_price") is None):
            raise Unprocessable("Each line needs an amount, or a unit price and quantity.")
        for k in ("amount", "quantity", "unit_price", "discount", "tax_amount"):
            v = _as_decimal(ln.get(k))
            if v is not None and v < 0:
                raise Unprocessable(
                    "Amounts cannot be negative; credit notes and credits are expressed by the transaction type."
                )


def create_draft(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    """A hand-made draft (no document). Lines: `amount` (+ optional `tax_amount` as printed), `tax_code_id`, `account_id`, `entry_side`."""
    cfg = acc.require_config(ctx, lock=True)
    typ = body["txn_type"]
    if typ not in core.TXN_TYPES:
        raise Unprocessable("Unknown transaction type.")
    if typ == "reversal_entry":
        raise Unprocessable("Reversal entries are created by reversing an approved transaction.")
    _check_lines(ctx, typ, body.get("lines") or [])
    _guard_creation(ctx)
    for k in ("contact_id", "settlement_account_id", "counter_account_id"):
        if body.get(k):
            (acc.get_contact if k == "contact_id" else acc.get_account)(ctx, U(body[k]))
    h = {**body, "currency": body.get("currency") or cfg["base_currency"]}
    if body.get("exchange_rate") is not None:
        h["exchange_rate"] = core.D(body["exchange_rate"])
        h.setdefault("rate_source", "manual")
        h.setdefault("rate_date", body["transaction_date"])
    tid = _insert_txn(ctx, cfg, h)
    _write_lines(ctx, tid, body.get("lines") or [])
    if typ in core.MANUAL_TYPES:
        ctx.conn.execute(text("UPDATE acct_transactions SET journal_mode = 'generated' WHERE id = :i"), {"i": tid})
    _trail(ctx, tid, "create", None, "draft")
    acc.audit_event(ctx, "accounting.transaction_created", "acct_transaction", tid, {"type": typ})
    rebuild(ctx, tid)
    return view(ctx, tid)


# ------------------------------------------------------------------ extraction -> transaction
_PARTY: dict[str, dict[str, tuple[str, str | None]]] = {
    "invoice": {
        "purchase": ("supplier_name", "supplier_tax_number"),
        "sales": ("customer_name", "customer_tax_number"),
    },
    "adjustment_note": {"purchase": ("supplier_name", None), "sales": ("customer_name", None)},
    "receipt": {"purchase": ("merchant_name", None)},
}
_AMOUNTS = {
    "invoice": ("net_amount", "vat_amount", "gross_amount", "invoice_number", "invoice_date", "due_date"),
    "adjustment_note": ("net_adjustment", "vat_adjustment", "gross_adjustment", "document_number", "issue_date", None),
    "receipt": ("net_amount", "vat_amount", "total_amount", "receipt_number", "transaction_date", None),
}


def _extraction(ctx: RequestCtx, extraction_id: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    e = one(
        ctx,
        "SELECT e.id, e.document_id, e.status, e.type_key, e.schema_key, e.classification_id, d.content_sha256, d.display_name FROM extractions e"
        " JOIN documents d ON d.id = e.document_id WHERE e.company_id = :c AND e.id = :i",
        c=co,
        i=extraction_id,
    )
    if e is None or not _visible_doc(ctx, e["document_id"]):
        raise NotFound("Extraction not found.")
    return e


def _direction(ctx: RequestCtx, e: dict[str, Any]) -> tuple[str | None, str | None]:
    """(transaction type, direction) for an approved extraction, from the classification the accountant confirmed."""
    kind = None
    if e["classification_id"]:
        c = one(ctx, "SELECT kind_key FROM document_classifications WHERE id = :i", i=U(e["classification_id"]))
        kind = c["kind_key"] if c else None
    if kind in ("sales_invoice", "purchase_invoice", "sales_credit_note", "purchase_credit_note"):
        return kind, "sales" if kind.startswith("sales") else "purchase"
    if kind == "expense_receipt":
        return ("cash_expense" if e["schema_key"] == "receipt" else "general_expense"), "purchase"
    return None, None


def _field_values(ctx: RequestCtx, extraction_id: Any) -> dict[str, str]:
    return {
        r["field_key"]: r["value"]
        for r in rows(
            ctx,
            "SELECT field_key, value FROM extracted_fields WHERE extraction_id = :e AND review_status IN ('confirmed', 'corrected', 'extracted') AND value IS NOT NULL",
            e=U(extraction_id),
        )
    }


def _guess_tax_code(ctx: RequestCtx, rate: Decimal | None, direction: str, on: date) -> tuple[str | None, str | None]:
    """The tax code whose rate in force on `on` equals the printed rate. Several or none = no guess (the accountant picks)."""
    if rate is None or rate == 0:
        return None, None
    _, co = _co(ctx)
    cands = rows(
        ctx,
        "SELECT t.id, t.code FROM acct_tax_codes t JOIN acct_tax_rates r ON r.tax_code_id = t.id WHERE t.company_id = :c AND t.is_active AND t.treatment = 'standard'"
        " AND t.direction IN (:d, 'both') AND r.rate = :r AND r.effective_from <= :on AND (r.effective_to IS NULL OR r.effective_to >= :on)",
        c=co,
        d="output" if direction == "sales" else "input",
        r=rate,
        on=on,
    )
    return (
        (str(cands[0]["id"]), None)
        if len(cands) == 1
        else (
            None,
            f"{len(cands)} tax codes match a {format(rate, 'f')}% rate"
            if cands
            else f"no tax code matches a {format(rate, 'f')}% rate",
        )
    )


def create_from_extraction(ctx: RequestCtx, extraction_id: uuid.UUID, *, rows_limit: int = 300) -> dict[str, Any]:
    """Draft transaction(s) from an APPROVED extraction. Unapproved extractions never reach the accounting engine."""
    cfg = acc.require_config(ctx, lock=True)
    e = _extraction(ctx, extraction_id)
    if e["status"] != "approved":
        raise Conflict("Only extractions that an accountant has approved can become accounting transactions.")
    if e["schema_key"] == "bank_statement":
        return _from_statement(ctx, cfg, e, rows_limit)
    if e["schema_key"] not in _AMOUNTS:
        raise Unprocessable("Documents of this type do not create accounting transactions.")
    typ, direction = _direction(ctx, e)
    if typ is None or direction is None:
        raise Unprocessable(
            "Whether this document is a sale or a purchase has not been determined. Confirm the document type in the review screen first."
        )
    if one(
        ctx,
        "SELECT 1 AS x FROM acct_transactions WHERE extraction_id = :e AND source_key = 'doc' AND status <> 'rejected' AND reversal_of_id IS NULL AND amends_id IS NULL",
        e=extraction_id,
    ):
        raise Conflict("A transaction already exists for this extraction. Reject it first to create another.")
    _guard_creation(ctx)
    f = _field_values(ctx, extraction_id)
    net_k, vat_k, gross_k, num_k, date_k, due_k = _AMOUNTS[e["schema_key"]]
    stated = {"net": _as_decimal(f.get(net_k)), "tax": _as_decimal(f.get(vat_k)), "gross": _as_decimal(f.get(gross_k))}
    tx_date = _parse_date(f.get(date_k))
    if tx_date is None:
        raise Unprocessable("The document date is missing or unreadable; correct it in the extraction review first.")
    currency = (f.get("currency") or cfg["base_currency"]).upper()
    name_k, tax_k = _PARTY.get(e["schema_key"], {}).get(direction) or (None, None)
    suggestions = acc.match_contact_candidates(
        ctx, f.get(name_k) if name_k else None, f.get(tax_k) if tax_k else None, CONTACT_KIND.get(typ) or "supplier"
    )
    rate = _as_decimal(f.get("vat_rate"))
    code_id, why = _guess_tax_code(ctx, rate, direction, tx_date)
    lines = _extraction_lines(ctx, extraction_id, e["schema_key"], stated, code_id)
    h = {
        "txn_type": typ,
        "source_document_id": e["document_id"],
        "extraction_id": extraction_id,
        "document_checksum": e["content_sha256"],
        "transaction_date": tx_date,
        "due_date": _parse_date(f.get(due_k)) if due_k else None,
        "doc_number": f.get(num_k),
        "contact_suggestions": suggestions,
        "currency": currency,
        "stated_amounts": {
            **{k: v for k, v in stated.items() if v is not None},
            "vat_rate": rate,
            "tax_code_note": why,
        },
    }
    tid = _insert_txn(ctx, cfg, h)
    _write_lines(ctx, tid, lines)
    _trail(ctx, tid, "create", None, "draft", f"From approved extraction of “{e['display_name']}”")
    acc.audit_event(
        ctx,
        "accounting.transaction_created",
        "acct_transaction",
        tid,
        {"type": typ, "extraction_id": str(extraction_id)},
    )
    rebuild(ctx, tid)
    return view(ctx, tid)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _extraction_lines(
    ctx: RequestCtx, extraction_id: Any, schema_key: str, stated: dict[str, Decimal | None], code_id: str | None
) -> list[dict[str, Any]]:
    items = rows(
        ctx,
        "SELECT id, line_no, page_no, \"values\" AS vals FROM extracted_line_items WHERE extraction_id = :e AND review_status IN ('confirmed', 'corrected', 'extracted') ORDER BY line_no",
        e=U(extraction_id),
    )
    usable = [i for i in items if i["vals"].get("amount")]
    if schema_key == "invoice" and usable and len(usable) == len(items):
        out = []
        for i in usable:
            v = i["vals"]
            out.append(
                {
                    "description": v.get("description"),
                    "quantity": v.get("quantity"),
                    "unit_price": v.get("unit_price"),
                    "discount": v.get("discount"),
                    "amount": v["amount"],
                    "tax_amount": v.get("vat_amount"),
                    "tax_code_id": code_id,
                    "source_ref": {"line_item_id": str(i["id"]), "page": i["page_no"]},
                }
            )
        return out
    net, tax, gross = stated["net"], stated["tax"], stated["gross"]
    note = None
    if net is None and gross is not None and tax is not None:
        net, note = gross - tax, "net derived from gross minus tax"
    elif gross is None and net is not None and tax is not None:
        gross, note = net + tax, "gross derived from net plus tax"
    if net is None and gross is not None and tax is None:
        net = gross  # a receipt with only a total: tax is unknown, not zero; flagged by the missing tax code
        note = "only a total was extracted; tax is not separated"
    if net is None:
        return []
    return [
        {
            "description": None,
            "amount": net,
            "tax_amount": tax,
            "tax_code_id": code_id,
            "source_ref": {"derived": note} if note else {},
        }
    ]


def _from_statement(ctx: RequestCtx, cfg: dict[str, Any], e: dict[str, Any], limit: int) -> dict[str, Any]:
    """One draft per confirmed bank-statement row. Bank rows have no counter account: the accountant (or a rule) chooses it."""
    st = one(ctx, "SELECT id, currency FROM bank_statements WHERE extraction_id = :e", e=e["id"])
    if st is None:
        raise Unprocessable("This statement has no rows.")
    txs = rows(
        ctx,
        "SELECT x.id, x.tx_date, x.description, x.reference, x.debit, x.credit, x.currency FROM bank_statement_transactions x WHERE x.statement_id = :s"
        " AND x.review_status IN ('confirmed', 'corrected', 'extracted') AND x.tx_date IS NOT NULL ORDER BY x.row_no",
        s=st["id"],
    )
    done = {
        r["source_key"]
        for r in rows(
            ctx, "SELECT source_key FROM acct_transactions WHERE extraction_id = :e AND status <> 'rejected'", e=e["id"]
        )
    }
    todo = [t for t in txs if str(t["id"]) not in done and ((t["debit"] or 0) > 0) != ((t["credit"] or 0) > 0)]
    already = len([t for t in txs if str(t["id"]) in done])
    created, skipped = [], len(txs) - len(todo) - already
    for t in todo[:limit]:
        _guard_creation(ctx)
        withdrawal = (t["debit"] or 0) > 0
        amount = t["debit"] if withdrawal else t["credit"]
        h = {
            "txn_type": "bank_withdrawal" if withdrawal else "bank_deposit",
            "source_document_id": e["document_id"],
            "extraction_id": e["id"],
            "source_ref": t["id"],
            "document_checksum": e["content_sha256"],
            "transaction_date": t["tx_date"],
            "doc_number": t["reference"],
            "currency": (t["currency"] or st["currency"] or cfg["base_currency"]).upper(),
            "stated_amounts": {"gross": amount},
            "narration": t["description"],
        }
        tid = _insert_txn(ctx, cfg, h)
        _write_lines(
            ctx,
            tid,
            [{"description": t["description"], "amount": amount, "source_ref": {"statement_row": str(t["id"])}}],
        )
        _trail(ctx, tid, "create", None, "draft", "From an approved bank statement row")
        rebuild(ctx, tid)
        created.append(str(tid))
    acc.audit_event(ctx, "accounting.statement_drafts_created", "acct_transaction", e["id"], {"created": len(created)})
    return ser(
        {
            "created": created,
            "remaining": max(0, len(todo) - limit),
            "skipped_rows": max(0, skipped),
            "transactions": [brief(ctx, U(i)) for i in created[:50]],
        }
    )


# ------------------------------------------------------------------ the rebuild: resolve, calculate, journal, validate
def _facts(
    t: dict[str, Any], ln: dict[str, Any], contact: dict[str, Any] | None, doc_type: str | None
) -> dict[str, Any]:
    dims = ln.get("dimensions") or {}
    return {
        "txn_type": t["txn_type"],
        "document_type": doc_type,
        "contact_id": str(t["contact_id"]) if t["contact_id"] else None,
        "contact_name": (contact or {}).get("display_name") or (contact or {}).get("legal_name"),
        "contact_tax_number": (contact or {}).get("tax_number"),
        "description": ln.get("description") or t.get("narration"),
        "currency": t["currency"],
        "amount": format(ln["gross_amount"], "f") if ln.get("gross_amount") is not None else None,
        "doc_number": t.get("doc_number"),
        "project": dims.get("project"),
        "cost_center": dims.get("cost_center"),
        "department": dims.get("department"),
    }


def rebuild(ctx: RequestCtx, tid: uuid.UUID) -> list[core.Issue]:
    """Recompute everything derived from a DRAFT transaction's inputs and store it: line amounts, resolved accounts and tax, the
    journal (unless the accountant edited it by hand), base-currency amounts, duplicate matches and the validation result.
    Pure decisions come from `acct_core`; this function only loads data and stores the outcome. Never changes what the document says."""
    org, co = _co(ctx)
    cfg = acc.require_config(ctx)
    t = load_txn(ctx, tid, lock=True)
    if t["status"] != "draft":
        raise Conflict("Only a draft transaction can be recalculated.")
    scale = _currency_scale(ctx, t["currency"])
    base_scale, mode, scope = cfg["amount_scale"], cfg["rounding_mode"], cfg["tax_rounding_scope"]
    typ = t["txn_type"]
    issues: list[core.Issue] = []
    lines = _lines(ctx, tid)
    contact = acc.get_contact(ctx, U(t["contact_id"])) if t["contact_id"] else None
    roles = acc.role_accounts(ctx)
    rules = acc.load_rules(ctx)
    on = t["transaction_date"]
    manual_type = typ in core.MANUAL_TYPES

    # --- tax specs and amounts per line
    specs: dict[int, core.TaxSpec | None] = {}
    tax_dir: dict[int, str] = {}
    doc_type = None
    if t["extraction_id"]:
        r = one(ctx, "SELECT type_key FROM extractions WHERE id = :e", e=U(t["extraction_id"]))
        doc_type = r["type_key"] if r else None
    for ln in lines:
        code_id = ln["tax_code_id"]
        if code_id is None and not manual_type and typ not in core.SETTLEMENT_TYPES:
            res = core.resolve_rules(rules, _facts(t, ln, contact, doc_type), on, kind="tax")
            if res.conflict:
                issues.append(
                    core.err(
                        "rule_conflict",
                        f"Line {ln['line_no']}: tax rules conflict.",
                        rules=list(res.rule_ids),
                        line=ln["line_no"],
                    )
                )
            elif res.value:
                code_id = uuid.UUID(str(res.value))
            elif contact and contact.get("default_tax_code_id"):
                code_id = contact["default_tax_code_id"]
        if code_id:
            spec, spec_issues = acc.tax_spec_for(ctx, U(code_id), on)
            issues += [
                core.Issue(i.code, i.severity, f"Line {ln['line_no']}: {i.message}", i.blocking, i.data)
                for i in spec_issues
            ]
            specs[ln["line_no"]] = spec
            ln["tax_code_id"] = code_id
            d = one(ctx, "SELECT direction FROM acct_tax_codes WHERE id = :i", i=U(code_id))
            tax_dir[ln["line_no"]] = d["direction"] if d else "both"
        else:
            specs[ln["line_no"]] = None

    calc_in: list[core.LineIn] = []
    for ln in lines:
        inp = (ln["calc"] or {}).get("input") or {}
        amount = _as_decimal(inp.get("amount"))
        if amount is None and ln["unit_price"] is None:
            amount = core.ZERO
        calc_in.append(
            core.LineIn(ln["quantity"], ln["unit_price"], amount, ln["discount"] or core.ZERO, specs.get(ln["line_no"]))
        )
    inclusive = bool(t["prices_include_tax"])
    if manual_type or typ in core.SETTLEMENT_TYPES:
        calcs = [
            core.LineCalc(c.amount or core.ZERO, core.ZERO, c.amount or core.ZERO, core.ZERO, core.ZERO)
            for c in calc_in
        ]
    else:
        calcs, _totals = (
            core.calc_lines(calc_in, inclusive=inclusive, scale=scale, mode=mode, scope=scope)
            if calc_in
            else ([], core.DocTotals(core.ZERO, core.ZERO, core.ZERO, {}))
        )

    # explicit tax as printed on the document wins over our calculation; the difference is reported, never hidden
    final: list[tuple[Decimal, Decimal, Decimal]] = []
    for ln, c, ci in zip(lines, calcs, calc_in, strict=True):
        inp = (ln["calc"] or {}).get("input") or {}
        explicit = _as_decimal(inp.get("tax_amount"))
        if explicit is not None and not manual_type and typ not in core.SETTLEMENT_TYPES:
            net, tax, gross = (
                ((ci.amount or core.ZERO) - explicit, explicit, ci.amount or core.ZERO)
                if inclusive
                else (ci.amount or core.ZERO, explicit, (ci.amount or core.ZERO) + explicit)
            )
            net, tax, gross = (
                core.rnd(net or core.ZERO, scale, mode),
                core.rnd(tax, scale, mode),
                core.rnd(gross or core.ZERO, scale, mode),
            )
            if abs(tax - c.tax) > cfg["tolerance"]:
                issues.append(
                    core.soft_err(
                        "tax_mismatch",
                        f"Line {ln['line_no']}: the tax on the document ({format(tax, 'f')}) differs from the calculation ({format(c.tax, 'f')}).",
                        line=ln["line_no"],
                        stated=format(tax, "f"),
                        calculated=format(c.tax, "f"),
                        difference=format(tax - c.tax, "f"),
                    )
                )
            final.append((net, tax, gross))
        else:
            final.append((c.net, c.tax, c.gross))
        if (
            explicit is None
            and ci.tax is None
            and typ not in core.SETTLEMENT_TYPES
            and not manual_type
            and cfg["tax_registered"]
        ):
            issues.append(
                core.warn(
                    "tax_treatment_unresolved",
                    f"Line {ln['line_no']} has no tax code. Choose standard, zero-rated, exempt or out-of-scope; none is assumed.",
                    line=ln["line_no"],
                )
            )
    net_total = sum((f[0] for f in final), core.ZERO)
    tax_total = sum((f[1] for f in final), core.ZERO)
    gross_total = sum((f[2] for f in final), core.ZERO)

    # --- compare the document's own totals with what the lines add up to (reports only)
    stated = {k: _as_decimal((t["stated_amounts"] or {}).get(k)) for k in ("net", "tax", "gross")}
    if t["extraction_id"] and t["source_ref"] is None:
        issues += core.compare_totals(
            stated, core.DocTotals(net_total, tax_total, gross_total, {}), tolerance=cfg["tolerance"], scale=scale
        )

    # --- account resolution
    resolved: list[core.TLine] = []
    for ln, (net, tax, gross) in zip(lines, final, strict=True):
        src = ln["account_source"] if ln["account_id"] else "unresolved"
        account_id = ln["account_id"]
        if account_id is None and typ not in core.SETTLEMENT_TYPES and typ != "bank_transfer":
            res = core.resolve_rules(
                rules, _facts(t, {**ln, "gross_amount": gross}, contact, doc_type), on, kind="account", target="line"
            )
            if res.conflict:
                issues.append(
                    core.err(
                        "rule_conflict",
                        f"Line {ln['line_no']}: accounting rules conflict; choose the account yourself.",
                        rules=list(res.rule_ids),
                        line=ln["line_no"],
                    )
                )
            elif res.value:
                account_id, src = uuid.UUID(str(res.value)), "rule"
            elif LINE_ROLE.get(typ) and roles.get(LINE_ROLE[typ]):
                account_id, src = uuid.UUID(roles[LINE_ROLE[typ]]), "role_default"
                issues.append(
                    core.warn(
                        "account_defaulted",
                        f"Line {ln['line_no']} uses the company's default {LINE_ROLE[typ].replace('_', ' ')} account.",
                        line=ln["line_no"],
                    )
                )
        dims = dict(ln["dimensions"] or {})
        for dim in core.DIMENSIONS:
            if not dims.get(dim):
                dres = core.resolve_rules(
                    [r for r in rules if r.kind == "dimension" and r.action.get("name") == dim],
                    _facts(t, ln, contact, doc_type),
                    on,
                    kind="dimension",
                )
                if dres.value:
                    dims[dim] = dres.value[1]
        spec = specs.get(ln["line_no"])
        input_acct = (
            (str(spec.input_account_id) if spec and spec.input_account_id else roles.get("vat_input")) if spec else None
        )
        output_acct = (
            (str(spec.output_account_id) if spec and spec.output_account_id else roles.get("vat_output"))
            if spec
            else None
        )
        want = TAX_DIRECTION.get(typ)
        if spec and want and tax_dir.get(ln["line_no"]) not in (want, "both", None):
            issues.append(
                core.err(
                    "tax_direction_mismatch",
                    f"Line {ln['line_no']}: tax code {ln['tax_code'] or ''} is for {tax_dir[ln['line_no']]} tax but this is a {want} transaction.".replace(
                        "  ", " "
                    ),
                    line=ln["line_no"],
                )
            )
        if spec and tax and want:
            need_out = want == "output" or spec.treatment == "reverse_charge"
            need_in = spec.recoverable and (want == "input" or spec.treatment == "reverse_charge")
            if (need_out and not output_acct) or (need_in and not input_acct):
                issues.append(
                    core.err(
                        "tax_account_missing",
                        f"Line {ln['line_no']}: no tax account is configured for this tax code (set it on the tax code or map the company's input/output tax roles).",
                        line=ln["line_no"],
                    )
                )
        if (
            spec
            and spec.treatment in ("reverse_charge", "import")
            and not entitlements.has_feature(ctx.conn, org, "feature.advanced_accounting")
        ):
            issues.append(
                core.err(
                    "feature_not_in_plan",
                    f"The “{spec.treatment}” treatment is not included in the current plan.",
                    line=ln["line_no"],
                )
            )
        resolved.append(
            core.TLine(
                ln["line_no"],
                ln["description"],
                str(account_id) if account_id else None,
                net,
                tax,
                gross,
                str(ln["tax_code_id"]) if ln["tax_code_id"] else None,
                spec.treatment if spec else None,
                input_acct,
                output_acct,
                spec.recoverable if spec else True,
                ln["entry_side"],
                dims,
            )
        )
        ln["_resolved"] = (account_id, src, dims, net, tax, gross)

    # control / settlement / counter accounts
    control = None
    if typ in CONTROL_ROLE:
        cres = core.resolve_rules(rules, _facts(t, {}, contact, doc_type), on, kind="account", target="control")
        if cres.conflict:
            issues.append(
                core.err(
                    "rule_conflict", "Accounting rules conflict on the control account.", rules=list(cres.rule_ids)
                )
            )
        elif cres.value:
            control = str(cres.value)
        elif contact and CONTACT_KIND.get(typ) == "customer" and contact["receivable_account_id"]:
            control = str(contact["receivable_account_id"])
        elif contact and CONTACT_KIND.get(typ) == "supplier" and contact["payable_account_id"]:
            control = str(contact["payable_account_id"])
        else:
            control = roles.get(CONTROL_ROLE[typ])
    settlement = str(t["settlement_account_id"]) if t["settlement_account_id"] else None
    if settlement is None and (
        typ in core.BANK_TYPES
        or typ in core.SETTLEMENT_TYPES
        or typ in ("cash_expense", "bank_expense", "general_expense")
    ):
        sres = core.resolve_rules(rules, _facts(t, {}, contact, doc_type), on, kind="account", target="settlement")
        if sres.value and not sres.conflict:
            settlement = str(sres.value)
        elif typ != "general_expense":
            settlement = roles.get("cash" if typ == "cash_expense" else "bank")
    counter = str(t["counter_account_id"]) if t["counter_account_id"] else None
    if typ == "bank_transfer" and counter is None:
        issues.append(core.err("missing_account_mapping", "A bank transfer needs the account the money moves to."))
    accounts = {
        str(a["id"]): core.AccountInfo(
            str(a["id"]),
            a["code"],
            a["name"],
            a["account_type"],
            a["normal_balance"],
            a["is_active"],
            a["is_postable"],
            a["currency"],
        )
        for a in rows(
            ctx,
            "SELECT id, code, name, account_type, normal_balance, is_active, is_postable, currency FROM acct_accounts WHERE company_id = :c",
            c=co,
        )
    }
    if (
        settlement
        and accounts.get(settlement)
        and accounts[settlement].account_type not in core.SETTLEMENT_ACCOUNT_TYPES
        and typ != "general_expense"
    ):
        issues.append(
            core.err("settlement_account_type", "The bank or cash account must be an account of type bank or cash.")
        )
    if typ in CONTACT_KIND and typ in core.CONTACT_REQUIRED and contact is None:
        issues.append(
            core.err(
                "contact_required",
                f"Choose the {CONTACT_KIND[typ]} before approval (suggested matches are shown; none is applied automatically).",
            )
        )
    if contact and typ in CONTACT_KIND and contact["kind"] != CONTACT_KIND[typ]:
        issues.append(
            core.err(
                "contact_kind_mismatch",
                f"A {typ.replace('_', ' ')} needs a {CONTACT_KIND[typ]}, but this contact is a {contact['kind']}.",
            )
        )
    if contact and not contact["is_active"]:
        issues.append(core.err("contact_inactive", "The selected customer/supplier is inactive."))
    if t["contact_suggestions"] and not t["contact_id"]:
        issues.append(
            core.Issue(
                "contact_unconfirmed",
                "info",
                "Existing customers/suppliers match this document; confirm one if it is the right party.",
                False,
                {},
            )
        )
    if not resolved:
        issues.append(core.err("no_lines", "The transaction has no lines."))
    elif gross_total == 0 and net_total == 0:
        issues.append(core.err("zero_amount", "The transaction total is zero."))

    # --- currency
    rate, rate_src, rate_date = t["exchange_rate"], t["rate_source"], t["rate_date"]
    if t["currency"] != cfg["base_currency"]:
        if not entitlements.has_feature(ctx.conn, org, "feature.multi_currency"):
            issues.append(
                core.err("feature_not_in_plan", "Foreign-currency transactions are not included in the current plan.")
            )
        if rate is None:
            found = acc.lookup_rate(ctx, t["currency"], on)
            if found:
                rate, rate_src, rate_date = found["rate"], found["source"], found["rate_date"]
        issues += core.check_rate(t["currency"], cfg["base_currency"], rate, rate_date, on, cfg["max_rate_age_days"])
        if rate is None:
            rate = core.ZERO

    # --- journal
    if t["journal_mode"] == "manual":
        existing = (_journal(ctx, tid) or {}).get("lines", [])
        jlines = [
            core.JLine(
                str(x["account_id"]),
                x["debit"],
                x["credit"],
                x["description"],
                x["reference"],
                str(x["tax_code_id"]) if x["tax_code_id"] else None,
                str(x["contact_id"]) if x["contact_id"] else None,
                x["dimensions"] or {},
                "manual",
            )
            for x in existing
            if x["source"] != "rounding"
        ]
    else:
        jlines, jissues = core.build_journal(
            typ,
            resolved,
            control_account=control,
            settlement_account=settlement,
            counter_account=counter,
            contact_id=str(t["contact_id"]) if t["contact_id"] else None,
            reference=t["doc_number"],
        )
        issues += jissues
    if t["currency"] == cfg["base_currency"]:
        jlines, rissues = core.to_base(
            jlines, rate=Decimal(1), base_scale=base_scale, mode=mode, rounding_account=None, tolerance=cfg["tolerance"]
        )
    elif rate and rate > 0:
        jlines, rissues = core.to_base(
            jlines,
            rate=rate,
            base_scale=base_scale,
            mode=mode,
            rounding_account=roles.get("rounding"),
            tolerance=cfg["tolerance"] * max(1, len(jlines)),
        )
    else:
        rissues = []
    issues += rissues
    issues += core.validate_journal(
        jlines,
        accounts,
        base_currency=cfg["base_currency"],
        currency=t["currency"],
        required_dimensions=cfg["required_dimensions"],
        scale=scale,
        base_scale=base_scale,
    )

    # --- period, duplicates
    periods = rows(ctx, "SELECT id, name, start_date, end_date, status FROM acct_periods WHERE company_id = :c", c=co)
    issues += core.check_period(on, periods, required=cfg["require_open_period"])
    period_id = next((p["id"] for p in periods if p["start_date"] <= on <= p["end_date"]), None)
    issues += _detect_duplicates(ctx, t, gross_total, tid)

    # --- narration default (deterministic text, only used for the journal; the transaction keeps what the user typed)
    narration = t["narration"]
    if not narration:
        nres = core.resolve_rules(rules, _facts(t, {}, contact, doc_type), on, kind="narration")
        if nres.conflict:
            issues.append(
                core.warn("rule_conflict", "Narration rules conflict; none was applied.", rules=list(nres.rule_ids))
            )
        elif nres.value:
            narration = core.render_narration(
                nres.value,
                {
                    "contact": (contact or {}).get("display_name"),
                    "doc_number": t["doc_number"],
                    "date": on.isoformat(),
                    "description": (lines[0]["description"] if lines else None),
                    "txn_type": TXN_LABEL[typ],
                },
            )
    journal_narration = narration or " ".join(
        x for x in (TXN_LABEL[typ], t["doc_number"], (contact or {}).get("display_name")) if x
    )

    # --- store
    for ln in lines:
        account_id, src, dims, net, tax, gross = ln["_resolved"]
        ctx.conn.execute(
            text(
                "UPDATE acct_transaction_lines SET account_id = :a, account_source = :s, dimensions = CAST(:d AS jsonb), net_amount = :n, tax_amount = :t, gross_amount = :g, tax_code_id = :tc, calc = calc || CAST(:c AS jsonb) WHERE id = :i"
            ),
            {
                "a": _u(account_id),
                "s": src,
                "d": json.dumps(dims),
                "n": net,
                "t": tax,
                "g": gross,
                "tc": _u(ln["tax_code_id"]),
                "c": json.dumps(
                    {
                        "tax_rate": format(sp.rate, "f") if (sp := specs.get(ln["line_no"])) else None,
                        "treatment": sp.treatment if sp else None,
                    }
                ),
                "i": U(ln["id"]),
            },
        )
    _store_journal(ctx, cfg, t, jlines, period_id, journal_narration, rate, rate_src, rate_date)
    base_gross = core.rnd(
        gross_total * (rate if t["currency"] != cfg["base_currency"] and rate else Decimal(1)), base_scale, mode
    )
    blocking_or_not = [i.as_dict() for i in issues]
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET net_amount = :n, tax_amount = :t, gross_amount = :g, base_gross_amount = :bg, exchange_rate = :er, rate_source = :rs, rate_date = :rd,"
            " validation = CAST(:v AS jsonb), validated_at = now(), updated_at = now() WHERE id = :i"
        ),
        {
            "n": net_total,
            "t": tax_total,
            "g": gross_total,
            "bg": base_gross,
            "er": rate if t["currency"] != cfg["base_currency"] and rate else None,
            "rs": rate_src,
            "rd": rate_date,
            "v": json.dumps(ser(blocking_or_not)),
            "i": tid,
        },
    )
    return issues


def _store_journal(
    ctx: RequestCtx,
    cfg: dict[str, Any],
    t: dict[str, Any],
    jlines: list[core.JLine],
    period_id: Any,
    narration: str,
    rate: Any,
    rate_src: Any,
    rate_date: Any,
) -> None:
    org, co = _co(ctx)
    jtype = core.JOURNAL_TYPE_OF.get(t["txn_type"], "general")
    foreign = t["currency"] != cfg["base_currency"]
    j = one(ctx, "SELECT id FROM acct_journals WHERE transaction_id = :t", t=U(t["id"]))
    if j is None:
        jid = new_id()
        ctx.conn.execute(
            text(
                "INSERT INTO acct_journals (id, organization_id, company_id, journal_type, journal_date, period_id, narration, transaction_id, source_document_id, currency,"
                " base_currency, exchange_rate, rate_source, rate_date, created_by) VALUES (:id, :o, :c, :jt, :d, :p, :n, :t, :sd, :cur, :b, :er, :rs, :rd, :u)"
            ),
            {
                "id": jid,
                "o": org,
                "c": co,
                "jt": jtype,
                "d": t["transaction_date"],
                "p": _u(period_id),
                "n": narration,
                "t": U(t["id"]),
                "sd": _u(t["source_document_id"]),
                "cur": t["currency"],
                "b": cfg["base_currency"],
                "er": rate if foreign and rate else None,
                "rs": rate_src,
                "rd": rate_date,
                "u": ctx.user_id,
            },
        )
    else:
        jid = U(j["id"])
        ctx.conn.execute(
            text(
                "UPDATE acct_journals SET journal_type = :jt, journal_date = :d, period_id = :p, narration = :n, currency = :cur, exchange_rate = :er, rate_source = :rs, rate_date = :rd, updated_at = now() WHERE id = :i"
            ),
            {
                "jt": jtype,
                "d": t["transaction_date"],
                "p": _u(period_id),
                "n": narration,
                "cur": t["currency"],
                "er": rate if foreign and rate else None,
                "rs": rate_src,
                "rd": rate_date,
                "i": jid,
            },
        )
    ctx.conn.execute(text("DELETE FROM acct_journal_lines WHERE journal_id = :j"), {"j": jid})
    n = 0
    for ln in jlines:
        if ln.account_id is None:
            continue  # reported by validation as a missing account; a line without an account cannot be stored
        n += 1
        ctx.conn.execute(
            text(
                "INSERT INTO acct_journal_lines (id, organization_id, company_id, journal_id, line_no, account_id, debit, credit, base_debit, base_credit, currency, description,"
                " reference, tax_code_id, contact_id, dimensions, source) VALUES (:id, :o, :c, :j, :n, :a, :d, :cr, :bd, :bc, :cur, :ds, :rf, :tc, :ct, CAST(:dm AS jsonb), :s)"
            ),
            {
                "id": new_id(),
                "o": org,
                "c": co,
                "j": jid,
                "n": n,
                "a": uuid.UUID(ln.account_id),
                "d": ln.debit,
                "cr": ln.credit,
                "bd": ln.base_debit,
                "bc": ln.base_credit,
                "cur": t["currency"],
                "ds": ln.description,
                "rf": ln.reference,
                "tc": uuid.UUID(ln.tax_code_id) if ln.tax_code_id else None,
                "ct": uuid.UUID(ln.contact_id) if ln.contact_id else None,
                "dm": json.dumps(dict(ln.dimensions)),
                "s": ln.source,
            },
        )


def _detect_duplicates(ctx: RequestCtx, t: dict[str, Any], gross: Decimal, tid: Any) -> list[core.Issue]:
    org, co = _co(ctx)
    new = core.DupFacts(
        str(t["contact_id"]) if t["contact_id"] else None,
        t["doc_number"],
        gross,
        t["currency"],
        t["transaction_date"],
        (acc.get_contact(ctx, U(t["contact_id"])).get("tax_number") if t["contact_id"] else None),
        t["document_checksum"],
        t["txn_type"],
    )
    cands = rows(
        ctx,
        "SELECT x.id, x.number, x.status, x.contact_id, x.doc_number, x.gross_amount, x.currency, x.transaction_date, x.document_checksum, x.txn_type, c.tax_number FROM acct_transactions x"
        " LEFT JOIN acct_contacts c ON c.id = x.contact_id WHERE x.company_id = :c AND x.id <> :i AND x.status <> 'rejected' AND x.reversal_of_id IS NULL AND x.amends_id IS NULL"
        " AND ((:dn <> '' AND lower(x.doc_number) = lower(:dn)) OR (:ck <> '' AND x.document_checksum = :ck)"
        " OR (x.contact_id = :ct AND x.gross_amount = :g AND x.transaction_date BETWEEN :d1 AND :d2)) AND x.status <> 'reversed'",
        c=co,
        i=U(tid),
        dn=t["doc_number"] or "",
        ck=t["document_checksum"] or "",
        ct=_u(t["contact_id"]) or uuid.UUID(int=0),
        g=gross,
        d1=date.fromordinal(t["transaction_date"].toordinal() - 7),
        d2=date.fromordinal(t["transaction_date"].toordinal() + 7),
    )
    found: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for o in cands:
        other = core.DupFacts(
            str(o["contact_id"]) if o["contact_id"] else None,
            o["doc_number"],
            o["gross_amount"],
            o["currency"],
            o["transaction_date"],
            o["tax_number"],
            o["document_checksum"],
            o["txn_type"],
        )
        level, sig = core.duplicate_level(new, other)
        if level:
            found[str(o["id"])] = (level, sig, o)
    existing = {
        str(r["matched_transaction_id"]): r
        for r in rows(
            ctx,
            "SELECT matched_transaction_id, decision FROM acct_duplicate_matches WHERE transaction_id = :t",
            t=U(tid),
        )
    }
    for mid, (level, sig, _o) in found.items():
        if mid in existing:
            ctx.conn.execute(
                text(
                    "UPDATE acct_duplicate_matches SET level = :l, signals = CAST(:s AS jsonb) WHERE transaction_id = :t AND matched_transaction_id = :m"
                ),
                {"l": level, "s": json.dumps(sig), "t": U(tid), "m": uuid.UUID(mid)},
            )
        else:
            ctx.conn.execute(
                text(
                    "INSERT INTO acct_duplicate_matches (id, organization_id, company_id, transaction_id, matched_transaction_id, level, signals) VALUES (:id, :o, :c, :t, :m, :l, CAST(:s AS jsonb))"
                ),
                {"id": new_id(), "o": org, "c": co, "t": U(tid), "m": uuid.UUID(mid), "l": level, "s": json.dumps(sig)},
            )
    out: list[core.Issue] = []
    status = "none"
    for mid, (level, _sig, o) in found.items():
        decision = (existing.get(mid) or {}).get("decision")
        if decision == "not_duplicate":
            continue
        if decision == "duplicate":
            out.append(
                core.err(
                    "confirmed_duplicate",
                    f"Marked as a duplicate of {o['number']}; reject this transaction.",
                    matched=o["number"],
                )
            )
            status = "exact"
        elif level == "exact":
            out.append(
                core.err(
                    "exact_duplicate",
                    f"Looks like an exact duplicate of {o['number']}. Review it and record whether it is a duplicate.",
                    matched=o["number"],
                )
            )
            status = "exact"
        else:
            out.append(
                core.warn(
                    "possible_duplicate",
                    f"Possible duplicate of {o['number']}; compare before approving.",
                    matched=o["number"],
                )
            )
            status = status if status == "exact" else "possible"
    ctx.conn.execute(
        text("UPDATE acct_transactions SET duplicate_status = :s WHERE id = :i"), {"s": status, "i": U(tid)}
    )
    return out


# ------------------------------------------------------------------ views
def brief(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    t = load_txn(ctx, tid)
    return ser(
        {
            k: t[k]
            for k in (
                "id",
                "number",
                "txn_type",
                "status",
                "transaction_date",
                "doc_number",
                "currency",
                "gross_amount",
                "duplicate_status",
            )
        }
    )


def _can(ctx: RequestCtx, t: dict[str, Any], issues: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, bool]:
    perms = acc.user_perms(ctx)
    me, st = ctx.user_id, t["status"]
    blocking = any(i["blocking"] for i in issues)
    separated = bool(policy["separate_preparer_approver"]) and _same(t["prepared_by"], me)
    return {
        "edit": st == "draft" and "accounting.prepare" in perms,
        "submit": st == "draft" and "accounting.prepare" in perms and not blocking,
        "review": st == "awaiting_review"
        and "accounting.review" in perms
        and not (separated and policy["levels"] == 2),
        "approve": st == "awaiting_approval"
        and "accounting.approve" in perms
        and not separated
        and not (policy["levels"] == 2 and policy["distinct_levels"] and _same(t["reviewed_by"], me)),
        "reject": (st == "awaiting_review" and "accounting.review" in perms)
        or (st == "awaiting_approval" and "accounting.approve" in perms),
        "request_correction": (st == "awaiting_review" and "accounting.review" in perms)
        or (st == "awaiting_approval" and "accounting.approve" in perms),
        "revise": st == "rejected" and "accounting.prepare" in perms,
        "reverse": st in ("approved", "posted") and "accounting.reverse" in perms,
    }


def view(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    t = load_txn(ctx, tid)
    lines = _lines(ctx, tid)
    j = _journal(ctx, tid)
    issues = t["validation"] or []
    trail = rows(
        ctx,
        "SELECT a.action, a.from_status, a.to_status, a.actor_type, a.note, a.created_at, u.display_name AS actor FROM acct_approvals a LEFT JOIN users u ON u.id = a.actor_id"
        " WHERE a.transaction_id = :t ORDER BY a.created_at, a.id",
        t=U(tid),
    )
    dups = rows(
        ctx,
        "SELECT m.id, m.level, m.signals, m.decision, m.note, x.id AS matched_id, x.number AS matched_number, x.doc_number AS matched_doc_number, x.status AS matched_status, x.gross_amount AS matched_gross, x.transaction_date AS matched_date"
        " FROM acct_duplicate_matches m JOIN acct_transactions x ON x.id = m.matched_transaction_id WHERE m.transaction_id = :t ORDER BY m.level, m.created_at",
        t=U(tid),
    )
    doc = (
        one(ctx, "SELECT id, display_name FROM documents WHERE id = :d", d=U(t["source_document_id"]))
        if t["source_document_id"]
        else None
    )
    contact = acc.get_contact(ctx, U(t["contact_id"])) if t["contact_id"] else None
    policy = acc.get_policy(ctx) or {
        "separate_preparer_approver": True,
        "levels": 1,
        "distinct_levels": True,
        "approval_limit": None,
    }
    names = {
        str(r["id"]): r["display_name"]
        for r in rows(
            ctx,
            "SELECT id, display_name FROM users WHERE id = ANY(:ids)",
            ids=[
                U(x)
                for x in {
                    t[k]
                    for k in ("prepared_by", "submitted_by", "reviewed_by", "approved_by", "rejected_by", "reversed_by")
                }
                if x
            ],
        )
    }
    totals = (j or {}).get("lines", [])
    out = {
        **{k: t[k] for k in t},
        "type_label": TXN_LABEL[t["txn_type"]],
        "lines": lines,
        "journal": j,
        "debit_total": sum((x["debit"] for x in totals), core.ZERO),
        "credit_total": sum((x["credit"] for x in totals), core.ZERO),
        "base_debit_total": sum((x["base_debit"] for x in totals), core.ZERO),
        "base_credit_total": sum((x["base_credit"] for x in totals), core.ZERO),
        "balanced": bool(totals)
        and sum((x["debit"] for x in totals), core.ZERO) == sum((x["credit"] for x in totals), core.ZERO)
        and sum((x["base_debit"] for x in totals), core.ZERO) == sum((x["base_credit"] for x in totals), core.ZERO),
        "issues": issues,
        "blocking_count": sum(1 for i in issues if i["blocking"]),
        "trail": trail,
        "duplicates": dups,
        "document": doc,
        "contact": contact,
        "people": {
            k: names.get(str(t[k]))
            for k in ("prepared_by", "submitted_by", "reviewed_by", "approved_by", "rejected_by", "reversed_by")
        },
        "can": _can(ctx, t, issues, policy),
        "policy": policy,
    }
    return ser(out)


def list_transactions(
    ctx: RequestCtx,
    *,
    status: str | None = None,
    txn_type: str | None = None,
    contact_id: uuid.UUID | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _, co = _co(ctx)
    vis = visible_node_ids(doc_access(ctx))
    where = "t.company_id = :c AND (t.source_document_id IS NULL OR d.purged_at IS NOT NULL OR d.node_id = ANY(:vis))"
    p: dict[str, Any] = {"c": co, "vis": vis}
    for col, val, key in (
        ("t.status", status, "s"),
        ("t.txn_type", txn_type, "ty"),
        ("t.contact_id", contact_id, "ct"),
    ):
        if val:
            where += f" AND {col} = :{key}"
            p[key] = val
    if q:
        where += " AND (t.number ILIKE :q OR t.doc_number ILIKE :q OR t.narration ILIKE :q OR c.display_name ILIKE :q)"
        p["q"] = f"%{q}%"
    if date_from:
        where += " AND t.transaction_date >= :df"
        p["df"] = date_from
    if date_to:
        where += " AND t.transaction_date <= :dt"
        p["dt"] = date_to
    base = f"FROM acct_transactions t LEFT JOIN documents d ON d.id = t.source_document_id LEFT JOIN acct_contacts c ON c.id = t.contact_id WHERE {where}"
    total = one(ctx, f"SELECT count(*) AS n {base}", **p)
    items = rows(
        ctx,
        f"SELECT t.id, t.number, t.txn_type, t.status, t.transaction_date, t.doc_number, t.currency, t.gross_amount, t.duplicate_status, c.display_name AS contact_name,"
        f" t.source_document_id, d.display_name AS document_name, t.validation {base} ORDER BY t.transaction_date DESC, t.number DESC LIMIT :l OFFSET :o",
        **p,
        l=limit,
        o=offset,
    )
    for i in items:
        v = i.pop("validation") or []
        i["blocking_count"] = sum(1 for x in v if x["blocking"])
        i["type_label"] = TXN_LABEL[i["txn_type"]]
    return ser({"items": items, "total": total["n"] if total else 0, "limit": limit, "offset": offset})


# ------------------------------------------------------------------ editing (drafts only)
def _editable(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    t = load_txn(ctx, tid, lock=True)
    if t["status"] != "draft":
        raise Conflict(
            f"A transaction that is {t['status'].replace('_', ' ')} cannot be edited. Ask for a correction, or reverse an approved one."
        )
    return t


def _touch(ctx: RequestCtx, tid: uuid.UUID, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    ctx.conn.execute(
        text(
            f"UPDATE acct_transactions SET {sets + ', ' if sets else ''}prepared_by = :__u, updated_at = now(), row_version = row_version + 1 WHERE id = :__i"
        ),
        {**fields, "__u": ctx.user_id, "__i": tid},
    )


HEADER_EDITABLE = (
    "txn_type",
    "transaction_date",
    "due_date",
    "doc_number",
    "currency",
    "exchange_rate",
    "rate_source",
    "rate_date",
    "prices_include_tax",
    "narration",
    "settlement_account_id",
    "counter_account_id",
)


def update_header(
    ctx: RequestCtx, tid: uuid.UUID, patch: dict[str, Any], base_row_version: int | None
) -> dict[str, Any]:
    t = _editable(ctx, tid)
    if base_row_version is not None and base_row_version != t["row_version"]:
        raise Conflict("The transaction changed since you opened it. Reload and try again.")
    sets = {k: patch[k] for k in HEADER_EDITABLE if k in patch}
    if "txn_type" in sets:
        if sets["txn_type"] not in core.TXN_TYPES or sets["txn_type"] == "reversal_entry":
            raise Unprocessable("Unknown or unavailable transaction type.")
        if t["extraction_id"] and t["source_ref"] is None and (sets["txn_type"] in core.MANUAL_TYPES):
            raise Unprocessable("A document-based transaction cannot become a manual journal type.")
    for k in ("settlement_account_id", "counter_account_id"):
        if sets.get(k):
            acc.get_account(ctx, U(sets[k]))
    if "currency" in sets:
        sets["currency"] = str(sets["currency"]).upper()
        _currency_scale(ctx, sets["currency"])
        if sets["currency"] != t["currency"] and "exchange_rate" not in sets:
            sets.update(exchange_rate=None, rate_source=None, rate_date=None)
    if "exchange_rate" in sets and sets["exchange_rate"] is not None:
        sets["exchange_rate"] = core.D(sets["exchange_rate"])
        if sets["exchange_rate"] <= 0:
            raise Unprocessable("An exchange rate must be positive.")
        sets.setdefault("rate_source", "manual")
        sets.setdefault("rate_date", sets.get("transaction_date") or t["transaction_date"])
    _touch(ctx, tid, sets)
    acc.audit_event(ctx, "accounting.transaction_updated", "acct_transaction", tid, {"fields": sorted(sets)})
    rebuild(ctx, tid)
    return view(ctx, tid)


def replace_lines(ctx: RequestCtx, tid: uuid.UUID, lines: list[dict[str, Any]]) -> dict[str, Any]:
    t = _editable(ctx, tid)
    _check_lines(ctx, t["txn_type"], lines)
    _write_lines(ctx, tid, lines)
    ctx.conn.execute(text("UPDATE acct_transactions SET journal_mode = 'generated' WHERE id = :i"), {"i": tid})
    _touch(ctx, tid, {})
    acc.audit_event(ctx, "accounting.transaction_lines_updated", "acct_transaction", tid, {"lines": len(lines)})
    rebuild(ctx, tid)
    return view(ctx, tid)


def set_contact(ctx: RequestCtx, tid: uuid.UUID, contact_id: uuid.UUID | None) -> dict[str, Any]:
    """The accountant confirms (or clears) the customer/supplier. Matches are only ever suggested."""
    _editable(ctx, tid)
    if contact_id is not None:
        acc.get_contact(ctx, contact_id)
    _touch(ctx, tid, {"contact_id": contact_id})
    acc.audit_event(
        ctx,
        "accounting.transaction_contact_set",
        "acct_transaction",
        tid,
        {"contact_id": str(contact_id) if contact_id else None},
    )
    rebuild(ctx, tid)
    return view(ctx, tid)


def replace_journal_lines(ctx: RequestCtx, tid: uuid.UUID, jlines: list[dict[str, Any]]) -> dict[str, Any]:
    """The accountant edits the journal itself (add/remove/change lines). The journal then stays as edited until `regenerate`;
    the backend still validates balance, accounts, period and currency."""
    t = _editable(ctx, tid)
    org, co = _co(ctx)
    cfg = acc.require_config(ctx)
    j = one(ctx, "SELECT id FROM acct_journals WHERE transaction_id = :t", t=tid)
    if j is None:
        rebuild(ctx, tid)
        j = one(ctx, "SELECT id FROM acct_journals WHERE transaction_id = :t", t=tid)
    assert j is not None
    ctx.conn.execute(text("DELETE FROM acct_journal_lines WHERE journal_id = :j"), {"j": U(j["id"])})
    for n, ln in enumerate(jlines, 1):
        a = acc.get_account(ctx, U(ln["account_id"]))
        debit, credit = _as_decimal(ln.get("debit")) or core.ZERO, _as_decimal(ln.get("credit")) or core.ZERO
        if debit < 0 or credit < 0 or (debit > 0 and credit > 0):
            raise Unprocessable(f"Line {n}: enter either a debit or a credit, never negative.")
        ctx.conn.execute(
            text(
                "INSERT INTO acct_journal_lines (id, organization_id, company_id, journal_id, line_no, account_id, debit, credit, currency, description, reference, tax_code_id, contact_id, dimensions, source)"
                " VALUES (:id, :o, :c, :j, :n, :a, :d, :cr, :cur, :ds, :rf, :tc, :ct, CAST(:dm AS jsonb), 'manual')"
            ),
            {
                "id": new_id(),
                "o": org,
                "c": co,
                "j": U(j["id"]),
                "n": n,
                "a": U(a["id"]),
                "d": debit,
                "cr": credit,
                "cur": t["currency"],
                "ds": ln.get("description"),
                "rf": ln.get("reference"),
                "tc": _u(ln.get("tax_code_id")),
                "ct": _u(ln.get("contact_id")),
                "dm": json.dumps(ln.get("dimensions") or {}),
            },
        )
    ctx.conn.execute(text("UPDATE acct_transactions SET journal_mode = 'manual' WHERE id = :i"), {"i": tid})
    _touch(ctx, tid, {})
    acc.audit_event(ctx, "accounting.journal_edited", "acct_transaction", tid, {"lines": len(jlines)})
    _ = cfg
    rebuild(ctx, tid)
    return view(ctx, tid)


def regenerate_journal(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    _editable(ctx, tid)
    ctx.conn.execute(text("UPDATE acct_transactions SET journal_mode = 'generated' WHERE id = :i"), {"i": tid})
    _touch(ctx, tid, {})
    acc.audit_event(ctx, "accounting.journal_regenerated", "acct_transaction", tid, {})
    rebuild(ctx, tid)
    return view(ctx, tid)


def validate(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    t = load_txn(ctx, tid)
    if t["status"] == "draft":
        rebuild(ctx, tid)
    return view(ctx, tid)


def decide_duplicate(
    ctx: RequestCtx, tid: uuid.UUID, match_id: uuid.UUID, decision: str, note: str | None
) -> dict[str, Any]:
    _editable(ctx, tid)
    m = one(ctx, "SELECT id FROM acct_duplicate_matches WHERE id = :m AND transaction_id = :t", m=match_id, t=tid)
    if m is None:
        raise NotFound("Duplicate match not found.")
    if decision == "not_duplicate" and not (note and note.strip()):
        raise Unprocessable("Say why this is not a duplicate (for example a recurring charge).")
    ctx.conn.execute(
        text(
            "UPDATE acct_duplicate_matches SET decision = :d, decided_by = :u, decided_at = now(), note = :n WHERE id = :m"
        ),
        {"d": decision, "u": ctx.user_id, "n": note, "m": match_id},
    )
    acc.audit_event(ctx, "accounting.duplicate_decided", "acct_transaction", tid, {"decision": decision})
    rebuild(ctx, tid)
    return view(ctx, tid)


# ------------------------------------------------------------------ approval workflow
def _require_unblocked(ctx: RequestCtx, tid: uuid.UUID, what: str) -> list[core.Issue]:
    issues = rebuild(ctx, tid)
    blocking = [i for i in issues if i.blocking]
    if blocking:
        raise Conflict(
            f"Resolve the blocking items before you {what}: {blocking[0].message}",
            extra={"issues": ser([i.as_dict() for i in blocking])},
        )
    return issues


def submit(ctx: RequestCtx, tid: uuid.UUID, note: str | None) -> dict[str, Any]:
    t = _editable(ctx, tid)
    policy = acc.get_policy(ctx) or {"levels": 1}
    _require_unblocked(ctx, tid, "submit")
    to = "awaiting_review" if policy["levels"] == 2 else "awaiting_approval"
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = :s, submitted_by = :u, submitted_at = now(), correction_note = NULL, prepared_by = COALESCE(prepared_by, :u), updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"s": to, "u": ctx.user_id, "i": tid},
    )
    _trail(ctx, tid, "submit", t["status"], to, note)
    acc.audit_event(ctx, "accounting.transaction_submitted", "acct_transaction", tid, {"to": to})
    return view(ctx, tid)


def review_approve(ctx: RequestCtx, tid: uuid.UUID, note: str | None) -> dict[str, Any]:
    t = load_txn(ctx, tid, lock=True)
    policy = acc.get_policy(ctx) or {"separate_preparer_approver": True, "levels": 1}
    if t["status"] != "awaiting_review":
        raise Conflict("This transaction is not waiting for review.")
    if policy["separate_preparer_approver"] and _same(t["prepared_by"], ctx.user_id):
        raise Forbidden("You prepared this transaction; a different person must review it.")
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = 'awaiting_approval', reviewed_by = :u, reviewed_at = now(), updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "i": tid},
    )
    _trail(ctx, tid, "review_approve", "awaiting_review", "awaiting_approval", note)
    acc.audit_event(ctx, "accounting.transaction_reviewed", "acct_transaction", tid, {})
    return view(ctx, tid)


def approve(
    ctx: RequestCtx, tid: uuid.UUID, *, acknowledge: bool, note: str | None, actor_is_user: bool = True
) -> dict[str, Any]:
    """The only way a transaction becomes approved. A person, the right permission, the company's separation rules, no
    blocking validation issue, a balanced journal in an open period. Soft errors need an explicit, written acknowledgement."""
    if not actor_is_user or ctx.user_id is None:
        raise Forbidden("Only a person can approve an accounting transaction.")

    cfg = acc.require_config(ctx)
    t = load_txn(ctx, tid, lock=True)
    if t["status"] != "awaiting_approval":
        raise Conflict("This transaction is not waiting for approval.")
    policy = acc.get_policy(ctx) or {
        "separate_preparer_approver": True,
        "levels": 1,
        "distinct_levels": True,
        "approval_limit": None,
    }
    perms = acc.user_perms(ctx)
    if policy["separate_preparer_approver"] and _same(t["prepared_by"], ctx.user_id):
        raise Forbidden(
            "You prepared this transaction; the company's policy requires a different person to approve it."
        )
    if policy["levels"] == 2 and policy["distinct_levels"] and _same(t["reviewed_by"], ctx.user_id):
        raise Forbidden("You reviewed this transaction; a different person must give final approval.")
    # recompute under lock: the stored validation could be stale (rates, periods and configuration may have changed)
    issues = _revalidate_pending(ctx, tid)
    if (
        policy["approval_limit"] is not None
        and t["base_gross_amount"] is not None
        and t["base_gross_amount"] > policy["approval_limit"]
        and "accounting.approve_high" not in perms
    ):
        raise Forbidden(
            f"This transaction is above the approval limit ({format(policy['approval_limit'], 'f')} {cfg['base_currency']}); a user with high-value approval rights must approve it."
        )
    blocking = [i for i in issues if i.blocking]
    if blocking:
        raise Conflict(
            f"This transaction cannot be approved: {blocking[0].message}",
            extra={"issues": ser([i.as_dict() for i in blocking])},
        )
    soft = [i for i in issues if i.severity == "error"]
    if soft and not (acknowledge and note and note.strip()):
        raise Conflict(
            "The document differs from the calculation. Review the differences, then approve with an acknowledgement and a written reason.",
            extra={"issues": ser([i.as_dict() for i in soft])},
        )
    j = one(ctx, "SELECT id FROM acct_journals WHERE transaction_id = :t FOR UPDATE", t=tid)
    if j is None:
        raise Conflict("The transaction has no journal.")
    ctx.conn.execute(
        text(
            "UPDATE acct_journals SET status = 'approved', number = :n, approved_by = :u, approved_at = now(), updated_at = now(), row_version = row_version + 1 WHERE id = :j"
        ),
        {"n": _next_number(ctx, "journal", cfg), "u": ctx.user_id, "j": U(j["id"])},
    )
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = 'approved', approved_by = :u, approved_at = now(), override_note = :n, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "n": note if soft else None, "i": tid},
    )
    _trail(ctx, tid, "approve", "awaiting_approval", "approved", note)
    if soft:
        _trail(
            ctx,
            tid,
            "override",
            "approved",
            "approved",
            f"Accepted differences: {', '.join(i.code for i in soft)}. {note}",
        )
    acc.audit_event(
        ctx,
        "accounting.transaction_approved",
        "acct_transaction",
        tid,
        {"journal": str(j["id"]), "acknowledged": [i.code for i in soft]},
    )
    return view(ctx, tid)


def _revalidate_pending(ctx: RequestCtx, tid: uuid.UUID) -> list[core.Issue]:
    """Recompute a transaction that is awaiting approval without letting it be edited: temporarily reopen as a draft inside
    the same locked transaction, rebuild, and put the status back. The status change is never visible outside this call."""
    t = load_txn(ctx, tid)
    status = t["status"]
    ctx.conn.execute(text("UPDATE acct_transactions SET status = 'draft' WHERE id = :i"), {"i": tid})
    try:
        issues = rebuild(ctx, tid)
    finally:
        ctx.conn.execute(text("UPDATE acct_transactions SET status = :s WHERE id = :i"), {"s": status, "i": tid})
    return issues


def reject(ctx: RequestCtx, tid: uuid.UUID, reason: str) -> dict[str, Any]:
    t = load_txn(ctx, tid, lock=True)
    perms = acc.user_perms(ctx)
    need = {"awaiting_review": "accounting.review", "awaiting_approval": "accounting.approve"}.get(t["status"])
    if need is None:
        raise Conflict("Only a transaction that is waiting for review or approval can be rejected.")
    if need not in perms:
        raise Forbidden("You do not have permission to reject at this stage.")
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = 'rejected', rejected_by = :u, rejected_at = now(), rejection_reason = :r, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "r": reason, "i": tid},
    )
    _trail(ctx, tid, "reject", t["status"], "rejected", reason)
    acc.audit_event(ctx, "accounting.transaction_rejected", "acct_transaction", tid, {"reason": reason})
    return view(ctx, tid)


def request_correction(ctx: RequestCtx, tid: uuid.UUID, reason: str) -> dict[str, Any]:
    t = load_txn(ctx, tid, lock=True)
    perms = acc.user_perms(ctx)
    need = {"awaiting_review": "accounting.review", "awaiting_approval": "accounting.approve"}.get(t["status"])
    if need is None:
        raise Conflict("Only a transaction that is waiting for review or approval can be sent back.")
    if need not in perms:
        raise Forbidden("You do not have permission to send this back.")
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = 'draft', correction_note = :r, reviewed_by = NULL, reviewed_at = NULL, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"r": reason, "i": tid},
    )
    _trail(ctx, tid, "request_correction", t["status"], "draft", reason)
    acc.audit_event(ctx, "accounting.correction_requested", "acct_transaction", tid, {"reason": reason})
    return view(ctx, tid)


def revise(ctx: RequestCtx, tid: uuid.UUID, note: str | None) -> dict[str, Any]:
    t = load_txn(ctx, tid, lock=True)
    if t["status"] != "rejected":
        raise Conflict("Only a rejected transaction can be reopened for correction.")
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET status = 'draft', prepared_by = :u, rejected_by = NULL, rejected_at = NULL, rejection_reason = NULL, correction_note = :n, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"u": ctx.user_id, "n": f"Reopened after rejection: {t['rejection_reason']}", "i": tid},
    )
    _trail(ctx, tid, "revise", "rejected", "draft", note)
    acc.audit_event(ctx, "accounting.transaction_revised", "acct_transaction", tid, {})
    rebuild(ctx, tid)
    return view(ctx, tid)


def reverse(ctx: RequestCtx, tid: uuid.UUID, *, reversal_date: date, reason: str) -> dict[str, Any]:
    """Undo an approved transaction with an approved mirror journal dated `reversal_date` (which must be in an open period).
    The original is never edited: it becomes `reversed`, and both journals stay in the ledger so history is reproducible."""
    org, co = _co(ctx)
    cfg = acc.require_config(ctx, lock=True)
    t = load_txn(ctx, tid, lock=True)
    if t["status"] not in ("approved", "posted"):
        raise Conflict("Only an approved transaction can be reversed.")
    if not reason.strip():
        raise Unprocessable("A reversal needs a written reason.")
    periods = rows(ctx, "SELECT id, name, start_date, end_date, status FROM acct_periods WHERE company_id = :c", c=co)
    bad = [i for i in core.check_period(reversal_date, periods, required=cfg["require_open_period"]) if i.blocking]
    if bad:
        raise Conflict(f"The reversal cannot be dated {reversal_date.isoformat()}: {bad[0].message}")
    orig_j = _journal(ctx, tid)
    if orig_j is None or orig_j["status"] not in ("approved", "posted"):
        raise Conflict("The transaction has no approved journal to reverse.")
    rid = _insert_txn(
        ctx,
        cfg,
        {
            "txn_type": "reversal_entry",
            "transaction_date": reversal_date,
            "currency": t["currency"],
            "exchange_rate": t["exchange_rate"],
            "rate_source": t["rate_source"],
            "rate_date": t["rate_date"],
            "doc_number": t["number"],
            "narration": f"Reversal of {t['number']}: {reason}",
            "source_document_id": t["source_document_id"],
            "journal_mode": "manual",
        },
    )
    ctx.conn.execute(
        text(
            "UPDATE acct_transactions SET reversal_of_id = :o, net_amount = :n, tax_amount = :t, gross_amount = :g, base_gross_amount = :bg WHERE id = :i"
        ),
        {
            "o": tid,
            "n": t["net_amount"],
            "t": t["tax_amount"],
            "g": t["gross_amount"],
            "bg": t["base_gross_amount"],
            "i": rid,
        },
    )
    for n, ln in enumerate(orig_j["lines"], 1):
        amount = ln["debit"] if ln["debit"] > 0 else ln["credit"]
        ctx.conn.execute(
            text(
                "INSERT INTO acct_transaction_lines (id, organization_id, company_id, transaction_id, line_no, description, account_id, account_source, entry_side, net_amount, gross_amount, tax_code_id, dimensions)"
                " VALUES (:id, :o, :c, :t, :n, :d, :a, 'manual', :es, :am, :am, :tc, CAST(:dm AS jsonb))"
            ),
            {
                "id": new_id(),
                "o": org,
                "c": co,
                "t": rid,
                "n": n,
                "d": ln["description"],
                "a": U(ln["account_id"]),
                "es": "credit" if ln["debit"] > 0 else "debit",
                "am": amount,
                "tc": _u(ln["tax_code_id"]),
                "dm": json.dumps(ln["dimensions"] or {}),
            },
        )
    period_id = next((p["id"] for p in periods if p["start_date"] <= reversal_date <= p["end_date"]), None)
    jid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_journals (id, organization_id, company_id, journal_type, journal_date, period_id, narration, transaction_id, source_document_id, currency, base_currency,"
            " exchange_rate, rate_source, rate_date, created_by, reversal_of_id) VALUES (:id, :o, :c, 'reversal', :d, :p, :n, :t, :sd, :cur, :b, :er, :rs, :rd, :u, :ro)"
        ),
        {
            "id": jid,
            "o": org,
            "c": co,
            "d": reversal_date,
            "p": _u(period_id),
            "n": f"Reversal of {t['number']}: {reason}",
            "t": rid,
            "sd": _u(t["source_document_id"]),
            "cur": t["currency"],
            "b": cfg["base_currency"],
            "er": t["exchange_rate"],
            "rs": t["rate_source"],
            "rd": t["rate_date"],
            "u": ctx.user_id,
            "ro": U(orig_j["id"]),
        },
    )
    for ln in orig_j["lines"]:
        ctx.conn.execute(
            text(
                "INSERT INTO acct_journal_lines (id, organization_id, company_id, journal_id, line_no, account_id, debit, credit, base_debit, base_credit, currency, description, reference,"
                " tax_code_id, contact_id, dimensions, source) VALUES (:id, :o, :c, :j, :n, :a, :d, :cr, :bd, :bc, :cur, :ds, :rf, :tc, :ct, CAST(:dm AS jsonb), :s)"
            ),
            {
                "id": new_id(),
                "o": org,
                "c": co,
                "j": jid,
                "n": ln["line_no"],
                "a": U(ln["account_id"]),
                "d": ln["credit"],
                "cr": ln["debit"],
                "bd": ln["base_credit"],
                "bc": ln["base_debit"],
                "cur": ln["currency"],
                "ds": f"Reversal: {ln['description'] or ''}".strip(),
                "rf": ln["reference"],
                "tc": _u(ln["tax_code_id"]),
                "ct": _u(ln["contact_id"]),
                "dm": json.dumps(ln["dimensions"] or {}),
                "s": ln["source"],
            },
        )
    ctx.conn.execute(
        text(
            "UPDATE acct_journals SET status = 'approved', number = :n, approved_by = :u, approved_at = now() WHERE id = :j"
        ),
        {"n": _next_number(ctx, "journal", cfg), "u": ctx.user_id, "j": jid},
    )
    ctx.conn.execute(
        text("UPDATE acct_transactions SET status = 'approved', approved_by = :u, approved_at = now() WHERE id = :i"),
        {"u": ctx.user_id, "i": rid},
    )
    ctx.conn.execute(
        text("UPDATE acct_journals SET status = 'reversed', updated_at = now() WHERE id = :j"), {"j": U(orig_j["id"])}
    )
    ctx.conn.execute(
        text("UPDATE acct_transactions SET status = 'reversed', reversed_by = :u, reversed_at = now() WHERE id = :i"),
        {"u": ctx.user_id, "i": tid},
    )
    _trail(ctx, tid, "reverse", t["status"], "reversed", reason)
    _trail(ctx, rid, "create", None, "approved", f"Reversal of {t['number']}: {reason}")
    acc.audit_event(
        ctx, "accounting.transaction_reversed", "acct_transaction", tid, {"reversal_id": str(rid), "reason": reason}
    )
    return view(ctx, tid)


def amend(ctx: RequestCtx, tid: uuid.UUID, *, reversal_date: date, reason: str) -> dict[str, Any]:
    """Correct an approved transaction: reverse it, then open a new draft copy that the accountant fixes and submits."""
    t = load_txn(ctx, tid, lock=True)
    lines = _lines(ctx, tid)
    reverse(ctx, tid, reversal_date=reversal_date, reason=reason)
    cfg = acc.require_config(ctx)
    new_header = {
        k: t[k]
        for k in (
            "txn_type",
            "transaction_date",
            "due_date",
            "doc_number",
            "contact_id",
            "settlement_account_id",
            "counter_account_id",
            "currency",
            "exchange_rate",
            "rate_source",
            "rate_date",
            "prices_include_tax",
            "narration",
            "source_document_id",
            "document_checksum",
        )
    }
    new_header.update(amends_id=tid, stated_amounts=t["stated_amounts"], journal_mode="generated")
    nid = _insert_txn(ctx, cfg, new_header)
    _write_lines(
        ctx,
        nid,
        [
            {
                "description": ln["description"],
                "account_id": ln["account_id"],
                "entry_side": ln["entry_side"],
                "quantity": ln["quantity"],
                "unit_price": ln["unit_price"],
                "discount": ln["discount"],
                "tax_code_id": ln["tax_code_id"],
                "dimensions": ln["dimensions"],
                "amount": (ln["calc"].get("input") or {}).get("amount")
                or (ln["net_amount"] if not t["prices_include_tax"] else ln["gross_amount"]),
                "tax_amount": (ln["calc"].get("input") or {}).get("tax_amount"),
            }
            for ln in lines
        ],
    )
    _trail(ctx, nid, "amend", None, "draft", f"Amends {t['number']}: {reason}")
    acc.audit_event(ctx, "accounting.transaction_amended", "acct_transaction", nid, {"amends": str(tid)})
    rebuild(ctx, nid)
    return view(ctx, nid)


# ------------------------------------------------------------------ journals
def list_journals(
    ctx: RequestCtx,
    *,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _, co = _co(ctx)
    vis = visible_node_ids(doc_access(ctx))
    where = "j.company_id = :c AND (j.source_document_id IS NULL OR d.purged_at IS NOT NULL OR d.node_id = ANY(:vis))"
    p: dict[str, Any] = {"c": co, "vis": vis}
    if status:
        where += " AND j.status = :s"
        p["s"] = status
    if date_from:
        where += " AND j.journal_date >= :df"
        p["df"] = date_from
    if date_to:
        where += " AND j.journal_date <= :dt"
        p["dt"] = date_to
    base = f"FROM acct_journals j LEFT JOIN documents d ON d.id = j.source_document_id JOIN acct_transactions t ON t.id = j.transaction_id WHERE {where}"
    total = one(ctx, f"SELECT count(*) AS n {base}", **p)
    items = rows(
        ctx,
        f"SELECT j.id, j.number, j.journal_type, j.journal_date, j.narration, j.status, j.currency, j.base_total_debit, j.base_total_credit, j.transaction_id, t.number AS transaction_number {base}"
        " ORDER BY j.journal_date DESC, j.number DESC NULLS LAST LIMIT :l OFFSET :o",
        **p,
        l=limit,
        o=offset,
    )
    return ser({"items": items, "total": total["n"] if total else 0, "limit": limit, "offset": offset})


def get_journal(ctx: RequestCtx, jid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    j = one(ctx, "SELECT transaction_id FROM acct_journals WHERE company_id = :c AND id = :i", c=co, i=jid)
    if j is None:
        raise NotFound("Journal not found.")
    t = load_txn(ctx, U(j["transaction_id"]))  # same visibility as the transaction
    out = _journal(ctx, t["id"])
    assert out is not None
    out["transaction"] = {"id": t["id"], "number": t["number"], "txn_type": t["txn_type"], "status": t["status"]}
    out["history"] = rows(
        ctx,
        "SELECT a.action, a.from_status, a.to_status, a.note, a.created_at, u.display_name AS actor FROM acct_approvals a LEFT JOIN users u ON u.id = a.actor_id WHERE a.transaction_id = :t ORDER BY a.created_at, a.id",
        t=U(t["id"]),
    )
    return ser(out)


def create_journal_draft(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    """A manual journal: a journal-type transaction whose lines are the journal lines."""
    typ = body.get("txn_type", "journal_entry")
    if typ not in core.MANUAL_TYPES - {"reversal_entry"}:
        raise Unprocessable(
            "Choose a manual journal type: journal entry, opening balance, adjustment, accrual, prepayment or depreciation."
        )
    lines = [
        {
            "description": ln.get("description"),
            "account_id": ln["account_id"],
            "entry_side": "debit" if core.D(ln.get("debit") or 0) > 0 else "credit",
            "amount": ln.get("debit") if core.D(ln.get("debit") or 0) > 0 else ln.get("credit"),
            "dimensions": ln.get("dimensions"),
            "tax_code_id": ln.get("tax_code_id"),
        }
        for ln in body["lines"]
    ]
    for ln in body["lines"]:
        if core.D(ln.get("debit") or 0) > 0 and core.D(ln.get("credit") or 0) > 0:
            raise Unprocessable("A line cannot be both a debit and a credit.")
    t = create_draft(
        ctx,
        {
            "txn_type": typ,
            "transaction_date": body["journal_date"],
            "currency": body.get("currency"),
            "exchange_rate": body.get("exchange_rate"),
            "narration": body.get("narration"),
            "doc_number": body.get("reference"),
            "lines": lines,
        },
    )
    return t
