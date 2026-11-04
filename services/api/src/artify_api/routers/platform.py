"""Platform realm (Artify Super Admin) endpoints. Phase 1 ships authentication, the admin's own
profile and a metadata-only organization list; the full Super Admin arrives in Phase 2."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from artify_api import audit, auth_service
from artify_api.context import PlatformCtx, PlatformRequires, Public, request_meta
from artify_api.errors import Conflict
from artify_api.ratelimit import enforce
from artify_api.routers.auth import to_login_response, to_token_response
from artify_api.routers.schemas import (
    LoginRequest,
    LoginResponse,
    MfaConfirmRequest,
    MfaConfirmResponse,
    MfaEnrollResponse,
    MfaVerifyRequest,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/platform", tags=["platform"])


class PlatformMe(BaseModel):
    admin_id: uuid.UUID
    email: str
    display_name: str
    role: str
    mfa_verified: bool


class OrgSummary(BaseModel):
    id: uuid.UUID
    type: str
    display_name: str
    country: str
    status: str


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    operation_id="platform_login",
    dependencies=[Depends(Public("platform credential login"))],
)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    return to_login_response(auth_service.login("platform", body.email, body.password, request_meta(request)))


@router.post(
    "/auth/mfa/verify",
    response_model=TokenResponse,
    operation_id="platform_mfa_verify",
    dependencies=[Depends(Public("second login step; presents a signed challenge token"))],
)
def mfa_verify(body: MfaVerifyRequest, request: Request) -> TokenResponse:
    return to_token_response(auth_service.verify_mfa("platform", body.mfa_token, body.code, request_meta(request)))


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    operation_id="platform_refresh",
    dependencies=[Depends(Public("refresh-token rotation; the token is the credential"))],
)
def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    enforce(f"refresh:platform:ip:{request_meta(request).ip}", 60, 60)
    return to_token_response(auth_service.refresh("platform", body.refresh_token))


@router.post("/auth/logout", status_code=204, operation_id="platform_logout")
def logout(
    ctx: Annotated[PlatformCtx, Depends(PlatformRequires(allow_unenrolled=True, reason="ends own session"))],
) -> Response:
    auth_service.revoke_session(ctx.conn, ctx.session_id, "logout")
    audit.write_platform_audit(ctx.conn, action="auth.logout", actor_id=ctx.admin_id, meta=ctx.meta)
    return Response(status_code=204)


@router.post("/auth/mfa/enroll", response_model=MfaEnrollResponse, operation_id="platform_mfa_enroll")
def mfa_enroll(
    ctx: Annotated[PlatformCtx, Depends(PlatformRequires(allow_unenrolled=True, reason="MFA enrollment"))],
) -> MfaEnrollResponse:
    email = ctx.conn.execute(text("SELECT email FROM platform_admins WHERE id = :a"), {"a": ctx.admin_id}).scalar_one()
    secret, uri = auth_service.mfa_enroll(ctx.conn, "platform", ctx.admin_id, str(email))
    return MfaEnrollResponse(secret=secret, otpauth_uri=uri)


@router.post("/auth/mfa/confirm", response_model=MfaConfirmResponse, operation_id="platform_mfa_confirm")
def mfa_confirm(
    body: MfaConfirmRequest,
    ctx: Annotated[PlatformCtx, Depends(PlatformRequires(allow_unenrolled=True, reason="MFA enrollment"))],
) -> MfaConfirmResponse:
    if ctx.mfa:
        raise Conflict("Multi-factor authentication is already verified for this session.")
    codes, pair = auth_service.mfa_confirm(ctx.conn, "platform", ctx.admin_id, ctx.session_id, body.code)
    audit.write_platform_audit(ctx.conn, action="auth.mfa_enabled", actor_id=ctx.admin_id, meta=ctx.meta)
    return MfaConfirmResponse(recovery_codes=codes, access_token=pair.access_token, expires_in=pair.expires_in)


@router.get("/me", response_model=PlatformMe, operation_id="platform_me")
def me(ctx: Annotated[PlatformCtx, Depends(PlatformRequires("platform.self.read"))]) -> PlatformMe:
    row = ctx.conn.execute(
        text("SELECT email, display_name, role_key FROM platform_admins WHERE id = :a"), {"a": ctx.admin_id}
    ).one()
    return PlatformMe(
        admin_id=ctx.admin_id,
        email=str(row.email),
        display_name=row.display_name,
        role=row.role_key,
        mfa_verified=ctx.mfa,
    )


@router.get("/organizations", response_model=list[OrgSummary], operation_id="platform_organizations")
def organizations(
    ctx: Annotated[PlatformCtx, Depends(PlatformRequires("platform.organizations.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OrgSummary]:
    rows = (
        ctx.conn.execute(
            text("SELECT id, type, display_name, country, status FROM organizations ORDER BY created_at DESC LIMIT :n"),
            {"n": limit},
        )
        .mappings()
        .all()
    )
    return [OrgSummary(**r) for r in rows]
