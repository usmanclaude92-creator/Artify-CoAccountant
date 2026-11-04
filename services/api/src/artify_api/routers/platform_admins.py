"""Super Admin: platform administrator management (least privilege, no self-escalation).

Rules, all enforced here and covered by tests:
- only holders of `platform.admins.manage` (the owner role) invite or change administrators;
- nobody changes their own role or status, and nobody grants a role above their own;
- the last active owner can never be demoted or suspended;
- suspending an administrator revokes all of their sessions immediately;
- administrators are invited by e-mail and choose their own password; nobody sets another person's password;
- MFA enrollment is mandatory at first sign-in (Phase 1 platform realm).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from artify_api import audit
from artify_api.context import PlatformCtx, Public, request_meta
from artify_api.db import DbContext, platform_engine, transaction
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.permissions import PLATFORM_ROLE_RANK, PLATFORM_ROLES
from artify_api.ratelimit import enforce
from artify_api.routers.platform_common import (
    Limit,
    Offset,
    Page,
    Reasoned,
    Sensitive,
    padmin,
    paudit,
    require_confirmation,
)
from artify_api.security import passwords
from artify_api.security.tokens import hash_token, new_opaque_token
from artify_api.settings import get_settings

router = APIRouter(prefix="/platform", tags=["platform-administrators"])

RoleKey = Literal[
    "platform_owner", "platform_admin", "platform_support", "platform_billing", "platform_auditor", "platform_readonly"
]


class AdminRow(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role_key: str
    status: Literal["active", "disabled"]
    created_at: datetime
    invited_by: uuid.UUID | None
    last_login_at: datetime | None
    permissions: list[str]


class InvitationRow(BaseModel):
    id: uuid.UUID
    email: str
    role_key: str
    invited_by: uuid.UUID
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    state: Literal["pending", "accepted", "revoked", "expired"]


class AdminList(BaseModel):
    administrators: list[AdminRow]
    invitations: list[InvitationRow]


class InviteAdmin(BaseModel):
    email: EmailStr
    role_key: RoleKey


class ChangeRole(Sensitive):
    role_key: RoleKey


class AcceptInvite(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class ActivityRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    reason: str | None


_ADMIN_SQL = (
    "SELECT a.id, a.email::text AS email, a.display_name, a.role_key, a.status, a.created_at, a.invited_by,"
    " (SELECT max(e.created_at) FROM platform_audit_events e WHERE e.actor_id = a.id AND e.action = 'auth.login') AS last_login_at"
    " FROM platform_admins a"
)


def _admin(ctx: PlatformCtx, admin_id: uuid.UUID) -> AdminRow:
    r = ctx.conn.execute(text(f"{_ADMIN_SQL} WHERE a.id = :i"), {"i": admin_id}).mappings().first()
    if r is None:
        raise NotFound("Administrator not found.")
    return AdminRow(**r, permissions=sorted(PLATFORM_ROLES[r["role_key"]]))


def _guard_target(
    ctx: PlatformCtx, target: AdminRow, new_role: str | None = None, *, leaving_active: bool = False
) -> None:
    if target.id == ctx.admin_id:
        raise Unprocessable("You cannot change your own role or status.")
    if PLATFORM_ROLE_RANK[target.role_key] > PLATFORM_ROLE_RANK[ctx.role_key]:
        raise Forbidden("You cannot modify an administrator above your own level.")
    if new_role is not None and PLATFORM_ROLE_RANK[new_role] > PLATFORM_ROLE_RANK[ctx.role_key]:
        raise Forbidden("You cannot grant a role above your own.")
    demoting_owner = target.role_key == "platform_owner" and (
        new_role not in (None, "platform_owner") or leaving_active
    )
    if demoting_owner and target.status == "active":
        owners = ctx.conn.execute(
            text("SELECT count(*) FROM platform_admins WHERE role_key = 'platform_owner' AND status = 'active'")
        ).scalar_one()
        if owners <= 1:
            raise Conflict("The last active platform owner cannot be demoted or suspended.")


@router.get("/admins", response_model=AdminList, operation_id="platform_admins")
def list_admins(ctx: Annotated[PlatformCtx, padmin("platform.admins.read")]) -> AdminList:
    admins = ctx.conn.execute(text(f"{_ADMIN_SQL} ORDER BY a.created_at")).mappings().all()
    invites = (
        ctx.conn.execute(
            text(
                "SELECT id, email::text AS email, role_key, invited_by, expires_at, accepted_at, revoked_at, created_at,"
                " CASE WHEN accepted_at IS NOT NULL THEN 'accepted' WHEN revoked_at IS NOT NULL THEN 'revoked'"
                " WHEN expires_at <= now() THEN 'expired' ELSE 'pending' END AS state"
                " FROM platform_admin_invitations ORDER BY created_at DESC LIMIT 100"
            )
        )
        .mappings()
        .all()
    )
    return AdminList(
        administrators=[AdminRow(**a, permissions=sorted(PLATFORM_ROLES[a["role_key"]])) for a in admins],
        invitations=[InvitationRow(**i) for i in invites],
    )


@router.post("/admins/invitations", response_model=InvitationRow, status_code=201, operation_id="platform_admin_invite")
def invite_admin(body: InviteAdmin, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]) -> InvitationRow:
    if PLATFORM_ROLE_RANK[body.role_key] > PLATFORM_ROLE_RANK[ctx.role_key]:
        raise Forbidden("You cannot invite someone to a role above your own.")
    email = str(body.email).lower()
    if ctx.conn.execute(text("SELECT 1 FROM platform_admins WHERE email = :e"), {"e": email}).first():
        raise Conflict("An administrator with this e-mail address already exists.")
    if ctx.conn.execute(
        text(
            "SELECT 1 FROM platform_admin_invitations WHERE email = :e AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()"
        ),
        {"e": email},
    ).first():
        raise Conflict("A pending invitation for this e-mail address already exists; revoke it first.")
    token, digest = new_opaque_token()
    iid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO platform_admin_invitations (id, email, role_key, token_hash, invited_by, expires_at)"
            " VALUES (:id, :e, :r, :h, :by, now() + make_interval(secs => :ttl))"
        ),
        {
            "id": iid,
            "e": email,
            "r": body.role_key,
            "h": digest,
            "by": ctx.admin_id,
            "ttl": get_settings().invitation_ttl_seconds,
        },
    )
    paudit(
        ctx,
        "admin.invited",
        target_type="platform_admin_invitation",
        target_id=iid,
        summary={"role": body.role_key, "email_domain": email.split("@")[-1]},
    )
    get_mailer().send(Message(to=email, template="platform_invitation", context={"token": token}))
    row = (
        ctx.conn.execute(
            text(
                "SELECT id, email::text AS email, role_key, invited_by, expires_at, accepted_at, revoked_at, created_at, 'pending' AS state FROM platform_admin_invitations WHERE id = :i"
            ),
            {"i": iid},
        )
        .mappings()
        .one()
    )
    return InvitationRow(**row)


@router.delete("/admins/invitations/{invitation_id}", status_code=204, operation_id="platform_admin_invitation_revoke")
def revoke_invitation(invitation_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]) -> None:
    res = ctx.conn.execute(
        text(
            "UPDATE platform_admin_invitations SET revoked_at = now() WHERE id = :i AND accepted_at IS NULL AND revoked_at IS NULL"
        ),
        {"i": invitation_id},
    )
    if not res.rowcount:
        raise NotFound("Pending invitation not found.")
    paudit(ctx, "admin.invitation_revoked", target_type="platform_admin_invitation", target_id=invitation_id)


@router.patch("/admins/{admin_id}", response_model=AdminRow, operation_id="platform_admin_change_role")
def change_role(
    admin_id: uuid.UUID, body: ChangeRole, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]
) -> AdminRow:
    target = _admin(ctx, admin_id)
    require_confirmation(body.confirm, target.email)
    if body.role_key == target.role_key:
        raise Conflict("The administrator already has this role.")
    _guard_target(ctx, target, body.role_key)
    ctx.conn.execute(
        text("UPDATE platform_admins SET role_key = :r, updated_at = now() WHERE id = :i"),
        {"r": body.role_key, "i": admin_id},
    )
    paudit(
        ctx,
        "admin.role_changed",
        target_type="platform_admin",
        target_id=admin_id,
        reason=body.reason,
        summary={"from": target.role_key, "to": body.role_key},
    )
    return _admin(ctx, admin_id)


def _revoke_sessions(ctx: PlatformCtx, admin_id: uuid.UUID, why: str) -> int:
    return ctx.conn.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), revoke_reason = :w WHERE platform_admin_id = :a AND revoked_at IS NULL"
        ),
        {"a": admin_id, "w": why},
    ).rowcount


@router.post("/admins/{admin_id}/suspend", response_model=AdminRow, operation_id="platform_admin_suspend")
def suspend_admin(
    admin_id: uuid.UUID, body: Sensitive, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]
) -> AdminRow:
    target = _admin(ctx, admin_id)
    require_confirmation(body.confirm, target.email)
    if target.status != "active":
        raise Conflict("The administrator is already suspended.")
    _guard_target(ctx, target, leaving_active=True)
    ctx.conn.execute(
        text("UPDATE platform_admins SET status = 'disabled', updated_at = now() WHERE id = :i"), {"i": admin_id}
    )
    revoked = _revoke_sessions(ctx, admin_id, "admin_suspended")
    paudit(
        ctx,
        "admin.suspended",
        target_type="platform_admin",
        target_id=admin_id,
        reason=body.reason,
        summary={"sessions_revoked": revoked},
    )
    return _admin(ctx, admin_id)


@router.post("/admins/{admin_id}/reactivate", response_model=AdminRow, operation_id="platform_admin_reactivate")
def reactivate_admin(
    admin_id: uuid.UUID, body: Reasoned, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]
) -> AdminRow:
    target = _admin(ctx, admin_id)
    if target.status != "disabled":
        raise Conflict("The administrator is not suspended.")
    _guard_target(ctx, target)
    ctx.conn.execute(
        text("UPDATE platform_admins SET status = 'active', updated_at = now() WHERE id = :i"), {"i": admin_id}
    )
    paudit(ctx, "admin.reactivated", target_type="platform_admin", target_id=admin_id, reason=body.reason)
    return _admin(ctx, admin_id)


@router.post("/admins/{admin_id}/revoke-sessions", status_code=204, operation_id="platform_admin_revoke_sessions")
def revoke_admin_sessions(
    admin_id: uuid.UUID, body: Reasoned, ctx: Annotated[PlatformCtx, padmin("platform.admins.manage")]
) -> None:
    target = _admin(ctx, admin_id)
    _guard_target(ctx, target)
    revoked = _revoke_sessions(ctx, admin_id, "admin_sessions_revoked")
    paudit(
        ctx,
        "admin.sessions_revoked",
        target_type="platform_admin",
        target_id=admin_id,
        reason=body.reason,
        summary={"sessions_revoked": revoked},
    )


@router.get("/admins/{admin_id}/activity", response_model=Page[ActivityRow], operation_id="platform_admin_activity")
def admin_activity(
    admin_id: uuid.UUID,
    ctx: Annotated[PlatformCtx, padmin("platform.admins.read")],
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[ActivityRow]:
    """Sign-ins and actions performed by one administrator (from the append-only platform audit)."""
    _admin(ctx, admin_id)
    total = ctx.conn.execute(
        text("SELECT count(*) FROM platform_audit_events WHERE actor_id = :a"), {"a": admin_id}
    ).scalar_one()
    rows = ctx.conn.execute(
        text(
            "SELECT id, created_at, action, outcome, target_type, target_id, reason FROM platform_audit_events"
            " WHERE actor_id = :a ORDER BY created_at DESC, id DESC LIMIT :l OFFSET :o"
        ),
        {"a": admin_id, "l": limit, "o": offset},
    ).mappings()
    return Page[ActivityRow](items=[ActivityRow(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.post(
    "/auth/invitations/accept",
    status_code=204,
    operation_id="platform_invitation_accept",
    dependencies=[Depends(Public("platform invitation token is the credential"))],
)
def accept_invitation(body: AcceptInvite, request: Request) -> None:
    meta = request_meta(request)
    enforce(f"platform_invite_accept:ip:{meta.ip}", 10, 3600)
    passwords.validate_password_policy(body.password)
    new_admin = new_id()
    with transaction(platform_engine()) as conn:
        role = conn.execute(
            text("SELECT app.platform_accept_invitation(:h, :a, :n, :p)"),
            {
                "h": hash_token(body.token),
                "a": new_admin,
                "n": body.display_name,
                "p": passwords.hash_password(body.password),
            },
        ).scalar()
    if role is None:
        raise Unprocessable("This invitation is invalid, already used, revoked or expired.")
    with transaction(platform_engine(), DbContext(user_id=new_admin)) as conn:
        audit.write_platform_audit(
            conn,
            action="admin.invitation_accepted",
            actor_id=new_admin,
            target_type="platform_admin",
            target_id=str(new_admin),
            summary={"role": role},
            meta=meta,
        )
