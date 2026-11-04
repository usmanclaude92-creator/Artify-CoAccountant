"""Customer-side authorization of Artify support access.

Only the firm owner (permission `support_grants.manage`) can approve, deny or revoke. Nothing is shared
until they approve; approval fixes the scope (`metadata` or the explicitly confirmed `read`) and a duration
no longer than the request. Access ends at expiry or on revoke, whichever comes first.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import audit
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Conflict, NotFound, Unprocessable

router = APIRouter(tags=["support-access"])

_EFFECTIVE = (
    "CASE WHEN g.status = 'active' AND g.expires_at <= now() THEN 'expired'"
    " WHEN g.status = 'requested' AND g.created_at < now() - interval '14 days' THEN 'expired' ELSE g.status END"
)


class SupportGrantOut(BaseModel):
    id: uuid.UUID
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


class Approve(BaseModel):
    access_level: Literal["metadata", "read"] = "metadata"
    hours: int = Field(ge=1, le=720)
    confirm_sensitive: bool = False
    note: str | None = Field(default=None, max_length=500)


class Deny(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class Revoke(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


_SELECT = (
    "SELECT g.id, g.requester_name, g.access_level, g.approved_level, g.reason, g.requested_hours,"
    f" {_EFFECTIVE} AS status, g.starts_at, g.expires_at, g.decided_at, g.decision_note, g.revoked_at,"
    " g.revoked_reason, g.revoked_by_type, g.created_at FROM support_access_grants g"
)


def _grant(ctx: RequestCtx, org_id: uuid.UUID, grant_id: uuid.UUID) -> SupportGrantOut:
    r = (
        ctx.conn.execute(text(f"{_SELECT} WHERE g.id = :i AND g.organization_id = :o"), {"i": grant_id, "o": org_id})
        .mappings()
        .first()
    )
    if r is None:
        raise NotFound("Support request not found.")
    return SupportGrantOut(**r)


@router.get("/orgs/{org_id}/support-grants", response_model=list[SupportGrantOut], operation_id="support_grants_list")
def list_grants(
    org_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("support_grants.manage"))]
) -> list[SupportGrantOut]:
    rows = ctx.conn.execute(
        text(f"{_SELECT} WHERE g.organization_id = :o ORDER BY g.created_at DESC LIMIT 100"), {"o": org_id}
    ).mappings()
    return [SupportGrantOut(**r) for r in rows]


@router.post(
    "/orgs/{org_id}/support-grants/{grant_id}/approve",
    response_model=SupportGrantOut,
    operation_id="support_grants_approve",
)
def approve(
    org_id: uuid.UUID,
    grant_id: uuid.UUID,
    body: Approve,
    ctx: Annotated[RequestCtx, Depends(Requires("support_grants.manage"))],
) -> SupportGrantOut:
    g = _grant(ctx, org_id, grant_id)
    if g.status != "requested":
        raise Conflict(f"This request is {g.status} and can no longer be approved.")
    if body.access_level == "read" and g.access_level != "read":
        raise Unprocessable("You cannot approve a broader level than was requested.")
    if body.access_level == "read" and not body.confirm_sensitive:
        raise Unprocessable("Approving access to sensitive data needs explicit confirmation (confirm_sensitive).")
    if body.hours > g.requested_hours:
        raise Unprocessable(f"You can approve at most the {g.requested_hours} hours that were requested.")
    ctx.conn.execute(
        text(
            "UPDATE support_access_grants SET status = 'active', approved_by = :u, approved_level = :l, starts_at = now(),"
            " expires_at = now() + make_interval(hours => :h), decided_at = now(), decision_note = :n WHERE id = :i"
        ),
        {"u": ctx.user_id, "l": body.access_level, "h": body.hours, "n": body.note, "i": grant_id},
    )
    audit.write_audit(
        ctx.conn,
        action="support.grant_approved",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="support_grant",
        entity_id=grant_id,
        summary={"level": body.access_level, "hours": body.hours},
        meta=ctx.meta,
    )
    return _grant(ctx, org_id, grant_id)


@router.post(
    "/orgs/{org_id}/support-grants/{grant_id}/deny", response_model=SupportGrantOut, operation_id="support_grants_deny"
)
def deny(
    org_id: uuid.UUID,
    grant_id: uuid.UUID,
    body: Deny,
    ctx: Annotated[RequestCtx, Depends(Requires("support_grants.manage"))],
) -> SupportGrantOut:
    g = _grant(ctx, org_id, grant_id)
    if g.status != "requested":
        raise Conflict(f"This request is {g.status} and can no longer be denied.")
    ctx.conn.execute(
        text(
            "UPDATE support_access_grants SET status = 'denied', decided_at = now(), decision_note = :n WHERE id = :i"
        ),
        {"n": body.note, "i": grant_id},
    )
    audit.write_audit(
        ctx.conn,
        action="support.grant_denied",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="support_grant",
        entity_id=grant_id,
        meta=ctx.meta,
    )
    return _grant(ctx, org_id, grant_id)


@router.post(
    "/orgs/{org_id}/support-grants/{grant_id}/revoke",
    response_model=SupportGrantOut,
    operation_id="support_grants_revoke",
)
def revoke(
    org_id: uuid.UUID,
    grant_id: uuid.UUID,
    body: Revoke,
    ctx: Annotated[RequestCtx, Depends(Requires("support_grants.manage"))],
) -> SupportGrantOut:
    g = _grant(ctx, org_id, grant_id)
    if g.status != "active":
        raise Conflict(f"Only an active grant can be revoked (this one is {g.status}).")
    ctx.conn.execute(
        text(
            "UPDATE support_access_grants SET status = 'revoked', revoked_at = now(), revoked_reason = :r, revoked_by_type = 'customer' WHERE id = :i"
        ),
        {"r": body.reason, "i": grant_id},
    )
    audit.write_audit(
        ctx.conn,
        action="support.grant_revoked",
        actor_id=ctx.user_id,
        org_id=org_id,
        entity_type="support_grant",
        entity_id=grant_id,
        summary={"reason": body.reason},
        meta=ctx.meta,
    )
    return _grant(ctx, org_id, grant_id)
