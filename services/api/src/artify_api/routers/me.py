"""The caller's own profile and devices."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import audit, entitlements
from artify_api.context import RequestCtx, Requires, resolve_principal
from artify_api.errors import NotFound
from artify_api.ids import new_id
from artify_api.permissions import SYSTEM_ROLES

router = APIRouter(tags=["me"])


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_status: str
    organization_role: str | None
    company_roles: dict[uuid.UUID, str]


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    locale: str
    mfa_enabled: bool
    session_mfa_verified: bool
    memberships: list[MembershipOut]
    active_organization_id: uuid.UUID | None = None
    permissions: list[str] = Field(default_factory=list)


class DeviceCreate(BaseModel):
    platform: Literal["windows", "android", "ios", "web"]
    name: str = Field(min_length=1, max_length=120)
    app_version: str | None = Field(default=None, max_length=40)
    key_fingerprint: str | None = Field(default=None, max_length=128)


class DeviceOut(BaseModel):
    id: uuid.UUID
    platform: str
    name: str
    app_version: str | None
    status: str


@router.get("/me", response_model=MeOut, operation_id="me_get")
def get_me(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="returns the caller's own profile"))],
    x_organization_id: Annotated[uuid.UUID | None, Header()] = None,
) -> MeOut:
    user = ctx.conn.execute(
        text("SELECT email, display_name, locale FROM users WHERE id = :u"), {"u": ctx.user_id}
    ).one()
    mfa_enabled = bool(
        ctx.conn.execute(
            text("SELECT 1 FROM mfa_factors WHERE user_id = :u AND confirmed_at IS NOT NULL"), {"u": ctx.user_id}
        ).scalar()
    )
    orgs = ctx.conn.execute(
        text(
            "SELECT o.id, o.display_name, o.status, r.key AS org_role FROM organizations o"
            " LEFT JOIN organization_memberships m ON m.organization_id = o.id AND m.user_id = :u"
            " AND m.status = 'active' LEFT JOIN roles r ON r.id = m.role_id ORDER BY o.display_name"
        ),
        {"u": ctx.user_id},
    ).all()
    company_roles: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
    for row in ctx.conn.execute(
        text(
            "SELECT cm.organization_id, cm.company_id, r.key FROM company_memberships cm"
            " JOIN roles r ON r.id = cm.role_id WHERE cm.user_id = :u AND cm.status = 'active'"
        ),
        {"u": ctx.user_id},
    ):
        company_roles.setdefault(row.organization_id, {})[row.company_id] = row.key
    memberships = [
        MembershipOut(
            organization_id=o.id,
            organization_name=o.display_name,
            organization_status=o.status,
            organization_role=o.org_role,
            company_roles=company_roles.get(o.id, {}),
        )
        for o in orgs
    ]
    permissions: list[str] = []
    if x_organization_id is not None:
        principal = resolve_principal(ctx.conn, ctx.user_id, x_organization_id, ctx.mfa)
        if principal is None:
            raise NotFound("Organization not found.")
        perms: set[str] = set()
        if principal.org_role:
            perms |= SYSTEM_ROLES[principal.org_role][2]
        for role_key in principal.company_roles.values():
            perms |= SYSTEM_ROLES[role_key][2]
        permissions = sorted(perms)
    return MeOut(
        user_id=ctx.user_id,
        email=str(user.email),
        display_name=user.display_name,
        locale=user.locale,
        mfa_enabled=mfa_enabled,
        session_mfa_verified=ctx.mfa,
        memberships=memberships,
        active_organization_id=x_organization_id,
        permissions=permissions,
    )


@router.post("/devices", response_model=DeviceOut, status_code=201, operation_id="devices_register")
def register_device(
    body: DeviceCreate, ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))]
) -> DeviceOut:
    assert ctx.org_id is not None
    entitlements.guard(ctx.conn, ctx.org_id, "limit.devices")
    device_id = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO devices (id, user_id, organization_id, platform, name, app_version, key_fingerprint,"
            " last_seen_at) VALUES (:id, :u, :o, :p, :n, :v, :k, now())"
        ),
        {
            "id": device_id,
            "u": ctx.user_id,
            "o": ctx.org_id,
            "p": body.platform,
            "n": body.name,
            "v": body.app_version,
            "k": body.key_fingerprint,
        },
    )
    entitlements.refresh_counter(ctx.conn, ctx.org_id, "limit.devices")
    audit.write_audit(
        ctx.conn,
        action="device.registered",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        entity_type="device",
        entity_id=device_id,
        summary={"platform": body.platform},
        meta=ctx.meta,
    )
    return DeviceOut(
        id=device_id, platform=body.platform, name=body.name, app_version=body.app_version, status="active"
    )


@router.get("/devices", response_model=list[DeviceOut], operation_id="devices_list")
def list_devices(
    ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))],
) -> list[DeviceOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT id, platform, name, app_version, status FROM devices"
                " WHERE user_id = :u AND organization_id = :o ORDER BY created_at DESC"
            ),
            {"u": ctx.user_id, "o": ctx.org_id},
        )
        .mappings()
        .all()
    )
    return [DeviceOut(**r) for r in rows]


@router.delete("/devices/{device_id}", status_code=204, operation_id="devices_revoke")
def revoke_device(
    device_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))]
) -> Response:
    assert ctx.org_id is not None
    res = ctx.conn.execute(
        text(
            "UPDATE devices SET status = 'revoked', revoked_at = now() WHERE id = :d AND user_id = :u"
            " AND organization_id = :o AND status = 'active'"
        ),
        {"d": device_id, "u": ctx.user_id, "o": ctx.org_id},
    )
    if not res.rowcount:
        raise NotFound("Device not found.")
    ctx.conn.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), revoke_reason = 'device_revoked'"
            " WHERE device_id = :d AND revoked_at IS NULL"
        ),
        {"d": device_id},
    )
    entitlements.refresh_counter(ctx.conn, ctx.org_id, "limit.devices")
    audit.write_audit(
        ctx.conn,
        action="device.revoked",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        entity_type="device",
        entity_id=device_id,
        meta=ctx.meta,
    )
    return Response(status_code=204)
