"""Accounting core (Phase 8): the deterministic, side-effect-free part of the accounting engine.

Everything that decides a financial number lives here and nowhere else: decimal arithmetic and rounding, tax calculation,
journal construction and validation, rule resolution, contact matching and duplicate detection. No I/O, no framework, no
clock: the service layer loads company configuration, calls these functions and stores the outcome.

Rules that hold throughout:
* Money is `Decimal`. `D()` refuses `float` so a binary float can never enter a calculation.
* Nothing is adjusted to make totals agree: differences are REPORTED as issues; the accountant decides.
* Nothing is guessed: a missing account, tax code or exchange rate is an unresolved item (a blocking issue), not a default
  chosen on the user's behalf.
* Country specifics are data (tax codes and rates supplied by the caller); there are no country constants here.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

ZERO = Decimal(0)
HUNDRED = Decimal(100)

# ------------------------------------------------------------------ vocabulary
TXN_TYPES: tuple[str, ...] = (
    "sales_invoice",
    "sales_credit_note",
    "customer_receipt",
    "customer_refund",
    "purchase_invoice",
    "purchase_credit_note",
    "supplier_payment",
    "supplier_refund",
    "cash_expense",
    "bank_expense",
    "employee_expense",
    "general_expense",
    "bank_deposit",
    "bank_withdrawal",
    "bank_charges",
    "bank_transfer",
    "journal_entry",
    "opening_balance",
    "adjustment_entry",
    "accrual",
    "prepayment",
    "depreciation",
    "reversal_entry",
)
# types whose journal is typed in by the accountant (the engine validates but never invents the accounts)
MANUAL_TYPES = frozenset(
    {"journal_entry", "opening_balance", "adjustment_entry", "accrual", "prepayment", "depreciation", "reversal_entry"}
)
SALES_TYPES = frozenset({"sales_invoice", "sales_credit_note"})
PURCHASE_TYPES = frozenset({"purchase_invoice", "purchase_credit_note"})
EXPENSE_TYPES = frozenset({"cash_expense", "bank_expense", "employee_expense", "general_expense"})
SETTLEMENT_TYPES = frozenset({"customer_receipt", "customer_refund", "supplier_payment", "supplier_refund"})
BANK_TYPES = frozenset({"bank_deposit", "bank_withdrawal", "bank_charges", "bank_transfer"})
# types that need a customer/supplier before approval (they feed receivable/payable balances)
CONTACT_REQUIRED = SALES_TYPES | PURCHASE_TYPES | SETTLEMENT_TYPES
JOURNAL_TYPE_OF: dict[str, str] = {
    **dict.fromkeys(SALES_TYPES | {"customer_receipt", "customer_refund"}, "sales"),
    **dict.fromkeys(PURCHASE_TYPES | {"supplier_payment", "supplier_refund"} | EXPENSE_TYPES, "purchases"),
    **dict.fromkeys(BANK_TYPES, "bank"),
    "opening_balance": "opening",
    "adjustment_entry": "adjustment",
    "accrual": "adjustment",
    "prepayment": "adjustment",
    "depreciation": "adjustment",
    "reversal_entry": "reversal",
    "journal_entry": "general",
}
TAX_TREATMENTS = ("standard", "zero_rated", "exempt", "out_of_scope", "reverse_charge", "import")
RATE_BEARING = frozenset({"standard", "reverse_charge", "import"})
ACCOUNT_TYPES = (
    "asset",
    "current_asset",
    "non_current_asset",
    "liability",
    "current_liability",
    "long_term_liability",
    "equity",
    "revenue",
    "other_income",
    "cost_of_sales",
    "expense",
    "other_expense",
    "tax",
    "bank",
    "cash",
    "receivable",
    "payable",
)
_DEBIT_NORMAL = frozenset(
    {
        "asset",
        "current_asset",
        "non_current_asset",
        "cost_of_sales",
        "expense",
        "other_expense",
        "bank",
        "cash",
        "receivable",
    }
)
_CREDIT_NORMAL = frozenset(
    {"liability", "current_liability", "long_term_liability", "equity", "revenue", "other_income", "payable"}
)
ACCOUNT_ROLES = (
    "ar_control",
    "ap_control",
    "employee_payable",
    "vat_input",
    "vat_output",
    "bank",
    "cash",
    "bank_charges",
    "expense_default",
    "revenue_default",
    "suspense",
    "rounding",
)
SETTLEMENT_ACCOUNT_TYPES = frozenset({"bank", "cash"})


def default_normal_balance(account_type: str) -> str | None:
    """Debit/credit for account types that have an obvious natural side; `tax` accounts must state it explicitly."""
    if account_type in _DEBIT_NORMAL:
        return "debit"
    if account_type in _CREDIT_NORMAL:
        return "credit"
    return None


# ------------------------------------------------------------------ decimals
def D(value: Any) -> Decimal:
    """Strict conversion. Accepts Decimal, int, and numeric strings; refuses float, bool, NaN and infinities."""
    if isinstance(value, bool | float):
        raise TypeError("binary floating point is not allowed in financial calculations")
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, int):
        out = Decimal(value)
    elif isinstance(value, str):
        try:
            out = Decimal(value.strip().replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal number: {value!r}") from exc
    else:
        raise TypeError(f"unsupported amount type: {type(value).__name__}")
    if not out.is_finite():
        raise ValueError("amount must be finite")
    return out


def d_or_none(value: Any) -> Decimal | None:
    return None if value is None or value == "" else D(value)


def quantum(scale: int) -> Decimal:
    return Decimal(1).scaleb(-scale)


def rnd(value: Decimal, scale: int, mode: str = "half_up") -> Decimal:
    """Round to `scale` decimals with the company's rounding mode (half_up | half_even)."""
    return value.quantize(quantum(scale), rounding=ROUND_HALF_EVEN if mode == "half_even" else ROUND_HALF_UP)


