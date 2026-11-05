"""Accounting API (Phase 8): configuration, chart of accounts, periods, tax, exchange rates, customers/suppliers, rules,
transactions, journals, approvals and the read-only reporting foundation.

Every route declares its permission (company scope). Authorization is decided here on the server from the signed-in user's
roles: nothing the client sends (company, account, approver) is trusted, and out-of-scope objects answer 404. Amounts are
strings in JSON and Decimal in code; the backend is the only place a financial result is calculated."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints

from artify_api import accounting as A
from artify_api import accounting_reports as R
from artify_api import accounting_txn as T
from artify_api.acct_core import D
from artify_api.context import RequestCtx, Requires

router = APIRouter(tags=["accounting"])
C = "/companies/{company_id}/accounting"


def _dep(perm: str) -> Any:
    return Depends(Requires(perm, company=True))


Read = Annotated[RequestCtx, _dep("accounting.read")]
Configure = Annotated[RequestCtx, _dep("accounting.configure")]
Contacts = Annotated[RequestCtx, _dep("accounting.contacts.manage")]
Prepare = Annotated[RequestCtx, _dep("accounting.prepare")]
Review = Annotated[RequestCtx, _dep("accounting.review")]
Approve = Annotated[RequestCtx, _dep("accounting.approve")]
Reverse = Annotated[RequestCtx, _dep("accounting.reverse")]
Periods = Annotated[RequestCtx, _dep("accounting.periods.manage")]
Reports = Annotated[RequestCtx, _dep("accounting.reports.read")]
Money = Annotated[str, StringConstraints(pattern=r"^-?\d{1,18}(\.\d{1,10})?$")]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
Cur = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{3}$")]


# ------------------------------------------------------------------ configuration
class InitIn(BaseModel):
    base_currency: Cur | None = None
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    accounting_method: Literal["accrual"] = "accrual"  # cash-basis accounting is not implemented yet
    rounding_mode: Literal["half_up", "half_even"] = "half_up"
    tax_rounding_scope: Literal["line", "document"] = "line"
    jurisdiction_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,16}$")


class ConfigPatch(BaseModel):
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    accounting_method: Literal["accrual"] | None = None
    rounding_mode: Literal["half_up", "half_even"] | None = None
    tax_rounding_scope: Literal["line", "document"] | None = None
    tolerance: Money | None = None
    jurisdiction_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,16}$")
    tax_registered: bool | None = None
    tax_registration_number: str | None = Field(default=None, max_length=40)
    require_open_period: bool | None = None
    required_dimensions: list[Literal["project", "cost_center", "department"]] | None = None
    max_rate_age_days: int | None = Field(default=None, ge=0, le=365)
    numbering: dict[str, Any] | None = None
    base_row_version: int | None = None


@router.get(C + "/config", operation_id="accounting_config")
def get_config(company_id: uuid.UUID, ctx: Read) -> Any:
    return A.config_view(ctx)


@router.post(C + "/config", status_code=201, operation_id="accounting_config_init")
def init_config(company_id: uuid.UUID, body: InitIn, ctx: Configure) -> Any:
    """Set up the company's own accounting profile (once). Nothing is copied from another company."""
    return A.init_config(ctx, body.model_dump(exclude_none=True))


@router.patch(C + "/config", operation_id="accounting_config_update")
def update_config(company_id: uuid.UUID, body: ConfigPatch, ctx: Configure) -> Any:
    data = body.model_dump(exclude_unset=True)
    return A.update_config(ctx, data, data.pop("base_row_version", None))


class PolicyIn(BaseModel):
    separate_preparer_approver: bool = True
    levels: Literal[1, 2] = 1
    distinct_levels: bool = True
    approval_limit: Money | None = None


@router.put(C + "/policy", operation_id="accounting_policy_set")
def set_policy(company_id: uuid.UUID, body: PolicyIn, ctx: Configure) -> Any:
    return A.set_policy(ctx, body.model_dump())


class TemplateIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)


@router.post(C + "/templates/chart", operation_id="accounting_apply_chart")
def apply_chart(company_id: uuid.UUID, body: TemplateIn, ctx: Configure) -> Any:
    """Copy a sample chart of accounts into this company (independent copy; only into an empty chart)."""
    return A.apply_chart_template(ctx, body.key)


