"""Accounting configuration service (Phase 8): the company's own accounting profile, chart of accounts, role mappings,
periods, tax codes and rates, exchange rates, customers/suppliers and accounting rules.

Every row belongs to exactly one company. Nothing is shared between companies: applying a template COPIES it, and later
template changes can never reach a company that already used it. Money never appears as a float: amounts leave this
module as strings. Transactions, journals, approvals and reports live in `accounting_txn` / `accounting_reports`."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from artify_api import acct_core as core
from artify_api import acct_templates_data as tpl
from artify_api import audit, entitlements
from artify_api import workspace as ws
from artify_api.context import RequestCtx
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.permissions import SYSTEM_ROLES


# ------------------------------------------------------------------ helpers
def ser[T](value: T) -> T:
    """JSON-safe copy: Decimal -> exact string (never a float), UUID -> string, containers recursively."""
    out: Any = value
    if isinstance(value, Decimal):
        out = format(value, "f")
    elif isinstance(value, uuid.UUID):
        out = str(value)
    elif isinstance(value, dict):
        out = {k: ser(v) for k, v in value.items()}
    elif isinstance(value, list | tuple):
        out = [ser(v) for v in value]
    return out  # type: ignore[no-any-return]


def rows(ctx_or_conn: RequestCtx | Connection, sql: str, **p: Any) -> list[dict[str, Any]]:
    conn = ctx_or_conn.conn if isinstance(ctx_or_conn, RequestCtx) else ctx_or_conn
    return [ws.row_dict(r) for r in conn.execute(text(sql), p)]


def one(ctx_or_conn: RequestCtx | Connection, sql: str, **p: Any) -> dict[str, Any] | None:
    conn = ctx_or_conn.conn if isinstance(ctx_or_conn, RequestCtx) else ctx_or_conn
    r = conn.execute(text(sql), p).first()
    return ws.row_dict(r) if r is not None else None


def must(ctx_or_conn: RequestCtx | Connection, sql: str, **p: Any) -> dict[str, Any]:
    r = one(ctx_or_conn, sql, **p)
    if r is None:
        raise NotFound("Record not found.")
    return r


def user_perms(ctx: RequestCtx) -> set[str]:
    p = ctx.principal
    assert p is not None
    perms = set(SYSTEM_ROLES[p.org_role][2]) if p.org_role else set()
    if ctx.company_id is not None:
        cr = p.company_roles.get(ctx.company_id)
        if cr:
            perms |= SYSTEM_ROLES[cr][2]
    return perms


def audit_event(
    ctx: RequestCtx, action: str, entity_type: str, entity_id: Any, summary: dict[str, Any] | None = None
) -> None:
    audit.write_audit(
        ctx.conn,
        action=action,
        actor_type="user",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=ctx.company_id,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary or {},
        meta=ctx.meta,
    )


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


# ------------------------------------------------------------------ configuration
CONFIG_COLS = (
    "id, base_currency, amount_scale, fiscal_year_start_month, accounting_method, rounding_mode, tax_rounding_scope, tolerance,"
    " jurisdiction_code, tax_registered, tax_registration_number, require_open_period, required_dimensions, max_rate_age_days,"
    " numbering, updated_at, row_version"
)


def get_config(ctx: RequestCtx) -> dict[str, Any] | None:
    _, co = _co(ctx)
    return one(ctx, f"SELECT {CONFIG_COLS} FROM acct_configurations WHERE company_id = :c", c=co)


def require_config(ctx: RequestCtx, *, lock: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    cfg = one(
        ctx,
        f"SELECT {CONFIG_COLS} FROM acct_configurations WHERE company_id = :c" + (" FOR UPDATE" if lock else ""),
        c=co,
    )
    if cfg is None:
        raise Conflict("Accounting has not been set up for this company yet. Set up its accounting profile first.")
    return cfg


def config_view(ctx: RequestCtx) -> dict[str, Any]:
    cfg = get_config(ctx)
    _, co = _co(ctx)
    company = one(ctx, "SELECT base_currency FROM companies WHERE id = :c", c=co)
    counts = one(
        ctx,
        "SELECT (SELECT count(*) FROM acct_accounts WHERE company_id = :c) AS accounts,"
        " (SELECT count(*) FROM acct_tax_codes WHERE company_id = :c) AS tax_codes,"
        " (SELECT count(*) FROM acct_periods WHERE company_id = :c) AS periods",
        c=co,
    )
    return ser(
        {
            "initialized": cfg is not None,
            "company_base_currency": company["base_currency"] if company else None,
            "config": cfg,
            "counts": counts,
            "policy": get_policy(ctx),
            "templates": {
                "charts": [{"key": k, "label": tpl.CHART_LABELS[k]} for k in tpl.CHARTS],
                "tax_packs": [
                    {
                        "key": p["key"],
                        "name": p["name"],
                        "jurisdiction_code": p["jurisdiction_code"],
                        "verification_status": p["verification_status"],
                        "source_reference": p["source_reference"],
                    }
                    for p in tpl.TAX_PACKS.values()
                ],
            },
        }
    )


def init_config(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    if get_config(ctx) is not None:
        raise Conflict("Accounting is already set up for this company.")
    company = one(ctx, "SELECT base_currency FROM companies WHERE id = :c", c=co)
    assert company is not None
    base = str(body.get("base_currency") or company["base_currency"]).upper()
    cur = one(ctx, "SELECT code, minor_unit FROM currencies WHERE code = :c", c=base)
    if cur is None:
        raise Unprocessable(f"Unknown currency {base}.")
    scale = int(body.get("amount_scale", cur["minor_unit"]))
    tolerance = Decimal(1).scaleb(-scale)
    ctx.conn.execute(
        text(
            "INSERT INTO acct_configurations (id, organization_id, company_id, base_currency, amount_scale, fiscal_year_start_month,"
            " accounting_method, rounding_mode, tax_rounding_scope, tolerance, jurisdiction_code, created_by, updated_by)"
            " VALUES (:id, :o, :c, :b, :s, :m, :am, :rm, :ts, :t, :j, :u, :u)"
        ),
        {
            "id": new_id(),
            "o": org,
            "c": co,
            "b": base,
            "s": scale,
            "m": int(body.get("fiscal_year_start_month", 1)),
            "am": body.get("accounting_method", "accrual"),
            "rm": body.get("rounding_mode", "half_up"),
            "ts": body.get("tax_rounding_scope", "line"),
            "t": tolerance,
            "j": body.get("jurisdiction_code"),
            "u": ctx.user_id,
        },
    )
    ctx.conn.execute(
        text(
            "INSERT INTO acct_approval_policies (id, organization_id, company_id, updated_by) VALUES (:id, :o, :c, :u)"
            " ON CONFLICT (company_id) DO NOTHING"
        ),
        {"id": new_id(), "o": org, "c": co, "u": ctx.user_id},
    )
    audit_event(ctx, "accounting.configured", "acct_configuration", co, {"base_currency": base})
    return config_view(ctx)


CONFIG_EDITABLE = (
    "fiscal_year_start_month",
    "accounting_method",
    "rounding_mode",
    "tax_rounding_scope",
    "tolerance",
    "jurisdiction_code",
    "tax_registered",
    "tax_registration_number",
    "require_open_period",
    "required_dimensions",
    "max_rate_age_days",
    "numbering",
)


def update_config(ctx: RequestCtx, patch: dict[str, Any], base_row_version: int | None) -> dict[str, Any]:
    cfg = require_config(ctx, lock=True)
    if base_row_version is not None and base_row_version != cfg["row_version"]:
        raise Conflict("The accounting configuration changed since you opened it. Reload and try again.")
    if cfg["base_currency"] != patch.get("base_currency", cfg["base_currency"]):
        raise Unprocessable("The base currency cannot be changed once accounting is set up.")
    sets, params = [], {"c": ctx.company_id, "u": ctx.user_id}
    for k in CONFIG_EDITABLE:
        if k in patch and patch[k] is not None:
            v = patch[k]
            if k == "tolerance":
                v = core.D(v)
            if k == "numbering":
                v = json.dumps(_clean_numbering(v))
                sets.append(f"{k} = CAST(:{k} AS jsonb)")
            else:
                sets.append(f"{k} = :{k}")
            params[k] = v
    if not sets:
        return config_view(ctx)
    ctx.conn.execute(
        text(
            f"UPDATE acct_configurations SET {', '.join(sets)}, updated_by = :u, updated_at = now(), row_version = row_version + 1"
            " WHERE company_id = :c"
        ),
        params,
    )
    audit_event(
        ctx,
        "accounting.config_updated",
        "acct_configuration",
        ctx.company_id,
        {"fields": sorted(k for k in patch if k in CONFIG_EDITABLE)},
    )
    return config_view(ctx)


def _clean_numbering(v: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("transaction", "journal"):
        spec = (v or {}).get(key) or {}
        prefix = str(spec.get("prefix", "TX-" if key == "transaction" else "JV-"))[:12]
        padding = int(spec.get("padding", 6))
        if not (1 <= padding <= 12) or not prefix.replace("-", "").replace("/", "").replace("_", "").isalnum():
            raise Unprocessable("Numbering needs a short alphanumeric prefix and padding between 1 and 12.")
        out[key] = {"prefix": prefix, "padding": padding}
    return out


# ------------------------------------------------------------------ approval policy
def get_policy(ctx: RequestCtx) -> dict[str, Any] | None:
    _, co = _co(ctx)
    return one(
        ctx,
        "SELECT separate_preparer_approver, levels, distinct_levels, approval_limit, updated_at FROM acct_approval_policies WHERE company_id = :c",
        c=co,
    )


def set_policy(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx, lock=True)
    levels = int(body.get("levels", 1))
    limit = core.d_or_none(body.get("approval_limit"))
    if (levels > 1 or limit is not None) and not entitlements.has_feature(ctx.conn, org, "feature.advanced_approval"):
        raise Conflict("Two-level approval and approval limits are not included in the current plan.")
    ctx.conn.execute(
        text(
            "INSERT INTO acct_approval_policies (id, organization_id, company_id, separate_preparer_approver, levels, distinct_levels,"
            " approval_limit, updated_by) VALUES (:id, :o, :c, :s, :l, :d, :a, :u)"
            " ON CONFLICT (company_id) DO UPDATE SET separate_preparer_approver = :s, levels = :l, distinct_levels = :d,"
            " approval_limit = :a, updated_by = :u, updated_at = now()"
        ),
        {
            "id": new_id(),
            "o": org,
            "c": co,
            "s": bool(body.get("separate_preparer_approver", True)),
            "l": levels,
            "d": bool(body.get("distinct_levels", True)),
            "a": limit,
            "u": ctx.user_id,
        },
    )
    audit_event(ctx, "accounting.policy_updated", "acct_approval_policy", co, {"levels": levels})
    return ser(
        must(
            ctx,
            "SELECT separate_preparer_approver, levels, distinct_levels, approval_limit, updated_at FROM acct_approval_policies WHERE company_id = :c",
            c=co,
        )
    )


# ------------------------------------------------------------------ chart of accounts
ACCOUNT_COLS = (
    "a.id, a.code, a.name, a.parent_id, a.account_type, a.normal_balance, a.currency, a.is_postable, a.is_active, a.description,"
    " a.external_code, a.archived_at, a.updated_at, a.row_version"
)


def list_accounts(
    ctx: RequestCtx, *, q: str | None = None, active: bool | None = None, account_type: str | None = None
) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    sql = (
        f"SELECT {ACCOUNT_COLS}, (SELECT count(*) FROM acct_accounts k WHERE k.parent_id = a.id) AS child_count,"
        " EXISTS (SELECT 1 FROM acct_journal_lines l WHERE l.account_id = a.id) AS in_use"
        " FROM acct_accounts a WHERE a.company_id = :c"
    )
    p: dict[str, Any] = {"c": co}
    if q:
        sql += " AND (a.code ILIKE :q OR a.name ILIKE :q)"
        p["q"] = f"%{q}%"
    if active is not None:
        sql += " AND a.is_active = :a"
        p["a"] = active
    if account_type:
        sql += " AND a.account_type = :t"
        p["t"] = account_type
    return ser(rows(ctx, sql + " ORDER BY a.code", **p))


def get_account(ctx: RequestCtx, account_id: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    a = one(
        ctx,
        f"SELECT {ACCOUNT_COLS} FROM acct_accounts a WHERE a.company_id = :c AND a.id = :i"
        + (" FOR UPDATE" if lock else ""),
        c=co,
        i=account_id,
    )
    if a is None:
        raise NotFound("Account not found.")
    return a


def _check_parent(ctx: RequestCtx, account_id: uuid.UUID | None, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        return
    if account_id is not None and parent_id == account_id:
        raise Unprocessable("An account cannot be its own parent.")
    parent = get_account(ctx, parent_id)  # 404 when it belongs to another company
    if parent["is_postable"]:
        raise Unprocessable("A parent must be a group account (not postable). Make it a group account first.")
    if account_id is not None:
        # walk up from the proposed parent: reaching the account itself would close a loop
        seen: set[str] = set()
        cur: str | None = str(parent_id)
        while cur:
            if cur == str(account_id):
                raise Unprocessable("That parent would create a circular hierarchy.")
            if cur in seen:
                raise Unprocessable("The existing hierarchy is already circular; fix it before moving accounts.")
            seen.add(cur)
            nxt = one(ctx, "SELECT parent_id FROM acct_accounts WHERE id = :i", i=uuid.UUID(cur))
            cur = nxt["parent_id"] if nxt else None


def _resolve_normal(account_type: str, given: str | None) -> str:
    default = core.default_normal_balance(account_type)
    if default is None:
        if given not in ("debit", "credit"):
            raise Unprocessable("A tax account must state whether its normal balance is debit or credit.")
        return given
    if given is not None and given != default:
        raise Unprocessable(f"A {account_type.replace('_', ' ')} account normally carries a {default} balance.")
    return default


def create_account(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx)
    atype = body["account_type"]
    if atype not in core.ACCOUNT_TYPES:
        raise Unprocessable("Unknown account type.")
    parent_id = body.get("parent_id")
    _check_parent(ctx, None, parent_id)
    if one(ctx, "SELECT 1 AS x FROM acct_accounts WHERE company_id = :c AND code = :k", c=co, k=body["code"]):
        raise Conflict(f"Account code {body['code']} is already used in this company.")
    cur = body.get("currency")
    if cur and not one(ctx, "SELECT 1 AS x FROM currencies WHERE code = :c", c=cur):
        raise Unprocessable(f"Unknown currency {cur}.")
    aid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_accounts (id, organization_id, company_id, code, name, parent_id, account_type, normal_balance, currency,"
            " is_postable, description, external_code, created_by, updated_by) VALUES (:id, :o, :c, :code, :name, :p, :t, :nb, :cur,"
            " :post, :d, :ext, :u, :u)"
        ),
        {
            "id": aid,
            "o": org,
            "c": co,
            "code": body["code"],
            "name": body["name"].strip(),
            "p": parent_id,
            "t": atype,
            "nb": _resolve_normal(atype, body.get("normal_balance")),
            "cur": cur,
            "post": bool(body.get("is_postable", True)),
            "d": body.get("description"),
            "ext": body.get("external_code"),
            "u": ctx.user_id,
        },
    )
    audit_event(ctx, "accounting.account_created", "acct_account", aid, {"code": body["code"]})
    return ser(get_account(ctx, aid))


def update_account(
    ctx: RequestCtx, account_id: uuid.UUID, patch: dict[str, Any], base_row_version: int | None
) -> dict[str, Any]:
    _, co = _co(ctx)
    a = get_account(ctx, account_id, lock=True)
    if base_row_version is not None and base_row_version != a["row_version"]:
        raise Conflict("The account changed since you opened it. Reload and try again.")
    in_use = bool(one(ctx, "SELECT 1 AS x FROM acct_journal_lines WHERE account_id = :a LIMIT 1", a=account_id))
    sets: dict[str, Any] = {}
    if "code" in patch and patch["code"] != a["code"]:
        if one(
            ctx,
            "SELECT 1 AS x FROM acct_accounts WHERE company_id = :c AND code = :k AND id <> :i",
            c=co,
            k=patch["code"],
            i=account_id,
        ):
            raise Conflict(f"Account code {patch['code']} is already used in this company.")
        sets["code"] = patch["code"]
    for k in ("name", "description", "external_code"):
        if k in patch:
            sets[k] = patch[k]
    if "account_type" in patch and patch["account_type"] != a["account_type"]:
        if in_use:
            raise Conflict("The type of an account that already has journal lines cannot be changed.")
        if patch["account_type"] not in core.ACCOUNT_TYPES:
            raise Unprocessable("Unknown account type.")
        sets["account_type"] = patch["account_type"]
        sets["normal_balance"] = _resolve_normal(patch["account_type"], patch.get("normal_balance"))
    if "currency" in patch and patch["currency"] != a["currency"]:
        if in_use:
            raise Conflict("The currency of an account that already has journal lines cannot be changed.")
        sets["currency"] = patch["currency"]
    if "parent_id" in patch and str(patch["parent_id"] or "") != str(a["parent_id"] or ""):
        _check_parent(ctx, account_id, patch["parent_id"])
        sets["parent_id"] = patch["parent_id"]
    if "is_postable" in patch and patch["is_postable"] != a["is_postable"]:
        children = one(
            ctx, "SELECT count(*) AS n FROM acct_accounts WHERE parent_id = :i AND company_id = :c", i=account_id, c=co
        )
        if patch["is_postable"] and children and children["n"]:
            raise Unprocessable("An account with sub-accounts is a group account and cannot be posted to.")
        if not patch["is_postable"] and in_use:
            raise Conflict("An account that already has journal lines must stay postable (archive it instead).")
        sets["is_postable"] = patch["is_postable"]
    if not sets:
        return ser(a)
    sql = ", ".join(f"{k} = :{k}" for k in sets)
    ctx.conn.execute(
        text(
            f"UPDATE acct_accounts SET {sql}, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {**sets, "u": ctx.user_id, "i": account_id},
    )
    audit_event(ctx, "accounting.account_updated", "acct_account", account_id, {"fields": sorted(sets)})
    return ser(get_account(ctx, account_id))


def set_account_active(ctx: RequestCtx, account_id: uuid.UUID, active: bool) -> dict[str, Any]:
    a = get_account(ctx, account_id, lock=True)
    if not active:
        if one(ctx, "SELECT 1 AS x FROM acct_accounts WHERE parent_id = :i AND is_active LIMIT 1", i=account_id):
            raise Conflict("Deactivate or move the sub-accounts first.")
        role = one(ctx, "SELECT role FROM acct_role_mappings WHERE account_id = :i LIMIT 1", i=account_id)
        if role:
            raise Conflict(
                f"The account is mapped to the “{role['role']}” role. Map another account to that role first."
            )
    ctx.conn.execute(
        text(
            "UPDATE acct_accounts SET is_active = :a, archived_at = CASE WHEN :a THEN NULL ELSE now() END, updated_by = :u,"
            " updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"a": active, "u": ctx.user_id, "i": account_id},
    )
    audit_event(
        ctx,
        "accounting.account_restored" if active else "accounting.account_archived",
        "acct_account",
        account_id,
        {"code": a["code"]},
    )
    return ser(get_account(ctx, account_id))


ROLE_TYPES: dict[str, tuple[str, ...] | None] = {
    "ar_control": ("receivable",),
    "ap_control": ("payable",),
    "employee_payable": ("payable", "current_liability", "liability"),
    "vat_input": ("tax",),
    "vat_output": ("tax",),
    "bank": ("bank",),
    "cash": ("cash",),
    "bank_charges": None,
    "expense_default": ("expense", "cost_of_sales", "other_expense"),
    "revenue_default": ("revenue", "other_income"),
    "suspense": None,
    "rounding": None,
}


def get_roles(ctx: RequestCtx) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    mapped = {
        r["role"]: r
        for r in rows(
            ctx,
            "SELECT m.role, m.account_id, a.code, a.name FROM acct_role_mappings m JOIN acct_accounts a ON a.id = m.account_id WHERE m.company_id = :c",
            c=co,
        )
    }
    return ser(
        [
            {
                "role": r,
                "account_id": (mapped.get(r) or {}).get("account_id"),
                "code": (mapped.get(r) or {}).get("code"),
                "name": (mapped.get(r) or {}).get("name"),
                "allowed_types": ROLE_TYPES[r],
            }
            for r in core.ACCOUNT_ROLES
        ]
    )


def set_role(ctx: RequestCtx, role: str, account_id: uuid.UUID | None) -> list[dict[str, Any]]:
    org, co = _co(ctx)
    if role not in core.ACCOUNT_ROLES:
        raise Unprocessable("Unknown account role.")
    require_config(ctx)
    if account_id is None:
        ctx.conn.execute(
            text("DELETE FROM acct_role_mappings WHERE company_id = :c AND role = :r"), {"c": co, "r": role}
        )
    else:
        a = get_account(ctx, account_id)
        if not a["is_active"] or not a["is_postable"]:
            raise Unprocessable("A role must point to an active, postable account.")
        allowed = ROLE_TYPES[role]
        if allowed and a["account_type"] not in allowed:
            raise Unprocessable(f"The “{role}” role needs an account of type {', '.join(allowed)}.")
        ctx.conn.execute(
            text(
                "INSERT INTO acct_role_mappings (id, organization_id, company_id, role, account_id, updated_by) VALUES (:id, :o, :c, :r, :a, :u)"
                " ON CONFLICT (company_id, role) DO UPDATE SET account_id = :a, updated_by = :u, updated_at = now()"
            ),
            {"id": new_id(), "o": org, "c": co, "r": role, "a": account_id, "u": ctx.user_id},
        )
    audit_event(
        ctx,
        "accounting.role_mapped",
        "acct_role_mapping",
        co,
        {"role": role, "account_id": str(account_id) if account_id else None},
    )
    return get_roles(ctx)


def role_accounts(ctx: RequestCtx) -> dict[str, str]:
    _, co = _co(ctx)
    return {
        r.role: str(r.account_id)
        for r in ctx.conn.execute(
            text("SELECT role, account_id FROM acct_role_mappings WHERE company_id = :c"), {"c": co}
        )
    }


# ------------------------------------------------------------------ periods
def list_periods(ctx: RequestCtx) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            "SELECT id, fiscal_year, name, start_date, end_date, status, closed_at, reopened_at FROM acct_periods WHERE company_id = :c ORDER BY start_date",
            c=co,
        )
    )


def _overlap(ctx: RequestCtx, start: date, end: date, ignore: uuid.UUID | None = None) -> dict[str, Any] | None:
    _, co = _co(ctx)
    return one(
        ctx,
        "SELECT name FROM acct_periods WHERE company_id = :c AND start_date <= :e AND end_date >= :s AND (CAST(:i AS uuid) IS NULL OR id <> :i) LIMIT 1",
        c=co,
        s=start,
        e=end,
        i=ignore,
    )


def _period_event(ctx: RequestCtx, period_id: Any, action: str, reason: str | None = None) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO acct_period_events (id, organization_id, company_id, period_id, action, actor_id, reason) VALUES (:id, :o, :c, :p, :a, :u, :r)"
        ),
        {"id": new_id(), "o": org, "c": co, "p": period_id, "a": action, "u": ctx.user_id, "r": reason},
    )


def create_period(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx, lock=True)
    start, end = body["start_date"], body["end_date"]
    if end < start:
        raise Unprocessable("A period cannot end before it starts.")
    clash = _overlap(ctx, start, end)
    if clash:
        raise Conflict(f"The dates overlap the existing period {clash['name']}.")
    pid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_periods (id, organization_id, company_id, fiscal_year, name, start_date, end_date) VALUES (:id, :o, :c, :fy, :n, :s, :e)"
        ),
        {
            "id": pid,
            "o": org,
            "c": co,
            "fy": body.get("fiscal_year") or start.year,
            "n": body["name"],
            "s": start,
            "e": end,
        },
    )
    _period_event(ctx, pid, "created")
    audit_event(ctx, "accounting.period_created", "acct_period", pid, {"name": body["name"]})
    return ser(
        must(ctx, "SELECT id, fiscal_year, name, start_date, end_date, status FROM acct_periods WHERE id = :i", i=pid)
    )


def generate_periods(ctx: RequestCtx, fiscal_year: int) -> list[dict[str, Any]]:
    """Twelve monthly periods for a fiscal year that starts in the company's configured month. Existing periods are kept."""
    org, co = _co(ctx)
    cfg = require_config(ctx, lock=True)
    month = cfg["fiscal_year_start_month"]
    created = 0
    for i in range(12):
        m0 = month - 1 + i
        y, m = fiscal_year + m0 // 12, m0 % 12 + 1
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
        if one(ctx, "SELECT 1 AS x FROM acct_periods WHERE company_id = :c AND start_date = :s", c=co, s=start):
            continue
        clash = _overlap(ctx, start, end)
        if clash:
            raise Conflict(f"Cannot generate {start:%Y-%m}: it overlaps the existing period {clash['name']}.")
        pid = new_id()
        ctx.conn.execute(
            text(
                "INSERT INTO acct_periods (id, organization_id, company_id, fiscal_year, name, start_date, end_date) VALUES (:id, :o, :c, :fy, :n, :s, :e)"
            ),
            {"id": pid, "o": org, "c": co, "fy": fiscal_year, "n": f"{y}-{m:02d}", "s": start, "e": end},
        )
        _period_event(ctx, pid, "created")
        created += 1
    audit_event(
        ctx, "accounting.periods_generated", "acct_period", co, {"fiscal_year": fiscal_year, "created": created}
    )
    return list_periods(ctx)


