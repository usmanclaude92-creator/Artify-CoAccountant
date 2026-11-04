"""Super Admin: billing records (provider-independent).

No payment provider is integrated (decision D-2). These are MANUAL administrative records: an invoice
issued by Artify and payments/refunds/adjustments entered by an authorized administrator. The system
never creates a payment on its own, never stores card data or provider credentials, and never sums
amounts across currencies. Payments, refunds and adjustments are append-only; corrections are new rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api.context import PlatformCtx
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.routers.platform_common import Limit, Offset, Page, Reasoned, like, order_clause, padmin, paudit

router = APIRouter(prefix="/platform/billing", tags=["platform-billing"])

Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]


class InvoiceRow(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    subscription_id: uuid.UUID | None
    number: str
    status: Literal["draft", "open", "paid", "void"]
    currency: str
    amount_total: Decimal
    adjustments_total: Decimal
    paid_total: Decimal
    outstanding: Decimal
    period_start: datetime | None
    period_end: datetime | None
    issued_at: datetime | None
    due_at: datetime | None
    description: str | None
    created_at: datetime
    overdue: bool


class PaymentOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    organization_id: uuid.UUID
    kind: Literal["payment", "refund"]
    amount: Decimal
    currency: str
    method: str
    reference: str | None
    received_at: datetime
    note: str | None
    recorded_by: uuid.UUID
    created_at: datetime


class AdjustmentOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal
    currency: str
    reason: str
    created_by: uuid.UUID
    created_at: datetime


class InvoiceDetail(BaseModel):
    invoice: InvoiceRow
    payments: list[PaymentOut]
    adjustments: list[AdjustmentOut]


class CurrencyTotal(BaseModel):
    currency: str
    amount: Decimal


class BillingSummary(BaseModel):
    provider: str | None  # None until a billing provider is integrated (D-2)
    invoices_by_status: dict[str, int]
    outstanding: list[CurrencyTotal]
    overdue: list[CurrencyTotal]
    collected_last_30_days: list[CurrencyTotal]
    note: str


class InvoiceCreate(BaseModel):
    organization_id: uuid.UUID
    amount_total: Decimal = Field(ge=0, max_digits=18, decimal_places=3)
    currency: str = Field(pattern="^[A-Z]{3}$")
    period_start: datetime | None = None
    period_end: datetime | None = None
    due_at: datetime | None = None
    description: str | None = Field(default=None, max_length=500)
    issue: bool = False


class PaymentCreate(BaseModel):
    amount: Money
    method: Literal["bank_transfer", "cash", "cheque", "other"]
    reference: str | None = Field(default=None, max_length=120)
    received_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class RefundCreate(BaseModel):
    amount: Money
    method: Literal["bank_transfer", "cash", "cheque", "other"]
    reference: str | None = Field(default=None, max_length=120)
    note: str = Field(min_length=10, max_length=500)


class AdjustmentCreate(BaseModel):
    amount: Decimal = Field(max_digits=18, decimal_places=3)
    reason: str = Field(min_length=10, max_length=500)


_INVOICE_SQL = """
SELECT i.id, i.organization_id, o.display_name AS organization_name, i.subscription_id, i.number, i.status, i.currency,
       i.amount_total, i.period_start, i.period_end, i.issued_at, i.due_at, i.description, i.created_at,
       COALESCE((SELECT sum(a.amount) FROM billing_adjustments a WHERE a.invoice_id = i.id), 0)::numeric(18, 3) AS adjustments_total,
       COALESCE((SELECT sum(CASE p.kind WHEN 'payment' THEN p.amount ELSE -p.amount END)
                   FROM billing_payments p WHERE p.invoice_id = i.id), 0)::numeric(18, 3) AS paid_total
  FROM billing_invoices i JOIN organizations o ON o.id = i.organization_id