def money_str(value: Decimal | None, scale: int | None = None) -> str | None:
    if value is None:
        return None
    return format(rnd(value, scale) if scale is not None else value, "f")


# ------------------------------------------------------------------ issues
@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # error | warning | info
    message: str
    blocking: bool = False  # a blocking issue can never be overridden; approval is refused until it is fixed
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "blocking": self.blocking,
            "data": self.data,
        }


def err(code: str, message: str, **data: Any) -> Issue:
    return Issue(code, "error", message, True, data)


def warn(code: str, message: str, **data: Any) -> Issue:
    return Issue(code, "warning", message, False, data)


def soft_err(code: str, message: str, **data: Any) -> Issue:
    """An error the approver may knowingly accept (with a written reason); not blocking."""
    return Issue(code, "error", message, False, data)


# ------------------------------------------------------------------ tax
@dataclass(frozen=True)
class TaxSpec:
    code_id: str | None
    treatment: str
    rate: Decimal  # percent, e.g. Decimal("5")
    recoverable: bool = True
    input_account_id: str | None = None
    output_account_id: str | None = None


def validate_tax_spec(spec: TaxSpec) -> list[Issue]:
    out: list[Issue] = []
    if spec.treatment not in TAX_TREATMENTS:
        out.append(err("invalid_tax_treatment", f"Unknown tax treatment “{spec.treatment}”."))
    if spec.rate < 0 or spec.rate > HUNDRED:
        out.append(err("invalid_tax_rate", "A tax rate must be between 0 and 100 percent."))
    if spec.treatment not in RATE_BEARING and spec.rate != 0:
        out.append(err("invalid_tax_rate", f"Treatment “{spec.treatment}” must carry a 0% rate."))
    if spec.treatment in RATE_BEARING and spec.rate == 0:
        out.append(warn("zero_rate_on_taxable_treatment", f"Treatment “{spec.treatment}” has a 0% rate."))
    return out


@dataclass(frozen=True)
class LineIn:
    quantity: Decimal | None  # None when the line carries only an amount
    unit_price: Decimal | None
    amount: Decimal | None  # explicit line amount (net, or gross when tax-inclusive); wins over quantity times price
    discount: Decimal = ZERO
    tax: TaxSpec | None = None


@dataclass(frozen=True)
class LineCalc:
    net: Decimal
    tax: Decimal
    gross: Decimal
    taxable: Decimal  # the base the rate was applied to (0 for exempt/zero/out-of-scope lines)
    rate: Decimal


@dataclass(frozen=True)
class DocTotals:
    net: Decimal
    tax: Decimal
    gross: Decimal
    by_rate: dict[str, dict[str, str]]  # treatment@rate → {taxable, tax}


def line_base(line: LineIn) -> Decimal:
    if line.amount is not None:
        return line.amount
    if line.unit_price is None:
        raise ValueError("a line needs an amount, or a unit price and a quantity")
    return (line.quantity if line.quantity is not None else Decimal(1)) * line.unit_price - line.discount