def set_period_status(ctx: RequestCtx, period_id: uuid.UUID, status: str, reason: str | None) -> dict[str, Any]:
    _, co = _co(ctx)
    p = one(
        ctx, "SELECT id, name, status FROM acct_periods WHERE company_id = :c AND id = :i FOR UPDATE", c=co, i=period_id
    )
    if p is None:
        raise NotFound("Period not found.")
    if p["status"] == status:
        raise Conflict(f"The period is already {status}.")
    if status == "open" and not (reason and reason.strip()):
        raise Unprocessable("Reopening a period needs a written reason.")
    if status == "closed":
        pending = one(
            ctx,
            "SELECT count(*) AS n FROM acct_transactions t WHERE t.company_id = :c AND t.status IN ('awaiting_review', 'awaiting_approval')"
            " AND t.transaction_date BETWEEN (SELECT start_date FROM acct_periods WHERE id = :i) AND (SELECT end_date FROM acct_periods WHERE id = :i)",
            c=co,
            i=period_id,
        )
        if pending and pending["n"]:
            raise Conflict(f"{pending['n']} transaction(s) dated in this period still await review or approval.")
        ctx.conn.execute(
            text("UPDATE acct_periods SET status = 'closed', closed_by = :u, closed_at = now() WHERE id = :i"),
            {"u": ctx.user_id, "i": period_id},
        )
    else:
        ctx.conn.execute(
            text("UPDATE acct_periods SET status = 'open', reopened_by = :u, reopened_at = now() WHERE id = :i"),
            {"u": ctx.user_id, "i": period_id},
        )
    _period_event(ctx, period_id, "closed" if status == "closed" else "reopened", reason)
    audit_event(
        ctx,
        "accounting.period_closed" if status == "closed" else "accounting.period_reopened",
        "acct_period",
        period_id,
        {"name": p["name"], "reason": reason},
    )
    return ser(
        must(
            ctx,
            "SELECT id, fiscal_year, name, start_date, end_date, status, closed_at, reopened_at FROM acct_periods WHERE id = :i",
            i=period_id,
        )
    )


