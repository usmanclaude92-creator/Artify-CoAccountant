"""Firm Control Center API: the customer-facing management surface of ONE organization.

Everything here is scoped by the organization in the path (validated against the caller's memberships) and by
row-level security; a caller only ever sees rows of the companies they may reach. Sections the caller has no
permission for are omitted from responses (never faked). No platform bypass exists on these routes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from artify_api import audit, entitlements
from artify_api import workspace as ws
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Forbidden, NotFound, Unprocessable
from artify_api.permissions import ACCOUNTANT_ROLES, SYSTEM_ROLES
from artify_api.platform_usage import UsageRow, usage_for_orgs
from artify_api.policy import authorize
from artify_api.routers.common import Limit, Offset, Page, like, order_clause
from artify_api.settings import get_settings

router = APIRouter(tags=["control-center"])

NOTIFICATION_KEYS = ("invitations", "subscription_alerts", "support_requests", "security_events")
ENFORCED_KEYS = ("limit.companies", "limit.users", "limit.accountants", "limit.devices")


# ------------------------------------------------------------------ shared helpers
def email_delivery() -> dict[str, Any]:
    """Be honest about whether invitation e-mails can actually leave the system."""
    backend = get_settings().mailer_backend
    return {
        "configured": backend == "smtp",
        "backend": backend,
        "note": (
            "E-mail is delivered through the configured SMTP server (best effort)."
            if backend == "smtp"
            else "No e-mail server is configured: invitations are created, but nothing is sent. "
            "The platform operator must configure SMTP before invitees receive messages."
        ),
    }


def _can(ctx: RequestCtx, permission: str) -> bool:
    assert ctx.principal is not None
    return authorize(ctx.principal, permission).allowed


def _live_usage(ctx: RequestCtx, org_id: uuid.UUID) -> list[UsageRow]:
    """Entitlement rows for this organization; enforceable count limits use a LIVE measurement."""
    measured = {k: entitlements.measure(ctx.conn, org_id, k) for k in (*ENFORCED_KEYS, *sorted(entitlements.QUOTAS))}
    return usage_for_orgs(ctx.conn, [org_id], measured={org_id: measured})[org_id]


# ------------------------------------------------------------------ dashboard
class CompanyCounts(BaseModel):
    total: int
    active: int
    inactive: int
    archived: int


class TeamCounts(BaseModel):
    members_active: int
    members_suspended: int
    accountants_active: int


class DashSubscription(BaseModel):
    plan_name: str
    status: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None


class UsageBrief(BaseModel):
    key: str
    limit: int | None
    unlimited: bool
    current: int | None
    percent: float | None
    status: str


class OrgActivityRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    company_id: uuid.UUID | None
    company_name: str | None
    entity_type: str | None
    entity_id: str | None
    summary: dict[str, Any]


class Viewer(BaseModel):
    user_id: uuid.UUID
    role: str | None
    reaches_all_companies: bool
    permissions: list[str]


class DashboardOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    organization_status: str
    viewer: Viewer
    companies: CompanyCounts  # only companies the viewer may see
    assigned_to_me: int
    team: TeamCounts | None
    pending_invitations: int | None
    subscription: DashSubscription | None
    usage: list[UsageBrief] | None
    recent_activity: list[OrgActivityRow] | None
    email_delivery: dict[str, Any]


_ACTIVITY_SELECT = (
    "SELECT e.id, e.created_at, e.action, e.actor_id, u.display_name AS actor_name, e.company_id,"
    " c.name AS company_name, e.entity_type, e.entity_id, e.summary FROM audit_events e"
    " LEFT JOIN users u ON u.id = e.actor_id LEFT JOIN companies c ON c.id = e.company_id"
)


@router.get("/orgs/{org_id}/dashboard", response_model=DashboardOut, operation_id="control_center_dashboard")
def dashboard(org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("organization.read"))]) -> DashboardOut:
    assert ctx.principal is not None
    c = ctx.conn
    org = c.execute(text("SELECT display_name, type, status FROM organizations WHERE id = :o"), {"o": org_id}).one()
    by_status = {
        r.status: r.n
        for r in c.execute(
            text("SELECT status, count(*) AS n FROM companies WHERE organization_id = :o GROUP BY status"),
            {"o": org_id},
        )
    }
    counts = CompanyCounts(
        total=sum(by_status.values()),
        active=by_status.get("active", 0),
        inactive=by_status.get("suspended", 0),
        archived=by_status.get("archived", 0),
    )
    mine = c.execute(
        text(
            "SELECT count(*) FROM accountant_assignments WHERE organization_id = :o AND user_id = :u AND starts_on <= current_date"
            " AND (ends_on IS NULL OR ends_on >= current_date)"
        ),
        {"o": org_id, "u": ctx.user_id},
    ).scalar_one()
    team = None
    if _can(ctx, "members.read"):
        row = c.execute(
            text(
                "SELECT count(*) FILTER (WHERE m.status = 'active') AS active, count(*) FILTER (WHERE m.status = 'suspended') AS susp,"
                " count(*) FILTER (WHERE m.status = 'active' AND r.key = ANY(:acct)) AS acct"
                " FROM organization_memberships m JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o"
            ),
            {"o": org_id, "acct": sorted(ACCOUNTANT_ROLES)},
        ).one()
        team = TeamCounts(members_active=row.active, members_suspended=row.susp, accountants_active=row.acct)
    pending = None
    if _can(ctx, "invitations.manage"):
        pending = c.execute(
            text(
                "SELECT count(*) FROM invitations WHERE organization_id = :o AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()"
            ),
            {"o": org_id},
        ).scalar_one()
    sub = usage = None
    if _can(ctx, "entitlements.read"):
        srow = (
            c.execute(
                text(
                    "SELECT p.name AS plan_name, s.status, s.trial_ends_at, s.current_period_end FROM subscriptions s"
                    " JOIN subscription_plan_versions v ON v.id = s.plan_version_id JOIN subscription_plans p ON p.id = v.plan_id"
                    " WHERE s.organization_id = :o"
                ),
                {"o": org_id},
            )
            .mappings()
            .first()
        )
        sub = DashSubscription(**srow) if srow else None
        usage = [
            UsageBrief(
                key=r.key, limit=r.limit, unlimited=r.unlimited, current=r.current, percent=r.percent, status=r.status
            )
            for r in _live_usage(ctx, org_id)
            if r.key in ENFORCED_KEYS
        ]
    activity = None
    if _can(ctx, "audit.read"):
        activity = [
            OrgActivityRow(**r)
            for r in c.execute(
                text(f"{_ACTIVITY_SELECT} WHERE e.organization_id = :o ORDER BY e.created_at DESC, e.id DESC LIMIT 8"),
                {"o": org_id},
            ).mappings()
        ]
    role = ctx.principal.org_role
    perms = sorted(SYSTEM_ROLES[role][2]) if role else []
    return DashboardOut(
        organization_id=org_id,
        organization_name=org.display_name,
        organization_type=org.type,
        organization_status=org.status,
        viewer=Viewer(
            user_id=ctx.user_id,
            role=role,
            reaches_all_companies=ctx.principal.company_scope == "all",
            permissions=perms,
        ),
        companies=counts,
        assigned_to_me=mine,
        team=team,
        pending_invitations=pending,
        subscription=sub,
        usage=usage,
        recent_activity=activity,
        email_delivery=email_delivery(),
    )


# ------------------------------------------------------------------ organization settings
class OrgSettingsOut(BaseModel):
    id: uuid.UUID
    type: str
    legal_name: str
    display_name: str
    slug: str
    country: str
    default_currency: str
    locale: str
    timezone: str
    contact_email: str | None
    contact_phone: str | None
    address_line: str | None
    city: str | None
    notification_preferences: dict[str, bool]
    status: str
    email_delivery: dict[str, Any]
    notifications_sent: bool  # false until a notification framework exists (Phase 3 stores preferences only)


class OrgSettingsUpdate(BaseModel):
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    address_line: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=60)
    default_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    notification_preferences: dict[str, bool] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and v not in available_timezones():
            raise ValueError(f"unknown time zone {v}")
        return v

    @field_validator("notification_preferences")
    @classmethod
    def _prefs(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v is not None and not set(v) <= set(NOTIFICATION_KEYS):
            raise ValueError(f"unknown notification preference; allowed: {', '.join(NOTIFICATION_KEYS)}")
        return v


_SETTINGS_SQL = (
    "SELECT id, type, legal_name, display_name, slug::text AS slug, country, default_currency, locale, timezone,"
    " contact_email, contact_phone, address_line, city, notification_preferences, status FROM organizations WHERE id = :o"
)


def _settings(ctx: RequestCtx, org_id: uuid.UUID) -> OrgSettingsOut:
    row = ctx.conn.execute(text(_SETTINGS_SQL), {"o": org_id}).mappings().first()
    if row is None:
        raise NotFound("Organization not found.")
    prefs = {k: bool(row["notification_preferences"].get(k, True)) for k in NOTIFICATION_KEYS}
    return OrgSettingsOut(
        **{**row, "notification_preferences": prefs}, email_delivery=email_delivery(), notifications_sent=False
    )


@router.get("/orgs/{org_id}/settings", response_model=OrgSettingsOut, operation_id="control_center_settings")
def get_settings_view(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("organization.read"))]
) -> OrgSettingsOut:
    return _settings(ctx, org_id)


@router.patch("/orgs/{org_id}/settings", response_model=OrgSettingsOut, operation_id="control_center_update_settings")
def update_settings(
    org_id: uuid.UUID,
    body: OrgSettingsUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("organization.settings.update"))],
) -> OrgSettingsOut:
    """Organization-level contact, regional and notification settings. These never overwrite any company's own
    currency, financial year, VAT or configuration (each company owns those). The organization's name, display
    name and language are changed through `PATCH /orgs/{id}` (owner only)."""
    changes = body.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise Unprocessable("Provide at least one setting.")
    if "notification_preferences" in changes:
        current = _settings(ctx, org_id).notification_preferences
        changes["notification_preferences"] = {**current, **changes["notification_preferences"]}
    import json

    sets = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k == "notification_preferences" else f"{k} = :{k}" for k in changes
    )
    params = {**changes, "id": org_id}
    if "notification_preferences" in params:
        params["notification_preferences"] = json.dumps(params["notification_preferences"])
    try:
        ctx.conn.execute(
            text(f"UPDATE organizations SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
            params,
        )
    except IntegrityError as exc:
        raise Unprocessable("The settings are not valid (check the currency code).") from exc
    audit.write_audit(
        ctx.conn,
        action="organization.settings_updated",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="organization",
        entity_id=org_id,
        summary={"fields": sorted(changes)},
        meta=ctx.meta,
    )
    return _settings(ctx, org_id)


# ------------------------------------------------------------------ team
class TeamMember(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: Literal["active", "suspended", "removed"]
    joined_at: datetime
    last_activity_at: datetime | None
    job_title: str | None
    phone: str | None
    license_number: str | None
    assigned_company_count: int | None  # None when the viewer cannot see every company


_TEAM_SORT = {
    "name": "u.display_name",
    "role": "r.key",
    "status": "m.status",
    "joined": "m.created_at",
    "activity": "last_activity_at",
}
_TEAM_SELECT = (
    "SELECT u.id AS user_id, u.email::text AS email, u.display_name, r.key AS role, m.status, m.created_at AS joined_at,"
    " (SELECT max(e.created_at) FROM audit_events e WHERE e.actor_id = u.id AND e.organization_id = m.organization_id) AS last_activity_at,"
    " p.job_title, p.phone, p.license_number,"
    " (SELECT count(*) FROM accountant_assignments a WHERE a.user_id = u.id AND a.organization_id = m.organization_id"
    "   AND a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date)) AS assigned_company_count"
    " FROM organization_memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id"
    " LEFT JOIN member_profiles p ON p.organization_id = m.organization_id AND p.user_id = m.user_id"
)


def _team_rows(
    ctx: RequestCtx, where: str, params: dict[str, Any], order: str, limit: int, offset: int
) -> tuple[int, list[TeamMember]]:
    assert ctx.principal is not None
    total = ctx.conn.execute(text(f"SELECT count(*) FROM ({_TEAM_SELECT} WHERE {where}) t"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(f"{_TEAM_SELECT} WHERE {where} ORDER BY {order} LIMIT :l OFFSET :o"),
            {**params, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    full_view = ctx.principal.company_scope == "all"
    items = [
        TeamMember(**{**r, "assigned_company_count": r["assigned_company_count"] if full_view else None}) for r in rows
    ]
    return total, items


@router.get("/orgs/{org_id}/team", response_model=Page[TeamMember], operation_id="control_center_team")
def team(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("members.read"))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["active", "suspended", "removed"] | None = None,
    role: Annotated[str | None, Query(max_length=40)] = None,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[TeamMember]:
    where = ["m.organization_id = :org"]
    params: dict[str, Any] = {"org": org_id}
    if pattern := like(q):
        where.append("(u.display_name ILIKE :q ESCAPE '\\' OR u.email::text ILIKE :q ESCAPE '\\')")
        params["q"] = pattern
    if status:
        where.append("m.status = :status")
        params["status"] = status
    if role:
        where.append("r.key = :role")
        params["role"] = role
    total, items = _team_rows(
        ctx, " AND ".join(where), params, order_clause(sort, order, _TEAM_SORT, "name"), limit, offset
    )
    return Page[TeamMember](items=items, total=total, limit=limit, offset=offset)


class ProfileUpdate(BaseModel):
    job_title: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    license_number: str | None = Field(default=None, max_length=60)


@router.patch(
    "/orgs/{org_id}/members/{user_id}/profile", response_model=TeamMember, operation_id="control_center_member_profile"
)
def update_member_profile(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ProfileUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("organization.read"))],
) -> TeamMember:
    """Professional details (job title, phone, licence). They are descriptive only and never grant or limit access:
    the organization role does. A person may edit their own; `member_profiles.manage` edits anyone's."""
    if user_id != ctx.user_id and not _can(ctx, "member_profiles.manage"):
        raise Forbidden("You can only edit your own professional profile.")
    if not ctx.conn.execute(
        text("SELECT 1 FROM organization_memberships WHERE organization_id = :o AND user_id = :u"),
        {"o": org_id, "u": user_id},
    ).first():
        raise NotFound("Member not found.")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise Unprocessable("Provide at least one field.")
    cols = ", ".join(changes)
    marks = ", ".join(f":{k}" for k in changes)
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in changes)
    ctx.conn.execute(
        text(
            f"INSERT INTO member_profiles (organization_id, user_id, {cols}, updated_by, updated_at) VALUES (:o, :u, {marks}, :by, now())"
            f" ON CONFLICT (organization_id, user_id) DO UPDATE SET {sets}, updated_by = :by, updated_at = now()"
        ),
        {**changes, "o": org_id, "u": user_id, "by": ctx.user_id},
    )
    audit.write_audit(
        ctx.conn,
        action="member.profile_updated",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="user",
        entity_id=user_id,
        summary={"fields": sorted(changes)},
        meta=ctx.meta,
    )
    _, items = _team_rows(ctx, "m.organization_id = :org AND m.user_id = :u", {"org": org_id, "u": user_id}, "1", 1, 0)
    return items[0]