def calc_lines(
    lines: Sequence[LineIn], *, inclusive: bool, scale: int, mode: str = "half_up", scope: str = "line"
) -> tuple[list[LineCalc], DocTotals]:
    """Net, tax and gross per line and in total. `inclusive`: the line amounts already contain tax.

    scope="line": tax is rounded line by line. scope="document": tax is computed on each rate group's total and rounded
    once; the group's rounding difference is allocated to its largest line so line taxes still add up to the document
    tax. Exact rounding is deterministic (largest net first, later line wins ties)."""
    prepared: list[tuple[Decimal, Decimal, Decimal]] = []  # (amount, rate, unrounded tax)
    for ln in lines:
        base = line_base(ln)
        rate = ln.tax.rate if ln.tax is not None and ln.tax.treatment in RATE_BEARING else ZERO
        inclusive_tax = base - base / (1 + rate / HUNDRED) if rate else ZERO
        tax_raw = inclusive_tax if inclusive else base * rate / HUNDRED
        prepared.append((base, rate, tax_raw))
    taxes: list[Decimal] = [rnd(t, scale, mode) for _, _, t in prepared]
    if scope == "document":
        groups: dict[Decimal, list[int]] = defaultdict(list)
        for i, (_, rate, _) in enumerate(prepared):
            groups[rate].append(i)
        for rate, idx in groups.items():
            if not rate:
                continue
            total = rnd(sum((prepared[i][2] for i in idx), ZERO), scale, mode)
            diff = total - sum((taxes[i] for i in idx), ZERO)
            if diff:
                biggest = max(idx, key=lambda i: (abs(prepared[i][0]), i))
                taxes[biggest] += diff
    calcs: list[LineCalc] = []
    for (base, rate, _), tax in zip(prepared, taxes, strict=True):
        amount = rnd(base, scale, mode)
        if inclusive:
            gross, net = amount, amount - tax
        else:
            net, gross = amount, amount + tax
        calcs.append(LineCalc(net, tax, gross, net if rate else ZERO, rate))
    by_rate: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"taxable": ZERO, "tax": ZERO})
    for ln, c in zip(lines, calcs, strict=True):
        key = f"{ln.tax.treatment if ln.tax else 'none'}@{c.rate}"
        by_rate[key]["taxable"] += c.taxable
        by_rate[key]["tax"] += c.tax
    totals = DocTotals(
        sum((c.net for c in calcs), ZERO),
        sum((c.tax for c in calcs), ZERO),
        sum((c.gross for c in calcs), ZERO),
        {k: {a: format(b, "f") for a, b in v.items()} for k, v in by_rate.items()},
    )
    return calcs, totals


def compare_totals(
    stated: Mapping[str, Decimal | None], calc: DocTotals, *, tolerance: Decimal, scale: int
) -> list[Issue]:
    """Compare the amounts printed on the document with the deterministic calculation. Never changes either side."""
    out: list[Issue] = []
    for key, computed in (("net", calc.net), ("tax", calc.tax), ("gross", calc.gross)):
        stated_value = stated.get(key)
        if stated_value is None:
            continue
        diff = rnd(stated_value - computed, scale)
        if abs(diff) > tolerance:
            out.append(
                soft_err(
                    f"{key}_mismatch",
                    f"The {key} amount on the document differs from the calculation by {format(diff, 'f')}.",
                    stated=format(stated_value, "f"),
                    calculated=format(computed, "f"),
                    difference=format(diff, "f"),
                )
            )
    net, tax, gross = stated.get("net"), stated.get("tax"), stated.get("gross")
    if net is not None and tax is not None and gross is not None:
        diff = rnd(net + tax - gross, scale)
        if abs(diff) > tolerance:
            out.append(
                soft_err(
                    "net_plus_tax_not_gross",
                    f"Net plus tax differs from gross on the document by {format(diff, 'f')}.",
                    difference=format(diff, "f"),
                )
            )
    return out


# ------------------------------------------------------------------ journals
@dataclass(frozen=True)
class AccountInfo:
    id: str
    code: str
    name: str
    account_type: str
    normal_balance: str
    is_active: bool
    is_postable: bool
    currency: str | None = None


@dataclass(frozen=True)
class TLine:
    """A transaction line as the journal builder sees it (all amounts in the transaction currency, never negative)."""

    line_no: int
    description: str | None
    account_id: str | None
    net: Decimal
    tax: Decimal
    gross: Decimal
    tax_code_id: str | None = None
    treatment: str | None = None
    input_account_id: str | None = None
    output_account_id: str | None = None
    recoverable: bool = True
    entry_side: str | None = None  # manual journal types only
    dimensions: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JLine:
    account_id: str | None
    debit: Decimal
    credit: Decimal
    description: str | None = None
    reference: str | None = None
    tax_code_id: str | None = None
    contact_id: str | None = None
    dimensions: Mapping[str, str] = field(default_factory=dict)
    source: str = "generated"  # generated | manual | rounding
    base_debit: Decimal = ZERO
    base_credit: Decimal = ZERO


def _dr(account: str | None, amount: Decimal, **kw: Any) -> JLine:
    return JLine(account, amount, ZERO, **kw)