def period_events(ctx: RequestCtx, period_id: uuid.UUID) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            "SELECT e.action, e.reason, e.created_at, u.display_name AS actor FROM acct_period_events e LEFT JOIN users u ON u.id = e.actor_id"
            " WHERE e.company_id = :c AND e.period_id = :p ORDER BY e.created_at",
            c=co,
            p=period_id,
        )
    )


# ------------------------------------------------------------------ tax
TAX_COLS = (
    "t.id, t.code, t.name, t.treatment, t.direction, t.input_account_id, t.output_account_id, t.recoverable, t.is_active,"
    " t.jurisdiction_code, t.source_reference, t.verification_status, t.verified_on, t.verified_by_name"
)


def _rates(ctx: RequestCtx, tax_code_id: Any) -> list[dict[str, Any]]:
    return rows(
        ctx,
        "SELECT id, rate, effective_from, effective_to FROM acct_tax_rates WHERE tax_code_id = :t ORDER BY effective_from",
        t=tax_code_id,
    )


def list_tax_codes(ctx: RequestCtx) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    out = rows(ctx, f"SELECT {TAX_COLS} FROM acct_tax_codes t WHERE t.company_id = :c ORDER BY t.code", c=co)
    for t in out:
        t["rates"] = _rates(ctx, t["id"])
    return ser(out)


