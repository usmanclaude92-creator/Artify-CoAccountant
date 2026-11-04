"""Organization-level endpoints: profile, members, invitations, entitlements, audit log."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from artify_api import audit, entitlements
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.permissions import INVITABLE_COMPANY_ROLES, INVITABLE_ORG_ROLES
from artify_api.routers.schemas import (
    AuditEventOut,
    EntitlementOut,
    InvitationOut,
    MemberOut,
    OrganizationOut,
)
from artify_api.security.tokens import new_opaque_token
from artify_api.settings import get_settings

router = APIRouter(tags=["organizations"])


class OrgInvite(BaseModel):
    email: EmailStr
    role: Literal["practice_admin", "accountant", "staff"]


class MemberUpdate(BaseModel):
    status: Literal["active", "suspended", "removed"]


@router.get("/orgs/{org_id}", response_model=OrganizationOut, operation_id="organizations_get")
def get_organization(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("organization.read"))]
) -> OrganizationOut:
    row = (
        ctx.conn.execute(
            text(
                "SELECT id, type, display_name, slug, country, default_currency, locale, status"
                " FROM organizations WHERE id = :o"
            ),
            {"o": org_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("Organization not found.")
    return OrganizationOut(**row)


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut], operation_id="organizations_list_members")
def list_members(org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("members.read"))]) -> list[MemberOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT u.id AS user_id, u.email, u.display_name, r.key AS role, m.status"
                " FROM organization_memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id"
                " WHERE m.organization_id = :o ORDER BY u.display_name"
            ),
            {"o": org_id},
        )
        .mappings()
        .all()
    )
    return [MemberOut(**r) for r in rows]


@router.patch("/orgs/{org_id}/members/{user_id}", response_model=MemberOut, operation_id="organizations_update_member")
def update_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("members.manage"))],
) -> MemberOut:
    target = ctx.conn.execute(
        text(
            "SELECT r.key AS role FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
            " WHERE m.organization_id = :o AND m.user_id = :u"
        ),
        {"o": org_id, "u": user_id},
    ).first()
    if target is None:
        raise NotFound("Member not found.")
    if target.role == "firm_owner" and body.status != "active":
        owners = ctx.conn.execute(
            text(
                "SELECT count(*) FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
                " WHERE m.organization_id = :o AND m.status = 'active' AND r.key = 'firm_owner'"
            ),
            {"o": org_id},
        ).scalar_one()
        if owners <= 1:
            raise Conflict("The last firm owner cannot be deactivated.")
    if target.role == "firm_owner" and ctx.principal and ctx.principal.org_role != "firm_owner":
        raise Unprocessable("Only a firm owner can change a firm owner.")
    if body.status == "active":
        entitlements.guard(ctx.conn, org_id, "limit.users", 0)
    ctx.conn.execute(
        text(
            "UPDATE organization_memberships SET status = :s, updated_at = now()"
            " WHERE organization_id = :o AND user_id = :u"
        ),
        {"s": body.status, "o": org_id, "u": user_id},
    )
    for key in ("limit.users", "limit.accountants"):
        entitlements.refresh_counter(ctx.conn, org_id, key)
    audit.write_audit(
        ctx.conn,
        action="member.status_changed",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="user",
        entity_id=user_id,
        summary={"status": body.status},
        meta=ctx.meta,
    )
    row = (
        ctx.conn.execute(
            text(
                "SELECT u.id AS user_id, u.email, u.display_name, r.key AS role, m.status"
                " FROM organization_memberships m JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id"
                " WHERE m.organization_id = :o AND m.user_id = :u"
            ),
            {"o": org_id, "u": user_id},
        )
        .mappings()
        .one()
    )
    return MemberOut(**row)


def _create_invitation(
    ctx: RequestCtx, *, org_id: uuid.UUID, email: str, role_key: str, company_id: uuid.UUID | None
) -> InvitationOut:
    plaintext, digest = new_opaque_token()
    inv_id = new_id()
    role_id = ctx.conn.execute(
        text("SELECT id FROM roles WHERE key = :k AND organization_id IS NULL"), {"k": role_key}
    ).scalar_one()
    expires = ctx.conn.execute(
        text("SELECT now() + make_interval(secs => :t)"), {"t": get_settings().invitation_ttl_seconds}
    ).scalar_one()
    ctx.conn.execute(
        text(
            "INSERT INTO invitations (id, organization_id, email, org_role_id, company_id, company_role_id,"
            " token_hash, invited_by, expires_at) VALUES (:id, :o, :e, :orole, :c, :crole, :h, :by, :exp)"
        ),
        {
            "id": inv_id,
            "o": org_id,
            "e": email,
            "orole": None if company_id else role_id,
            "c": company_id,
            "crole": role_id if company_id else None,
            "h": digest,
            "by": ctx.user_id,
            "exp": expires,
        },
    )
    audit.write_audit(
        ctx.conn,
        action="invitation.created",
        actor_id=ctx.user_id,
        org_id=org_id,
        company_id=company_id,
        entity_type="invitation",
        entity_id=inv_id,
        summary={"role": role_key},
        meta=ctx.meta,
    )
    get_mailer().send(Message(to=email, template="invitation", context={"token": plaintext}))
    return InvitationOut(
        id=inv_id,
        email=email,
        role=role_key,
        company_id=company_id,
        expires_at=expires,
        accepted_at=None,
        revoked_at=None,
    )


@router.post(
    "/orgs/{org_id}/invitations",
    response_model=InvitationOut,
    status_code=201,
    operation_id="organizations_invite_member",
)
def invite_member(
    org_id: uuid.UUID, body: OrgInvite, ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))]
) -> InvitationOut:
    assert body.role in INVITABLE_ORG_ROLES
    return _create_invitation(ctx, org_id=org_id, email=body.email, role_key=body.role, company_id=None)


@router.get(
    "/orgs/{org_id}/invitations", response_model=list[InvitationOut], operation_id="organizations_list_invitations"
)
def list_invitations(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))]
) -> list[InvitationOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT i.id, i.email, r.key AS role, i.company_id, i.expires_at, i.accepted_at, i.revoked_at"
                " FROM invitations i JOIN roles r ON r.id = COALESCE(i.org_role_id, i.company_role_id)"
                " WHERE i.organization_id = :o ORDER BY i.created_at DESC"
            ),
            {"o": org_id},
        )
        .mappings()
        .all()
    )
    return [InvitationOut(**r) for r in rows]


@router.delete(
    "/orgs/{org_id}/invitations/{invitation_id}", status_code=204, operation_id="organizations_revoke_invitation"
)
def revoke_invitation(
    org_id: uuid.UUID, invitation_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))]
) -> Response:
    res = ctx.conn.execute(
        text(
            "UPDATE invitations SET revoked_at = now() WHERE id = :i AND organization_id = :o"
            " AND accepted_at IS NULL AND revoked_at IS NULL"
        ),
        {"i": invitation_id, "o": org_id},
    )
    if not res.rowcount:
        raise NotFound("Invitation not found.")
    audit.write_audit(
        ctx.conn,
        action="invitation.revoked",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="invitation",
        entity_id=invitation_id,
        meta=ctx.meta,
    )
    return Response(status_code=204)


@router.get(
    "/orgs/{org_id}/entitlements", response_model=list[EntitlementOut], operation_id="organizations_entitlements"
)
def get_entitlements(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("entitlements.read"))]
) -> list[EntitlementOut]:
    return [EntitlementOut(**row) for row in entitlements.summarize(ctx.conn, org_id)]


@router.get(
    "/orgs/{org_id}/audit-events", response_model=list[AuditEventOut], operation_id="organizations_audit_events"
)
def list_audit_events(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("audit.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEventOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT id, created_at, actor_type, actor_id, action, entity_type, entity_id, company_id, summary"
                " FROM audit_events WHERE organization_id = :o ORDER BY created_at DESC, id DESC LIMIT :n"
            ),
            {"o": org_id, "n": limit},
        )
        .mappings()
        .all()
    )
    return [AuditEventOut(**r) for r in rows]


__all__ = ["INVITABLE_COMPANY_ROLES", "Field", "_create_invitation", "router"]
