"""Governed datasets (Phase 9): the ONLY way Excel mappings and reports read accounting data.

Each dataset is a fixed, parameterised query over the Phase 8 tables, scoped to one company and to APPROVED data (journals
whose transaction a person approved; transactions approved or posted). Filters are validated against the dataset's
declaration and bound as parameters; customers never write SQL, expressions or column names. Rows come back as plain
Python values (Decimal, date, text) and are shared by the report screens, the export preview and the generated workbook,
so what you preview is what is written.

Reversed transactions keep both their original and their mirror journal in ledger datasets (balances net to zero); register
datasets list live transactions only (approved/posted) and exclude reversal entries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from artify_api import acct_core as core
from artify_api import xl_catalogue as cat
from artify_api.errors import Unprocessable

LEDGER = "('approved', 'posted', 'reversed')"
LIVE = ("approved", "posted")
UNAPPROVED = ("draft", "awaiting_review", "awaiting_approval")
ASSET_TYPES = ("asset", "current_asset", "non_current_asset", "bank", "cash", "receivable")
LIABILITY_TYPES = ("liability", "current_liability", "long_term_liability", "payable")
P_AND_L = {
    "revenue": "1 Revenue",
    "other_income": "2 Other income",
    "cost_of_sales": "3 Cost of sales",
    "expense": "4 Operating expenses",
    "other_expense": "5 Other expenses",
}
INPUT_TYPES = tuple(core.PURCHASE_TYPES | core.EXPENSE_TYPES | {"bank_withdrawal", "bank_charges"})
MAX_IDS = 5000


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    as_of: date | None = None
    txn_types: list[str] = field(default_factory=list)
    contact_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    project: str | None = None
    cost_center: str | None = None
    department: str | None = None
    transaction_ids: list[uuid.UUID] = field(default_factory=list)
    include_unapproved: bool = False
    group_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if v in (None, [], False, ""):
                continue
            out[k] = (
                [str(x) for x in v]
                if isinstance(v, list)
                else v.isoformat()
                if isinstance(v, date)
                else str(v)
                if isinstance(v, uuid.UUID)
                else v
            )
        return out


@dataclass
class DatasetResult:
    key: str
    rows: list[dict[str, Any]]
    meta: dict[str, Any]
    truncated: bool = False


def _date(v: Any, name: str) -> date | None:
    if v in (None, ""):
        return None
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    except ValueError:
        raise Unprocessable(f"“{name}” is not a valid date (use YYYY-MM-DD).") from None


def _uuid(v: Any, name: str) -> uuid.UUID | None:
    if v in (None, ""):
        return None
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except ValueError:
        raise Unprocessable(f"“{name}” is not a valid identifier.") from None


def parse_filters(dataset_key: str, params: dict[str, Any]) -> Filters:
    """Validate request parameters against the dataset's declared filters. Unknown filters are refused, not ignored."""
    ds = cat.DATASETS.get(dataset_key)
    if ds is None:
        raise Unprocessable(f"Unknown data source “{dataset_key}”.")
    unknown = [
        k
        for k, v in params.items()
        if v not in (None, "", [], False) and k not in ds.filters and k not in ("period_id",)
    ]
    if unknown:
        raise Unprocessable(f"The filter “{unknown[0]}” does not apply to “{ds.label}”.")
    f = Filters(
        date_from=_date(params.get("date_from"), "date_from"),
        date_to=_date(params.get("date_to"), "date_to"),
        as_of=_date(params.get("as_of"), "as_of"),
        contact_id=_uuid(params.get("contact_id"), "contact_id"),
        account_id=_uuid(params.get("account_id"), "account_id"),
        project=(params.get("project") or None),
        cost_center=(params.get("cost_center") or None),
        department=(params.get("department") or None),
        include_unapproved=bool(params.get("include_unapproved")),
        group_by=params.get("group_by") or None,
    )
    if f.date_from and f.date_to and f.date_to < f.date_from:
        raise Unprocessable("The end date is before the start date.")
    types = params.get("txn_types") or []
    if not isinstance(types, list) or any(t not in core.TXN_TYPES for t in types):
        raise Unprocessable("Unknown transaction type in the filter.")
    f.txn_types = list(types)
    tids = params.get("transaction_ids") or []
    if not isinstance(tids, list) or len(tids) > MAX_IDS:
        raise Unprocessable(f"At most {MAX_IDS} transactions can be selected.")
    f.transaction_ids = [u for u in (_uuid(t, "transaction_ids") for t in tids) if u]
    for dim in ("project", "cost_center", "department"):
        v = getattr(f, dim)
        if v is not None and (not isinstance(v, str) or len(v) > 100):
            raise Unprocessable(f"“{dim}” is too long.")
    if ds.extras.get("requires") == "account_id" and f.account_id is None:
        raise Unprocessable("This report needs an account.")
    if dataset_key == "dimension_summary" and f.group_by not in ("project", "cost_center", "department"):
        raise Unprocessable("Choose project, cost_center or department to group by.")
    return f