def get_tax_code(ctx: RequestCtx, tax_code_id: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    t = one(ctx, f"SELECT {TAX_COLS} FROM acct_tax_codes t WHERE t.company_id = :c AND t.id = :i", c=co, i=tax_code_id)
    if t is None:
        raise NotFound("Tax code not found.")
    t["rates"] = _rates(ctx, t["id"])
    return t


def _tax_account(ctx: RequestCtx, account_id: Any) -> None:
    if account_id is None:
        return
    a = get_account(ctx, uuid.UUID(str(account_id)))
    if not a["is_active"] or not a["is_postable"]:
        raise Unprocessable("Tax accounts must be active, postable accounts.")


def _rate_overlap(ctx: RequestCtx, tax_code_id: Any, start: date, end: date | None, ignore: Any = None) -> bool:
    return bool(
        one(
            ctx,
            "SELECT 1 AS x FROM acct_tax_rates WHERE tax_code_id = :t AND effective_from <= COALESCE(:e, 'infinity'::date)"
            " AND COALESCE(effective_to, 'infinity'::date) >= :s AND (CAST(:i AS uuid) IS NULL OR id <> :i) LIMIT 1",
            t=tax_code_id,
            s=start,
            e=end,
            i=ignore,
        )
    )


def create_tax_code(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx)
    treatment = body["treatment"]
    if treatment in ("reverse_charge", "import") and not entitlements.has_feature(
        ctx.conn, org, "feature.advanced_accounting"
    ):
        raise Conflict(f"The “{treatment}” treatment is not included in the current plan.")
    rate = core.d_or_none(body.get("rate"))
    problems = core.validate_tax_spec(core.TaxSpec(None, treatment, rate or core.ZERO))
    blocking = [p for p in problems if p.blocking]
    if blocking:
        raise Unprocessable(blocking[0].message)
    if treatment in core.RATE_BEARING and rate is None:
        raise Unprocessable("This treatment needs a rate (percent) and the date it takes effect.")
    for k in ("input_account_id", "output_account_id"):
        _tax_account(ctx, body.get(k))
    if one(ctx, "SELECT 1 AS x FROM acct_tax_codes WHERE company_id = :c AND code = :k", c=co, k=body["code"]):
        raise Conflict(f"Tax code {body['code']} already exists in this company.")
    tid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_tax_codes (id, organization_id, company_id, code, name, treatment, direction, input_account_id, output_account_id,"
            " recoverable, jurisdiction_code, source_reference, created_by) VALUES (:id, :o, :c, :code, :n, :t, :d, :ia, :oa, :r, :j, :s, :u)"
        ),
        {
            "id": tid,
            "o": org,
            "c": co,
            "code": body["code"],
            "n": body["name"],
            "t": treatment,
            "d": body.get("direction", "both"),
            "ia": body.get("input_account_id"),
            "oa": body.get("output_account_id"),
            "r": bool(body.get("recoverable", True)),
            "j": body.get("jurisdiction_code"),
            "s": body.get("source_reference"),
            "u": ctx.user_id,
        },
    )
    if treatment in core.RATE_BEARING and rate is not None:
        _insert_rate(ctx, tid, rate, body.get("effective_from") or date(2000, 1, 1), None)
    audit_event(
        ctx, "accounting.tax_code_created", "acct_tax_code", tid, {"code": body["code"], "treatment": treatment}
    )
    return ser(get_tax_code(ctx, tid))


