"""Client companies, company-side members and accountant assignments."""

from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from artify_api import audit, entitlements, outbox
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.idempotency import run_idempotent
from artify_api.ids import new_id
from artify_api.permissions import COMPANY_ADMIN_MANAGEABLE_ROLES, SYSTEM_ROLES
from artify_api.routers.common import Limit, Offset, Page, like, order_clause
from artify_api.routers.orgs import _create_invitation
from artify_api.routers.schemas import CompanyOut, InvitationOut, MemberOut

router = APIRouter(tags=["companies"])
_COLUMNS = (
    "id, organization_id, name, code, tax_registration, country, base_currency, status, created_at, updated_at,"
    " legal_name, registration_number, company_type, industry, city, address_line, contact_email, contact_phone,"
    " financial_year_start_month, financial_year_start_day, accounting_basis, vat_registered, vat_number, tax_jurisdiction"
)

# Which transitions each lifecycle action performs. "Inactive" in the UI is the stored status `suspended`.
_ACTIONS: dict[str, tuple[set[str], str]] = {
    "activate": ({"suspended"}, "active"),
    "deactivate": ({"active"}, "suspended"),
    "archive": ({"active", "suspended"}, "archived"),
    "restore": ({"archived"}, "active"),
}


class CompanyProfile(BaseModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    registration_number: str | None = Field(default=None, max_length=64)
    company_type: str | None = Field(default=None, max_length=60)
    industry: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address_line: str | None = Field(default=None, max_length=300)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)


class CompanyAccounting(BaseModel):
    """Accounting settings of ONE company. Jurisdiction-specific tax rules are not encoded anywhere: the
    jurisdiction is configuration that later phases interpret through configurable rule sets."""

    financial_year_start_month: int = Field(default=1, ge=1, le=12)
    financial_year_start_day: int = Field(default=1, ge=1, le=31)
    accounting_basis: Literal["accrual", "cash"] | None = None
    vat_registered: bool = False
    vat_number: str | None = Field(default=None, max_length=64)
    tax_jurisdiction: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _consistent(self) -> CompanyAccounting:
        if self.vat_number and not self.vat_registered:
            raise ValueError("a VAT number needs vat_registered to be true")
        if self.financial_year_start_day > calendar.monthrange(2000, self.financial_year_start_month)[1]:
            raise ValueError("the financial year start day does not exist in that month")
        return self


class AssignmentInput(BaseModel):
    user_id: uuid.UUID
    assignment_type: Literal["primary", "secondary", "reviewer"] = "secondary"
    responsibility: str | None = Field(default=None, max_length=200)
    starts_on: date | None = None
    ends_on: date | None = None

    @model_validator(mode="after")
    def _window(self) -> AssignmentInput:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must not be before starts_on")
        return self


class CompanyCreate(CompanyProfile, CompanyAccounting):
    name: str = Field(min_length=1, max_length=200)  # display name
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    country: str = Field(pattern="^[A-Z]{2}$")
    base_currency: str = Field(pattern="^[A-Z]{3}$")
    tax_registration: str | None = Field(default=None, max_length=64)
    assignments: list[AssignmentInput] = Field(default_factory=list, max_length=50)


class CompanyUpdate(CompanyProfile):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tax_registration: str | None = Field(default=None, max_length=64)


class CompanySettingsUpdate(BaseModel):
    base_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    financial_year_start_month: int | None = Field(default=None, ge=1, le=12)
    financial_year_start_day: int | None = Field(default=None, ge=1, le=31)
    accounting_basis: Literal["accrual", "cash"] | None = None
    vat_registered: bool | None = None
    vat_number: str | None = Field(default=None, max_length=64)
    tax_jurisdiction: str | None = Field(default=None, max_length=100)


class StatusAction(BaseModel):
    action: Literal["activate", "deactivate", "archive", "restore"]
    reason: str | None = Field(default=None, max_length=500)


class AssignmentCreate(AssignmentInput):
    pass


