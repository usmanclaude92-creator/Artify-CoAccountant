"""Super Admin: controlled support access (platform side).

Flow: a support administrator REQUESTS access to one organization with a reason -> the organization's
firm owner APPROVES (scope, duration) or denies -> while the grant is active and unexpired the platform
realm may read that organization's member list and audit log, nothing else -> the customer or Artify can
REVOKE at any time. There is no impersonation: administrators never act as a customer user and never
receive a customer session. Every use of a grant is audited twice: in the platform audit and in the
customer's own audit log (support_grant_id set). Access level `read` (sensitive data) exists in the data
model and needs a separate, explicit customer confirmation, but Phase 2 has no documents, so it unlocks
nothing beyond `metadata`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api.context import PlatformCtx
from artify_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.routers.platform_common import Limit, Offset, Page, Reasoned, padmin, paudit, paudit_isolated

router = APIRouter(prefix="/platform/support", tags=["platform-support"])

DEFAULT_MAX_HOURS = 72
REQUEST_VALID_DAYS = 14

_EFFECTIVE = (
    "CASE WHEN g.status = 'active' AND g.expires_at <= now() THEN 'expired'"
    f" WHEN g.status = 'requested' AND g.created_at < now() - interval '{REQUEST_VALID_DAYS} days' THEN 'expired'"
    " ELSE g.status END"
)


class GrantRow(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    platform_admin_id: uuid.UUID
    requester_name: str
    access_level: Literal["metadata", "read"]
    approved_level: Literal["metadata", "read"] | None
    reason: str
    requested_hours: int
    status: Literal["requested", "active", "revoked", "expired", "denied"]
    starts_at: datetime | None
    expires_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    revoked_at: datetime | None
    revoked_reason: str | None
    revoked_by_type: str | None
    created_at: datetime


class GrantRequest(BaseModel):
    organization_id: uuid.UUID
    access_level: Literal["metadata", "read"] = "metadata"
    reason: str = Field(min_length=20, max_length=500)
    requested_hours: int = Field(default=24, ge=1, le=720)


class MemberView(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    user_status: str
    role: str
    membership_status: str


class AuditView(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor_type: str
    action: str
    entity_type: str | None
    entity_id: str | None
    summary: dict[str, Any]


_SELECT = (
    "SELECT g.id, g.organization_id, o.display_name AS organization_name, g.platform_admin_id, g.requester_name,"
    f" g.access_level, g.approved_level, g.reason, g.requested_hours, {_EFFECTIVE} AS status, g.starts_at, g.expires_at,"
    " g.decided_at, g.decision_note, g.revoked_at, g.revoked_reason, g.revoked_by_type, g.created_at"
    " FROM support_access_grants g JOIN organizations o ON o.id = g.organization_id"
)
_SORT = "g.created_at DESC, g.id"


def _grant(ctx: PlatformCtx, grant_id: uuid.UUID) -> GrantRow:
    r = ctx.conn.execute(text(f"{_SELECT} WHERE g.id = :i"), {"i": grant_id}).mappings().first()
    if r is None:
        raise NotFound("Support grant not found.")
    return GrantRow(**r)


def _max_hours(ctx: PlatformCtx) -> int:
    v = ctx.conn.execute(
        text("SELECT value #>> '{}' FROM platform_settings WHERE key = 'support.grant_max_hours'")
    ).scalar()
    return int(v) if v else DEFAULT_MAX_HOURS


@router.get("/grants", response_model=Page[GrantRow], operation_id="platform_support_grants")
def list_grants(
    ctx: Annotated[PlatformCtx, padmin("platform.support.read")],
    status: Literal["requested", "active", "revoked", "expired", "denied"] | None = None,
    organization_id: uuid.UUID | None = None,
    mine: bool = False,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[GrantRow]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if organization_id:
        where.append("g.organization_id = :org")
        params["org"] = organization_id
    if mine:
        where.append("g.platform_admin_id = :me")
        params["me"] = ctx.admin_id
    if status:
        where.append(f"({_EFFECTIVE}) = :status")
        params["status"] = status
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    total = ctx.conn.execute(text(f"SELECT count(*) FROM support_access_grants g{clause}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(f"{_SELECT}{clause} ORDER BY {_SORT} LIMIT :l OFFSET :o"), {**params, "l": limit, "o": offset}
    ).mappings()
    return Page[GrantRow](items=[GrantRow(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/grants/{grant_id}", response_model=GrantRow, operation_id="platform_support_grant_get")
def get_grant(grant_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.support.read")]) -> GrantRow:
    return _grant(ctx, grant_id)


@router.post("/grants", response_model=GrantRow, status_code=201, operation_id="platform_support_grant_request")
def request_access(body: GrantRequest, ctx: Annotated[PlatformCtx, padmin("platform.support.request")]) -> GrantRow:
    org = ctx.conn.execute(
        text("SELECT id, display_name, owner_email, status FROM platform_org_overview WHERE id = :o"),
        {"o": body.organization_id},
    ).first()
    if org is None or org.status == "closed":
        raise NotFound("Organization not found.")
    cap = _max_hours(ctx)
    if body.requested_hours > cap:
        raise Unprocessable(f"Support access is limited to {cap} hours per grant (Platform settings).")
    if ctx.conn.execute(
        text(
            f"SELECT 1 FROM ({_SELECT} WHERE g.organization_id = :o AND g.platform_admin_id = :a) x WHERE x.status IN ('requested', 'active')"
        ),
        {"o": body.organization_id, "a": ctx.admin_id},
    ).first():
        raise Conflict("You already have an open request or active grant for this organization.")
    name = ctx.conn.execute(
        text("SELECT display_name FROM platform_admins WHERE id = :a"), {"a": ctx.admin_id}
    ).scalar_one()
    gid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO support_access_grants (id, platform_admin_id, organization_id, access_level, reason, status,"
            " requester_name, requested_hours) VALUES (:id, :a, :o, :lvl, :r, 'requested', :n, :h)"
        ),
        {
            "id": gid,
            "a": ctx.admin_id,
            "o": body.organization_id,
            "lvl": body.access_level,
            "r": body.reason,
            "n": name,
            "h": body.requested_hours,
        },
    )
    paudit(
        ctx,
        "support.requested",
        target_type="support_grant",
        target_id=gid,
        org=body.organization_id,
        reason=body.reason,
        summary={"level": body.access_level, "hours": body.requested_hours},
    )
    if org.owner_email:
        get_mailer().send(
            Message(
                to=org.owner_email,
                template="support_access_request",
                context={"organization": org.display_name, "reason": body.reason},
            )
        )
    return _grant(ctx, gid)


@router.post("/grants/{grant_id}/revoke", response_model=GrantRow, operation_id="platform_support_grant_revoke")
def revoke(
    grant_id: uuid.UUID, body: Reasoned, ctx: Annotated[PlatformCtx, padmin("platform.support.read")]
) -> GrantRow:
    """The requester can always withdraw their own request/grant; revoking someone else's needs platform.support.revoke."""
    g = _grant(ctx, grant_id)
    own = g.platform_admin_id == ctx.admin_id
    if not (own and ctx.can("platform.support.request")) and not ctx.can("platform.support.revoke"):
        raise Forbidden("You cannot revoke this support grant.")
    if g.status not in ("requested", "active"):
        raise Conflict(f"A {g.status} grant cannot be revoked.")
    ctx.conn.execute(
        text(
            "UPDATE support_access_grants SET status = 'revoked', revoked_at = now(), revoked_reason = :r, revoked_by_type = 'platform_admin' WHERE id = :i"
        ),
        {"r": body.reason, "i": grant_id},
    )
    paudit(
        ctx,
        "support.revoked",
        target_type="support_grant",
        target_id=grant_id,
        org=g.organization_id,
        reason=body.reason,
    )
    return _grant(ctx, grant_id)