"""
_WRAP = (
    "SELECT t.*, CASE WHEN t.status = 'open' THEN t.amount_total + t.adjustments_total - t.paid_total ELSE 0::numeric(18, 3) END AS outstanding,"
    " (t.status = 'open' AND t.due_at IS NOT NULL AND t.due_at < now()) AS overdue FROM ({inner}) t"
)
_SORT = {
    "created": "created_at",
    "number": "number",
    "organization": "organization_name",
    "due": "due_at",
    "amount": "amount_total",
}


def _invoice(ctx: PlatformCtx, invoice_id: uuid.UUID, *, lock: bool = False) -> InvoiceRow:
    if lock:
        ctx.conn.execute(text("SELECT 1 FROM billing_invoices WHERE id = :i FOR UPDATE"), {"i": invoice_id})
    r = (
        ctx.conn.execute(text(_WRAP.format(inner=f"{_INVOICE_SQL} WHERE i.id = :i")), {"i": invoice_id})
        .mappings()
        .first()
    )
    if r is None:
        raise NotFound("Invoice not found.")
    return InvoiceRow(**r)


def _resync(ctx: PlatformCtx, invoice_id: uuid.UUID) -> InvoiceRow:
    """open <-> paid follows the balance; draft and void never change here."""
    inv = (
        ctx.conn.execute(text(_WRAP.format(inner=f"{_INVOICE_SQL} WHERE i.id = :i")), {"i": invoice_id})
        .mappings()
        .one()
    )
    balance = inv["amount_total"] + inv["adjustments_total"] - inv["paid_total"]
    new_status = inv["status"]
    if inv["status"] == "open" and balance <= 0:
        new_status = "paid"
    elif inv["status"] == "paid" and balance > 0:
        new_status = "open"
    if new_status != inv["status"]:
        ctx.conn.execute(
            text("UPDATE billing_invoices SET status = :s, updated_at = now() WHERE id = :i"),
            {"s": new_status, "i": invoice_id},
        )
    return _invoice(ctx, invoice_id)


@router.get("/summary", response_model=BillingSummary, operation_id="platform_billing_summary")
def summary(ctx: Annotated[PlatformCtx, padmin("platform.billing.read")]) -> BillingSummary:
    statuses = {
        r.status: r.n
        for r in ctx.conn.execute(text("SELECT status, count(*) AS n FROM billing_invoices GROUP BY status"))
    }
    base = _WRAP.format(inner=_INVOICE_SQL)

    def totals(sql: str) -> list[CurrencyTotal]:
        return [CurrencyTotal(currency=r.currency, amount=r.amount) for r in ctx.conn.execute(text(sql))]

    return BillingSummary(
        provider=None,
        invoices_by_status=statuses,
        outstanding=totals(
            f"SELECT currency, sum(outstanding) AS amount FROM ({base}) x WHERE status = 'open' GROUP BY currency HAVING sum(outstanding) > 0 ORDER BY currency"
        ),
        overdue=totals(
            f"SELECT currency, sum(outstanding) AS amount FROM ({base}) x WHERE overdue GROUP BY currency HAVING sum(outstanding) > 0 ORDER BY currency"
        ),
        collected_last_30_days=totals(
            "SELECT currency, sum(CASE kind WHEN 'payment' THEN amount ELSE -amount END) AS amount FROM billing_payments"
            " WHERE received_at > now() - interval '30 days' GROUP BY currency ORDER BY currency"
        ),
        note="Manual records only. No payment provider is integrated; nothing here was processed by a gateway.",
    )


@router.get("/invoices", response_model=Page[InvoiceRow], operation_id="platform_invoices")
def list_invoices(
    ctx: Annotated[PlatformCtx, padmin("platform.billing.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    organization_id: uuid.UUID | None = None,
    status: Literal["draft", "open", "paid", "void"] | None = None,
    overdue: bool | None = None,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[InvoiceRow]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if pattern := like(q):
        where.append("(x.number ILIKE :q ESCAPE '\\' OR x.organization_name ILIKE :q ESCAPE '\\')")
        params["q"] = pattern
    if organization_id:
        where.append("x.organization_id = :org")
        params["org"] = organization_id
    if status:
        where.append("x.status = :status")
        params["status"] = status
    if overdue is not None:
        where.append("x.overdue = :overdue")
        params["overdue"] = overdue
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    base = _WRAP.format(inner=_INVOICE_SQL)
    total = ctx.conn.execute(text(f"SELECT count(*) FROM ({base}) x{clause}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(
            f"SELECT * FROM ({base}) x{clause} ORDER BY {order_clause(sort, order, _SORT, 'created')} LIMIT :l OFFSET :o"
        ),
        {**params, "l": limit, "o": offset},
    ).mappings()
    return Page[InvoiceRow](items=[InvoiceRow(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail, operation_id="platform_invoice_get")
def get_invoice(invoice_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.billing.read")]) -> InvoiceDetail:
    inv = _invoice(ctx, invoice_id)
    pays = ctx.conn.execute(
        text("SELECT * FROM billing_payments WHERE invoice_id = :i ORDER BY received_at, created_at"), {"i": invoice_id}
    ).mappings()
    adjs = ctx.conn.execute(
        text("SELECT * FROM billing_adjustments WHERE invoice_id = :i ORDER BY created_at"), {"i": invoice_id}
    ).mappings()
    return InvoiceDetail(
        invoice=inv, payments=[PaymentOut(**p) for p in pays], adjustments=[AdjustmentOut(**a) for a in adjs]
    )


@router.get("/payments", response_model=Page[PaymentOut], operation_id="platform_payments")
def list_payments(
    ctx: Annotated[PlatformCtx, padmin("platform.billing.read")],
    organization_id: uuid.UUID | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[PaymentOut]:
    clause, params = (" WHERE organization_id = :o", {"o": organization_id}) if organization_id else ("", {})
    total = ctx.conn.execute(text(f"SELECT count(*) FROM billing_payments{clause}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(f"SELECT * FROM billing_payments{clause} ORDER BY received_at DESC, created_at DESC LIMIT :l OFFSET :o2"),
        {**params, "l": limit, "o2": offset},
    ).mappings()
    return Page[PaymentOut](items=[PaymentOut(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("/invoices", response_model=InvoiceRow, status_code=201, operation_id="platform_invoice_create")
def create_invoice(body: InvoiceCreate, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]) -> InvoiceRow:
    if not ctx.conn.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": body.organization_id}).first():
        raise NotFound("Organization not found.")
    if not ctx.conn.execute(text("SELECT 1 FROM currencies WHERE code = :c"), {"c": body.currency}).first():
        raise Unprocessable("Unknown currency code.")
    if body.period_start and body.period_end and body.period_end <= body.period_start:
        raise Unprocessable("period_end must be after period_start.")
    prefix = (
        ctx.conn.execute(
            text("SELECT value #>> '{}' FROM platform_settings WHERE key = 'billing.invoice_prefix'")
        ).scalar()
        or "INV"
    )
    seq = ctx.conn.execute(text("SELECT nextval('billing_invoice_seq')")).scalar_one()
    number = f"{prefix}-{datetime.now().year}-{seq:06d}"
    sub_id = ctx.conn.execute(
        text("SELECT id FROM subscriptions WHERE organization_id = :o"), {"o": body.organization_id}
    ).scalar()
    iid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO billing_invoices (id, organization_id, subscription_id, number, status, currency, amount_total,"
            " period_start, period_end, issued_at, due_at, description, created_by)"
            " VALUES (:id, :o, :s, :n, :st, :c, :a, :ps, :pe, CASE WHEN :issue THEN now() END, :due, :d, :by)"
        ),
        {
            "id": iid,
            "o": body.organization_id,
            "s": sub_id,
            "n": number,
            "st": "open" if body.issue else "draft",
            "c": body.currency,
            "a": body.amount_total,
            "ps": body.period_start,
            "pe": body.period_end,
            "issue": body.issue,
            "due": body.due_at,
            "d": body.description,
            "by": ctx.admin_id,
        },
    )
    paudit(
        ctx,
        "invoice.created",
        target_type="invoice",
        target_id=iid,
        org=body.organization_id,
        summary={"number": number, "amount": str(body.amount_total), "currency": body.currency, "issued": body.issue},
    )
    return _invoice(ctx, iid)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceRow, operation_id="platform_invoice_issue")
def issue_invoice(invoice_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]) -> InvoiceRow:
    inv = _invoice(ctx, invoice_id, lock=True)
    if inv.status != "draft":
        raise Conflict("Only a draft invoice can be issued.")
    ctx.conn.execute(
        text("UPDATE billing_invoices SET status = 'open', issued_at = now(), updated_at = now() WHERE id = :i"),
        {"i": invoice_id},
    )
    paudit(
        ctx,
        "invoice.issued",
        target_type="invoice",
        target_id=invoice_id,
        org=inv.organization_id,
        summary={"number": inv.number},
    )
    return _resync(ctx, invoice_id)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceRow, operation_id="platform_invoice_void")
def void_invoice(
    invoice_id: uuid.UUID, body: Reasoned, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]
) -> InvoiceRow:
    inv = _invoice(ctx, invoice_id, lock=True)
    if inv.status not in ("draft", "open"):
        raise Conflict("Only a draft or open invoice can be voided.")
    if inv.paid_total != 0:
        raise Conflict("An invoice with recorded payments cannot be voided; refund the payments first.")
    ctx.conn.execute(
        text("UPDATE billing_invoices SET status = 'void', void_reason = :r, updated_at = now() WHERE id = :i"),
        {"r": body.reason, "i": invoice_id},
    )
    paudit(
        ctx,
        "invoice.voided",
        target_type="invoice",
        target_id=invoice_id,
        org=inv.organization_id,
        reason=body.reason,
        summary={"number": inv.number},
    )
    return _invoice(ctx, invoice_id)


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=InvoiceDetail,
    status_code=201,
    operation_id="platform_payment_record",
)
def record_payment(
    invoice_id: uuid.UUID, body: PaymentCreate, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]
) -> InvoiceDetail:
    """Record money Artify actually received (entered by an administrator). Overpayment is rejected."""
    inv = _invoice(ctx, invoice_id, lock=True)
    if inv.status != "open":
        raise Conflict("Payments can only be recorded against an open invoice.")
    if body.amount > inv.outstanding:
        raise Unprocessable(f"The payment exceeds the outstanding balance of {inv.outstanding} {inv.currency}.")
    pid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO billing_payments (id, organization_id, invoice_id, kind, amount, currency, method, reference,"
            " received_at, note, recorded_by) VALUES (:id, :o, :i, 'payment', :a, :c, :m, :r, COALESCE(:rc, now()), :n, :by)"
        ),
        {
            "id": pid,
            "o": inv.organization_id,
            "i": invoice_id,
            "a": body.amount,
            "c": inv.currency,
            "m": body.method,
            "r": body.reference,
            "rc": body.received_at,
            "n": body.note,
            "by": ctx.admin_id,
        },
    )
    paudit(
        ctx,
        "payment.recorded",
        target_type="invoice",
        target_id=invoice_id,
        org=inv.organization_id,
        summary={"number": inv.number, "amount": str(body.amount), "currency": inv.currency, "method": body.method},
    )
    _resync(ctx, invoice_id)
    return get_invoice(invoice_id, ctx)


@router.post(
    "/invoices/{invoice_id}/refunds",
    response_model=InvoiceDetail,
    status_code=201,
    operation_id="platform_refund_record",
)
def record_refund(
    invoice_id: uuid.UUID, body: RefundCreate, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]
) -> InvoiceDetail:
    inv = _invoice(ctx, invoice_id, lock=True)
    if inv.status not in ("open", "paid"):
        raise Conflict("Refunds can only be recorded against an open or paid invoice.")
    if body.amount > inv.paid_total:
        raise Unprocessable(f"The refund exceeds the net amount paid ({inv.paid_total} {inv.currency}).")
    ctx.conn.execute(
        text(
            "INSERT INTO billing_payments (id, organization_id, invoice_id, kind, amount, currency, method, reference,"
            " note, recorded_by) VALUES (:id, :o, :i, 'refund', :a, :c, :m, :r, :n, :by)"
        ),
        {
            "id": new_id(),
            "o": inv.organization_id,
            "i": invoice_id,
            "a": body.amount,
            "c": inv.currency,
            "m": body.method,
            "r": body.reference,
            "n": body.note,
            "by": ctx.admin_id,
        },
    )
    paudit(
        ctx,
        "refund.recorded",
        target_type="invoice",
        target_id=invoice_id,
        org=inv.organization_id,
        reason=body.note,
        summary={"number": inv.number, "amount": str(body.amount), "currency": inv.currency},
    )
    _resync(ctx, invoice_id)
    return get_invoice(invoice_id, ctx)


@router.post(
    "/invoices/{invoice_id}/adjustments",
    response_model=InvoiceDetail,
    status_code=201,
    operation_id="platform_adjustment_record",
)
def record_adjustment(
    invoice_id: uuid.UUID, body: AdjustmentCreate, ctx: Annotated[PlatformCtx, padmin("platform.billing.manage")]
) -> InvoiceDetail:
    """Signed manual adjustment (credit < 0, debit > 0) with a mandatory reason. The invoice total is never edited."""
    inv = _invoice(ctx, invoice_id, lock=True)
    if inv.status != "open":
        raise Conflict("Adjustments can only be recorded against an open invoice.")
    if body.amount == 0:
        raise Unprocessable("An adjustment cannot be zero.")
    if inv.amount_total + inv.adjustments_total + body.amount < 0:
        raise Unprocessable("The adjustment would make the invoice total negative.")
    ctx.conn.execute(
        text(
            "INSERT INTO billing_adjustments (id, organization_id, invoice_id, amount, currency, reason, created_by)"
            " VALUES (:id, :o, :i, :a, :c, :r, :by)"
        ),
        {
            "id": new_id(),
            "o": inv.organization_id,
            "i": invoice_id,
            "a": body.amount,
            "c": inv.currency,
            "r": body.reason,
            "by": ctx.admin_id,
        },
    )
    paudit(
        ctx,
        "invoice.adjusted",
        target_type="invoice",
        target_id=invoice_id,
        org=inv.organization_id,
        reason=body.reason,
        summary={"number": inv.number, "amount": str(body.amount), "currency": inv.currency},
    )
    _resync(ctx, invoice_id)
    return get_invoice(invoice_id, ctx)