# ------------------------------------------------------------------ accountants and assignments
class AssignedCompany(BaseModel):
    company_id: uuid.UUID
    company_name: str
    company_status: str
    assignment_type: str
    responsibility: str | None
    starts_on: date
    ends_on: date | None
    active: bool


class AccountantRow(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: Literal["active", "suspended", "removed"]
    job_title: str | None
    license_number: str | None
    active_company_count: int
    assignments: list[AssignedCompany]


def _assigned(ctx: RequestCtx, org_id: uuid.UUID, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AssignedCompany]]:
    out: dict[uuid.UUID, list[AssignedCompany]] = {u: [] for u in user_ids}
    if not user_ids:
        return out
    for r in ctx.conn.execute(
        text(
            "SELECT a.user_id, c.id AS company_id, c.name AS company_name, c.status AS company_status, a.assignment_type, a.responsibility,"
            " a.starts_on, a.ends_on, (a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date)) AS active"
            " FROM accountant_assignments a JOIN companies c ON c.id = a.company_id"
            " WHERE a.organization_id = :o AND a.user_id = ANY(CAST(:u AS uuid[])) ORDER BY c.name"
        ),
        {"o": org_id, "u": user_ids},
    ).mappings():
        out[r["user_id"]].append(AssignedCompany(**{k: v for k, v in r.items() if k != "user_id"}))
    return out