def _company(conn: Connection, company_id: uuid.UUID) -> dict[str, Any]:
    r = conn.execute(
        text(
            "SELECT c.name, c.code, a.base_currency, a.amount_scale FROM companies c JOIN acct_configurations a ON a.company_id = c.id WHERE c.id = :c"
        ),
        {"c": company_id},
    ).first()
    if r is None:
        raise Unprocessable("Accounting has not been set up for this company.")
    return {
        "company_name": r.name,
        "company_code": r.code,
        "base_currency": r.base_currency,
        "scale": int(r.amount_scale),
    }


def _fetch(conn: Connection, sql: str, params: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    res = conn.execute(text(sql).execution_options(yield_per=2000), params)
    for r in res:
        if len(rows) >= limit:
            res.close()
            return rows, True
        d = dict(r._mapping)
        for k, v in list(d.items()):
            if isinstance(v, uuid.UUID):
                d[k] = str(v)
        rows.append(d)
    return rows, False


def _dim_sql(f: Filters, alias: str, params: dict[str, Any]) -> str:
    out = ""
    for dim in ("project", "cost_center", "department"):
        v = getattr(f, dim)
        if v:
            out += f" AND {alias}.dimensions ->> '{dim}' = :d_{dim}"
            params[f"d_{dim}"] = v
    return out


def _txn_where(f: Filters, ds: cat.Dataset, params: dict[str, Any]) -> str:
    statuses = list(LIVE) + (list(UNAPPROVED) if f.include_unapproved else [])
    params["statuses"] = statuses
    where = " t.company_id = :c AND t.status = ANY(:statuses)"
    types = f.txn_types or (ds.extras["types"].split(",") if "types" in ds.extras else [])
    if types:
        where += " AND t.txn_type = ANY(:types)"
        params["types"] = types
    else:
        where += " AND t.txn_type <> 'reversal_entry'"
    if f.date_from:
        where += " AND t.transaction_date >= :df"
        params["df"] = f.date_from
    if f.date_to:
        where += " AND t.transaction_date <= :dt"
        params["dt"] = f.date_to
    if f.contact_id:
        where += " AND t.contact_id = :ct"
        params["ct"] = f.contact_id
    if f.transaction_ids:
        where += " AND t.id = ANY(:tids)"
        params["tids"] = f.transaction_ids
    if f.account_id:
        where += " AND EXISTS (SELECT 1 FROM acct_journals xj JOIN acct_journal_lines xl ON xl.journal_id = xj.id WHERE xj.transaction_id = t.id AND xl.account_id = :acc)"
        params["acc"] = f.account_id
    for dim in ("project", "cost_center", "department"):
        v = getattr(f, dim)
        if v:
            where += f" AND EXISTS (SELECT 1 FROM acct_transaction_lines dl WHERE dl.transaction_id = t.id AND dl.dimensions ->> '{dim}' = :d_{dim})"
            params[f"d_{dim}"] = v
    return where


def _txn_cols() -> str:
    dims = ", ".join(
        f"(SELECT min(dl.dimensions ->> '{d}') FROM acct_transaction_lines dl WHERE dl.transaction_id = t.id) AS {d}"
        for d in ("project", "cost_center", "department")
    )
    return (
        "t.id AS transaction_id, t.number, t.txn_type, t.status, t.transaction_date, t.due_date, t.doc_number, t.doc_number AS reference, d.display_name AS source_document,"
        " c.display_name AS contact_name, c.external_ref AS contact_code, c.tax_number AS contact_tax_number, c.address AS contact_address, t.currency, t.exchange_rate,"
        " t.net_amount, t.tax_amount, t.gross_amount, t.base_gross_amount, t.narration, j.number AS journal_number, j.journal_date, j.id AS journal_id,"
        " (SELECT string_agg(DISTINCT tc.code, ', ' ORDER BY tc.code) FROM acct_transaction_lines l JOIN acct_tax_codes tc ON tc.id = l.tax_code_id WHERE l.transaction_id = t.id) AS tax_codes, "
        + dims
    )


_TXN_FROM = " FROM acct_transactions t LEFT JOIN acct_contacts c ON c.id = t.contact_id LEFT JOIN documents d ON d.id = t.source_document_id LEFT JOIN acct_journals j ON j.transaction_id = t.id"


def _label(txn_type: str) -> str:
    return txn_type.replace("_", " ").capitalize()


def _finish_txn_rows(rows: list[dict[str, Any]], base: str) -> None:
    for r in rows:
        r["type_label"] = _label(r["txn_type"])
        r["foreign_amount"] = r["gross_amount"] if r["currency"] != base else None


def _journal_where(f: Filters, params: dict[str, Any]) -> str:
    where = f" j.company_id = :c AND j.status IN {LEDGER}"
    if f.date_from:
        where += " AND j.journal_date >= :df"
        params["df"] = f.date_from
    if f.date_to:
        where += " AND j.journal_date <= :dt"
        params["dt"] = f.date_to
    if f.as_of:
        where += " AND j.journal_date <= :asof"
        params["asof"] = f.as_of
    if f.account_id:
        where += " AND l.account_id = :acc"
        params["acc"] = f.account_id
    if f.contact_id:
        where += " AND l.contact_id = :ct"
        params["ct"] = f.contact_id
    if f.txn_types:
        where += " AND t.txn_type = ANY(:types)"
        params["types"] = f.txn_types
    if f.transaction_ids:
        where += " AND t.id = ANY(:tids)"
        params["tids"] = f.transaction_ids
    where += _dim_sql(f, "l", params)
    return where


_JL_FROM = (
    " FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id JOIN acct_transactions t ON t.id = j.transaction_id JOIN acct_accounts a ON a.id = l.account_id"
    " LEFT JOIN acct_contacts c ON c.id = l.contact_id LEFT JOIN acct_tax_codes tc ON tc.id = l.tax_code_id"
)
_JL_COLS = (
    "t.id AS transaction_id, j.id AS journal_id, j.number AS journal_number, j.journal_date, t.number AS transaction_number, t.txn_type, t.doc_number, a.code AS account_code, a.name AS account_name, a.account_type,"
    " l.debit, l.credit, l.base_debit, l.base_credit, l.currency, l.description, l.reference, j.narration, c.display_name AS contact_name, c.external_ref AS contact_code,"
    " c.tax_number AS contact_tax_number, tc.code AS tax_code, l.dimensions ->> 'project' AS project, l.dimensions ->> 'cost_center' AS cost_center,"
    " l.dimensions ->> 'department' AS department, l.line_no"
)


def _with_running(rows: list[dict[str, Any]], key: str, opening: dict[str, Decimal], sign: int = 1) -> None:
    run: dict[str, Decimal] = dict(opening)
    for r in rows:
        k = str(r[key]) if r.get(key) is not None else ""
        run[k] = run.get(k, core.ZERO) + sign * (r["base_debit"] - r["base_credit"])
        r["running_balance"] = run[k]


def _opening(
    conn: Connection, company: uuid.UUID, f: Filters, group_col: str, extra_where: str, params_in: dict[str, Any]
) -> dict[str, Decimal]:
    if not f.date_from:
        return {}
    params = {**params_in, "c": company, "df": f.date_from}
    sql = (
        f"SELECT {group_col} AS k, sum(l.base_debit - l.base_credit) AS bal FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id JOIN acct_accounts a ON a.id = l.account_id"
        f" LEFT JOIN acct_contacts c ON c.id = l.contact_id WHERE j.company_id = :c AND j.status IN {LEDGER} AND j.journal_date < :df {extra_where} GROUP BY 1"
    )
    return {str(r.k): r.bal for r in conn.execute(text(sql), params)}


def fetch(conn: Connection, company_id: uuid.UUID, dataset_key: str, f: Filters, *, limit: int) -> DatasetResult:
    """Run one governed dataset. `limit` caps rows (the caller reports truncation instead of silently dropping data)."""
    ds = cat.DATASETS[dataset_key]
    co = _company(conn, company_id)
    base = co["base_currency"]
    p: dict[str, Any] = {"c": company_id}
    meta: dict[str, Any] = {
        **co,
        "report_title": ds.label,
        "period_start": f.date_from,
        "period_end": f.date_to or f.as_of,
        "dataset": dataset_key,
    }
    rows: list[dict[str, Any]] = []
    trunc = False

    if dataset_key in (
        "transactions",
        "sales_register",
        "purchase_register",
        "expense_register",
        "receipts_register",
        "payments_register",
    ):
        sql = f"SELECT {_txn_cols()}{_TXN_FROM} WHERE {_txn_where(f, ds, p)} ORDER BY t.transaction_date, t.number"
        rows, trunc = _fetch(conn, sql, p, limit)
        _finish_txn_rows(rows, base)
    elif dataset_key in ("transaction_lines", "vat_transactions"):
        where = _txn_where(f, ds, p)
        if dataset_key == "vat_transactions":
            where += " AND l.tax_code_id IS NOT NULL"
        sql = (
            f"SELECT {_txn_cols()}, l.line_no, l.description, a.code AS account_code, a.name AS account_name, ltc.code AS tax_code, (l.calc ->> 'tax_rate')::numeric AS tax_rate,"
            " l.calc ->> 'treatment' AS tax_treatment, l.net_amount AS net_amount_line, l.tax_amount AS tax_amount_line, l.gross_amount AS gross_amount_line,"
            " l.dimensions ->> 'project' AS line_project, l.dimensions ->> 'cost_center' AS line_cost_center, l.dimensions ->> 'department' AS line_department"
            f"{_TXN_FROM} JOIN acct_transaction_lines l ON l.transaction_id = t.id LEFT JOIN acct_accounts a ON a.id = l.account_id LEFT JOIN acct_tax_codes ltc ON ltc.id = l.tax_code_id"
            f" WHERE {where} ORDER BY t.transaction_date, t.number, l.line_no"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
        _finish_txn_rows(rows, base)
        for r in rows:  # line amounts replace the header amounts in line-grain datasets
            r["net_amount"], r["tax_amount"], r["gross_amount"] = (
                r.pop("net_amount_line"),
                r.pop("tax_amount_line"),
                r.pop("gross_amount_line"),
            )
            r["project"], r["cost_center"], r["department"] = (
                r.pop("line_project"),
                r.pop("line_cost_center"),
                r.pop("line_department"),
            )
            r["foreign_amount"] = r["gross_amount"] if r["currency"] != base else None
            if dataset_key == "vat_transactions":
                r["direction"] = "Input" if r["txn_type"] in INPUT_TYPES else "Output"
    elif dataset_key in (
        "journal_lines",
        "bank_transactions",
        "general_ledger",
        "account_ledger",
        "customer_ledger",
        "supplier_ledger",
    ):
        where = _journal_where(f, p)
        kind_where = ""
        if dataset_key == "bank_transactions":
            kind_where = " AND a.account_type IN ('bank', 'cash')"
        elif dataset_key == "customer_ledger":
            kind_where = " AND a.account_type = 'receivable' AND l.contact_id IS NOT NULL"
        elif dataset_key == "supplier_ledger":
            kind_where = " AND a.account_type = 'payable' AND l.contact_id IS NOT NULL"
        sql = f"SELECT {_JL_COLS}{_JL_FROM} WHERE {where}{kind_where} ORDER BY j.journal_date, j.number, l.line_no"
        rows, trunc = _fetch(conn, sql, p, limit)
        if dataset_key in (
            "bank_transactions",
            "general_ledger",
            "account_ledger",
            "customer_ledger",
            "supplier_ledger",
        ):
            by_contact = dataset_key in ("customer_ledger", "supplier_ledger")
            key = "contact_name" if by_contact else "account_code"
            op: dict[str, Decimal] = {}
            if f.date_from:
                op_params: dict[str, Any] = {}
                extra = kind_where
                if f.account_id:
                    extra += " AND l.account_id = :acc"
                    op_params["acc"] = f.account_id
                if f.contact_id:
                    extra += " AND l.contact_id = :ct"
                    op_params["ct"] = f.contact_id
                op = _opening(conn, company_id, f, "c.display_name" if by_contact else "a.code", extra, op_params)
            sign = -1 if dataset_key == "supplier_ledger" else 1
            _with_running(rows, key, {k: sign * v for k, v in op.items()}, sign)
            if dataset_key in ("account_ledger", "bank_transactions") and f.account_id:
                opening = next(iter(op.values()), core.ZERO)
                meta["opening_balance"] = opening
                meta["closing_balance"] = opening + sum((r["base_debit"] - r["base_credit"] for r in rows), core.ZERO)
        meta["total_debit"] = sum((r["base_debit"] for r in rows), core.ZERO)
        meta["total_credit"] = sum((r["base_credit"] for r in rows), core.ZERO)
    elif dataset_key == "journal_listing":
        where = f" j.company_id = :c AND j.status IN {LEDGER}"
        if f.date_from:
            where += " AND j.journal_date >= :df"
            p["df"] = f.date_from
        if f.date_to:
            where += " AND j.journal_date <= :dt"
            p["dt"] = f.date_to
        if f.txn_types:
            where += " AND t.txn_type = ANY(:types)"
            p["types"] = f.txn_types
        sql = (
            "SELECT j.number AS journal_number, j.journal_date, j.journal_type, t.number AS transaction_number, j.narration, j.status, j.base_total_debit AS total_debit,"
            f" j.base_total_credit AS total_credit, j.base_currency AS currency FROM acct_journals j JOIN acct_transactions t ON t.id = j.transaction_id WHERE {where} ORDER BY j.journal_date, j.number"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
    elif dataset_key in ("trial_balance", "account_balances"):
        date_sql = ""
        if f.as_of:
            date_sql = " AND j.journal_date <= :asof"
            p["asof"] = f.as_of
        sql = (
            "SELECT a.code AS account_code, a.name AS account_name, a.account_type, a.normal_balance, sum(l.base_debit) AS total_debit, sum(l.base_credit) AS total_credit"
            " FROM acct_accounts a JOIN acct_journal_lines l ON l.account_id = a.id JOIN acct_journals j ON j.id = l.journal_id"
            f" WHERE a.company_id = :c AND j.company_id = :c AND j.status IN {LEDGER}{date_sql} GROUP BY a.code, a.name, a.account_type, a.normal_balance ORDER BY a.code"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
        for r in rows:
            net = r["total_debit"] - r["total_credit"]
            r["debit"], r["credit"] = (net, core.ZERO) if net >= 0 else (core.ZERO, -net)
            r["balance"] = net if r["normal_balance"] == "debit" else -net
        meta["total_debit"] = sum((r["debit"] for r in rows), core.ZERO) if dataset_key == "trial_balance" else None
        meta["total_credit"] = sum((r["credit"] for r in rows), core.ZERO) if dataset_key == "trial_balance" else None
    elif dataset_key in ("profit_and_loss", "balance_sheet"):
        date_sql = ""
        if dataset_key == "profit_and_loss":
            if f.date_from:
                date_sql += " AND j.journal_date >= :df"
                p["df"] = f.date_from
            if f.date_to:
                date_sql += " AND j.journal_date <= :dt"
                p["dt"] = f.date_to
        elif f.as_of:
            date_sql = " AND j.journal_date <= :asof"
            p["asof"] = f.as_of
        sql = (
            "SELECT a.code AS account_code, a.name AS account_name, a.account_type, a.normal_balance, sum(l.base_debit - l.base_credit) AS net FROM acct_accounts a"
            " JOIN acct_journal_lines l ON l.account_id = a.id JOIN acct_journals j ON j.id = l.journal_id"
            f" WHERE a.company_id = :c AND j.company_id = :c AND j.status IN {LEDGER}{date_sql} GROUP BY a.code, a.name, a.account_type, a.normal_balance ORDER BY a.code"
        )
        raw, trunc = _fetch(conn, sql, p, limit)
        if dataset_key == "profit_and_loss":
            for r in raw:
                if r["account_type"] in P_AND_L:
                    amount = -r["net"] if r["account_type"] in ("revenue", "other_income") else r["net"]
                    rows.append(
                        {
                            "section": P_AND_L[r["account_type"]],
                            "account_code": r["account_code"],
                            "account_name": r["account_name"],
                            "amount": amount,
                        }
                    )
            rows.sort(key=lambda x: (x["section"], x["account_code"]))
            income = sum((r["amount"] for r in rows if r["section"][0] in "12"), core.ZERO)
            cost = sum((r["amount"] for r in rows if r["section"][0] in "345"), core.ZERO)
            meta["net_profit"] = income - cost
        else:
            assets = liabs = equity = core.ZERO
            result = core.ZERO
            for r in raw:
                t, n = r["account_type"], r["net"]
                if t in P_AND_L:
                    result += (
                        -n
                    )  # revenue (credit) positive, expense (debit) negative: the result not yet closed into equity
                elif t in ASSET_TYPES or (t == "tax" and r["normal_balance"] == "debit"):
                    rows.append(
                        {
                            "section": "1 Assets",
                            "account_code": r["account_code"],
                            "account_name": r["account_name"],
                            "amount": n,
                        }
                    )
                    assets += n
                elif t in LIABILITY_TYPES or t == "tax":
                    rows.append(
                        {
                            "section": "2 Liabilities",
                            "account_code": r["account_code"],
                            "account_name": r["account_name"],
                            "amount": -n,
                        }
                    )
                    liabs += -n
                elif t == "equity":
                    rows.append(
                        {
                            "section": "3 Equity",
                            "account_code": r["account_code"],
                            "account_name": r["account_name"],
                            "amount": -n,
                        }
                    )
                    equity += -n
            if result != 0:
                rows.append(
                    {
                        "section": "3 Equity",
                        "account_code": None,
                        "account_name": "Result for the period (not closed to equity)",
                        "amount": result,
                    }
                )
                equity += result
            rows.sort(key=lambda x: (x["section"], x["account_code"] or "~"))
            meta.update(total_assets=assets, total_liabilities=liabs, total_equity=equity, net_profit=result)
    elif dataset_key in ("receivables_summary", "payables_summary"):
        customer = dataset_key == "receivables_summary"
        sign_sql = "l.base_debit - l.base_credit" if customer else "l.base_credit - l.base_debit"
        date_sql = ""
        if f.as_of:
            date_sql = " AND j.journal_date <= :asof"
            p["asof"] = f.as_of
        p["k"] = "customer" if customer else "supplier"
        sql = (
            f"SELECT c.display_name AS contact_name, c.tax_number AS contact_tax_number, sum({sign_sql}) AS balance FROM acct_journal_lines l JOIN acct_journals j ON j.id = l.journal_id"
            " JOIN acct_accounts a ON a.id = l.account_id JOIN acct_contacts c ON c.id = l.contact_id"
            f" WHERE j.company_id = :c AND c.kind = :k AND a.account_type IN ('receivable', 'payable') AND j.status IN {LEDGER}{date_sql}"
            f" GROUP BY c.display_name, c.tax_number HAVING sum({sign_sql}) <> 0 ORDER BY c.display_name"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
        meta["total_gross"] = sum((r["balance"] for r in rows), core.ZERO)
    elif dataset_key == "vat_summary":
        date_sql = ""
        if f.date_from:
            date_sql += " AND j.journal_date >= :df"
            p["df"] = f.date_from
        if f.date_to:
            date_sql += " AND j.journal_date <= :dt"
            p["dt"] = f.date_to
        roles = {
            r.role: r.account_id
            for r in conn.execute(
                text("SELECT role, account_id FROM acct_role_mappings WHERE company_id = :c"), {"c": company_id}
            )
        }
        p["vi"], p["vo"] = roles.get("vat_input"), roles.get("vat_output")
        sql = (
            "SELECT t.code AS tax_code, t.name AS tax_name, t.treatment AS tax_treatment,"
            " COALESCE(sum(CASE WHEN l.account_id = COALESCE(t.input_account_id, CAST(:vi AS uuid)) THEN l.base_debit - l.base_credit ELSE 0 END), 0) AS input_tax,"
            " COALESCE(sum(CASE WHEN l.account_id = COALESCE(t.output_account_id, CAST(:vo AS uuid)) THEN l.base_credit - l.base_debit ELSE 0 END), 0) AS output_tax"
            " FROM acct_tax_codes t JOIN acct_journal_lines l ON l.tax_code_id = t.id JOIN acct_journals j ON j.id = l.journal_id"
            f" WHERE t.company_id = :c AND j.company_id = :c AND j.status IN {LEDGER}{date_sql} GROUP BY t.code, t.name, t.treatment ORDER BY t.code"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
        for r in rows:
            r["net_tax"] = r["output_tax"] - r["input_tax"]
        meta["total_tax"] = sum((r["net_tax"] for r in rows), core.ZERO)
    elif dataset_key == "bank_summary":
        date_sql = ""
        if f.date_from:
            date_sql += " AND j.journal_date >= :df"
            p["df"] = f.date_from
        upper = f.date_to or f.as_of
        if upper:
            date_sql += " AND j.journal_date <= :up"
            p["up"] = upper
        acct_sql = " AND a.id = :acc" if f.account_id else ""
        if f.account_id:
            p["acc"] = f.account_id
        sql = (
            "SELECT a.code AS account_code, a.name AS account_name, COALESCE(sum(l.base_debit), 0) AS money_in, COALESCE(sum(l.base_credit), 0) AS money_out"
            " FROM acct_accounts a JOIN acct_journal_lines l ON l.account_id = a.id JOIN acct_journals j ON j.id = l.journal_id"
            f" WHERE a.company_id = :c AND a.account_type IN ('bank', 'cash') AND j.company_id = :c AND j.status IN {LEDGER}{date_sql}{acct_sql} GROUP BY a.code, a.name ORDER BY a.code"
        )
        rows, trunc = _fetch(conn, sql, p, limit)
        closing_sql = (
            "SELECT a.code, COALESCE(sum(l.base_debit - l.base_credit), 0) AS bal FROM acct_accounts a JOIN acct_journal_lines l ON l.account_id = a.id JOIN acct_journals j ON j.id = l.journal_id"
            f" WHERE a.company_id = :c AND a.account_type IN ('bank', 'cash') AND j.company_id = :c AND j.status IN {LEDGER}"
            + (" AND j.journal_date <= :up" if upper else "")
            + " GROUP BY a.code"
        )
        closing = {
            r.code: r.bal
            for r in conn.execute(text(closing_sql), {"c": company_id, **({"up": upper} if upper else {})})
        }
        for r in rows:
            r["closing_balance"] = closing.get(r["account_code"], core.ZERO)
    elif dataset_key in ("expense_analysis", "dimension_summary"):
        date_sql = ""
        if f.date_from:
            date_sql += " AND j.journal_date >= :df"
            p["df"] = f.date_from
        if f.date_to:
            date_sql += " AND j.journal_date <= :dt"
            p["dt"] = f.date_to
        date_sql += _dim_sql(f, "l", p)
        types = "('cost_of_sales', 'expense', 'other_expense')"
        if dataset_key == "expense_analysis":
            sql = (
                "SELECT a.code AS account_code, a.name AS account_name, sum(l.base_debit - l.base_credit) AS amount, count(*) AS entries FROM acct_journal_lines l"
                " JOIN acct_journals j ON j.id = l.journal_id JOIN acct_accounts a ON a.id = l.account_id"
                f" WHERE j.company_id = :c AND j.status IN {LEDGER} AND a.account_type IN {types}{date_sql} GROUP BY a.code, a.name ORDER BY a.code"
            )
        else:
            dim = f.group_by
            assert dim in ("project", "cost_center", "department")
            sql = (
                f"SELECT '{dim}' AS dimension, COALESCE(l.dimensions ->> '{dim}', '(none)') AS value, sum(l.base_debit - l.base_credit) AS amount, count(*) AS entries FROM acct_journal_lines l"
                " JOIN acct_journals j ON j.id = l.journal_id JOIN acct_accounts a ON a.id = l.account_id"
                f" WHERE j.company_id = :c AND j.status IN {LEDGER} AND a.account_type IN {types}{date_sql} GROUP BY 2 ORDER BY 2"
            )
        rows, trunc = _fetch(conn, sql, p, limit)
        meta["total_gross"] = sum((r["amount"] for r in rows), core.ZERO)
    else:  # pragma: no cover - the catalogue and this function are kept in step by a test
        raise Unprocessable(f"No query is defined for “{dataset_key}”.")

    meta["record_count"] = len(rows)
    if rows and "gross_amount" in rows[0]:
        meta["total_net"] = sum((r.get("net_amount") or core.ZERO for r in rows), core.ZERO)
        meta["total_tax"] = sum((r.get("tax_amount") or core.ZERO for r in rows), core.ZERO)
        meta["total_gross"] = sum((r.get("gross_amount") or core.ZERO for r in rows), core.ZERO)
    return DatasetResult(dataset_key, rows, meta, trunc)