def _cr(account: str | None, amount: Decimal, **kw: Any) -> JLine:
    return JLine(account, ZERO, amount, **kw)


def build_journal(
    txn_type: str,
    lines: Sequence[TLine],
    *,
    control_account: str | None,
    settlement_account: str | None,
    counter_account: str | None = None,
    contact_id: str | None = None,
    reference: str | None = None,
) -> tuple[list[JLine], list[Issue]]:
    """Generate the journal for a transaction from its lines and the resolved accounts. Pure and deterministic.

    Sign convention: every amount is positive; direction comes from the transaction type (credit notes mirror their
    invoice). A line whose account cannot be resolved is emitted with `account_id=None` so validation reports it."""
    issues: list[Issue] = []
    out: list[JLine] = []
    ctx: dict[str, Any] = {"reference": reference}
    sub: dict[str, Any] = {"contact_id": contact_id, **ctx}

    if txn_type in MANUAL_TYPES:
        for ln in lines:
            if ln.entry_side not in ("debit", "credit"):
                issues.append(err("entry_side_missing", f"Line {ln.line_no} must be a debit or a credit."))
                continue
            amount = ln.gross if ln.gross else ln.net
            side = _dr if ln.entry_side == "debit" else _cr
            out.append(
                side(
                    ln.account_id,
                    amount,
                    description=ln.description,
                    tax_code_id=ln.tax_code_id,
                    dimensions=ln.dimensions,
                    source="manual",
                    **ctx,
                )
            )
        return out, issues

    gross_total = sum((ln.gross for ln in lines), ZERO)
    net_total = sum((ln.net for ln in lines), ZERO)
    # reverse-charged tax is self-assessed: it is not part of what is owed to the supplier
    payable_total = gross_total - sum((ln.tax for ln in lines if ln.treatment == "reverse_charge"), ZERO)

    def expense_items() -> None:
        """Dr item (net, plus tax that cannot be recovered) and Dr input tax; reverse charge also Cr output tax."""
        for ln in lines:
            folded = ln.tax if ln.tax and not ln.recoverable else ZERO
            out.append(
                _dr(
                    ln.account_id,
                    ln.net + folded,
                    description=ln.description,
                    tax_code_id=ln.tax_code_id,
                    dimensions=ln.dimensions,
                    **ctx,
                )
            )
            if not ln.tax:
                continue
            if ln.treatment == "reverse_charge":
                if ln.recoverable:
                    out.append(
                        _dr(
                            ln.input_account_id,
                            ln.tax,
                            description="Reverse charge — input tax",
                            tax_code_id=ln.tax_code_id,
                            **ctx,
                        )
                    )
                out.append(
                    _cr(
                        ln.output_account_id,
                        ln.tax,
                        description="Reverse charge — output tax",
                        tax_code_id=ln.tax_code_id,
                        **ctx,
                    )
                )
            elif ln.recoverable:
                out.append(_dr(ln.input_account_id, ln.tax, description="Input tax", tax_code_id=ln.tax_code_id, **ctx))

    def expense_items_mirrored() -> None:
        for ln in lines:
            folded = ln.tax if ln.tax and not ln.recoverable else ZERO
            out.append(
                _cr(
                    ln.account_id,
                    ln.net + folded,
                    description=ln.description,
                    tax_code_id=ln.tax_code_id,
                    dimensions=ln.dimensions,
                    **ctx,
                )
            )
            if not ln.tax:
                continue
            if ln.treatment == "reverse_charge":
                if ln.recoverable:
                    out.append(
                        _cr(
                            ln.input_account_id,
                            ln.tax,
                            description="Reverse charge — input tax",
                            tax_code_id=ln.tax_code_id,
                            **ctx,
                        )
                    )
                out.append(
                    _dr(
                        ln.output_account_id,
                        ln.tax,
                        description="Reverse charge — output tax",
                        tax_code_id=ln.tax_code_id,
                        **ctx,
                    )
                )
            elif ln.recoverable:
                out.append(_cr(ln.input_account_id, ln.tax, description="Input tax", tax_code_id=ln.tax_code_id, **ctx))

    def revenue_items(item_side: Any, tax_side: Any) -> None:
        for ln in lines:
            out.append(
                item_side(
                    ln.account_id,
                    ln.net,
                    description=ln.description,
                    tax_code_id=ln.tax_code_id,
                    dimensions=ln.dimensions,
                    **ctx,
                )
            )
            if ln.tax:
                out.append(
                    tax_side(ln.output_account_id, ln.tax, description="Output tax", tax_code_id=ln.tax_code_id, **ctx)
                )

    if txn_type in ("purchase_invoice", "general_expense", "employee_expense", "cash_expense", "bank_expense"):
        credit_acct = control_account
        if txn_type in ("cash_expense", "bank_expense") or (txn_type == "general_expense" and settlement_account):
            credit_acct = settlement_account
        expense_items()
        out.append(_cr(credit_acct, payable_total, description="Payable / settlement", **sub))
    elif txn_type == "purchase_credit_note":
        out.append(_dr(control_account, payable_total, description="Supplier credit", **sub))
        expense_items_mirrored()
    elif txn_type == "sales_invoice":
        out.append(_dr(control_account, gross_total, description="Receivable", **sub))
        revenue_items(_cr, _cr)
    elif txn_type == "sales_credit_note":
        out.append(_cr(control_account, gross_total, description="Customer credit", **sub))
        revenue_items(_dr, _dr)
    elif txn_type in SETTLEMENT_TYPES:
        if txn_type in ("customer_receipt", "supplier_refund"):
            out.append(_dr(settlement_account, gross_total, description="Received", **ctx))
            out.append(_cr(control_account, gross_total, description="Settled", **sub))
        else:  # customer_refund, supplier_payment
            out.append(_dr(control_account, gross_total, description="Settled", **sub))
            out.append(_cr(settlement_account, gross_total, description="Paid", **ctx))
    elif txn_type == "bank_deposit":
        out.append(_dr(settlement_account, gross_total, description="Deposit", **ctx))
        revenue_items(_cr, _cr)
    elif txn_type in ("bank_withdrawal", "bank_charges"):
        expense_items()
        out.append(_cr(settlement_account, payable_total, description="Bank", **ctx))
    elif txn_type == "bank_transfer":
        amount = net_total or gross_total
        out.append(_dr(counter_account, amount, description="Transfer in", **ctx))
        out.append(_cr(settlement_account, amount, description="Transfer out", **ctx))
    else:
        issues.append(err("unsupported_transaction_type", f"No journal template exists for “{txn_type}”."))
    # drop zero-amount lines that arise from empty tax columns (never a line the accountant typed)
    out = [ln for ln in out if ln.debit or ln.credit or ln.source == "manual"]
    return out, issues


