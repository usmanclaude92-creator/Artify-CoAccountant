"""The caller's own profile and devices."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import audit, entitlements
from artify_api.context import RequestCtx, Requires, resolve_principal
from artify_api.errors import NotFound, Unprocessable
from artify_api.idempotency import run_idempotent
from artify_api.ids import new_id
from artify_api.permissions import (
    ACCOUNTANT_ROLES,
    ALL_COMPANY_ROLES,
    ASSIGNABLE_ROLES,
    INVITABLE_ORG_ROLES,
    MFA_REQUIRED_ROLES,
    ORG_ROLE_RANK,
    PERMISSIONS,
    SYSTEM_ROLES,
)
from artify_api.ratelimit import enforce
from artify_api.routers.schemas import PermissionOut, RoleOut
from artify_api.security import passwords

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
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None


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
    body: DeviceCreate,
    ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    assert ctx.org_id is not None
    org_id = ctx.org_id

    def work() -> tuple[int, dict[str, object]]:
        entitlements.guard(ctx.conn, org_id, "limit.devices")
        device_id = new_id()
        ctx.conn.execute(
            text(
                "INSERT INTO devices (id, user_id, organization_id, platform, name, app_version, key_fingerprint,"
                " last_seen_at) VALUES (:id, :u, :o, :p, :n, :v, :k, now())"
            ),
            {
                "id": device_id,
                "u": ctx.user_id,
                "o": org_id,
                "p": body.platform,
                "n": body.name,
                "v": body.app_version,
                "k": body.key_fingerprint,
            },
        )
        # The registering session becomes device-bound, so revoking the device ends it (and its refresh tokens).
        ctx.conn.execute(
            text("UPDATE sessions SET device_id = :d WHERE id = :s AND user_id = :u AND device_id IS NULL"),
            {"d": device_id, "s": ctx.session_id, "u": ctx.user_id},
        )
        entitlements.refresh_counter(ctx.conn, org_id, "limit.devices")
        audit.write_audit(
            ctx.conn,
            action="device.registered",
            actor_id=ctx.user_id,
            org_id=org_id,
            entity_type="device",
            entity_id=device_id,
            summary={"platform": body.platform},
            meta=ctx.meta,
        )
        now = datetime.now(UTC)
        out = DeviceOut(
            id=device_id,
            platform=body.platform,
            name=body.name,
            app_version=body.app_version,
            status="active",
            registered_at=now,
            last_seen_at=now,
        )
        return 201, out.model_dump(mode="json")

    status, payload = run_idempotent(ctx, idempotency_key, body.model_dump(), work)
    return JSONResponse(payload, status_code=status)


@router.get("/devices", response_model=list[DeviceOut], operation_id="devices_list")
def list_devices(
    ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))],
) -> list[DeviceOut]:
    rows = (
        ctx.conn.execute(
            text(
                "SELECT id, platform, name, app_version, status, created_at AS registered_at, last_seen_at FROM devices"
                " WHERE user_id = :u AND organization_id = :o ORDER BY created_at DESC"
            ),
            {"u": ctx.user_id, "o": ctx.org_id},
        )
        .mappings()
        .all()
    )
    return [DeviceOut(**r) for r in rows]


@router.post("/devices/{device_id}/session", response_model=DeviceOut, operation_id="devices_bind_session")
def bind_device_session(
    device_id: uuid.UUID, ctx: Annotated[RequestCtx, Depends(Requires("devices.manage_own", org_source="header"))]
) -> DeviceOut:
    """Bind the caller's CURRENT session to one of their own ACTIVE devices and record activity (also the heartbeat).
    A revoked or foreign device answers 404, so a revoked installation cannot re-attach a new session to itself."""
    assert ctx.org_id is not None
    row = (
        ctx.conn.execute(
            text(
                "UPDATE devices SET last_seen_at = now() WHERE id = :d AND user_id = :u AND organization_id = :o"
                " AND status = 'active' RETURNING id, platform, name, app_version, status,"
                " created_at AS registered_at, last_seen_at"
            ),
            {"d": device_id, "u": ctx.user_id, "o": ctx.org_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("Device not found.")
    ctx.conn.execute(
        text("UPDATE sessions SET device_id = :d WHERE id = :s AND user_id = :u"),
        {"d": device_id, "s": ctx.session_id, "u": ctx.user_id},
    )
    return DeviceOut(**row)


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


@router.get("/roles", response_model=list[RoleOut], operation_id="roles_list")
def list_roles(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="static catalog of system roles; contains no tenant data"))],
) -> list[RoleOut]:
    return [
        RoleOut(
            key=key,
            scope=scope,
            name=name,
            permissions=sorted(perms),
            rank=ORG_ROLE_RANK.get(key),
            invitable=key in INVITABLE_ORG_ROLES,
            assignable_to_companies=key in ASSIGNABLE_ROLES,
            reaches_all_companies=key in ALL_COMPANY_ROLES,
            mfa_required=key in MFA_REQUIRED_ROLES,
            counts_as_accountant=key in ACCOUNTANT_ROLES,
        )
        for key, (scope, name, perms) in SYSTEM_ROLES.items()
    ]


class ProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    locale: Literal["en", "ar"] | None = None


@router.patch("/me", response_model=MeOut, operation_id="me_update")
def update_me(
    body: ProfilePatch,
    ctx: Annotated[RequestCtx, Depends(Requires(reason="edits the caller's own account profile"))],
    x_organization_id: Annotated[uuid.UUID | None, Header()] = None,
) -> MeOut:
    """Change your own display name or language. E-mail and roles are not editable here."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise Unprocessable("Provide display_name and/or locale.")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    ctx.conn.execute(text(f"UPDATE users SET {sets}, updated_at = now() WHERE id = :u"), {**changes, "u": ctx.user_id})
    audit.write_user_event(
        ctx.conn,
        actor_id=ctx.user_id,
        action="user.profile_updated",
        summary={"fields": sorted(changes)},
        meta=ctx.meta,
    )
    return get_me(ctx, x_organization_id)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/me/password", status_code=204, operation_id="me_change_password")