class AssignmentUpdate(BaseModel):
    assignment_type: Literal["primary", "secondary", "reviewer"] | None = None
    responsibility: str | None = Field(default=None, max_length=200)
    starts_on: date | None = None
    ends_on: date | None = None


class AssignmentOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    assignment_type: str
    responsibility: str | None
    starts_on: date
    ends_on: date | None
    active: bool
    created_at: datetime


class CompanyInvite(BaseModel):
    email: EmailStr
    role: Literal["company_owner", "company_admin", "company_employee"]


class CompanyMemberUpdate(BaseModel):
    status: Literal["active", "suspended", "removed"] | None = None
    role: Literal["company_owner", "company_admin", "company_employee"] | None = None


class CompanyListRow(BaseModel):
    id: uuid.UUID
    name: str
    legal_name: str | None
    code: str
    country: str
    base_currency: str
    status: Literal["active", "suspended", "archived"]
    created_at: datetime
    accountant_count: int
    member_count: int
    assigned_to_me: bool


class ConfigurationOut(BaseModel):
    company_id: uuid.UUID
    workspace_state: Literal["not_configured", "configured"]
    config_version: int
    created_at: datetime
    updated_at: datetime
    # What Phase 4 will attach to this company. Listed so the UI can say so honestly; none of it exists yet.
    future_areas: list[str]