def _usable(ctx: PlatformCtx, grant_id: uuid.UUID) -> GrantRow:
    """Only the grant holder may use it, only while active and unexpired. Anything else is a 403 and is audited."""
    g = _grant(ctx, grant_id)
    reason = None
    if g.platform_admin_id != ctx.admin_id:
        reason = "not_grant_holder"
    elif g.status != "active":
        reason = f"grant_{g.status}"
    if reason:
        paudit_isolated(
            ctx,
            "support.access_denied",
            target_type="support_grant",
            target_id=grant_id,
            org=g.organization_id,
            summary={"reason": reason},
        )
        raise Forbidden("This support grant cannot be used (not yours, or not active).", extra={"reason": reason})
    return g


def _tenant_audit(ctx: PlatformCtx, g: GrantRow, action: str) -> None:
    """Mirror the use of a grant into the CUSTOMER's audit log so they can see exactly what Artify looked at."""
    ctx.conn.execute(
        text(
            "INSERT INTO audit_events (id, organization_id, actor_type, actor_id, action, entity_type, entity_id, summary,"
            " ip, user_agent, correlation_id, support_grant_id) VALUES (:id, :o, 'platform_admin', :a, :act, 'support_grant',"
            " :gt, CAST(:s AS jsonb), CAST(:ip AS inet), :ua, :cid, :g)"
        ),
        {
            "id": new_id(),
            "o": g.organization_id,
            "a": ctx.admin_id,
            "act": action,
            "g": g.id,
            "gt": str(g.id),
            "s": json.dumps({"admin": g.requester_name, "level": g.approved_level}),
            "ip": ctx.meta.ip,
            "ua": (ctx.meta.user_agent or "")[:300],
            "cid": ctx.meta.correlation_id,
        },
    )