@router.post(C + "/templates/tax-pack", operation_id="accounting_apply_tax_pack")
def apply_tax_pack(company_id: uuid.UUID, body: TemplateIn, ctx: Configure) -> Any:
    """Copy a starter tax pack (configuration, not advice). It stays UNVERIFIED until a person records a review."""
    return A.apply_tax_pack(ctx, body.key)


# ------------------------------------------------------------------ chart of accounts
class AccountIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    name: str = Field(min_length=1, max_length=160)
    account_type: str
    parent_id: uuid.UUID | None = None
    normal_balance: Literal["debit", "credit"] | None = None
    currency: Cur | None = None
    is_postable: bool = True
    description: str | None = Field(default=None, max_length=500)
    external_code: str | None = Field(default=None, max_length=64)


class AccountPatch(BaseModel):
    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    account_type: str | None = None
    parent_id: uuid.UUID | None = None
    normal_balance: Literal["debit", "credit"] | None = None
    currency: Cur | None = None
    is_postable: bool | None = None
    description: str | None = Field(default=None, max_length=500)
    external_code: str | None = Field(default=None, max_length=64)
    base_row_version: int | None = None


@router.get(C + "/accounts", operation_id="accounting_accounts")
def list_accounts(
    company_id: uuid.UUID, ctx: Read, q: str | None = None, active: bool | None = None, account_type: str | None = None
) -> Any:
    return {"items": A.list_accounts(ctx, q=q, active=active, account_type=account_type)}


@router.post(C + "/accounts", status_code=201, operation_id="accounting_account_create")
def create_account(company_id: uuid.UUID, body: AccountIn, ctx: Configure) -> Any:
    return A.create_account(ctx, body.model_dump())


@router.get(C + "/accounts/{account_id}", operation_id="accounting_account")
def get_account(company_id: uuid.UUID, account_id: uuid.UUID, ctx: Read) -> Any:
    return A.ser(A.get_account(ctx, account_id))


@router.patch(C + "/accounts/{account_id}", operation_id="accounting_account_update")
def update_account(company_id: uuid.UUID, account_id: uuid.UUID, body: AccountPatch, ctx: Configure) -> Any:
    data = body.model_dump(exclude_unset=True)
    return A.update_account(ctx, account_id, data, data.pop("base_row_version", None))


@router.post(C + "/accounts/{account_id}/archive", operation_id="accounting_account_archive")
def archive_account(company_id: uuid.UUID, account_id: uuid.UUID, ctx: Configure) -> Any:
    return A.set_account_active(ctx, account_id, False)


@router.post(C + "/accounts/{account_id}/restore", operation_id="accounting_account_restore")
def restore_account(company_id: uuid.UUID, account_id: uuid.UUID, ctx: Configure) -> Any:
    return A.set_account_active(ctx, account_id, True)


class RoleIn(BaseModel):
    account_id: uuid.UUID | None = None


@router.get(C + "/roles", operation_id="accounting_roles")
def get_roles(company_id: uuid.UUID, ctx: Read) -> Any:
    return {"items": A.get_roles(ctx)}


@router.put(C + "/roles/{role}", operation_id="accounting_role_set")
def set_role(company_id: uuid.UUID, role: str, body: RoleIn, ctx: Configure) -> Any:
    return {"items": A.set_role(ctx, role, body.account_id)}


# ------------------------------------------------------------------ periods
class PeriodIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    start_date: date
    end_date: date
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)


class GenerateIn(BaseModel):
    fiscal_year: int = Field(ge=1900, le=2200)


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get(C + "/periods", operation_id="accounting_periods")
def list_periods(company_id: uuid.UUID, ctx: Read) -> Any:
    return {"items": A.list_periods(ctx)}


@router.post(C + "/periods", status_code=201, operation_id="accounting_period_create")
def create_period(company_id: uuid.UUID, body: PeriodIn, ctx: Periods) -> Any:
    return A.create_period(ctx, body.model_dump())


@router.post(C + "/periods/generate", operation_id="accounting_periods_generate")
def generate_periods(company_id: uuid.UUID, body: GenerateIn, ctx: Periods) -> Any:
    return {"items": A.generate_periods(ctx, body.fiscal_year)}