def _insert_rate(ctx: RequestCtx, tax_code_id: Any, rate: Decimal, start: date, end: date | None) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO acct_tax_rates (id, organization_id, company_id, tax_code_id, rate, effective_from, effective_to, created_by)"
            " VALUES (:id, :o, :c, :t, :r, :s, :e, :u)"
        ),
        {"id": new_id(), "o": org, "c": co, "t": tax_code_id, "r": rate, "s": start, "e": end, "u": ctx.user_id},
    )


def update_tax_code(ctx: RequestCtx, tax_code_id: uuid.UUID, patch: dict[str, Any]) -> dict[str, Any]:
    t = get_tax_code(ctx, tax_code_id)
    sets: dict[str, Any] = {}
    for k in ("name", "direction", "recoverable", "is_active", "source_reference", "jurisdiction_code"):
        if k in patch and patch[k] is not None:
            sets[k] = patch[k]
    for k in ("input_account_id", "output_account_id"):
        if k in patch:
            _tax_account(ctx, patch[k])
            sets[k] = patch[k]
    if sets:
        # editing the configuration of a tax code invalidates an earlier verification: it was verified as it was
        sets["verification_status"] = (
            "unverified" if t["verification_status"] == "verified" else t["verification_status"]
        )
        if sets["verification_status"] == "unverified":
            sets["verified_on"], sets["verified_by_name"] = None, None
        ctx.conn.execute(
            text(
                f"UPDATE acct_tax_codes SET {', '.join(f'{k} = :{k}' for k in sets)}, updated_at = now() WHERE id = :i"
            ),
            {**sets, "i": tax_code_id},
        )
        audit_event(ctx, "accounting.tax_code_updated", "acct_tax_code", tax_code_id, {"fields": sorted(sets)})
    return ser(get_tax_code(ctx, tax_code_id))


def add_tax_rate(
    ctx: RequestCtx, tax_code_id: uuid.UUID, rate: Decimal, start: date, end: date | None
) -> dict[str, Any]:
    t = get_tax_code(ctx, tax_code_id)
    if t["treatment"] not in core.RATE_BEARING:
        raise Unprocessable(f"A “{t['treatment']}” tax code carries no rate.")
    if rate < 0 or rate > 100:
        raise Unprocessable("A tax rate must be between 0 and 100 percent.")
    if end is not None and end < start:
        raise Unprocessable("A rate cannot end before it starts.")
    if _rate_overlap(ctx, tax_code_id, start, end):
        raise Conflict("The dates overlap another rate of this tax code. End the earlier rate first.")
    _insert_rate(ctx, tax_code_id, rate, start, end)
    ctx.conn.execute(
        text(
            "UPDATE acct_tax_codes SET verification_status = 'unverified', verified_on = NULL, verified_by_name = NULL, updated_at = now() WHERE id = :i"
        ),
        {"i": tax_code_id},
    )
    audit_event(
        ctx,
        "accounting.tax_rate_added",
        "acct_tax_code",
        tax_code_id,
        {"rate": format(rate, "f"), "from": start.isoformat()},
    )
    return ser(get_tax_code(ctx, tax_code_id))


def end_tax_rate(ctx: RequestCtx, tax_code_id: uuid.UUID, rate_id: uuid.UUID, end: date) -> dict[str, Any]:
    get_tax_code(ctx, tax_code_id)
    r = one(
        ctx,
        "SELECT id, effective_from FROM acct_tax_rates WHERE id = :r AND tax_code_id = :t",
        r=rate_id,
        t=tax_code_id,
    )
    if r is None:
        raise NotFound("Rate not found.")
    if end < r["effective_from"]:
        raise Unprocessable("A rate cannot end before it starts.")
    ctx.conn.execute(text("UPDATE acct_tax_rates SET effective_to = :e WHERE id = :r"), {"e": end, "r": rate_id})
    audit_event(ctx, "accounting.tax_rate_ended", "acct_tax_code", tax_code_id, {"until": end.isoformat()})
    return ser(get_tax_code(ctx, tax_code_id))


def verify_tax_code(
    ctx: RequestCtx, tax_code_id: uuid.UUID, verified_by_name: str, verified_on: date, source_reference: str | None
) -> dict[str, Any]:
    """Record that a qualified person reviewed this tax code. Artify never marks a code verified by itself."""
    get_tax_code(ctx, tax_code_id)
    ctx.conn.execute(
        text(
            "UPDATE acct_tax_codes SET verification_status = 'verified', verified_on = :d, verified_by_name = :n,"
            " source_reference = COALESCE(:s, source_reference), updated_at = now() WHERE id = :i"
        ),
        {"d": verified_on, "n": verified_by_name.strip(), "s": source_reference, "i": tax_code_id},
    )
    audit_event(
        ctx,
        "accounting.tax_code_verified",
        "acct_tax_code",
        tax_code_id,
        {"verified_by": verified_by_name, "on": verified_on.isoformat()},
    )
    return ser(get_tax_code(ctx, tax_code_id))


