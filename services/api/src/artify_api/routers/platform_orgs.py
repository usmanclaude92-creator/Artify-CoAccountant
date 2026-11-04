"""Super Admin: organizations (accounting firms and independent accountants), companies, global search.

Everything here is metadata or aggregates. The platform database role has no policy on tenant
business tables; the overview rows come from definer-owned views (migration 0004).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from artify_api.auth_service import _slugify
from artify_api.context import PlatformCtx
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.platform_usage import SubscriptionOut, UsageRow, fetch_subscription, usage_for_orgs
from artify_api.routers.platform_common import (
    Limit,
    Offset,
    Page,
    Sensitive,
    like,
    order_clause,
    padmin,
    paudit,
    require_confirmation,
)
from artify_api.security.tokens import new_opaque_token
from artify_api.settings import get_settings

router = APIRouter(prefix="/platform", tags=["platform-organizations"])


class OrgRow(BaseModel):
    id: uuid.UUID
    type: Literal["accounting_firm", "independent_accountant"]
    display_name: str
    legal_name: str
    slug: str
    country: str
    default_currency: str
    locale: str
    status: Literal["active", "suspended", "closed"]
    created_at: datetime
    owner_email: str | None
    owner_name: str | None
    user_count: int
    accountant_count: int
    company_count: int
    company_total: int
    plan_key: str | None = None
    plan_name: str | None = None
    subscription_status: str | None = None
    trial_ends_at: datetime | None = None


class StatusEventOut(BaseModel):
    id: uuid.UUID
    from_status: str
    to_status: str
    reason: str
    actor_id: uuid.UUID
    created_at: datetime


class CompanyRow(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    name: str
    code: str
    country: str
    base_currency: str
    status: str
    created_at: datetime
    accountant_count: int
    user_count: int
    documents_processed: int | None = None  # None = not metered yet (documents arrive in Phase 5+)
    storage_bytes: int | None = None


class OrgDetail(BaseModel):
    organization: OrgRow
    subscription: SubscriptionOut | None
    usage: list[UsageRow]
    status_history: list[StatusEventOut]


class OrgCreate(BaseModel):
    type: Literal["accounting_firm", "independent_accountant"]
    name: str = Field(min_length=2, max_length=200)
    country: str = Field(pattern="^[A-Z]{2}$")
    default_currency: str = Field(pattern="^[A-Z]{3}$")
    locale: Literal["en", "ar"] = "en"
    owner_email: EmailStr
    plan_key: str | None = Field(default=None, pattern="^[a-z0-9_]+$")


class OrgUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)


class TimelineEntry(BaseModel):
    at: datetime
    kind: str
    title: str
    detail: str | None = None
    actor_id: uuid.UUID | None = None


class SearchHit(BaseModel):
    kind: Literal["organization", "company", "administrator"]
    id: uuid.UUID
    title: str
    subtitle: str | None = None


_ORG_SELECT = (
    "SELECT v.id, v.type, v.display_name, v.legal_name, v.slug::text AS slug, v.country, v.default_currency, v.locale,"
    " v.status, v.created_at, v.owner_email, v.owner_name, v.user_count, v.accountant_count, v.company_count,"
    " v.company_total, p.key AS plan_key, p.name AS plan_name, s.status AS subscription_status, s.trial_ends_at"
    " FROM platform_org_overview v LEFT JOIN subscriptions s ON s.organization_id = v.id"
    " LEFT JOIN subscription_plan_versions pv ON pv.id = s.plan_version_id"
    " LEFT JOIN subscription_plans p ON p.id = pv.plan_id"
)
_ORG_SORT = {
    "name": "v.display_name",
    "created": "v.created_at",
    "companies": "v.company_count",
    "users": "v.user_count",
    "status": "v.status",
}


def _org_row(ctx: PlatformCtx, org_id: uuid.UUID) -> OrgRow:
    r = ctx.conn.execute(text(f"{_ORG_SELECT} WHERE v.id = :i"), {"i": org_id}).mappings().first()
    if r is None:
        raise NotFound("Organization not found.")
    return OrgRow(**r)


@router.get("/organizations", response_model=Page[OrgRow], operation_id="platform_organizations")
def list_organizations(
    ctx: Annotated[PlatformCtx, padmin("platform.organizations.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    type: Literal["accounting_firm", "independent_accountant"] | None = None,
    status: Literal["active", "suspended", "closed"] | None = None,
    subscription_status: str | None = None,
    plan: Annotated[str | None, Query(max_length=60)] = None,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[OrgRow]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if pattern := like(q):
        where.append(
            "(v.display_name ILIKE :q ESCAPE '\\' OR v.legal_name ILIKE :q ESCAPE '\\'"
            " OR v.slug::text ILIKE :q ESCAPE '\\' OR v.owner_email ILIKE :q ESCAPE '\\')"
        )
        params["q"] = pattern
    for column, value, key in (
        ("v.type", type, "type"),
        ("v.status", status, "status"),
        ("s.status", subscription_status, "sstatus"),
        ("p.key", plan, "plan"),
    ):
        if value:
            where.append(f"{column} = :{key}")
            params[key] = value
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    total = ctx.conn.execute(text(f"SELECT count(*) FROM ({_ORG_SELECT}{clause}) t"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                f"{_ORG_SELECT}{clause} ORDER BY {order_clause(sort, order, _ORG_SORT, 'created')} LIMIT :l OFFSET :o"
            ),
            {**params, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return Page[OrgRow](items=[OrgRow(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/organizations/{org_id}", response_model=OrgDetail, operation_id="platform_organization_get")
def get_organization(
    org_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.organizations.read")]
) -> OrgDetail:
    org = _org_row(ctx, org_id)
    sub = fetch_subscription(ctx.conn, org_id=org_id)
    history = (
        ctx.conn.execute(
            text(
                "SELECT id, from_status, to_status, reason, actor_id, created_at FROM organization_status_events"
                " WHERE organization_id = :o ORDER BY created_at DESC"
            ),
            {"o": org_id},
        )
        .mappings()
        .all()
    )
    return OrgDetail(
        organization=org,
        subscription=SubscriptionOut(**sub) if sub else None,
        usage=usage_for_orgs(ctx.conn, [org_id])[org_id],
        status_history=[StatusEventOut(**h) for h in history],
    )


@router.post("/organizations", response_model=OrgDetail, status_code=201, operation_id="platform_organization_create")
def create_organization(
    body: OrgCreate, ctx: Annotated[PlatformCtx, padmin("platform.organizations.manage")]
) -> OrgDetail:
    """Provision an organization for a customer: organization + trial subscription + a pending
    firm-owner invitation, atomically. The owner sets their own password and enrolls MFA on first sign-in;
    nobody at Artify ever knows or sets a customer password."""
    plan_key = (
        body.plan_key
        or ctx.conn.execute(
            text("SELECT value #>> '{}' FROM platform_settings WHERE key = 'default_plan_key'")
        ).scalar()
    )
    version = ctx.conn.execute(
        text(
            "SELECT v.id, v.trial_days FROM subscription_plan_versions v JOIN subscription_plans p ON p.id = v.plan_id"
            " WHERE p.key = :k AND p.status = 'active' ORDER BY v.version DESC LIMIT 1"
        ),
        {"k": plan_key},
    ).first()
    if version is None:
        raise Unprocessable("The plan does not exist or is not active.")
    org_id, inv_id, sub_id = new_id(), new_id(), new_id()
    token, digest = new_opaque_token()
    try:
        ctx.conn.execute(
            text(
                "SELECT app.platform_provision_organization(:org, :t, :n, :n, :slug, :c, :cur, :l, :pv, :td,"
                " :email, :inv, :sub, :tok, now() + make_interval(secs => :ttl))"
            ),
            {
                "org": org_id,
                "t": body.type,
                "n": body.name,
                "slug": _slugify(body.name),
                "c": body.country,
                "cur": body.default_currency,
                "l": body.locale,
                "pv": version.id,
                "td": version.trial_days,
                "email": str(body.owner_email),
                "inv": inv_id,
                "sub": sub_id,
                "tok": digest,
                "ttl": get_settings().invitation_ttl_seconds,
            },
        )
    except IntegrityError as exc:
        raise Conflict("The organization could not be created (check the currency code or retry).") from exc
    paudit(
        ctx,
        "organization.provisioned",
        target_type="organization",
        target_id=org_id,
        org=org_id,
        summary={"type": body.type, "plan": plan_key, "owner_email_domain": str(body.owner_email).split("@")[-1]},
    )
    get_mailer().send(Message(to=str(body.owner_email), template="invitation", context={"token": token}))
    return get_organization(org_id, ctx)


@router.patch("/organizations/{org_id}", response_model=OrgRow, operation_id="platform_organization_update")
def update_organization(
    org_id: uuid.UUID, body: OrgUpdate, ctx: Annotated[PlatformCtx, padmin("platform.organizations.manage")]
) -> OrgRow:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise Unprocessable("Provide display_name and/or legal_name.")
    sets = ", ".join(f"{k} = :{k}" for k in changes)  # keys come from the fixed pydantic model
    res = ctx.conn.execute(
        text(f"UPDATE organizations SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
        {**changes, "id": org_id},
    )
    if not res.rowcount:
        raise NotFound("Organization not found.")
    paudit(
        ctx,
        "organization.updated",
        target_type="organization",
        target_id=org_id,
        org=org_id,
        summary={"fields": sorted(changes)},
    )
    return _org_row(ctx, org_id)


def _transition(
    ctx: PlatformCtx, org_id: uuid.UUID, body: Sensitive, *, to: str, allowed_from: str, action: str
) -> OrgRow:
    org = _org_row(ctx, org_id)
    require_confirmation(body.confirm, org.slug)
    if org.status != allowed_from:
        raise Conflict(f"The organization is {org.status}; it can only move to {to} from {allowed_from}.")
    ctx.conn.execute(
        text("UPDATE organizations SET status = :s, updated_at = now(), row_version = row_version + 1 WHERE id = :i"),
        {"s": to, "i": org_id},
    )
    ctx.conn.execute(
        text(
            "INSERT INTO organization_status_events (id, organization_id, from_status, to_status, reason, actor_id)"
            " VALUES (:id, :o, :f, :t, :r, :a)"
        ),
        {"id": new_id(), "o": org_id, "f": org.status, "t": to, "r": body.reason, "a": ctx.admin_id},
    )
    paudit(
        ctx,
        action,
        target_type="organization",
        target_id=org_id,
        org=org_id,
        reason=body.reason,
        summary={"from": org.status, "to": to},
    )
    return _org_row(ctx, org_id)


@router.post("/organizations/{org_id}/suspend", response_model=OrgRow, operation_id="platform_organization_suspend")
def suspend_organization(
    org_id: uuid.UUID, body: Sensitive, ctx: Annotated[PlatformCtx, padmin("platform.organizations.suspend")]
) -> OrgRow:
    """Suspension makes the organization read-only (Phase 1 behaviour); data is preserved."""
    return _transition(ctx, org_id, body, to="suspended", allowed_from="active", action="organization.suspended")


@router.post(
    "/organizations/{org_id}/reactivate", response_model=OrgRow, operation_id="platform_organization_reactivate"
)
def reactivate_organization(
    org_id: uuid.UUID, body: Sensitive, ctx: Annotated[PlatformCtx, padmin("platform.organizations.suspend")]
) -> OrgRow:
    return _transition(ctx, org_id, body, to="active", allowed_from="suspended", action="organization.reactivated")


@router.get(
    "/organizations/{org_id}/history", response_model=list[TimelineEntry], operation_id="platform_organization_history"
)
def organization_history(
    org_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.organizations.read")], limit: Limit = 100
) -> list[TimelineEntry]:
    _org_row(ctx, org_id)
    rows = ctx.conn.execute(
        text(
            "SELECT * FROM ("
            " SELECT created_at AS at, 'registered' AS kind, 'Organization registered' AS title, NULL::text AS detail,"
            "        NULL::uuid AS actor_id FROM organizations WHERE id = :o"
            " UNION ALL SELECT created_at, 'status', 'Status ' || from_status || ' → ' || to_status, reason, actor_id"
            "   FROM organization_status_events WHERE organization_id = :o"
            " UNION ALL SELECT created_at, 'subscription', replace(event_type, '_', ' ') ||"
            "   COALESCE(': ' || from_status || ' → ' || to_status, ''), reason, actor_id"
            "   FROM subscription_events WHERE organization_id = :o"
            " UNION ALL SELECT created_at, 'admin_action', action, reason, actor_id"
            "   FROM platform_audit_events WHERE target_organization = :o AND outcome = 'success'"
            ") t ORDER BY at DESC LIMIT :l"
        ),
        {"o": org_id, "l": limit},
    ).mappings()
    return [TimelineEntry(**r) for r in rows]


# ------------------------------------------------------------------ companies (overview only)
_COMPANY_SORT = {
    "name": "c.name",
    "created": "c.created_at",
    "organization": "c.organization_name",
    "status": "c.status",
}


@router.get("/companies", response_model=Page[CompanyRow], operation_id="platform_companies")
def list_companies(
    ctx: Annotated[PlatformCtx, padmin("platform.companies.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    organization_id: uuid.UUID | None = None,
    status: Literal["active", "suspended", "archived"] | None = None,
    country: Annotated[str | None, Query(pattern="^[A-Z]{2}$")] = None,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[CompanyRow]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if pattern := like(q):
        where.append(
            "(c.name ILIKE :q ESCAPE '\\' OR c.code ILIKE :q ESCAPE '\\' OR c.organization_name ILIKE :q ESCAPE '\\')"
        )
        params["q"] = pattern
    for column, value, key in (
        ("c.organization_id", organization_id, "org"),
        ("c.status", status, "status"),
        ("c.country", country, "country"),
    ):
        if value:
            where.append(f"{column} = :{key}")
            params[key] = value
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    base = (
        "SELECT c.*, (SELECT value FROM usage_counters u WHERE u.scope_key = 'company:' || c.id::text"
        "   AND u.metric = 'quota.documents_per_month' ORDER BY u.period_key DESC LIMIT 1) AS documents_processed,"
        " (SELECT value FROM usage_counters u WHERE u.scope_key = 'company:' || c.id::text"
        "   AND u.metric = 'quota.storage_bytes' AND u.period_key = 'all') AS storage_bytes"
        " FROM platform_company_overview c"
    )
    total = ctx.conn.execute(text(f"SELECT count(*) FROM platform_company_overview c{clause}"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                f"SELECT * FROM ({base}) c{clause} ORDER BY {order_clause(sort, order, _COMPANY_SORT, 'created')} LIMIT :l OFFSET :o"
            ),
            {**params, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return Page[CompanyRow](items=[CompanyRow(**r) for r in rows], total=total, limit=limit, offset=offset)


# ------------------------------------------------------------------ global search
@router.get("/search", response_model=list[SearchHit], operation_id="platform_search")
def search(
    ctx: Annotated[PlatformCtx, padmin("platform.dashboard.read")],
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[SearchHit]:
    pattern = like(q)
    hits: list[SearchHit] = []
    if ctx.can("platform.organizations.read"):
        for r in ctx.conn.execute(
            text(
                "SELECT id, display_name, type, owner_email FROM platform_org_overview WHERE display_name ILIKE :q ESCAPE '\\'"
                " OR legal_name ILIKE :q ESCAPE '\\' OR slug::text ILIKE :q ESCAPE '\\' OR owner_email ILIKE :q ESCAPE '\\'"
                " ORDER BY display_name LIMIT 6"
            ),
            {"q": pattern},
        ):
            hits.append(
                SearchHit(
                    kind="organization",
                    id=r.id,
                    title=r.display_name,
                    subtitle=f"{r.type.replace('_', ' ')} · {r.owner_email or 'owner invited'}",
                )
            )
    if ctx.can("platform.companies.read"):
        for r in ctx.conn.execute(
            text(
                "SELECT id, name, organization_name, code FROM platform_company_overview WHERE name ILIKE :q ESCAPE '\\'"
                " OR code ILIKE :q ESCAPE '\\' ORDER BY name LIMIT 6"
            ),
            {"q": pattern},
        ):
            hits.append(SearchHit(kind="company", id=r.id, title=r.name, subtitle=f"{r.organization_name} · {r.code}"))
    if ctx.can("platform.admins.read"):
        for r in ctx.conn.execute(
            text(
                "SELECT id, display_name, email, role_key FROM platform_admins WHERE display_name ILIKE :q ESCAPE '\\' OR email::text ILIKE :q ESCAPE '\\' LIMIT 4"
            ),
            {"q": pattern},
        ):
            hits.append(
                SearchHit(kind="administrator", id=r.id, title=r.display_name, subtitle=f"{r.email} · {r.role_key}")
            )
    return hits


__all__ = ["Depends", "router"]
