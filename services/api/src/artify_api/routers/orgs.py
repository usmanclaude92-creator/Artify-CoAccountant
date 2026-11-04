"""Organization-level endpoints: profile, members, invitations, entitlements, audit log."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from artify_api import audit, entitlements
from artify_api import workspace as ws
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.idempotency import run_idempotent
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.permissions import (
    ACCOUNTANT_ROLES,
    ASSIGNABLE_ROLES,
    INVITABLE_COMPANY_ROLES,
    INVITABLE_ORG_ROLES,
    ORG_ROLE_RANK,
)
from artify_api.routers.control_center import email_delivery
from artify_api.routers.schemas import (
    AuditEventOut,
    EntitlementOut,
    InvitationOut,
    MemberOut,
    OrganizationOut,
    OrganizationUpdate,
)
from artify_api.security.tokens import new_opaque_token
from artify_api.settings import get_settings

router = APIRouter(tags=["organizations"])


class OrgInvite(BaseModel):
    email: EmailStr
    company_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=50
    )  # companies the invitee is assigned to on acceptance
    role: Literal[
        "practice_admin",
        "practice_manager",
        "accountant",
        "assistant_accountant",
        "document_operator",
        "read_only",
        "staff",
    ]


class MemberUpdate(BaseModel):
    status: Literal["active", "suspended", "removed"] | None = None
    role: (
        Literal[
            "firm_owner",
            "practice_admin",
            "practice_manager",
            "accountant",
            "assistant_accountant",
            "document_operator",
            "read_only",
            "staff",
        ]
        | None
    ) = None


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


@router.get("/orgs", response_model=list[OrganizationOut], operation_id="organizations_list")
def list_organizations(
    ctx: Annotated[
        RequestCtx, Depends(Requires(reason="lists only organizations the caller holds an active membership in"))
    ],
) -> list[OrganizationOut]:
    # RLS (org_select) limits the rows to organizations where the caller has an active membership.
    rows = (
        ctx.conn.execute(
            text(
                "SELECT id, type, display_name, slug, country, default_currency, locale, status"
                " FROM organizations WHERE status <> 'closed' ORDER BY display_name"
            )
        )
        .mappings()
        .all()
    )
    return [OrganizationOut(**r) for r in rows]


@router.patch("/orgs/{org_id}", response_model=OrganizationOut, operation_id="organizations_update")
def update_organization(
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    ctx: Annotated[RequestCtx, Depends(Requires("organization.update"))],
) -> OrganizationOut:
    """Edit the basic profile (display name, locale). Legal identity, type, country, currency and
    status are not editable here: they change through Super Admin / support processes."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise Unprocessable("Provide display_name and/or locale.")
    sets = ", ".join(f"{k} = :{k}" for k in changes)  # keys come from the fixed pydantic model
    ctx.conn.execute(
        text(f"UPDATE organizations SET {sets}, updated_at = now(), row_version = row_version + 1 WHERE id = :id"),
        {**changes, "id": org_id},
    )
    audit.write_audit(
        ctx.conn,
        action="organization.updated",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="organization",
        entity_id=org_id,
        summary={"fields": sorted(changes)},
        meta=ctx.meta,
    )
    return get_organization(org_id, ctx)


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
    """Change a member's status and/or role. Safety rules: only a firm owner may grant or remove
    the firm-owner role; the last firm owner can neither be demoted nor deactivated; nobody changes
    their own role; roles that count against limit.accountants are guarded; assignments end when a
    member stops being an accountant/staff member."""
    if body.status is None and body.role is None:
        raise Unprocessable("Provide status and/or role.")
    assert ctx.principal is not None
    target = ctx.conn.execute(
        text(
            "SELECT r.key AS role, m.status FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
            " WHERE m.organization_id = :o AND m.user_id = :u"
        ),
        {"o": org_id, "u": user_id},
    ).first()
    if target is None:
        raise NotFound("Member not found.")
    new_role = body.role or target.role
    new_status = body.status or target.status
    caller_is_owner = ctx.principal.org_role == "firm_owner"

    if body.role is not None and user_id == ctx.user_id:
        raise Unprocessable("You cannot change your own role.")
    caller_rank = ORG_ROLE_RANK.get(ctx.principal.org_role or "", 0)
    if (body.role is not None and ORG_ROLE_RANK[body.role] > caller_rank) or ORG_ROLE_RANK.get(
        target.role, 0
    ) > caller_rank:
        raise Forbidden("You cannot grant or change a role above your own.")
    if (target.role == "firm_owner" or new_role == "firm_owner") and not caller_is_owner:
        raise Forbidden("Only a firm owner can grant, change or remove the firm-owner role.")
    losing_owner = (
        target.role == "firm_owner"
        and target.status == "active"
        and (new_role != "firm_owner" or new_status != "active")
    )
    if losing_owner:
        owners = ctx.conn.execute(
            text(
                "SELECT count(*) FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
                " WHERE m.organization_id = :o AND m.status = 'active' AND r.key = 'firm_owner'"
            ),
            {"o": org_id},
        ).scalar_one()
        if owners <= 1:
            raise Conflict("The last firm owner cannot be demoted or deactivated.")

    counted_before = target.status == "active"
    counted_after = new_status == "active"
    if counted_after and not counted_before:
        has_company_seat = ctx.conn.execute(
            text("SELECT 1 FROM company_memberships WHERE organization_id = :o AND user_id = :u AND status = 'active'"),
            {"o": org_id, "u": user_id},
        ).first()
        if has_company_seat is None:  # a user is counted once, however many memberships they hold
            entitlements.guard(ctx.conn, org_id, "limit.users")
    acct_roles = ACCOUNTANT_ROLES
    was_acct = counted_before and target.role in acct_roles
    will_acct = counted_after and new_role in acct_roles
    if will_acct and not was_acct:
        entitlements.guard(ctx.conn, org_id, "limit.accountants")

    ctx.conn.execute(
        text(
            "UPDATE organization_memberships SET status = :s, updated_at = now(),"
            " role_id = (SELECT id FROM roles WHERE key = :r AND organization_id IS NULL)"
            " WHERE organization_id = :o AND user_id = :u"
        ),
        {"s": new_status, "r": new_role, "o": org_id, "u": user_id},
    )
    if new_role not in acct_roles and target.role in acct_roles:
        ctx.conn.execute(
            text("DELETE FROM accountant_assignments WHERE organization_id = :o AND user_id = :u"),
            {"o": org_id, "u": user_id},
        )
        ws.revoke_user_grants(
            ctx.conn, org_id=org_id, user_id=user_id, actor_id=ctx.user_id, meta=ctx.meta, reason="role changed"
        )
    for key in ("limit.users", "limit.accountants"):
        entitlements.refresh_counter(ctx.conn, org_id, key)
    if new_status != target.status:
        audit.write_audit(
            ctx.conn,
            action="member.status_changed",
            actor_id=ctx.user_id,
            org_id=org_id,
            entity_type="user",
            entity_id=user_id,
            summary={"from": target.status, "to": new_status},
            meta=ctx.meta,
        )
    if new_role != target.role:
        audit.write_audit(
            ctx.conn,
            action="member.role_changed",
            actor_id=ctx.user_id,
            org_id=org_id,
            entity_type="user",
            entity_id=user_id,
            summary={"from": target.role, "to": new_role},
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
    ctx: RequestCtx,
    *,
    org_id: uuid.UUID,
    email: str,
    role_key: str,
    company_id: uuid.UUID | None,
    company_ids: list[uuid.UUID] | None = None,
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
            " token_hash, invited_by, expires_at, company_ids) VALUES (:id, :o, :e, :orole, :c, :crole, :h, :by, :exp,"
            " CAST(:cids AS uuid[]))"
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
            "cids": company_ids or [],
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
        summary={"role": role_key, "company_ids": [str(c) for c in company_ids or []]},
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
        company_ids=company_ids or [],
        email_delivery=email_delivery(),
    )


