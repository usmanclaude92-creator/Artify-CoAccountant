"""Client companies, company-side members and accountant assignments."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from artify_api import audit, entitlements, outbox
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, NotFound
from artify_api.idempotency import run_idempotent
from artify_api.ids import new_id
from artify_api.routers.orgs import _create_invitation
from artify_api.routers.schemas import CompanyOut, InvitationOut, MemberOut

router = APIRouter(tags=["companies"])
_COLUMNS = "id, organization_id, name, code, tax_registration, country, base_currency, status, created_at"


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    country: str = Field(pattern="^[A-Z]{2}$")
    base_currency: str = Field(pattern="^[A-Z]{3}$")
    tax_registration: str | None = Field(default=None, max_length=64)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tax_registration: str | None = Field(default=None, max_length=64)
    status: Literal["active", "suspended", "archived"] | None = None


class AssignmentCreate(BaseModel):
    user_id: uuid.UUID
    assignment_type: Literal["primary", "secondary", "reviewer"] = "secondary"


class AssignmentOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    assignment_type: str


class CompanyInvite(BaseModel):
    email: EmailStr
    role: Literal["company_owner", "company_employee"]


@router.post("/orgs/{org_id}/companies", response_model=CompanyOut, status_code=201, operation_id="companies_create")
def create_company(
    org_id: uuid.UUID,
    body: CompanyCreate,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.create"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    def work() -> tuple[int, dict[str, object]]:
        entitlements.guard(ctx.conn, org_id, "limit.companies")
        company_id = new_id()
        try:
            ctx.conn.execute(
                text(
                    "INSERT INTO companies (id, organization_id, name, code, tax_registration, country,"
                    " base_currency) VALUES (:id, :o, :n, :c, :t, :co, :cur)"
                ),
                {
                    "id": company_id,
                    "o": org_id,
                    "n": body.name,
                    "c": body.code,
                    "t": body.tax_registration,
                    "co": body.country,
                    "cur": body.base_currency,
                },
            )
        except IntegrityError as exc:
            msg = str(exc.orig)
            if "companies_organization_id_code_key" in msg:
                raise Conflict("A company with this code already exists.") from exc
            raise Conflict("The company could not be created (check the currency code).") from exc
        entitlements.refresh_counter(ctx.conn, org_id, "limit.companies")
        audit.write_audit(
            ctx.conn,
            action="company.created",
            actor_id=ctx.user_id,
            org_id=org_id,
            company_id=company_id,
            entity_type="company",
            entity_id=company_id,
            summary={"code": body.code},
            meta=ctx.meta,
        )
        outbox.enqueue(ctx.conn, event_type="company.created", org_id=org_id, company_id=company_id)
        row = (
            ctx.conn.execute(text(f"SELECT {_COLUMNS} FROM companies WHERE id = :i"), {"i": company_id})
            .mappings()
            .one()
        )
        return 201, CompanyOut(**row).model_dump(mode="json")

    status, payload = run_idempotent(ctx, idempotency_key, body.model_dump(), work)
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


@router.get("/companies/{company_id}", response_model=CompanyOut, operation_id="companies_get")
def get_company(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("companies.read", company=True))]
) -> CompanyOut:
    row = (
        ctx.conn.execute(text(f"SELECT {_COLUMNS} FROM companies WHERE id = :i"), {"i": company_id}).mappings().first()
    )
    if row is None:
        raise NotFound("Company not found.")
    return CompanyOut(**row)


@router.patch("/companies/{company_id}", response_model=CompanyOut, operation_id="companies_update")
def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("companies.update", company=True))],
) -> CompanyOut:
    assert ctx.org_id is not None
    changes = body.model_dump(exclude_unset=True)
    if changes.get("status", "archived") != "archived":
        # Reactivating an archived company counts against limit.companies (archiving frees a slot).
        current = ctx.conn.execute(text("SELECT status FROM companies WHERE id = :i"), {"i": company_id}).scalar()
        if current == "archived":
            entitlements.guard(ctx.conn, ctx.org_id, "limit.companies")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)  # keys come from the fixed pydantic model
        ctx.conn.execute(
            text(f"UPDATE companies SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
            {**changes, "id": company_id},
        )
        entitlements.refresh_counter(ctx.conn, ctx.org_id, "limit.companies")
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
    row = ctx.conn.execute(text(f"SELECT {_COLUMNS} FROM companies WHERE id = :i"), {"i": company_id}).mappings().one()
    return CompanyOut(**row)


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
) -> InvitationOut:
    assert ctx.org_id is not None
    return _create_invitation(ctx, org_id=ctx.org_id, email=body.email, role_key=body.role, company_id=company_id)


@router.get(
    "/companies/{company_id}/assignments", response_model=list[AssignmentOut], operation_id="companies_assignments"
)
def list_assignments(
    company_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("assignments.read", company=True))]
) -> list[AssignmentOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT a.user_id, u.email, u.display_name, a.assignment_type FROM accountant_assignments a"
                " JOIN users u ON u.id = a.user_id WHERE a.company_id = :c ORDER BY u.display_name"
            ),
            {"c": company_id},
        )
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
    try:
        ctx.conn.execute(
            text(
                "INSERT INTO accountant_assignments (id, organization_id, company_id, user_id, assignment_type)"
                " VALUES (:id, :o, :c, :u, :t)"
                " ON CONFLICT (company_id, user_id) DO UPDATE SET assignment_type = EXCLUDED.assignment_type"
            ),
            {"id": new_id(), "o": ctx.org_id, "c": company_id, "u": body.user_id, "t": body.assignment_type},
        )
    except IntegrityError as exc:
        raise Conflict("The user must be an active accountant or staff member of this organization.") from exc
    audit.write_audit(
        ctx.conn,
        action="assignment.set",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="user",
        entity_id=body.user_id,
        summary={"type": body.assignment_type},
        meta=ctx.meta,
    )
    row = (
        ctx.conn.execute(
            text(
                "SELECT a.user_id, u.email, u.display_name, a.assignment_type FROM accountant_assignments a"
                " JOIN users u ON u.id = a.user_id WHERE a.company_id = :c AND a.user_id = :u"
            ),
            {"c": company_id, "u": body.user_id},
        )
        .mappings()
        .one()
    )
    return AssignmentOut(**row)


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