class HistoryEvent(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    entity_type: str | None
    entity_id: str | None
    summary: dict[str, object]


FUTURE_AREAS = [
    "Workspace sections and nested folders",
    "Document categories and kinds mapping",
    "Custom metadata fields",
    "Excel templates and mappings",
    "Reporting settings",
    "Workflow configuration",
    "Local Windows folder mappings",
]


def _company(ctx: RequestCtx, company_id: uuid.UUID) -> CompanyOut:
    row = (
        ctx.conn.execute(text(f"SELECT {_COLUMNS} FROM companies WHERE id = :i"), {"i": company_id}).mappings().first()
    )
    if row is None:
        raise NotFound("Company not found.")
    return CompanyOut(**row)


def _assert_editable(company: CompanyOut) -> None:
    if company.status == "archived":
        raise Conflict("The company is archived. Restore it before changing it.", extra={"company_status": "archived"})


def _guard_company_role_grant(ctx: RequestCtx, company_id: uuid.UUID, *roles: str | None) -> None:
    """Callers reaching the company through an organization role (owner / practice admin / practice manager /
    assigned accountant with the permission) or as company_owner may manage every company role.
    A company_admin may only manage company_employee members (no lateral or upward escalation)."""
    assert ctx.principal is not None
    if ctx.principal.org_role is not None:
        return
    own_role = ctx.principal.company_roles.get(company_id)
    if own_role == "company_owner":
        return
    for role in roles:
        if role is not None and role not in COMPANY_ADMIN_MANAGEABLE_ROLES:
            raise Forbidden("Your role cannot grant, change or remove this company role.")


def _insert_assignment(ctx: RequestCtx, org_id: uuid.UUID, company_id: uuid.UUID, a: AssignmentInput) -> None:
    try:
        ctx.conn.execute(
            text(
                "INSERT INTO accountant_assignments (id, organization_id, company_id, user_id, assignment_type,"
                " responsibility, starts_on, ends_on) VALUES (:id, :o, :c, :u, :t, :r, COALESCE(:s, current_date), :e)"
                " ON CONFLICT (company_id, user_id) DO UPDATE SET assignment_type = EXCLUDED.assignment_type,"
                " responsibility = EXCLUDED.responsibility, starts_on = EXCLUDED.starts_on, ends_on = EXCLUDED.ends_on,"
                " updated_at = now()"
            ),
            {
                "id": new_id(),
                "o": org_id,
                "c": company_id,
                "u": a.user_id,
                "t": a.assignment_type,
                "r": a.responsibility,
                "s": a.starts_on,
                "e": a.ends_on,
            },
        )
    except IntegrityError as exc:
        raise Conflict("The user must be an active, assignable member of this organization.") from exc
    audit.write_audit(
        ctx.conn,
        action="assignment.set",
        actor_id=ctx.user_id,
        org_id=org_id,
        company_id=company_id,
        entity_type="user",
        entity_id=a.user_id,
        summary={
            "type": a.assignment_type,
            "responsibility": a.responsibility,
            "starts_on": str(a.starts_on) if a.starts_on else None,
            "ends_on": str(a.ends_on) if a.ends_on else None,
        },
        meta=ctx.meta,
    )


@router.post("/orgs/{org_id}/companies", response_model=CompanyOut, status_code=201, operation_id="companies_create")
def create_company(
    org_id: uuid.UUID,
    body: CompanyCreate,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.create"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Create a company with its profile, its OWN accounting settings, its configuration-ownership record and
    (optionally) its first accountant assignments, atomically. Nothing is copied from the organization or from
    another company, and no default sections, folders or categories are created."""

    def work() -> tuple[int, dict[str, object]]:
        assert ctx.principal is not None
        if body.assignments and "assignments.manage" not in SYSTEM_ROLES[ctx.principal.org_role or ""][2]:
            raise Forbidden("Your role cannot assign accountants.")
        entitlements.guard(ctx.conn, org_id, "limit.companies")
        company_id = new_id()
        try:
            ctx.conn.execute(
                text(
                    "INSERT INTO companies (id, organization_id, name, code, tax_registration, country, base_currency,"
                    " legal_name, registration_number, company_type, industry, city, address_line, contact_email,"
                    " contact_phone, financial_year_start_month, financial_year_start_day, accounting_basis,"
                    " vat_registered, vat_number, tax_jurisdiction) VALUES (:id, :o, :n, :c, :t, :co, :cur, :ln, :rn,"
                    " :ct, :ind, :city, :addr, :em, :ph, :fm, :fd, :ab, :vr, :vn, :tj)"
                ),
                {
                    "id": company_id,
                    "o": org_id,
                    "n": body.name,
                    "c": body.code,
                    "t": body.tax_registration,
                    "co": body.country,
                    "cur": body.base_currency,
                    "ln": body.legal_name,
                    "rn": body.registration_number,
                    "ct": body.company_type,
                    "ind": body.industry,
                    "city": body.city,
                    "addr": body.address_line,
                    "em": str(body.contact_email) if body.contact_email else None,
                    "ph": body.contact_phone,
                    "fm": body.financial_year_start_month,
                    "fd": body.financial_year_start_day,
                    "ab": body.accounting_basis,
                    "vr": body.vat_registered,
                    "vn": body.vat_number,
                    "tj": body.tax_jurisdiction,
                },
            )
        except IntegrityError as exc:
            msg = str(exc.orig)
            if "companies_organization_id_code_key" in msg:
                raise Conflict("A company with this code already exists.") from exc
            raise Conflict("The company could not be created (check the currency code).") from exc
        ctx.conn.execute(
            text("INSERT INTO company_configurations (company_id, organization_id) VALUES (:c, :o)"),
            {"c": company_id, "o": org_id},
        )
        for a in body.assignments:
            _insert_assignment(ctx, org_id, company_id, a)
        entitlements.refresh_counter(ctx.conn, org_id, "limit.companies")
        audit.write_audit(
            ctx.conn,
            action="company.created",
            actor_id=ctx.user_id,
            org_id=org_id,
            company_id=company_id,
            entity_type="company",
            entity_id=company_id,
            summary={"code": body.code, "country": body.country, "assignments": len(body.assignments)},
            meta=ctx.meta,
        )
        outbox.enqueue(ctx.conn, event_type="company.created", org_id=org_id, company_id=company_id)
        return 201, _company(ctx, company_id).model_dump(mode="json")

    status, payload = run_idempotent(ctx, idempotency_key, body.model_dump(mode="json"), work)
    return JSONResponse(payload, status_code=status)


@router.get("/orgs/{org_id}/companies", response_model=list[CompanyOut], operation_id="companies_list")
def list_companies(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("companies.list"))]
) -> list[CompanyOut]:
    rows = (
        ctx.conn.execute(
            text(f"SELECT {_COLUMNS} FROM companies WHERE organization_id = :o ORDER BY name"), {"o": org_id}
        )
        .mappings()
        .all()
    )  # RLS limits the rows to the caller's companies
    return [CompanyOut(**r) for r in rows]


_LIST_SORT = {
    "name": "c.name",
    "created": "c.created_at",
    "status": "c.status",
    "code": "c.code",
    "country": "c.country",
}


@router.get("/orgs/{org_id}/companies/overview", response_model=Page[CompanyListRow], operation_id="companies_overview")
def companies_overview(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.list"))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["active", "suspended", "archived", "open"] | None = None,
    country: Annotated[str | None, Query(pattern="^[A-Z]{2}$")] = None,
    assigned_to_me: bool = False,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[CompanyListRow]:
    """Searchable, filterable, paginated company directory. `status=open` means active or inactive (not archived).
    Rows are limited by row-level security to the companies the caller may see."""
    where = ["c.organization_id = :org"]
    params: dict[str, object] = {"org": org_id, "me": ctx.user_id}
    if pattern := like(q):
        where.append(
            "(c.name ILIKE :q ESCAPE '\\' OR c.legal_name ILIKE :q ESCAPE '\\' OR c.code ILIKE :q ESCAPE '\\')"
        )
        params["q"] = pattern
    if status == "open":
        where.append("c.status <> 'archived'")
    elif status:
        where.append("c.status = :status")
        params["status"] = status
    if country:
        where.append("c.country = :country")
        params["country"] = country
    if assigned_to_me:
        where.append(
            "EXISTS (SELECT 1 FROM accountant_assignments a WHERE a.company_id = c.id AND a.user_id = :me"
            " AND a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date))"
        )
    clause = " AND ".join(where)
    base = (
        "SELECT c.id, c.name, c.legal_name, c.code, c.country, c.base_currency, c.status, c.created_at,"
        " (SELECT count(*) FROM accountant_assignments a WHERE a.company_id = c.id AND a.starts_on <= current_date"
        "   AND (a.ends_on IS NULL OR a.ends_on >= current_date)) AS accountant_count,"
        " (SELECT count(*) FROM company_memberships m WHERE m.company_id = c.id AND m.status = 'active') AS member_count,"
        " EXISTS (SELECT 1 FROM accountant_assignments a WHERE a.company_id = c.id AND a.user_id = :me"
        "   AND a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date)) AS assigned_to_me"
        f" FROM companies c WHERE {clause}"
    )
    total = ctx.conn.execute(text(f"SELECT count(*) FROM ({base}) t"), params).scalar_one()
    rows = (
        ctx.conn.execute(
            text(f"{base} ORDER BY {order_clause(sort, order, _LIST_SORT, 'name')} LIMIT :l OFFSET :o"),
            {**params, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return Page[CompanyListRow](items=[CompanyListRow(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/companies/{company_id}", response_model=CompanyOut, operation_id="companies_get")
def get_company(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("companies.read", company=True))]
) -> CompanyOut:
    return _company(ctx, company_id)


@router.patch("/companies/{company_id}", response_model=CompanyOut, operation_id="companies_update")
def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.update", company=True))],
) -> CompanyOut:
    """Edit the company PROFILE (names, registration, contact). Accounting settings have their own endpoint and
    permission; lifecycle (archive, restore, activate, deactivate) has its own too."""
    assert ctx.org_id is not None
    current = _company(ctx, company_id)
    _assert_editable(current)
    changes = body.model_dump(exclude_unset=True, mode="json")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)  # keys come from the fixed pydantic model
        ctx.conn.execute(
            text(f"UPDATE companies SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
            {**changes, "id": company_id},
        )
        audit.write_audit(
            ctx.conn,
            action="company.updated",
            actor_id=ctx.user_id,
            org_id=ctx.org_id,
            company_id=company_id,
            entity_type="company",
            entity_id=company_id,
            summary={"fields": sorted(changes)},
            meta=ctx.meta,
        )
    return _company(ctx, company_id)


@router.patch("/companies/{company_id}/settings", response_model=CompanyOut, operation_id="companies_update_settings")
def update_company_settings(
    company_id: uuid.UUID,
    body: CompanySettingsUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("company_settings.manage", company=True))],
) -> CompanyOut:
    """Edit this company's accounting settings. They never propagate to, or inherit from, another company or the
    organization. The base currency can only change while nothing has been configured for the company (later
    phases will attach data denominated in it)."""
    assert ctx.org_id is not None
    current = _company(ctx, company_id)
    _assert_editable(current)
    changes = body.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise Unprocessable("Provide at least one setting.")
    if changes.get("vat_registered") is False and "vat_number" not in changes:
        changes["vat_number"] = None  # leaving VAT registration clears the number
    combined = {
        **current.model_dump(include=set(CompanyAccounting.model_fields)),
        **{k: v for k, v in changes.items() if k in CompanyAccounting.model_fields},
    }
    try:
        CompanyAccounting(**combined)  # validates the COMBINATION (VAT number vs registration, day vs month)
    except ValidationError as exc:
        raise Unprocessable("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())) from exc
    if "base_currency" in changes and changes["base_currency"] != current.base_currency:
        state = ctx.conn.execute(
            text("SELECT workspace_state FROM company_configurations WHERE company_id = :c"), {"c": company_id}
        ).scalar()
        if state == "configured":
            raise Conflict(
                "The base currency can no longer change: the company has been configured.",
                extra={"code": "settings_locked"},
            )
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    try:
        ctx.conn.execute(
            text(f"UPDATE companies SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
            {**changes, "id": company_id},
        )
    except IntegrityError as exc:
        raise Unprocessable("The settings are not valid (check the currency code and VAT fields).") from exc
    audit.write_audit(
        ctx.conn,
        action="company.settings_updated",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="company",
        entity_id=company_id,
        summary={"fields": sorted(changes)},
        meta=ctx.meta,
    )
    return _company(ctx, company_id)


@router.post("/companies/{company_id}/status", response_model=CompanyOut, operation_id="companies_status")
def change_company_status(
    company_id: uuid.UUID,
    body: StatusAction,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.archive", company=True))],
) -> CompanyOut:
    """Activate / deactivate / archive / restore. Nothing is deleted: archived companies keep all their data and
    free their subscription slot; restoring needs a free slot again."""
    assert ctx.org_id is not None
    current = _company(ctx, company_id)
    allowed_from, to = _ACTIONS[body.action]
    if current.status not in allowed_from:
        raise Conflict(
            f"A {current.status} company cannot be {body.action}d.", extra={"company_status": current.status}
        )
    if current.status == "archived":
        entitlements.guard(ctx.conn, ctx.org_id, "limit.companies")
    ctx.conn.execute(
        text("UPDATE companies SET status = :s, updated_at = now(), row_version = row_version + 1 WHERE id = :i"),
        {"s": to, "i": company_id},
    )
    entitlements.refresh_counter(ctx.conn, ctx.org_id, "limit.companies")
    audit.write_audit(
        ctx.conn,
        action="company.status_changed",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="company",
        entity_id=company_id,
        summary={"action": body.action, "from": current.status, "to": to, "reason": body.reason},
        meta=ctx.meta,
    )
    return _company(ctx, company_id)


@router.get(
    "/companies/{company_id}/configuration", response_model=ConfigurationOut, operation_id="companies_configuration"
)
def company_configuration(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("companies.read", company=True))]
) -> ConfigurationOut:
    """The record that OWNS this company's configuration. Phase 4 attaches workspace structure, categories,
    folders, fields, templates, workflows and folder mappings to it; in Phase 3 it only says nothing is configured."""
    row = (
        ctx.conn.execute(
            text(
                "SELECT company_id, workspace_state, config_version, created_at, updated_at FROM company_configurations WHERE company_id = :c"
            ),
            {"c": company_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("Company configuration not found.")
    return ConfigurationOut(**row, future_areas=FUTURE_AREAS)


@router.get("/companies/{company_id}/history", response_model=Page[HistoryEvent], operation_id="companies_history")
def company_history(
    company_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.read", company=True))],
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[HistoryEvent]:
    """Creation, modification, lifecycle, settings, assignment and membership events of this company."""
    base = "FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id WHERE e.company_id = :c"
    total = ctx.conn.execute(text(f"SELECT count(*) {base}"), {"c": company_id}).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                "SELECT e.id, e.created_at, e.action, e.actor_id, u.display_name AS actor_name, e.entity_type, e.entity_id,"
                f" e.summary {base} ORDER BY e.created_at DESC, e.id DESC LIMIT :l OFFSET :o"
            ),
            {"c": company_id, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return Page[HistoryEvent](items=[HistoryEvent(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/companies/{company_id}/members", response_model=list[MemberOut], operation_id="companies_members")
def list_company_members(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("company_members.read", company=True))]
) -> list[MemberOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT u.id AS user_id, u.email, u.display_name, r.key AS role, cm.status"
                " FROM company_memberships cm JOIN users u ON u.id = cm.user_id JOIN roles r ON r.id = cm.role_id"
                " WHERE cm.company_id = :c ORDER BY u.display_name"
            ),
            {"c": company_id},
        )
        .mappings()
        .all()
    )
    return [MemberOut(**r) for r in rows]


@router.post(
    "/companies/{company_id}/invitations",
    response_model=InvitationOut,
    status_code=201,
    operation_id="companies_invite_member",
)
def invite_company_member(
    company_id: uuid.UUID,
    body: CompanyInvite,
    ctx: Annotated[RequestCtx, Depends(Requires("company_members.manage", company=True))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    assert ctx.org_id is not None
    org_id = ctx.org_id
    _guard_company_role_grant(ctx, company_id, body.role)

    def work() -> tuple[int, dict[str, object]]:
        inv = _create_invitation(ctx, org_id=org_id, email=body.email, role_key=body.role, company_id=company_id)
        return 201, inv.model_dump(mode="json")

    status, payload = run_idempotent(ctx, idempotency_key, {**body.model_dump(), "company": str(company_id)}, work)
    return JSONResponse(payload, status_code=status)


@router.patch(
    "/companies/{company_id}/members/{user_id}", response_model=MemberOut, operation_id="companies_update_member"
)
def update_company_member(
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CompanyMemberUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("company_members.manage", company=True))],
) -> MemberOut:
    """Change a client-side member's role/status. Nobody changes their own membership; the last
    active company_owner cannot be demoted or deactivated by a company-side caller's mistake;
    reactivation counts against limit.users (a user is counted once across memberships)."""
    if body.status is None and body.role is None:
        raise Unprocessable("Provide status and/or role.")
    assert ctx.org_id is not None
    org_id = ctx.org_id
    if user_id == ctx.user_id:
        raise Unprocessable("You cannot change your own company membership.")
    target = ctx.conn.execute(
        text(
            "SELECT r.key AS role, cm.status FROM company_memberships cm JOIN roles r ON r.id = cm.role_id"
            " WHERE cm.company_id = :c AND cm.user_id = :u"
        ),
        {"c": company_id, "u": user_id},
    ).first()
    if target is None:
        raise NotFound("Member not found.")
    new_role = body.role or target.role
    new_status = body.status or target.status
    _guard_company_role_grant(ctx, company_id, target.role, new_role)
    if (
        target.role == "company_owner"
        and target.status == "active"
        and (new_role != "company_owner" or new_status != "active")
    ):
        owners = ctx.conn.execute(
            text(
                "SELECT count(*) FROM company_memberships cm JOIN roles r ON r.id = cm.role_id"
                " WHERE cm.company_id = :c AND cm.status = 'active' AND r.key = 'company_owner'"
            ),
            {"c": company_id},
        ).scalar_one()
        if owners <= 1 and ctx.principal is not None and ctx.principal.org_role is None:
            raise Conflict("The last company owner cannot be demoted or deactivated by company staff.")
    if new_status == "active" and target.status != "active":
        has_seat = ctx.conn.execute(
            text(
                "SELECT 1 FROM organization_memberships WHERE organization_id = :o AND user_id = :u AND status = 'active'"
                " UNION ALL SELECT 1 FROM company_memberships WHERE organization_id = :o AND user_id = :u"
                " AND status = 'active'"
            ),
            {"o": org_id, "u": user_id},
        ).first()
        if has_seat is None:
            entitlements.guard(ctx.conn, org_id, "limit.users")
    ctx.conn.execute(
        text(
            "UPDATE company_memberships SET status = :s,"
            " role_id = (SELECT id FROM roles WHERE key = :r AND organization_id IS NULL)"
            " WHERE company_id = :c AND user_id = :u"
        ),
        {"s": new_status, "r": new_role, "c": company_id, "u": user_id},
    )
    entitlements.refresh_counter(ctx.conn, org_id, "limit.users")
    for action, before, after in (
        ("company_member.status_changed", target.status, new_status),
        ("company_member.role_changed", target.role, new_role),
    ):
        if before != after:
            audit.write_audit(
                ctx.conn,
                action=action,
                actor_id=ctx.user_id,
                org_id=org_id,
                company_id=company_id,
                entity_type="user",
                entity_id=user_id,
                summary={"from": before, "to": after},
                meta=ctx.meta,
            )
    row = (
        ctx.conn.execute(
            text(
                "SELECT u.id AS user_id, u.email, u.display_name, r.key AS role, cm.status"
                " FROM company_memberships cm JOIN users u ON u.id = cm.user_id JOIN roles r ON r.id = cm.role_id"
                " WHERE cm.company_id = :c AND cm.user_id = :u"
            ),
            {"c": company_id, "u": user_id},
        )
        .mappings()
        .one()
    )
    return MemberOut(**row)


_ASSIGN_SELECT = (
    "SELECT a.user_id, u.email::text AS email, u.display_name, r.key AS role, a.assignment_type, a.responsibility,"
    " a.starts_on, a.ends_on, (a.starts_on <= current_date AND (a.ends_on IS NULL OR a.ends_on >= current_date)) AS active,"
    " a.created_at FROM accountant_assignments a JOIN users u ON u.id = a.user_id"
    " JOIN organization_memberships m ON m.user_id = a.user_id AND m.organization_id = a.organization_id"
    " JOIN roles r ON r.id = m.role_id"
)


def _assignment(ctx: RequestCtx, company_id: uuid.UUID, user_id: uuid.UUID) -> AssignmentOut:
    row = (
        ctx.conn.execute(
            text(f"{_ASSIGN_SELECT} WHERE a.company_id = :c AND a.user_id = :u"), {"c": company_id, "u": user_id}
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("Assignment not found.")
    return AssignmentOut(**row)


@router.get(
    "/companies/{company_id}/assignments", response_model=list[AssignmentOut], operation_id="companies_assignments"
)
def list_assignments(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("assignments.read", company=True))]
) -> list[AssignmentOut]:
    rows = (
        ctx.conn.execute(text(f"{_ASSIGN_SELECT} WHERE a.company_id = :c ORDER BY u.display_name"), {"c": company_id})
        .mappings()
        .all()
    )
    return [AssignmentOut(**r) for r in rows]


@router.post(
    "/companies/{company_id}/assignments",
    response_model=AssignmentOut,
    status_code=201,
    operation_id="companies_assign",
)
def assign_accountant(
    company_id: uuid.UUID,
    body: AssignmentCreate,
    ctx: Annotated[RequestCtx, Depends(Requires("assignments.manage", company=True))],
) -> AssignmentOut:
    assert ctx.org_id is not None
    _assert_editable(_company(ctx, company_id))
    _insert_assignment(ctx, ctx.org_id, company_id, body)
    return _assignment(ctx, company_id, body.user_id)


@router.patch(
    "/companies/{company_id}/assignments/{user_id}",
    response_model=AssignmentOut,
    operation_id="companies_update_assignment",
)
def update_assignment(
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    body: AssignmentUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("assignments.manage", company=True))],
) -> AssignmentOut:
    """Change type, responsibility or the assignment window. Setting `ends_on` in the past ends the person's access to
    the company from that day; the row stays as history of who was assigned."""
    assert ctx.org_id is not None
    _assert_editable(_company(ctx, company_id))
    changes = body.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise Unprocessable("Provide at least one field.")
    current = _assignment(ctx, company_id, user_id)
    starts = date.fromisoformat(changes["starts_on"]) if changes.get("starts_on") else current.starts_on
    ends = (
        date.fromisoformat(changes["ends_on"])
        if changes.get("ends_on")
        else (None if "ends_on" in changes else current.ends_on)
    )
    if ends is not None and ends < starts:
        raise Unprocessable("ends_on must not be before starts_on.")
    cols = {"assignment_type": changes.get("assignment_type"), "responsibility": changes.get("responsibility")}
    sets = ["updated_at = now()"]
    params: dict[str, object] = {"c": company_id, "u": user_id}
    for key in ("assignment_type", "responsibility", "starts_on", "ends_on"):
        if key in changes:
            sets.append(f"{key} = :{key}")
            params[key] = starts if key == "starts_on" else (ends if key == "ends_on" else cols[key])
    ctx.conn.execute(
        text(f"UPDATE accountant_assignments SET {', '.join(sets)} WHERE company_id = :c AND user_id = :u"), params
    )
    audit.write_audit(
        ctx.conn,
        action="assignment.updated",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="user",
        entity_id=user_id,
        summary={"fields": sorted(changes), **dict(changes.items())},
        meta=ctx.meta,
    )
    return _assignment(ctx, company_id, user_id)


@router.delete("/companies/{company_id}/assignments/{user_id}", status_code=204, operation_id="companies_unassign")
def unassign_accountant(
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("assignments.manage", company=True))],
) -> Response:
    res = ctx.conn.execute(
        text("DELETE FROM accountant_assignments WHERE company_id = :c AND user_id = :u"),
        {"c": company_id, "u": user_id},
    )
    if not res.rowcount:
        raise NotFound("Assignment not found.")
    audit.write_audit(
        ctx.conn,
        action="assignment.removed",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="user",
        entity_id=user_id,
        meta=ctx.meta,
    )
    return Response(status_code=204)


@router.get(
    "/companies/{company_id}/assignments/history",
    response_model=Page[HistoryEvent],
    operation_id="companies_assignment_history",
)
def assignment_history(
    company_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("assignments.read", company=True))],
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[HistoryEvent]:
    """Who was assigned, changed or removed, by whom and when (from the append-only audit log)."""
    base = "FROM audit_events e LEFT JOIN users u ON u.id = e.actor_id WHERE e.company_id = :c AND e.action LIKE 'assignment.%'"
    total = ctx.conn.execute(text(f"SELECT count(*) {base}"), {"c": company_id}).scalar_one()
    rows = (
        ctx.conn.execute(
            text(
                "SELECT e.id, e.created_at, e.action, e.actor_id, u.display_name AS actor_name, e.entity_type, e.entity_id,"
                f" e.summary {base} ORDER BY e.created_at DESC, e.id DESC LIMIT :l OFFSET :o"
            ),
            {"c": company_id, "l": limit, "o": offset},
        )
        .mappings()
        .all()
    )
    return Page[HistoryEvent](items=[HistoryEvent(**r) for r in rows], total=total, limit=limit, offset=offset)