@router.post(C + "/periods/{period_id}/close", operation_id="accounting_period_close")
def close_period(company_id: uuid.UUID, period_id: uuid.UUID, body: ReasonIn, ctx: Periods) -> Any:
    return A.set_period_status(ctx, period_id, "closed", body.reason)


@router.post(C + "/periods/{period_id}/reopen", operation_id="accounting_period_reopen")
def reopen_period(company_id: uuid.UUID, period_id: uuid.UUID, body: ReasonIn, ctx: Periods) -> Any:
    return A.set_period_status(ctx, period_id, "open", body.reason)


@router.get(C + "/periods/{period_id}/events", operation_id="accounting_period_events")
def period_events(company_id: uuid.UUID, period_id: uuid.UUID, ctx: Read) -> Any:
    return {"items": A.period_events(ctx, period_id)}


# ------------------------------------------------------------------ tax
class TaxCodeIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$")
    name: str = Field(min_length=1, max_length=120)
    treatment: Literal["standard", "zero_rated", "exempt", "out_of_scope", "reverse_charge", "import"]
    direction: Literal["input", "output", "both"] = "both"
    rate: Money | None = None
    effective_from: date | None = None
    input_account_id: uuid.UUID | None = None
    output_account_id: uuid.UUID | None = None
    recoverable: bool = True
    jurisdiction_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,16}$")
    source_reference: str | None = Field(default=None, max_length=400)


class TaxCodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    direction: Literal["input", "output", "both"] | None = None
    input_account_id: uuid.UUID | None = None
    output_account_id: uuid.UUID | None = None
    recoverable: bool | None = None
    is_active: bool | None = None
    jurisdiction_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,16}$")
    source_reference: str | None = Field(default=None, max_length=400)


class TaxRateIn(BaseModel):
    rate: Money
    effective_from: date
    effective_to: date | None = None


class TaxVerifyIn(BaseModel):
    verified_by_name: str = Field(min_length=2, max_length=120)
    verified_on: date
    source_reference: str | None = Field(default=None, max_length=400)


class EndIn(BaseModel):
    effective_to: date


@router.get(C + "/tax-codes", operation_id="accounting_tax_codes")
def list_tax_codes(company_id: uuid.UUID, ctx: Read) -> Any:
    return {"items": A.list_tax_codes(ctx)}


@router.post(C + "/tax-codes", status_code=201, operation_id="accounting_tax_code_create")
def create_tax_code(company_id: uuid.UUID, body: TaxCodeIn, ctx: Configure) -> Any:
    return A.create_tax_code(ctx, body.model_dump())


@router.get(C + "/tax-codes/{tax_code_id}", operation_id="accounting_tax_code")
def get_tax_code(company_id: uuid.UUID, tax_code_id: uuid.UUID, ctx: Read) -> Any:
    return A.ser(A.get_tax_code(ctx, tax_code_id))


@router.patch(C + "/tax-codes/{tax_code_id}", operation_id="accounting_tax_code_update")
def update_tax_code(company_id: uuid.UUID, tax_code_id: uuid.UUID, body: TaxCodePatch, ctx: Configure) -> Any:
    return A.update_tax_code(ctx, tax_code_id, body.model_dump(exclude_unset=True))


@router.post(C + "/tax-codes/{tax_code_id}/rates", operation_id="accounting_tax_rate_add")
def add_tax_rate(company_id: uuid.UUID, tax_code_id: uuid.UUID, body: TaxRateIn, ctx: Configure) -> Any:
    return A.add_tax_rate(ctx, tax_code_id, D(body.rate), body.effective_from, body.effective_to)


@router.post(C + "/tax-codes/{tax_code_id}/rates/{rate_id}/end", operation_id="accounting_tax_rate_end")
def end_tax_rate(company_id: uuid.UUID, tax_code_id: uuid.UUID, rate_id: uuid.UUID, body: EndIn, ctx: Configure) -> Any:
    return A.end_tax_rate(ctx, tax_code_id, rate_id, body.effective_to)


@router.post(C + "/tax-codes/{tax_code_id}/verify", operation_id="accounting_tax_code_verify")
def verify_tax_code(company_id: uuid.UUID, tax_code_id: uuid.UUID, body: TaxVerifyIn, ctx: Configure) -> Any:
    """Record that a qualified person reviewed this tax code. Artify never marks a code verified by itself."""
    return A.verify_tax_code(ctx, tax_code_id, body.verified_by_name, body.verified_on, body.source_reference)