@router.post(
    "/orgs/{org_id}/invitations",
    response_model=InvitationOut,
    status_code=201,
    operation_id="organizations_invite_member",
)
def invite_member(
    org_id: uuid.UUID,
    body: OrgInvite,
    ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    assert body.role in INVITABLE_ORG_ROLES
    assert ctx.principal is not None
    if ORG_ROLE_RANK[body.role] > ORG_ROLE_RANK.get(ctx.principal.org_role or "", 0):
        raise Forbidden("You cannot invite someone to a role above your own.")
    if body.company_ids and body.role not in ASSIGNABLE_ROLES:
        raise Unprocessable("Company access applies only to roles that are assigned to specific companies.")
    email = str(body.email)

    def work() -> tuple[int, dict[str, object]]:
        if ctx.conn.execute(
            text(
                "SELECT 1 FROM organization_memberships m JOIN users u ON u.id = m.user_id"
                " WHERE m.organization_id = :o AND u.email = :e AND m.status = 'active'"
            ),
            {"o": org_id, "e": email},
        ).first():
            raise Conflict("This person is already an active member of the organization.")
        if ctx.conn.execute(
            text(
                "SELECT 1 FROM invitations WHERE organization_id = :o AND email = :e AND company_id IS NULL"
                " AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()"
            ),
            {"o": org_id, "e": email},
        ).first():
            raise Conflict("A pending invitation for this address already exists; resend or cancel it instead.")
        if body.company_ids:
            found = ctx.conn.execute(
                text(
                    "SELECT count(*) FROM companies WHERE organization_id = :o AND id = ANY(CAST(:c AS uuid[])) AND status <> 'archived'"
                ),
                {"o": org_id, "c": list(body.company_ids)},
            ).scalar_one()  # row-level security also hides companies the caller may not see
            if found != len(set(body.company_ids)):
                raise NotFound("One or more companies were not found.")
        inv = _create_invitation(
            ctx,
            org_id=org_id,
            email=email,
            role_key=body.role,
            company_id=None,
            company_ids=sorted(set(body.company_ids), key=str),
        )
        return 201, inv.model_dump(mode="json")

    status, payload = run_idempotent(ctx, idempotency_key, body.model_dump(mode="json"), work)
    return JSONResponse(payload, status_code=status)


_INV_SELECT = (
    "SELECT i.id, i.email::text AS email, r.key AS role, i.company_id, i.expires_at, i.accepted_at, i.revoked_at,"
    " i.declined_at, i.company_ids, i.resent_count, i.created_at, ub.display_name AS invited_by_name,"
    " CASE WHEN i.accepted_at IS NOT NULL THEN 'accepted' WHEN i.declined_at IS NOT NULL THEN 'declined'"
    " WHEN i.revoked_at IS NOT NULL THEN 'revoked' WHEN i.expires_at <= now() THEN 'expired' ELSE 'pending' END AS state"
    " FROM invitations i JOIN roles r ON r.id = COALESCE(i.org_role_id, i.company_role_id)"
    " LEFT JOIN users ub ON ub.id = i.invited_by"
)


@router.get(
    "/orgs/{org_id}/invitations", response_model=list[InvitationOut], operation_id="organizations_list_invitations"
)
def list_invitations(
    org_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))],
    state: Literal["pending", "accepted", "revoked", "declined", "expired"] | None = None,
) -> list[InvitationOut]:
    rows = (
        ctx.conn.execute(
            text(
                f"SELECT * FROM ({_INV_SELECT} WHERE i.organization_id = :o) t WHERE (CAST(:s AS text) IS NULL OR t.state = :s) ORDER BY t.created_at DESC LIMIT 200"
            ),
            {"o": org_id, "s": state},
        )
        .mappings()
        .all()
    )
    return [InvitationOut(**r, email_delivery=None) for r in rows]