def change_password(
    body: PasswordChange, ctx: Annotated[RequestCtx, Depends(Requires(reason="changes the caller's own password"))]
) -> Response:
    """Needs the current password. Every OTHER session of the user is signed out; this one continues."""
    enforce(f"pwchange:user:{ctx.user_id}", 5, 900)
    stored = ctx.conn.execute(
        text("SELECT password_hash FROM auth_credentials WHERE user_id = :u"), {"u": ctx.user_id}
    ).scalar()
    if not passwords.verify_password(stored, body.current_password):
        raise Unprocessable("The current password is not correct.")
    email = ctx.conn.execute(text("SELECT email::text FROM users WHERE id = :u"), {"u": ctx.user_id}).scalar_one()
    passwords.validate_password_policy(body.new_password, email=email)
    ctx.conn.execute(
        text("UPDATE auth_credentials SET password_hash = :h WHERE user_id = :u"),
        {"h": passwords.hash_password(body.new_password), "u": ctx.user_id},
    )
    revoked = ctx.conn.execute(
        text(
            "UPDATE sessions SET revoked_at = now(), revoke_reason = 'password_changed' WHERE user_id = :u AND id <> :s AND revoked_at IS NULL"
        ),
        {"u": ctx.user_id, "s": ctx.session_id},
    ).rowcount
    audit.write_user_event(
        ctx.conn,
        actor_id=ctx.user_id,
        action="user.password_changed",
        summary={"other_sessions_revoked": revoked},
        meta=ctx.meta,
    )
    return Response(status_code=204)


@router.get("/permissions", response_model=list[PermissionOut], operation_id="permissions_list")
def list_permissions(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="static permission catalog; contains no tenant data"))],
) -> list[PermissionOut]:
    return [
        PermissionOut(key=p.key, scope=p.scope, description=p.description) for p in PERMISSIONS if p.scope != "platform"
    ]