# ------------------------------------------------------------------ exchange rates
class RateIn(BaseModel):
    currency: Cur
    rate: Money
    rate_date: date
    source: str = Field(min_length=1, max_length=80)


@router.get(C + "/exchange-rates", operation_id="accounting_rates")
def list_rates(company_id: uuid.UUID, ctx: Read, currency: Cur | None = None) -> Any:
    return {"items": A.list_rates(ctx, currency)}


@router.post(C + "/exchange-rates", status_code=201, operation_id="accounting_rate_add")
def add_rate(company_id: uuid.UUID, body: RateIn, ctx: Configure) -> Any:
    """Rates are entered (or loaded from a configured source) and never overwritten or invented."""
    return A.add_rate(ctx, body.currency, D(body.rate), body.rate_date, body.source)


# ------------------------------------------------------------------ customers and suppliers
class ContactIn(BaseModel):
    kind: Literal["customer", "supplier"]
    legal_name: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    tax_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    currency: Cur | None = None
    receivable_account_id: uuid.UUID | None = None
    payable_account_id: uuid.UUID | None = None
    default_tax_code_id: uuid.UUID | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=3650)
    external_ref: str | None = Field(default=None, max_length=80)


class ContactPatch(BaseModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    tax_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    currency: Cur | None = None
    receivable_account_id: uuid.UUID | None = None
    payable_account_id: uuid.UUID | None = None
    default_tax_code_id: uuid.UUID | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=3650)
    is_active: bool | None = None
    external_ref: str | None = Field(default=None, max_length=80)
    base_row_version: int | None = None


@router.get(C + "/contacts", operation_id="accounting_contacts")
def list_contacts(
    company_id: uuid.UUID,
    ctx: Read,
    kind: Literal["customer", "supplier"] | None = None,
    q: str | None = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return A.list_contacts(ctx, kind=kind, q=q, active=active, limit=limit, offset=offset)


@router.get(C + "/contacts/match", operation_id="accounting_contacts_match")
def match_contacts(
    company_id: uuid.UUID,
    ctx: Read,
    name: str | None = Query(default=None, max_length=200),
    tax_number: str | None = Query(default=None, max_length=40),
    kind: Literal["customer", "supplier"] | None = None,
) -> Any:
    """Suggested existing contacts for a name / tax number. Suggestions only; nothing is linked or merged."""
    return {"items": A.match_contact_candidates(ctx, name, tax_number, kind)}


@router.post(C + "/contacts", status_code=201, operation_id="accounting_contact_create")
def create_contact(company_id: uuid.UUID, body: ContactIn, ctx: Contacts) -> Any:
    return A.create_contact(ctx, body.model_dump())


@router.get(C + "/contacts/{contact_id}", operation_id="accounting_contact")
def get_contact(company_id: uuid.UUID, contact_id: uuid.UUID, ctx: Read) -> Any:
    return A.ser(A.get_contact(ctx, contact_id))


@router.patch(C + "/contacts/{contact_id}", operation_id="accounting_contact_update")
def update_contact(company_id: uuid.UUID, contact_id: uuid.UUID, body: ContactPatch, ctx: Contacts) -> Any:
    data = body.model_dump(exclude_unset=True)
    return A.update_contact(ctx, contact_id, data, data.pop("base_row_version", None))


# ------------------------------------------------------------------ rules
class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["account", "tax", "narration", "dimension"]
    priority: int = Field(default=0, ge=-1000, le=1000)
    conditions: list[dict[str, Any]] = Field(min_length=1, max_length=10)
    action: dict[str, Any]
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool = True


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    priority: int | None = Field(default=None, ge=-1000, le=1000)
    conditions: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=10)
    action: dict[str, Any] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None


@router.get(C + "/rules", operation_id="accounting_rules")
def list_rules(company_id: uuid.UUID, ctx: Read, include_history: bool = False) -> Any:
    return {"items": A.list_rules(ctx, include_history=include_history)}


@router.post(C + "/rules", status_code=201, operation_id="accounting_rule_create")
def create_rule(company_id: uuid.UUID, body: RuleIn, ctx: Configure) -> Any:
    """Rules are data (fixed fields, operators and actions), versioned and audited. There is no scripting."""
    return A.create_rule(ctx, body.model_dump())