def to_base(
    lines: Sequence[JLine],
    *,
    rate: Decimal,
    base_scale: int,
    mode: str,
    rounding_account: str | None,
    tolerance: Decimal,
) -> tuple[list[JLine], list[Issue]]:
    """Convert transaction-currency lines to the base currency. A residual rounding difference within `tolerance` is
    posted to the company's rounding account; without that account (or beyond tolerance) the journal stays unbalanced and
    validation blocks it. Rates are never invented: the caller must supply `rate`."""
    converted = [
        JLine(
            ln.account_id,
            ln.debit,
            ln.credit,
            ln.description,
            ln.reference,
            ln.tax_code_id,
            ln.contact_id,
            ln.dimensions,
            ln.source,
            rnd(ln.debit * rate, base_scale, mode),
            rnd(ln.credit * rate, base_scale, mode),
        )
        for ln in lines
    ]
    issues: list[Issue] = []
    diff = sum((ln.base_debit for ln in converted), ZERO) - sum((ln.base_credit for ln in converted), ZERO)
    if diff and rounding_account is not None and abs(diff) <= tolerance:
        side = (
            JLine(rounding_account, ZERO, ZERO, "Rounding difference", source="rounding", base_credit=diff)
            if diff > 0
            else JLine(rounding_account, ZERO, ZERO, "Rounding difference", source="rounding", base_debit=-diff)
        )
        converted.append(side)
        issues.append(
            warn(
                "rounding_difference_posted",
                f"A base-currency rounding difference of {format(abs(diff), 'f')} was posted to the rounding account.",
                difference=format(diff, "f"),
            )
        )
    # otherwise validate_journal reports journal_unbalanced_base
    return converted, issues


def _total(lines: Iterable[JLine], attr: str) -> Decimal:
    return sum((getattr(ln, attr) for ln in lines), ZERO)