@router.get("/grants/{grant_id}/members", response_model=list[MemberView], operation_id="platform_support_members")
def view_members(
    grant_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.support.view_data")]
) -> list[MemberView]:
    g = _usable(ctx, grant_id)
    rows = (
        ctx.conn.execute(
            text(
                "SELECT u.id AS user_id, u.email::text AS email, u.display_name, u.status AS user_status, r.key AS role,"
                " m.status AS membership_status FROM organization_memberships m JOIN users u ON u.id = m.user_id"
                " JOIN roles r ON r.id = m.role_id WHERE m.organization_id = :o ORDER BY u.display_name"
            ),
            {
                "o": g.organization_id
            },  # RLS additionally restricts this to organizations with an active grant of THIS admin
        )
        .mappings()
        .all()
    )
    paudit(
        ctx,
        "support.members_viewed",
        target_type="support_grant",
        target_id=grant_id,
        org=g.organization_id,
        summary={"rows": len(rows)},
    )
    _tenant_audit(ctx, g, "support.members_viewed")
    return [MemberView(**r) for r in rows]


@router.get("/grants/{grant_id}/audit-events", response_model=list[AuditView], operation_id="platform_support_audit")
def view_org_audit(
    grant_id: uuid.UUID,
    ctx: Annotated[PlatformCtx, padmin("platform.support.view_data")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditView]:
    g = _usable(ctx, grant_id)
    rows = (
        ctx.conn.execute(
            text(
                "SELECT id, created_at, actor_type, action, entity_type, entity_id, summary FROM audit_events"
                " WHERE organization_id = :o ORDER BY created_at DESC, id DESC LIMIT :n"
            ),
            {"o": g.organization_id, "n": limit},
        )
        .mappings()
        .all()
    )
    paudit(
        ctx,
        "support.audit_viewed",
        target_type="support_grant",
        target_id=grant_id,
        org=g.organization_id,
        summary={"rows": len(rows)},
    )
    _tenant_audit(ctx, g, "support.audit_viewed")
    return [AuditView(**r) for r in rows]