def tax_spec_for(ctx: RequestCtx, tax_code_id: Any, on: date) -> tuple[core.TaxSpec | None, list[core.Issue]]:
    """The tax code as of `on`: treatment, the rate in force that day, accounts. No effective rate = a blocking issue."""
    _, co = _co(ctx)
    conn = ctx.conn
    t = one(
        conn,
        "SELECT id, code, treatment, recoverable, input_account_id, output_account_id, is_active FROM acct_tax_codes WHERE id = :i AND company_id = :c",
        i=tax_code_id,
        c=co,
    )
    if t is None:
        return None, [core.err("tax_code_not_found", "The tax code does not exist in this company.")]
    issues: list[core.Issue] = []
    if not t["is_active"]:
        issues.append(core.err("tax_code_inactive", f"Tax code {t['code']} is inactive."))
    rate = core.ZERO
    if t["treatment"] in core.RATE_BEARING:
        r = one(
            conn,
            "SELECT rate FROM acct_tax_rates WHERE tax_code_id = :t AND company_id = :c AND effective_from <= :d AND (effective_to IS NULL OR effective_to >= :d) ORDER BY effective_from DESC LIMIT 1",
            t=tax_code_id,
            c=co,
            d=on,
        )
        if r is None:
            issues.append(
                core.err("no_effective_tax_rate", f"Tax code {t['code']} has no rate in force on {on.isoformat()}.")
            )
        else:
            rate = r["rate"]
    spec = core.TaxSpec(
        str(t["id"]), t["treatment"], rate, t["recoverable"], t["input_account_id"], t["output_account_id"]
    )
    issues.extend(core.validate_tax_spec(spec))
    return spec, issues


def apply_tax_pack(ctx: RequestCtx, pack_key: str) -> list[dict[str, Any]]:
    """Copy a starter tax pack into this company. The copy is independent and stays UNVERIFIED until a person records a review."""
    org, co = _co(ctx)
    pack = tpl.TAX_PACKS.get(pack_key)
    if pack is None:
        raise NotFound("Unknown tax pack.")
    cfg = require_config(ctx, lock=True)
    roles = role_accounts(ctx)
    for code, name, treatment, direction, rate, recoverable in pack["codes"]:
        if one(ctx, "SELECT 1 AS x FROM acct_tax_codes WHERE company_id = :c AND code = :k", c=co, k=code):
            continue
        tid = new_id()
        ctx.conn.execute(
            text(
                "INSERT INTO acct_tax_codes (id, organization_id, company_id, code, name, treatment, direction, input_account_id, output_account_id,"
                " recoverable, jurisdiction_code, source_reference, verification_status, created_by) VALUES (:id, :o, :c, :code, :n, :t, :d, :ia, :oa, :r, :j, :s, 'unverified', :u)"
            ),
            {
                "id": tid,
                "o": org,
                "c": co,
                "code": code,
                "n": name,
                "t": treatment,
                "d": direction,
                "ia": roles.get("vat_input") if direction in ("input", "both") else None,
                "oa": roles.get("vat_output") if direction in ("output", "both") else None,
                "r": recoverable,
                "j": pack["jurisdiction_code"],
                "s": pack["source_reference"],
                "u": ctx.user_id,
            },
        )
        if rate is not None:
            _insert_rate(ctx, tid, Decimal(rate), pack["effective_from"], None)
    if not cfg["jurisdiction_code"]:
        ctx.conn.execute(
            text("UPDATE acct_configurations SET jurisdiction_code = :j WHERE company_id = :c"),
            {"j": pack["jurisdiction_code"], "c": co},
        )
    audit_event(
        ctx,
        "accounting.tax_pack_applied",
        "acct_tax_code",
        co,
        {"pack": pack_key, "verification": pack["verification_status"]},
    )
    return list_tax_codes(ctx)


def apply_chart_template(ctx: RequestCtx, chart_key: str) -> list[dict[str, Any]]:
    """Copy a sample chart into an EMPTY chart of accounts and map its default roles."""
    org, co = _co(ctx)
    chart = tpl.CHARTS.get(chart_key)
    if chart is None:
        raise NotFound("Unknown chart template.")
    require_config(ctx, lock=True)
    if one(ctx, "SELECT 1 AS x FROM acct_accounts WHERE company_id = :c LIMIT 1", c=co):
        raise Conflict("A chart template can only be applied to a company without accounts.")
    ids: dict[str, uuid.UUID] = {}
    for code, name, atype, parent, postable, _role in chart:
        aid = new_id()
        ids[code] = aid
        ctx.conn.execute(
            text(
                "INSERT INTO acct_accounts (id, organization_id, company_id, code, name, parent_id, account_type, normal_balance, is_postable, created_by, updated_by)"
                " VALUES (:id, :o, :c, :code, :n, :p, :t, :nb, :post, :u, :u)"
            ),
            {
                "id": aid,
                "o": org,
                "c": co,
                "code": code,
                "n": name,
                "p": ids.get(parent) if parent else None,
                "t": atype,
                "nb": core.default_normal_balance(atype) or ("debit" if code.startswith("1") else "credit"),
                "post": postable,
                "u": ctx.user_id,
            },
        )
    for code, _n, _t, _p, _post, role in chart:
        if role:
            ctx.conn.execute(
                text(
                    "INSERT INTO acct_role_mappings (id, organization_id, company_id, role, account_id, updated_by) VALUES (:id, :o, :c, :r, :a, :u)"
                ),
                {"id": new_id(), "o": org, "c": co, "r": role, "a": ids[code], "u": ctx.user_id},
            )
    audit_event(
        ctx, "accounting.chart_template_applied", "acct_account", co, {"template": chart_key, "accounts": len(chart)}
    )
    return list_accounts(ctx)


# ------------------------------------------------------------------ exchange rates
def list_rates(ctx: RequestCtx, currency: str | None = None) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    sql = "SELECT id, currency, base_currency, rate, rate_date, source, created_at FROM acct_currency_rates WHERE company_id = :c"
    p: dict[str, Any] = {"c": co}
    if currency:
        sql += " AND currency = :cur"
        p["cur"] = currency.upper()
    return ser(rows(ctx, sql + " ORDER BY rate_date DESC, created_at DESC LIMIT 500", **p))


def add_rate(ctx: RequestCtx, currency: str, rate: Decimal, rate_date: date, source: str) -> dict[str, Any]:
    org, co = _co(ctx)
    cfg = require_config(ctx)
    cur = currency.upper()
    if cur == cfg["base_currency"]:
        raise Unprocessable("The base currency needs no exchange rate.")
    if not one(ctx, "SELECT 1 AS x FROM currencies WHERE code = :c", c=cur):
        raise Unprocessable(f"Unknown currency {cur}.")
    if rate <= 0:
        raise Unprocessable("An exchange rate must be positive.")
    if one(
        ctx,
        "SELECT 1 AS x FROM acct_currency_rates WHERE company_id = :c AND currency = :cur AND rate_date = :d AND source = :s",
        c=co,
        cur=cur,
        d=rate_date,
        s=source,
    ):
        raise Conflict(
            "A rate from that source already exists for that date. Rates are never overwritten; use another source label or date."
        )
    rid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_currency_rates (id, organization_id, company_id, currency, base_currency, rate, rate_date, source, created_by)"
            " VALUES (:id, :o, :c, :cur, :b, :r, :d, :s, :u)"
        ),
        {
            "id": rid,
            "o": org,
            "c": co,
            "cur": cur,
            "b": cfg["base_currency"],
            "r": rate,
            "d": rate_date,
            "s": source,
            "u": ctx.user_id,
        },
    )
    audit_event(
        ctx, "accounting.rate_added", "acct_currency_rate", rid, {"currency": cur, "date": rate_date.isoformat()}
    )
    return ser(
        must(
            ctx,
            "SELECT id, currency, base_currency, rate, rate_date, source FROM acct_currency_rates WHERE id = :i",
            i=rid,
        )
    )