def validate_journal(
    lines: Sequence[JLine],
    accounts: Mapping[str, AccountInfo],
    *,
    base_currency: str,
    currency: str,
    required_dimensions: Sequence[str] = (),
    scale: int = 2,
    base_scale: int = 2,
) -> list[Issue]:
    """Backend validation of a journal. Every blocking issue here prevents approval."""
    out: list[Issue] = []
    real = [ln for ln in lines if ln.debit or ln.credit or ln.base_debit or ln.base_credit]
    if len(real) < 2:
        out.append(err("too_few_lines", "A journal needs at least two lines with amounts."))
    for i, ln in enumerate(lines, 1):
        if ln.debit < 0 or ln.credit < 0 or ln.base_debit < 0 or ln.base_credit < 0:
            out.append(err("negative_amount", f"Line {i}: amounts cannot be negative.", line=i))
        if ln.debit > 0 and ln.credit > 0:
            out.append(err("both_sides", f"Line {i}: a line cannot be both a debit and a credit.", line=i))
        if ln.debit == 0 and ln.credit == 0 and ln.source != "rounding":
            out.append(err("line_without_amount", f"Line {i} has no amount.", line=i))
        if ln.account_id is None:
            out.append(err("missing_account_mapping", f"Line {i} has no account.", line=i))
            continue
        acct = accounts.get(ln.account_id)
        if acct is None:
            out.append(err("account_not_found", f"Line {i}: the account does not exist in this company.", line=i))
            continue
        if not acct.is_active:
            out.append(
                err("account_inactive", f"Line {i}: account {acct.code} is inactive.", line=i, account=acct.code)
            )
        if not acct.is_postable:
            out.append(
                err(
                    "account_not_postable",
                    f"Line {i}: account {acct.code} is a group account and cannot be posted to.",
                    line=i,
                    account=acct.code,
                )
            )
        if acct.currency and acct.currency != currency and acct.currency != base_currency:
            out.append(
                err(
                    "account_currency_mismatch",
                    f"Line {i}: account {acct.code} is restricted to {acct.currency}.",
                    line=i,
                    account=acct.code,
                )
            )
        for dim in required_dimensions:
            if not ln.dimensions.get(dim):
                out.append(err("dimension_missing", f"Line {i}: the {dim.replace('_', ' ')} is required.", line=i))
    diff = rnd(_total(lines, "debit") - _total(lines, "credit"), max(scale, 6))
    if diff != 0:
        out.append(
            err(
                "journal_unbalanced",
                f"Debits and credits differ by {format(diff, 'f')}.",
                difference=format(diff, "f"),
                debit=format(_total(lines, "debit"), "f"),
                credit=format(_total(lines, "credit"), "f"),
            )
        )
    bdiff = rnd(_total(lines, "base_debit") - _total(lines, "base_credit"), max(base_scale, 6))
    if bdiff != 0:
        out.append(
            err(
                "journal_unbalanced_base",
                f"Base-currency debits and credits differ by {format(bdiff, 'f')}.",
                difference=format(bdiff, "f"),
            )
        )
    return out


def check_period(on: date, periods: Sequence[Mapping[str, Any]], *, required: bool) -> list[Issue]:
    """The accounting period that contains `on` must exist and be open. Dates are never moved to fit."""
    if not required:
        return []
    for p in periods:
        if p["start_date"] <= on <= p["end_date"]:
            if p["status"] != "open":
                return [err("period_closed", f"The accounting period {p['name']} is closed.", period=p["name"])]
            return []
    return [err("no_period", f"No accounting period is defined for {on.isoformat()}.")]


def check_rate(
    currency: str, base_currency: str, rate: Decimal | None, rate_date: date | None, on: date, max_age_days: int
) -> list[Issue]:
    if currency == base_currency:
        return []
    if rate is None or rate <= 0:
        return [
            err(
                "missing_exchange_rate",
                f"No exchange rate from {currency} to {base_currency} is available for {on.isoformat()}. Enter one or configure a rate.",
            )
        ]
    if rate_date is not None and (on - rate_date).days > max_age_days:
        return [
            warn(
                "stale_exchange_rate",
                f"The exchange rate is dated {rate_date.isoformat()}, {(on - rate_date).days} days before the transaction.",
            )
        ]
    return []


# ------------------------------------------------------------------ rules
RULE_FIELDS = (
    "txn_type",
    "document_type",
    "contact_id",
    "contact_name",
    "contact_tax_number",
    "description",
    "currency",
    "amount",
    "doc_number",
    "project",
    "cost_center",
    "department",
)
RULE_OPS = ("eq", "neq", "contains", "starts_with", "in", "gte", "lte")
RULE_KINDS = ("account", "tax", "narration", "dimension")
NARRATION_FIELDS = ("contact", "doc_number", "date", "description", "txn_type")
ACCOUNT_TARGETS = ("line", "control", "settlement")
DIMENSIONS = ("project", "cost_center", "department")


@dataclass(frozen=True)
class Rule:
    id: str
    rule_key: str
    name: str
    kind: str
    priority: int
    conditions: tuple[Mapping[str, Any], ...]
    action: Mapping[str, Any]
    effective_from: date | None
    effective_to: date | None
    is_active: bool = True
    version: int = 1