def _accountants(
    ctx: RequestCtx, org_id: uuid.UUID, extra: str, params: dict[str, Any], limit: int, offset: int
) -> tuple[int, list[AccountantRow]]:
    base = (
        "FROM organization_memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id"
        " LEFT JOIN member_profiles p ON p.organization_id = m.organization_id AND p.user_id = m.user_id"
        f" WHERE m.organization_id = :o AND r.key = ANY(:roles) {extra}"
    )
    p = {**params, "o": org_id, "roles": sorted(ACCOUNTANT_ROLES)}
    total = ctx.conn.execute(text(f"SELECT count(*) {base}"), p).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                f"SELECT u.id AS user_id, u.email::text AS email, u.display_name, r.key AS role, m.status, p.job_title, p.license_number {base} ORDER BY u.display_name LIMIT :l OFFSET :off"
            ),
            {**p, "l": limit, "off": offset},
        )
        .mappings()
        .all()
    )
    assigned = _assigned(ctx, org_id, [r["user_id"] for r in rows])
    return total, [
        AccountantRow(
            **r,
            active_company_count=sum(1 for a in assigned[r["user_id"]] if a.active and a.company_status != "archived"),
            assignments=assigned[r["user_id"]],
        )
        for r in rows
    ]


@router.get("/orgs/{org_id}/accountants", response_model=Page[AccountantRow], operation_id="control_center_accountants")
def accountants(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("members.read"))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["active", "suspended", "removed"] | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[AccountantRow]:
    """Professional staff (accountant, assistant accountant, staff) with their assigned companies and workload.
    The professional profile (job title, licence) is separate from the authorization role shown in `role`.
    Assignment lists show only companies the viewer may see."""
    extra, params = "", {}
    if pattern := like(q):
        extra += " AND (u.display_name ILIKE :q ESCAPE '\\' OR u.email::text ILIKE :q ESCAPE '\\')"
        params["q"] = pattern
    if status:
        extra += " AND m.status = :status"
        params["status"] = status
    total, items = _accountants(ctx, org_id, extra, params, limit, offset)
    return Page[AccountantRow](items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/orgs/{org_id}/accountants/{user_id}", response_model=AccountantRow, operation_id="control_center_accountant"
)
def accountant(
    org_id: uuid.UUID, user_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("members.read"))]
) -> AccountantRow:
    _, items = _accountants(ctx, org_id, "AND u.id = :uid", {"uid": user_id}, 1, 0)
    if not items:
        raise NotFound("Accountant not found.")
    return items[0]


class AssignmentRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    company_status: str
    user_id: uuid.UUID
    display_name: str
    email: str
    role: str
    assignment_type: str
    responsibility: str | None
    starts_on: date
    ends_on: date | None
    active: bool


@router.get("/orgs/{org_id}/assignments", response_model=Page[AssignmentRow], operation_id="control_center_assignments")
def assignments(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("members.read"))],
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AssignmentRow]:
    """All company-accountant assignments the viewer may see, filterable by company, person and whether the
    assignment window is currently open."""
    where = ["a.organization_id = :o"]
    params: dict[str, Any] = {"o": org_id}
    for col, val, key in (("a.company_id", company_id, "c"), ("a.user_id", user_id, "u")):
        if val:
            where.append(f"{col} = :{key}")
            params[key] = val
    window = "(a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date))"
    if active is True:
        where.append(window)
    elif active is False:
        where.append(f"NOT {window}")
    base = (
        "FROM accountant_assignments a JOIN companies c ON c.id = a.company_id JOIN users u ON u.id = a.user_id"
        " JOIN organization_memberships m ON m.user_id = a.user_id AND m.organization_id = a.organization_id JOIN roles r ON r.id = m.role_id"
        f" WHERE {' AND '.join(where)}"
    )
    total = ctx.conn.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                "SELECT c.id AS company_id, c.name AS company_name, c.status AS company_status, u.id AS user_id, u.display_name,"
                " u.email::text AS email, r.key AS role, a.assignment_type, a.responsibility, a.starts_on, a.ends_on,"
                f" {window} AS active {base} ORDER BY c.name, u.display_name LIMIT :l OFFSET :off"
            ),
            {**params, "l": limit, "off": offset},
        )
        .mappings()
        .all()
    )
    return Page[AssignmentRow](items=[AssignmentRow(**r) for r in rows], total=total, limit=limit, offset=offset)