@router.patch(C + "/rules/{rule_id}", operation_id="accounting_rule_update")
def update_rule(company_id: uuid.UUID, rule_id: uuid.UUID, body: RulePatch, ctx: Configure) -> Any:
    return A.update_rule(ctx, rule_id, body.model_dump(exclude_unset=True))


@router.get(C + "/rules/{rule_key}/history", operation_id="accounting_rule_history")
def rule_history(company_id: uuid.UUID, rule_key: uuid.UUID, ctx: Read) -> Any:
    return {"items": A.rule_history(ctx, rule_key)}


# ------------------------------------------------------------------ transactions
class LineIn(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    amount: Money | None = None
    quantity: Money | None = None
    unit_price: Money | None = None
    discount: Money | None = None
    tax_amount: Money | None = None
    tax_code_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    entry_side: Literal["debit", "credit"] | None = None
    dimensions: dict[str, str] | None = None


class DraftIn(BaseModel):
    txn_type: str
    transaction_date: date
    due_date: date | None = None
    doc_number: str | None = Field(default=None, max_length=80)
    contact_id: uuid.UUID | None = None
    currency: Cur | None = None
    exchange_rate: Money | None = None
    prices_include_tax: bool = False
    narration: str | None = Field(default=None, max_length=500)
    settlement_account_id: uuid.UUID | None = None
    counter_account_id: uuid.UUID | None = None
    lines: list[LineIn] = Field(default_factory=list, max_length=200)


class HeaderPatch(BaseModel):
    txn_type: str | None = None
    transaction_date: date | None = None
    due_date: date | None = None
    doc_number: str | None = Field(default=None, max_length=80)
    currency: Cur | None = None
    exchange_rate: Money | None = None
    rate_source: str | None = Field(default=None, max_length=80)
    rate_date: date | None = None
    prices_include_tax: bool | None = None
    narration: str | None = Field(default=None, max_length=500)
    settlement_account_id: uuid.UUID | None = None
    counter_account_id: uuid.UUID | None = None
    base_row_version: int | None = None


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(max_length=200)


class ContactSetIn(BaseModel):
    contact_id: uuid.UUID | None = None


class JLineIn(BaseModel):
    account_id: uuid.UUID
    debit: Money | None = None
    credit: Money | None = None
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=120)
    tax_code_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    dimensions: dict[str, str] | None = None