def validate_rule_definition(
    kind: str, conditions: Sequence[Mapping[str, Any]], action: Mapping[str, Any]
) -> list[str]:
    """Structural validation of a rule. Rules are data, never code: a closed vocabulary of fields, operators and actions."""
    problems: list[str] = []
    if kind not in RULE_KINDS:
        problems.append(f"Unknown rule kind “{kind}”.")
    if not conditions:
        problems.append("A rule needs at least one condition (a rule with no condition would match everything).")
    for c in conditions:
        if set(c) - {"field", "op", "value"}:
            problems.append("A condition may only contain field, op and value.")
        if c.get("field") not in RULE_FIELDS:
            problems.append(f"Unknown condition field “{c.get('field')}”.")
        if c.get("op") not in RULE_OPS:
            problems.append(f"Unknown condition operator “{c.get('op')}”.")
        value = c.get("value")
        if c.get("op") == "in" and not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
            problems.append("The “in” operator needs a non-empty list of text values.")
        elif c.get("op") in ("gte", "lte"):
            try:
                D(value if isinstance(value, str | int | Decimal) else None)
            except (TypeError, ValueError):
                problems.append("Numeric comparisons need a number (as text).")
        elif c.get("op") not in ("in", "gte", "lte") and not isinstance(value, str | int):
            problems.append("Condition values must be text.")
    allowed = {
        "account": {"account_id", "target"},
        "tax": {"tax_code_id"},
        "narration": {"template"},
        "dimension": {"name", "value"},
    }.get(kind, set())
    if set(action) - allowed or not set(action) >= (allowed - {"target"}):
        problems.append(f"A {kind} rule action must use exactly: {', '.join(sorted(allowed))}.")
    if kind == "account" and action.get("target", "line") not in ACCOUNT_TARGETS:
        problems.append(f"Account rule target must be one of {', '.join(ACCOUNT_TARGETS)}.")
    if kind == "dimension" and action.get("name") not in DIMENSIONS:
        problems.append(f"Dimension must be one of {', '.join(DIMENSIONS)}.")
    if kind == "narration":
        tpl = str(action.get("template", ""))
        if not tpl or len(tpl) > 200:
            problems.append("A narration template is 1 to 200 characters.")
        for name in re.findall(r"\{([^{}]*)\}", tpl):
            if name not in NARRATION_FIELDS:
                problems.append(f"Unknown narration placeholder {{{name}}}; use {', '.join(NARRATION_FIELDS)}.")
        if re.search(r"\{[^{}]*[.\[!:]", tpl):
            problems.append("Narration placeholders cannot use attribute access or format specifiers.")
    return problems


def _norm(value: Any) -> str:
    return str(value if value is not None else "").strip().casefold()