# ------------------------------------------------------------------ subscription and usage (read-only for customers)
class PlanInfo(BaseModel):
    name: str
    key: str
    version: int
    billing_interval: str
    price_amount: str | None
    price_currency: str | None
    support_level: str


class SubscriptionView(BaseModel):
    status: str
    plan: PlanInfo
    current_period_start: datetime
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    cancelled_at: datetime | None
    entitlements: list[UsageRow]
    attention: list[str]
    next_step: str


@router.get("/orgs/{org_id}/subscription", response_model=SubscriptionView, operation_id="control_center_subscription")
def subscription(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("entitlements.read"))]
) -> SubscriptionView:
    """The organization's plan, status and entitlements exactly as the platform configured them. Customers cannot
    change any of it; limits are enforced by the backend, never by this page."""
    row = (
        ctx.conn.execute(
            text(
                "SELECT s.status, s.current_period_start, s.current_period_end, s.trial_ends_at, s.cancel_at_period_end, s.cancelled_at,"
                " p.name, p.key, v.version, v.billing_interval, v.price_amount::text AS price_amount, v.price_currency, v.support_level"
                " FROM subscriptions s JOIN subscription_plan_versions v ON v.id = s.plan_version_id JOIN subscription_plans p ON p.id = v.plan_id"
                " WHERE s.organization_id = :o"
            ),
            {"o": org_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("This organization has no subscription.")
    rows = _live_usage(ctx, org_id)
    attention = [
        f"{r.key}: {'over the limit' if r.status == 'exceeded' else 'near the limit'} ({r.current} of {r.limit})"
        for r in rows
        if r.enforced_now and r.status in ("exceeded", "approaching")
    ]
    if row["status"] in ("suspended", "cancelled", "expired"):
        step = f"The subscription is {row['status']}: creating companies, inviting users and registering devices is blocked; everything that exists stays readable. Contact Artify support to resolve it."
    elif row["status"] == "past_due":
        step = "The subscription is past due. Contact Artify support to settle it before it is restricted."
    elif attention:
        step = "Some limits are reached. Existing data is never removed; further additions are blocked until usage falls below the limit or Artify changes your plan. Contact Artify support to discuss your plan."
    else:
        step = "Your usage is within your plan. To change your plan, contact Artify support."
    plan = PlanInfo(
        name=row["name"],
        key=row["key"],
        version=row["version"],
        billing_interval=row["billing_interval"],
        price_amount=row["price_amount"],
        price_currency=row["price_currency"],
        support_level=row["support_level"],
    )
    return SubscriptionView(
        status=row["status"],
        plan=plan,
        current_period_start=row["current_period_start"],
        current_period_end=row["current_period_end"],
        trial_ends_at=row["trial_ends_at"],
        cancel_at_period_end=row["cancel_at_period_end"],
        cancelled_at=row["cancelled_at"],
        entitlements=rows,
        attention=attention,
        next_step=step,
    )


# ------------------------------------------------------------------ activity
@router.get("/orgs/{org_id}/activity", response_model=Page[OrgActivityRow], operation_id="control_center_activity")
def activity(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("audit.read"))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    actor_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[OrgActivityRow]:
    """Organization activity (the customer's own audit log). Platform-internal logs are never part of it; Artify
    support activity appears here only because it is mirrored into the customer's log (see support policy)."""
    where = ["e.organization_id = :o"]
    params: dict[str, Any] = {"o": org_id}
    if pattern := like(q):
        where.append("(e.action ILIKE :q ESCAPE '\\' OR u.display_name ILIKE :q ESCAPE '\\')")
        params["q"] = pattern
    if action:
        where.append("e.action LIKE :action ESCAPE '\\'")
        params["action"] = (like(action) or "").strip("%") + "%"
    for col, val, key in (("e.actor_id", actor_id, "actor"), ("e.company_id", company_id, "company")):
        if val:
            where.append(f"{col} = :{key}")
            params[key] = val
    if since:
        where.append("e.created_at >= :since")
        params["since"] = since
    if until:
        where.append("e.created_at < :until")
        params["until"] = until
    clause = " AND ".join(where)
    base = "FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id LEFT JOIN companies c ON c.id = e.company_id"
    total = ctx.conn.execute(text(f"SELECT count(*) {base} WHERE {clause}"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(f"{_ACTIVITY_SELECT} WHERE {clause} ORDER BY e.created_at DESC, e.id DESC LIMIT :l OFFSET :off"),
            {**params, "l": limit, "off": offset},
        )
        .mappings()
        .all()
    )
    return Page[OrgActivityRow](items=[OrgActivityRow(**r) for r in rows], total=total, limit=limit, offset=offset)


# ------------------------------------------------------------------ ownership transfer
class OwnershipTransfer(BaseModel):
    new_owner_user_id: uuid.UUID
    confirm: str = Field(min_length=1, max_length=100)


@router.post(
    "/orgs/{org_id}/ownership-transfer", response_model=TeamMember, operation_id="control_center_transfer_ownership"
)
def transfer_ownership(
    org_id: uuid.UUID, body: OwnershipTransfer, ctx: Annotated[RequestCtx, Depends(Requires("ownership.transfer"))]
) -> TeamMember:
    """The approved way to hand an organization over: the current owner (MFA-verified, typing the organization slug)
    makes another ACTIVE member the firm owner and becomes a practice administrator in the same transaction. The
    organization therefore never has zero owners, and the last owner can never be removed any other way."""
    slug = ctx.conn.execute(text("SELECT slug::text FROM organizations WHERE id = :o"), {"o": org_id}).scalar_one()
    if body.confirm.strip().lower() != slug.lower():
        raise Unprocessable(
            f"Type the organization identifier “{slug}” to confirm.", extra={"code": "confirmation_mismatch"}
        )
    if body.new_owner_user_id == ctx.user_id:
        raise Unprocessable("You already own this organization.")
    target = ctx.conn.execute(
        text(
            "SELECT r.key AS role, m.status FROM organization_memberships m JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o AND m.user_id = :u"
        ),
        {"o": org_id, "u": body.new_owner_user_id},
    ).first()
    if target is None or target.status != "active":
        raise NotFound("The new owner must be an active member of this organization.")
    for uid, role in ((body.new_owner_user_id, "firm_owner"), (ctx.user_id, "practice_admin")):
        ctx.conn.execute(
            text(
                "UPDATE organization_memberships SET role_id = (SELECT id FROM roles WHERE key = :r AND organization_id IS NULL),"
                " updated_at = now() WHERE organization_id = :o AND user_id = :u"
            ),
            {"r": role, "o": org_id, "u": uid},
        )
    # an owner reaches every company; remove the now-redundant assignments of the new owner
    ctx.conn.execute(
        text("DELETE FROM accountant_assignments WHERE organization_id = :o AND user_id = :u"),
        {"o": org_id, "u": body.new_owner_user_id},
    )
    ws.revoke_user_grants(
        ctx.conn,
        org_id=org_id,
        user_id=body.new_owner_user_id,
        actor_id=ctx.user_id,
        meta=ctx.meta,
        reason="ownership transferred",
    )
    for key in ("limit.users", "limit.accountants"):
        entitlements.refresh_counter(ctx.conn, org_id, key)
    audit.write_audit(
        ctx.conn,
        action="organization.ownership_transferred",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="user",
        entity_id=body.new_owner_user_id,
        summary={
            "from": str(ctx.user_id),
            "to": str(body.new_owner_user_id),
            "previous_role_of_new_owner": target.role,
        },
        meta=ctx.meta,
    )
    _, items = _team_rows(
        ctx, "m.organization_id = :org AND m.user_id = :u", {"org": org_id, "u": body.new_owner_user_id}, "1", 1, 0
    )
    return items[0]