@router.post(
    "/orgs/{org_id}/invitations/{invitation_id}/resend",
    response_model=InvitationOut,
    operation_id="organizations_resend_invitation",
)
def resend_invitation(
    org_id: uuid.UUID, invitation_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("invitations.manage"))]
) -> InvitationOut:
    """Issue a NEW secret link (the old one stops working) and a fresh expiry for a pending invitation."""
    assert ctx.principal is not None
    row = ctx.conn.execute(
        text(
            "SELECT i.email::text AS email, r.key AS role FROM invitations i JOIN roles r ON r.id = COALESCE(i.org_role_id, i.company_role_id)"
            " WHERE i.id = :i AND i.organization_id = :o AND i.accepted_at IS NULL AND i.revoked_at IS NULL"
        ),
        {"i": invitation_id, "o": org_id},
    ).first()
    if row is None:
        raise NotFound("Pending invitation not found.")
    if row.role in ORG_ROLE_RANK and ORG_ROLE_RANK[row.role] > ORG_ROLE_RANK.get(ctx.principal.org_role or "", 0):
        raise Forbidden("You cannot manage an invitation for a role above your own.")
    plaintext, digest = new_opaque_token()
    ctx.conn.execute(
        text(
            "UPDATE invitations SET token_hash = :h, expires_at = now() + make_interval(secs => :t), resent_at = now(),"
            " resent_count = resent_count + 1 WHERE id = :i"
        ),
        {"h": digest, "t": get_settings().invitation_ttl_seconds, "i": invitation_id},
    )
    audit.write_audit(
        ctx.conn,
        action="invitation.resent",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="invitation",
        entity_id=invitation_id,
        meta=ctx.meta,
    )
    get_mailer().send(Message(to=row.email, template="invitation", context={"token": plaintext}))
    out = ctx.conn.execute(text(f"{_INV_SELECT} WHERE i.id = :i"), {"i": invitation_id}).mappings().one()
    return InvitationOut(**out, email_delivery=email_delivery())


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