def _cond_true(c: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    actual = facts.get(c["field"])
    op, want = c["op"], c["value"]
    if actual is None:
        return False
    if op == "eq":
        return _norm(actual) == _norm(want)
    if op == "neq":
        return _norm(actual) != _norm(want)
    if op == "contains":
        return _norm(want) in _norm(actual)
    if op == "starts_with":
        return _norm(actual).startswith(_norm(want))
    if op == "in":
        return _norm(actual) in {_norm(v) for v in want}
    try:
        value = D(actual if isinstance(actual, str | int | Decimal) else str(actual))
        bound = D(want)
    except (TypeError, ValueError):
        return False
    return value >= bound if op == "gte" else value <= bound


def rule_applies(rule: Rule, facts: Mapping[str, Any], on: date) -> bool:
    if not rule.is_active:
        return False
    if rule.effective_from and on < rule.effective_from:
        return False
    if rule.effective_to and on > rule.effective_to:
        return False
    return all(_cond_true(c, facts) for c in rule.conditions)


@dataclass(frozen=True)
class Resolution:
    value: Any | None
    rule_ids: tuple[str, ...]
    conflict: bool
    candidates: tuple[tuple[str, Any], ...] = ()


def _action_value(rule: Rule) -> Any:
    if rule.kind == "account":
        return rule.action["account_id"]
    if rule.kind == "tax":
        return rule.action["tax_code_id"]
    if rule.kind == "narration":
        return rule.action["template"]
    return (rule.action["name"], rule.action["value"])


def resolve_rules(
    rules: Sequence[Rule], facts: Mapping[str, Any], on: date, *, kind: str, target: str | None = None
) -> Resolution:
    """Highest-priority applicable rules win. If rules of the SAME top priority disagree the result is a conflict: no
    value is chosen and the transaction is flagged for the accountant (conflicts never resolve silently)."""
    applicable = [
        r
        for r in rules
        if r.kind == kind
        and (target is None or r.action.get("target", "line") == target)
        and rule_applies(r, facts, on)
    ]
    if not applicable:
        return Resolution(None, (), False)
    top = max(r.priority for r in applicable)
    best = [r for r in applicable if r.priority == top]
    values = {repr(_action_value(r)): _action_value(r) for r in best}
    cands = tuple((r.id, _action_value(r)) for r in best)
    if len(values) > 1:
        return Resolution(None, tuple(r.id for r in best), True, cands)
    return Resolution(next(iter(values.values())), tuple(r.id for r in best), False, cands)


def render_narration(template: str, values: Mapping[str, Any]) -> str:
    """Fill {contact}/{doc_number}/{date}/{description}/{txn_type}; unknown placeholders were refused at rule creation."""
    safe = {k: str(values.get(k) or "") for k in NARRATION_FIELDS}
    return re.sub(r"\{(\w+)\}", lambda m: safe.get(m.group(1), ""), template).strip()[:500]


# ------------------------------------------------------------------ contacts and duplicates
_LEGAL_SUFFIX = re.compile(
    r"\b(llc|l l c|ltd|limited|co|company|est|establishment|inc|corp|corporation|sarl|fze|fzc|llp|plc|saoc|saog|spc)\b"
)


def normalize_name(name: str | None) -> str:
    text = _norm(name)
    text = re.sub(r"[^\w\s]", " ", text)
    text = _LEGAL_SUFFIX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_tax_number(value: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (value or "").upper())


@dataclass(frozen=True)
class ContactMatch:
    contact_id: str
    strength: str  # tax_number | name_exact | name_similar
    score: float


def match_contacts(
    name: str | None, tax_number: str | None, contacts: Sequence[Mapping[str, Any]], *, similar_threshold: float = 0.88
) -> list[ContactMatch]:
    """Suggest existing contacts for a document party. Suggestions only: nothing is ever linked or merged automatically,
    and a merely similar name is labelled as such."""
    tn, nn = normalize_tax_number(tax_number), normalize_name(name)
    out: dict[str, ContactMatch] = {}
    for c in contacts:
        cid = str(c["id"])
        if tn and normalize_tax_number(c.get("tax_number")) == tn:
            out[cid] = ContactMatch(cid, "tax_number", 1.0)
            continue
        names = {normalize_name(c.get("legal_name")), normalize_name(c.get("display_name"))} - {""}
        if nn and nn in names:
            out[cid] = ContactMatch(cid, "name_exact", 0.95)
            continue
        if nn and names:
            best = max(difflib.SequenceMatcher(None, nn, n).ratio() for n in names)
            if best >= similar_threshold:
                out[cid] = ContactMatch(cid, "name_similar", round(best, 3))
    order = {"tax_number": 0, "name_exact": 1, "name_similar": 2}
    return sorted(out.values(), key=lambda m: (order[m.strength], -m.score, m.contact_id))


@dataclass(frozen=True)
class DupFacts:
    contact_id: str | None
    doc_number: str | None
    gross: Decimal
    currency: str
    on: date
    tax_number: str | None = None
    checksum: str | None = None
    txn_type: str | None = None


def duplicate_level(new: DupFacts, other: DupFacts) -> tuple[str | None, dict[str, Any]]:
    """Compare two transactions of one company. `exact` = the same document, or the same party + number + amount +
    currency; `possible` = strong overlap that a person must look at. Merely similar transactions (different party, or
    only the same amount) are NOT flagged, so legitimate repeats do not drown real duplicates."""
    sig: dict[str, Any] = {}
    same_contact = bool(new.contact_id) and new.contact_id == other.contact_id
    same_number = bool(_norm(new.doc_number)) and _norm(new.doc_number) == _norm(other.doc_number)
    same_amount = new.gross == other.gross and new.currency == other.currency
    same_tax = bool(normalize_tax_number(new.tax_number)) and normalize_tax_number(
        new.tax_number
    ) == normalize_tax_number(other.tax_number)
    days = abs((new.on - other.on).days)
    if same_contact:
        sig["same_contact"] = True
    if same_number:
        sig["same_document_number"] = True
    if same_amount:
        sig["same_amount_and_currency"] = True
    if same_tax:
        sig["same_tax_number"] = True
    if new.checksum and new.checksum == other.checksum:
        sig["same_source_checksum"] = True
    sig["days_apart"] = days
    if sig.get("same_source_checksum") and new.txn_type == other.txn_type:
        return "exact", sig
    if (same_contact or same_tax) and same_number and same_amount:
        return "exact", sig
    if (same_contact or same_tax) and same_number:
        return "possible", sig
    if same_contact and same_amount and days <= 7:
        return "possible", sig
    return None, sig


def next_number(prefix: str, padding: int, value: int) -> str:
    return f"{prefix}{value:0{padding}d}"