def lookup_rate(ctx: RequestCtx, currency: str, on: date) -> dict[str, Any] | None:
    """The most recent configured rate dated on or before `on`. Returns None rather than inventing one."""
    _, co = _co(ctx)
    return one(
        ctx,
        "SELECT rate, rate_date, source FROM acct_currency_rates WHERE company_id = :co AND currency = :c AND rate_date <= :d ORDER BY rate_date DESC, created_at DESC LIMIT 1",
        co=co,
        c=currency,
        d=on,
    )


# ------------------------------------------------------------------ customers and suppliers
CONTACT_COLS = (
    "c.id, c.kind, c.legal_name, c.display_name, c.tax_number, c.email, c.phone, c.address, c.currency, c.receivable_account_id,"
    " c.payable_account_id, c.default_tax_code_id, c.payment_terms_days, c.is_active, c.external_ref, c.updated_at, c.row_version"
)


def list_contacts(
    ctx: RequestCtx,
    *,
    kind: str | None = None,
    q: str | None = None,
    active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _, co = _co(ctx)
    where = "c.company_id = :c"
    p: dict[str, Any] = {"c": co}
    if kind:
        where += " AND c.kind = :k"
        p["k"] = kind
    if q:
        where += " AND (c.display_name ILIKE :q OR c.legal_name ILIKE :q OR c.tax_number ILIKE :q)"
        p["q"] = f"%{q}%"
    if active is not None:
        where += " AND c.is_active = :a"
        p["a"] = active
    total = one(ctx, f"SELECT count(*) AS n FROM acct_contacts c WHERE {where}", **p)
    items = rows(
        ctx,
        f"SELECT {CONTACT_COLS} FROM acct_contacts c WHERE {where} ORDER BY lower(c.display_name) LIMIT :l OFFSET :o",
        **p,
        l=limit,
        o=offset,
    )
    return ser({"items": items, "total": total["n"] if total else 0, "limit": limit, "offset": offset})


def get_contact(ctx: RequestCtx, contact_id: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    c = one(
        ctx, f"SELECT {CONTACT_COLS} FROM acct_contacts c WHERE c.company_id = :c AND c.id = :i", c=co, i=contact_id
    )
    if c is None:
        raise NotFound("Customer or supplier not found.")
    return c


def _contact_refs(ctx: RequestCtx, body: dict[str, Any]) -> None:
    for k in ("receivable_account_id", "payable_account_id"):
        if body.get(k):
            a = get_account(ctx, uuid.UUID(str(body[k])))
            want = "receivable" if k.startswith("receivable") else "payable"
            if not a["is_active"] or not a["is_postable"] or a["account_type"] != want:
                raise Unprocessable(f"{k.split('_')[0].title()} account must be an active, postable {want} account.")
    if body.get("default_tax_code_id"):
        get_tax_code(ctx, uuid.UUID(str(body["default_tax_code_id"])))


def create_contact(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx)
    _contact_refs(ctx, body)
    tn = core.normalize_tax_number(body.get("tax_number"))
    if tn and one(
        ctx,
        "SELECT 1 AS x FROM acct_contacts WHERE company_id = :c AND kind = :k AND upper(regexp_replace(tax_number, '[^0-9A-Za-z]', '', 'g')) = :t",
        c=co,
        k=body["kind"],
        t=tn,
    ):
        raise Conflict("A contact of this kind with that tax registration number already exists.")
    cid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_contacts (id, organization_id, company_id, kind, legal_name, display_name, tax_number, email, phone, address, currency,"
            " receivable_account_id, payable_account_id, default_tax_code_id, payment_terms_days, external_ref, created_by, updated_by)"
            " VALUES (:id, :o, :c, :k, :ln, :dn, :tn, :em, :ph, :ad, :cur, :ra, :pa, :tc, :pt, :ex, :u, :u)"
        ),
        {
            "id": cid,
            "o": org,
            "c": co,
            "k": body["kind"],
            "ln": body["legal_name"].strip(),
            "dn": (body.get("display_name") or body["legal_name"]).strip(),
            "tn": body.get("tax_number"),
            "em": body.get("email"),
            "ph": body.get("phone"),
            "ad": body.get("address"),
            "cur": body.get("currency"),
            "ra": body.get("receivable_account_id"),
            "pa": body.get("payable_account_id"),
            "tc": body.get("default_tax_code_id"),
            "pt": body.get("payment_terms_days"),
            "ex": body.get("external_ref"),
            "u": ctx.user_id,
        },
    )
    audit_event(ctx, "accounting.contact_created", "acct_contact", cid, {"kind": body["kind"]})
    return ser(get_contact(ctx, cid))


def update_contact(
    ctx: RequestCtx, contact_id: uuid.UUID, patch: dict[str, Any], base_row_version: int | None
) -> dict[str, Any]:
    _, co = _co(ctx)
    c = get_contact(ctx, contact_id)
    if base_row_version is not None and base_row_version != c["row_version"]:
        raise Conflict("The record changed since you opened it. Reload and try again.")
    _contact_refs(ctx, patch)
    allowed = (
        "legal_name",
        "display_name",
        "tax_number",
        "email",
        "phone",
        "address",
        "currency",
        "receivable_account_id",
        "payable_account_id",
        "default_tax_code_id",
        "payment_terms_days",
        "is_active",
        "external_ref",
    )
    sets = {k: patch[k] for k in allowed if k in patch}
    if "tax_number" in sets:
        tn = core.normalize_tax_number(sets["tax_number"])
        if tn and one(
            ctx,
            "SELECT 1 AS x FROM acct_contacts WHERE company_id = :c AND kind = :k AND id <> :i AND upper(regexp_replace(tax_number, '[^0-9A-Za-z]', '', 'g')) = :t",
            c=co,
            k=c["kind"],
            i=contact_id,
            t=tn,
        ):
            raise Conflict("Another contact of this kind already has that tax registration number.")
    if sets:
        ctx.conn.execute(
            text(
                f"UPDATE acct_contacts SET {', '.join(f'{k} = :{k}' for k in sets)}, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            {**sets, "u": ctx.user_id, "i": contact_id},
        )
        audit_event(ctx, "accounting.contact_updated", "acct_contact", contact_id, {"fields": sorted(sets)})
    return ser(get_contact(ctx, contact_id))


def match_contact_candidates(
    ctx: RequestCtx, name: str | None, tax_number: str | None, kind: str | None
) -> list[dict[str, Any]]:
    """Suggestions only. Nothing is linked, merged or created: a person confirms or rejects every match."""
    _, co = _co(ctx)
    cands = rows(
        ctx,
        "SELECT id, kind, legal_name, display_name, tax_number FROM acct_contacts WHERE company_id = :c AND is_active"
        + (" AND kind = :k" if kind else ""),
        c=co,
        **({"k": kind} if kind else {}),
    )
    by_id = {str(c["id"]): c for c in cands}
    return ser(
        [
            {
                "contact_id": m.contact_id,
                "strength": m.strength,
                "score": m.score,
                "display_name": by_id[m.contact_id]["display_name"],
                "kind": by_id[m.contact_id]["kind"],
            }
            for m in core.match_contacts(name, tax_number, cands)
        ]
    )


# ------------------------------------------------------------------ rules
RULE_COLS = "id, rule_key, version, name, kind, priority, conditions, action, effective_from, effective_to, is_active, is_current, created_at"


def _validate_rule(ctx: RequestCtx, kind: str, conditions: list[dict[str, Any]], action: dict[str, Any]) -> None:
    problems = core.validate_rule_definition(kind, conditions, action)
    if problems:
        raise Unprocessable(problems[0], extra={"problems": problems})
    if kind == "account":
        a = get_account(ctx, uuid.UUID(str(action["account_id"])))
        if not a["is_active"] or not a["is_postable"]:
            raise Unprocessable("A rule can only point to an active, postable account.")
    if kind == "tax":
        t = get_tax_code(ctx, uuid.UUID(str(action["tax_code_id"])))
        if not t["is_active"]:
            raise Unprocessable("A rule cannot point to an inactive tax code.")
    for c in conditions:
        if c["field"] == "contact_id":
            get_contact(ctx, uuid.UUID(str(c["value"])))


def _rule_conflicts(
    ctx: RequestCtx, kind: str, priority: int, action: dict[str, Any], ignore_key: Any = None
) -> list[str]:
    _, co = _co(ctx)
    same = rows(
        ctx,
        "SELECT name, action FROM acct_rules WHERE company_id = :c AND is_current AND is_active AND kind = :k AND priority = :p AND (CAST(:i AS uuid) IS NULL OR rule_key <> :i)",
        c=co,
        k=kind,
        p=priority,
        i=ignore_key,
    )
    return [r["name"] for r in same if r["action"] != action]


def list_rules(ctx: RequestCtx, *, include_history: bool = False) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            f"SELECT {RULE_COLS} FROM acct_rules WHERE company_id = :c"
            + ("" if include_history else " AND is_current")
            + " ORDER BY kind, priority DESC, name, version DESC",
            c=co,
        )
    )