class JLinesIn(BaseModel):
    lines: list[JLineIn] = Field(min_length=1, max_length=200)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ReasonRequired(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ApproveIn(BaseModel):
    acknowledge: bool = False
    note: str | None = Field(default=None, max_length=500)


class ReverseIn(BaseModel):
    reversal_date: date
    reason: str = Field(min_length=3, max_length=500)


class DupDecisionIn(BaseModel):
    decision: Literal["not_duplicate", "duplicate"]
    note: str | None = Field(default=None, max_length=500)


def _lines(lines: list[LineIn]) -> list[dict[str, Any]]:
    return [ln.model_dump(exclude_none=True) for ln in lines]


@router.get(C + "/transactions", operation_id="accounting_transactions")
def list_transactions(
    company_id: uuid.UUID,
    ctx: Read,
    status: str | None = None,
    txn_type: str | None = None,
    contact_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return T.list_transactions(
        ctx,
        status=status,
        txn_type=txn_type,
        contact_id=contact_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.post(C + "/transactions", status_code=201, operation_id="accounting_transaction_create")
def create_transaction(company_id: uuid.UUID, body: DraftIn, ctx: Prepare) -> Any:
    """A hand-made draft. Validation runs immediately and is stored with the draft."""
    data = body.model_dump(exclude={"lines"}, exclude_none=True)
    data["lines"] = _lines(body.lines)
    return T.create_draft(ctx, data)


@router.post(
    C + "/transactions/from-extraction/{extraction_id}",
    status_code=201,
    operation_id="accounting_transaction_from_extraction",
)
def from_extraction(company_id: uuid.UUID, extraction_id: uuid.UUID, ctx: Prepare) -> Any:
    """Draft(s) from an extraction an accountant has APPROVED. Bank statements create one draft per confirmed row."""
    return T.create_from_extraction(ctx, extraction_id)


@router.get(C + "/transactions/{transaction_id}", operation_id="accounting_transaction")
def get_transaction(company_id: uuid.UUID, transaction_id: uuid.UUID, ctx: Read) -> Any:
    return T.view(ctx, transaction_id)


@router.patch(C + "/transactions/{transaction_id}", operation_id="accounting_transaction_update")
def update_transaction(company_id: uuid.UUID, transaction_id: uuid.UUID, body: HeaderPatch, ctx: Prepare) -> Any:
    data = body.model_dump(exclude_unset=True)
    return T.update_header(ctx, transaction_id, data, data.pop("base_row_version", None))


@router.put(C + "/transactions/{transaction_id}/lines", operation_id="accounting_transaction_lines")
def put_lines(company_id: uuid.UUID, transaction_id: uuid.UUID, body: LinesIn, ctx: Prepare) -> Any:
    return T.replace_lines(ctx, transaction_id, _lines(body.lines))


@router.put(C + "/transactions/{transaction_id}/contact", operation_id="accounting_transaction_contact")
def put_contact(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ContactSetIn, ctx: Prepare) -> Any:
    """Confirm (or clear) the customer/supplier. Suggested matches are never applied automatically."""
    return T.set_contact(ctx, transaction_id, body.contact_id)


@router.put(C + "/transactions/{transaction_id}/journal-lines", operation_id="accounting_transaction_journal_lines")
def put_journal_lines(company_id: uuid.UUID, transaction_id: uuid.UUID, body: JLinesIn, ctx: Prepare) -> Any:
    """Edit the proposed journal by hand. The backend still validates balance, accounts, tax, period and currency."""
    return T.replace_journal_lines(ctx, transaction_id, [ln.model_dump(exclude_none=True) for ln in body.lines])


@router.post(C + "/transactions/{transaction_id}/regenerate", operation_id="accounting_transaction_regenerate")
def regenerate(company_id: uuid.UUID, transaction_id: uuid.UUID, ctx: Prepare) -> Any:
    return T.regenerate_journal(ctx, transaction_id)


@router.post(C + "/transactions/{transaction_id}/validate", operation_id="accounting_transaction_validate")
def validate(company_id: uuid.UUID, transaction_id: uuid.UUID, ctx: Read) -> Any:
    return T.validate(ctx, transaction_id)


@router.post(C + "/transactions/{transaction_id}/submit", operation_id="accounting_transaction_submit")
def submit(company_id: uuid.UUID, transaction_id: uuid.UUID, body: NoteIn, ctx: Prepare) -> Any:
    return T.submit(ctx, transaction_id, body.note)


@router.post(C + "/transactions/{transaction_id}/review-approve", operation_id="accounting_transaction_review")
def review_approve(company_id: uuid.UUID, transaction_id: uuid.UUID, body: NoteIn, ctx: Review) -> Any:
    return T.review_approve(ctx, transaction_id, body.note)


@router.post(C + "/transactions/{transaction_id}/approve", operation_id="accounting_transaction_approve")
def approve(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ApproveIn, ctx: Approve) -> Any:
    return T.approve(ctx, transaction_id, acknowledge=body.acknowledge, note=body.note)


@router.post(C + "/transactions/{transaction_id}/reject", operation_id="accounting_transaction_reject")
def reject(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ReasonRequired, ctx: Read) -> Any:
    return T.reject(ctx, transaction_id, body.reason)


@router.post(
    C + "/transactions/{transaction_id}/request-correction", operation_id="accounting_transaction_request_correction"
)
def request_correction(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ReasonRequired, ctx: Read) -> Any:
    return T.request_correction(ctx, transaction_id, body.reason)


@router.post(C + "/transactions/{transaction_id}/revise", operation_id="accounting_transaction_revise")
def revise(company_id: uuid.UUID, transaction_id: uuid.UUID, body: NoteIn, ctx: Prepare) -> Any:
    return T.revise(ctx, transaction_id, body.note)


@router.post(C + "/transactions/{transaction_id}/reverse", operation_id="accounting_transaction_reverse")
def reverse(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ReverseIn, ctx: Reverse) -> Any:
    return T.reverse(ctx, transaction_id, reversal_date=body.reversal_date, reason=body.reason)


@router.post(C + "/transactions/{transaction_id}/amend", operation_id="accounting_transaction_amend")
def amend(company_id: uuid.UUID, transaction_id: uuid.UUID, body: ReverseIn, ctx: Reverse) -> Any:
    """Reverse the approved transaction and open a corrected draft copy."""
    return T.amend(ctx, transaction_id, reversal_date=body.reversal_date, reason=body.reason)


@router.post(C + "/transactions/{transaction_id}/duplicates/{match_id}", operation_id="accounting_duplicate_decide")
def decide_duplicate(
    company_id: uuid.UUID, transaction_id: uuid.UUID, match_id: uuid.UUID, body: DupDecisionIn, ctx: Prepare
) -> Any:
    return T.decide_duplicate(ctx, transaction_id, match_id, body.decision, body.note)


# ------------------------------------------------------------------ journals
class JournalDraftIn(BaseModel):
    txn_type: Literal[
        "journal_entry", "opening_balance", "adjustment_entry", "accrual", "prepayment", "depreciation"
    ] = "journal_entry"
    journal_date: date
    narration: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=80)
    currency: Cur | None = None
    exchange_rate: Money | None = None
    lines: list[JLineIn] = Field(min_length=2, max_length=200)


@router.get(C + "/journals", operation_id="accounting_journals")
def list_journals(
    company_id: uuid.UUID,
    ctx: Read,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return T.list_journals(ctx, status=status, date_from=date_from, date_to=date_to, limit=limit, offset=offset)


@router.post(C + "/journals", status_code=201, operation_id="accounting_journal_create")
def create_journal(company_id: uuid.UUID, body: JournalDraftIn, ctx: Prepare) -> Any:
    """A manual journal draft (a journal-type transaction). It follows the same review and approval workflow."""
    data = body.model_dump(exclude_none=True)
    data["lines"] = [ln.model_dump(exclude_none=True) for ln in body.lines]
    return T.create_journal_draft(ctx, data)


@router.get(C + "/journals/{journal_id}", operation_id="accounting_journal")
def get_journal(company_id: uuid.UUID, journal_id: uuid.UUID, ctx: Read) -> Any:
    return T.get_journal(ctx, journal_id)


@router.post(C + "/journals/{journal_id}/validate", operation_id="accounting_journal_validate")
def validate_journal(company_id: uuid.UUID, journal_id: uuid.UUID, ctx: Read) -> Any:
    """Re-run the balance and posting checks for the journal's transaction and return the result."""
    j = T.get_journal(ctx, journal_id)
    return T.validate(ctx, uuid.UUID(j["transaction"]["id"]))


# ------------------------------------------------------------------ reports (read-only foundation)
@router.get(C + "/reports/trial-balance", operation_id="accounting_report_trial_balance")
def trial_balance(company_id: uuid.UUID, ctx: Reports, as_of: date | None = None) -> Any:
    return R.trial_balance(ctx, as_of)


@router.get(C + "/reports/account-balances", operation_id="accounting_report_account_balances")
def account_balances(company_id: uuid.UUID, ctx: Reports, as_of: date | None = None) -> Any:
    return R.account_balances(ctx, as_of)


@router.get(C + "/reports/ledger/{account_id}", operation_id="accounting_report_ledger")
def ledger(
    company_id: uuid.UUID,
    account_id: uuid.UUID,
    ctx: Reports,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Any:
    return R.ledger(ctx, account_id, date_from, date_to, limit, offset)


@router.get(C + "/reports/balances/{kind}", operation_id="accounting_report_contact_balances")
def contact_balances(
    company_id: uuid.UUID, kind: Literal["customer", "supplier"], ctx: Reports, as_of: date | None = None
) -> Any:
    return R.contact_balances(ctx, kind, as_of)


@router.get(C + "/reports/summary/{kind}", operation_id="accounting_report_summary")
def summary(
    company_id: uuid.UUID,
    kind: Literal["sales", "purchases", "expenses"],
    ctx: Reports,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Any:
    return R.summary(ctx, kind, date_from, date_to)


@router.get(C + "/reports/tax-summary", operation_id="accounting_report_tax_summary")
def tax_summary(company_id: uuid.UUID, ctx: Reports, date_from: date | None = None, date_to: date | None = None) -> Any:
    return R.tax_summary(ctx, date_from, date_to)