def _guard_rule_limits(ctx: RequestCtx) -> None:
    org = _co(ctx)[0]
    if not entitlements.has_feature(ctx.conn, org, "feature.advanced_accounting"):
        raise Conflict("Accounting rules are not included in the current plan.")


def create_rule(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    require_config(ctx, lock=True)
    _guard_rule_limits(ctx)
    kind, conditions, action = body["kind"], body["conditions"], body["action"]
    _validate_rule(ctx, kind, conditions, action)
    before = entitlements.company_usage(ctx.conn, org, co)
    rid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_rules (id, organization_id, company_id, rule_key, version, name, kind, priority, conditions, action, effective_from,"
            " effective_to, is_active, created_by) VALUES (:id, :o, :c, :id, 1, :n, :k, :p, CAST(:cd AS jsonb), CAST(:ac AS jsonb), :ef, :et, :act, :u)"
        ),
        {
            "id": rid,
            "o": org,
            "c": co,
            "n": body["name"],
            "k": kind,
            "p": body.get("priority", 0),
            "cd": json.dumps(conditions),
            "ac": json.dumps(action),
            "ef": body.get("effective_from"),
            "et": body.get("effective_to"),
            "act": body.get("is_active", True),
            "u": ctx.user_id,
        },
    )
    entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.accounting_rules_per_company"])
    audit_event(ctx, "accounting.rule_created", "acct_rule", rid, {"name": body["name"], "kind": kind})
    out = ser(must(ctx, f"SELECT {RULE_COLS} FROM acct_rules WHERE id = :i", i=rid))
    out["warnings"] = [
        f"Another active rule of the same kind and priority gives a different result: {n}. Transactions matching both will be flagged for review."
        for n in _rule_conflicts(ctx, kind, body.get("priority", 0), action, rid)
    ]
    return out


def update_rule(ctx: RequestCtx, rule_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    """A change creates a NEW version; earlier versions stay as history (transactions record which version fired)."""
    org, co = _co(ctx)
    require_config(ctx, lock=True)
    cur = one(
        ctx,
        f"SELECT {RULE_COLS} FROM acct_rules WHERE company_id = :c AND id = :i AND is_current FOR UPDATE",
        c=co,
        i=rule_id,
    )
    if cur is None:
        raise NotFound("Rule not found (or it is not the current version).")
    merged = {
        k: body.get(k, cur[k])
        for k in ("name", "kind", "priority", "conditions", "action", "effective_from", "effective_to", "is_active")
    }
    merged["kind"] = cur["kind"]
    _validate_rule(ctx, merged["kind"], merged["conditions"], merged["action"])
    before = entitlements.company_usage(ctx.conn, org, co)
    ctx.conn.execute(text("UPDATE acct_rules SET is_current = false WHERE id = :i"), {"i": rule_id})
    nid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO acct_rules (id, organization_id, company_id, rule_key, version, name, kind, priority, conditions, action, effective_from,"
            " effective_to, is_active, created_by) VALUES (:id, :o, :c, :key, :v, :n, :k, :p, CAST(:cd AS jsonb), CAST(:ac AS jsonb), :ef, :et, :act, :u)"
        ),
        {
            "id": nid,
            "o": org,
            "c": co,
            "key": cur["rule_key"],
            "v": cur["version"] + 1,
            "n": merged["name"],
            "k": merged["kind"],
            "p": merged["priority"],
            "cd": json.dumps(merged["conditions"]),
            "ac": json.dumps(merged["action"]),
            "ef": merged["effective_from"],
            "et": merged["effective_to"],
            "act": merged["is_active"],
            "u": ctx.user_id,
        },
    )
    entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.accounting_rules_per_company"])
    audit_event(
        ctx,
        "accounting.rule_updated",
        "acct_rule",
        nid,
        {"rule_key": str(cur["rule_key"]), "version": cur["version"] + 1},
    )
    out = ser(must(ctx, f"SELECT {RULE_COLS} FROM acct_rules WHERE id = :i", i=nid))
    out["warnings"] = [
        f"Another active rule of the same kind and priority gives a different result: {n}."
        for n in _rule_conflicts(ctx, merged["kind"], merged["priority"], merged["action"], cur["rule_key"])
    ]
    return out


def rule_history(ctx: RequestCtx, rule_key: uuid.UUID) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            f"SELECT {RULE_COLS} FROM acct_rules WHERE company_id = :c AND rule_key = :k ORDER BY version DESC",
            c=co,
            k=rule_key,
        )
    )


def load_rules(ctx: RequestCtx) -> list[core.Rule]:
    _, co = _co(ctx)
    out: list[core.Rule] = []
    for r in ctx.conn.execute(
        text(f"SELECT {RULE_COLS} FROM acct_rules WHERE company_id = :c AND is_current AND is_active"), {"c": co}
    ):
        out.append(
            core.Rule(
                str(r.id),
                str(r.rule_key),
                r.name,
                r.kind,
                r.priority,
                tuple(r.conditions),
                dict(r.action),
                r.effective_from,
                r.effective_to,
                r.is_active,
                r.version,
            )
        )
    return out
