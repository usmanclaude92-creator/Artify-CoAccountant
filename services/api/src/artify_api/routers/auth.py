"""Tenant-realm authentication endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from artify_api import audit, auth_service, entitlements
from artify_api.context import Public, RequestCtx, Requires, request_meta
from artify_api.db import DbContext, app_engine, transaction
from artify_api.errors import NotFound, Unauthenticated, Unprocessable
from artify_api.ids import new_id
from artify_api.ratelimit import enforce
from artify_api.routers.schemas import (
    LoginRequest,
    LoginResponse,
    MessageOut,
    MfaConfirmRequest,
    MfaConfirmResponse,
    MfaEnrollResponse,
    MfaVerifyRequest,
    RefreshRequest,
    SessionOut,
    TokenResponse,
)
from artify_api.security import passwords
from artify_api.security.tokens import decode_token, hash_token

router = APIRouter(prefix="/auth", tags=["auth"])


def to_token_response(t: auth_service.TokenPair) -> TokenResponse:
    return TokenResponse(access_token=t.access_token, refresh_token=t.refresh_token, expires_in=t.expires_in, mfa=t.mfa)


def to_login_response(r: auth_service.LoginResult) -> LoginResponse:
    if r.mfa_required:
        return LoginResponse(status="mfa_required", mfa_token=r.mfa_token)
    assert r.tokens is not None
    return LoginResponse(
        status="ok", tokens=to_token_response(r.tokens), mfa_enrollment_required=r.mfa_enrollment_required
    )


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    organization_name: str = Field(min_length=2, max_length=160)
    organization_type: str = Field(pattern="^(accounting_firm|independent_accountant)$")
    country: str = Field(pattern="^[A-Z]{2}$")
    default_currency: str = Field(pattern="^[A-Z]{3}$")
    locale: str = Field(default="en", pattern="^(en|ar)$")


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(max_length=256)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=256)


@router.post(
    "/signup", status_code=202, response_model=MessageOut, dependencies=[Depends(Public("self-service signup"))]
)
def signup(body: SignupRequest, request: Request) -> MessageOut:
    auth_service.signup(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        organization_name=body.organization_name,
        organization_type=body.organization_type,
        country=body.country,
        default_currency=body.default_currency,
        locale=body.locale,
        meta=request_meta(request),
    )
    return MessageOut(status="verification_email_sent")


@router.post("/verify-email", status_code=204, dependencies=[Depends(Public("email verification link"))])
def verify_email(body: TokenBody) -> Response:
    auth_service.verify_email(body.token)
    return Response(status_code=204)


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(Public("credential login"))])
def login(body: LoginRequest, request: Request) -> LoginResponse:
    return to_login_response(auth_service.login("tenant", body.email, body.password, request_meta(request)))


@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    dependencies=[Depends(Public("second login step; presents a signed challenge token"))],
)
def mfa_verify(body: MfaVerifyRequest, request: Request) -> TokenResponse:
    return to_token_response(auth_service.verify_mfa("tenant", body.mfa_token, body.code, request_meta(request)))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(Public("refresh-token rotation; the token is the credential"))],
)
def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    enforce(f"refresh:ip:{request_meta(request).ip}", 120, 60)
    return to_token_response(auth_service.refresh("tenant", body.refresh_token))


@router.post("/logout", status_code=204)
def logout(ctx: Annotated[RequestCtx, Depends(Requires(reason="ends the caller's own session"))]) -> Response:
    auth_service.revoke_session(ctx.conn, ctx.session_id, "logout")
    audit.write_audit(ctx.conn, action="auth.logout", actor_id=ctx.user_id, meta=ctx.meta)
    return Response(status_code=204)


@router.post(
    "/password-reset/request",
    status_code=202,
    response_model=MessageOut,
    dependencies=[Depends(Public("password reset request"))],
)
def password_reset_request(body: PasswordResetRequest, request: Request) -> MessageOut:
    auth_service.request_password_reset(body.email, request_meta(request))
    return MessageOut(status="reset_email_sent_if_account_exists")


@router.post("/password-reset/confirm", status_code=204, dependencies=[Depends(Public("password reset link"))])
def password_reset_confirm(body: PasswordResetConfirm, request: Request) -> Response:
    auth_service.confirm_password_reset(body.token, body.new_password, request_meta(request))
    return Response(status_code=204)


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
def mfa_enroll(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="MFA enrollment for the caller"))],
) -> MfaEnrollResponse:
    email = ctx.conn.execute(text("SELECT email FROM users WHERE id = :u"), {"u": ctx.user_id}).scalar_one()
    secret, uri = auth_service.mfa_enroll(ctx.conn, "tenant", ctx.user_id, str(email))
    return MfaEnrollResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/confirm", response_model=MfaConfirmResponse)
def mfa_confirm(
    body: MfaConfirmRequest, ctx: Annotated[RequestCtx, Depends(Requires(reason="MFA enrollment for the caller"))]
) -> MfaConfirmResponse:
    codes, pair = auth_service.mfa_confirm(ctx.conn, "tenant", ctx.user_id, ctx.session_id, body.code)
    audit.write_audit(ctx.conn, action="auth.mfa_enabled", actor_id=ctx.user_id, meta=ctx.meta)
    return MfaConfirmResponse(recovery_codes=codes, access_token=pair.access_token, expires_in=pair.expires_in)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    ctx: Annotated[RequestCtx, Depends(Requires(reason="lists the caller's own sessions"))],
) -> list[SessionOut]:
    rows = ctx.conn.execute(
        text(
            "SELECT id, created_at, last_used_at, expires_at, mfa_verified_at IS NOT NULL AS mfa, user_agent"
            " FROM sessions WHERE user_id = :u AND revoked_at IS NULL AND expires_at > now()"
            " ORDER BY created_at DESC"
        ),
        {"u": ctx.user_id},
    ).all()
    return [
        SessionOut(
            id=r.id,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
            mfa_verified=r.mfa,
            user_agent=r.user_agent,
            current=r.id == ctx.session_id,
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: uuid.UUID,
    ctx: Annotated[RequestCtx, Depends(Requires(reason="revokes one of the caller's own sessions"))],
) -> Response:
    if not auth_service.revoke_session(ctx.conn, session_id, "user_revoked"):
        raise NotFound("Session not found.")
    audit.write_audit(
        ctx.conn,
        action="auth.session_revoked",
        actor_id=ctx.user_id,
        entity_type="session",
        entity_id=session_id,
        meta=ctx.meta,
    )
    return Response(status_code=204)


@router.post(
    "/invitations/accept",
    response_model=MessageOut,
    dependencies=[Depends(Public("invitation token is the credential; existing users also present a bearer token"))],
)
def accept_invitation(body: AcceptInvitationRequest, request: Request) -> MessageOut:
    meta = request_meta(request)
    enforce(f"invite_accept:ip:{meta.ip}", 20, 3600)
    bearer_user: uuid.UUID | None = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        bearer_user = decode_token("tenant", header[7:].strip()).subject
    digest = hash_token(body.token)
    with transaction(app_engine()) as conn:
        inv = conn.execute(text("SELECT * FROM app.auth_invitation_by_hash(:h)"), {"h": digest}).first()
        if inv is None or inv.accepted_at is not None or inv.revoked_at is not None:
            raise Unprocessable("This invitation is invalid, already used or revoked.")
        if not conn.execute(text("SELECT CAST(:e AS timestamptz) > now()"), {"e": inv.expires_at}).scalar():
            raise Unprocessable("This invitation has expired.")
        user = conn.execute(text("SELECT * FROM app.auth_user_by_email(:e)"), {"e": inv.email}).first()
        if user is not None:
            if bearer_user != user.id:
                raise Unauthenticated("Sign in with the invited account to accept this invitation.")
            user_id = user.id
        else:
            if not body.password or not body.display_name:
                raise Unprocessable("display_name and password are required to create the account.")
            passwords.validate_password_policy(body.password, email=str(inv.email))
            user_id = new_id()
        is_company = inv.company_id is not None
        conn.execute(
            text(
                "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true),"
                " set_config('app.company_scope', :s, true), set_config('app.company_ids', :c, true)"
            ),
            {
                "u": str(user_id),
                "o": str(inv.organization_id),
                "s": "list" if is_company else "none",
                "c": str(inv.company_id) if is_company else "",
            },
        )
        if user is None:
            conn.execute(
                text("INSERT INTO users (id, email, display_name, email_verified_at) VALUES (:id, :e, :n, now())"),
                {"id": user_id, "e": inv.email, "n": body.display_name},
            )
            conn.execute(
                text("INSERT INTO auth_credentials (user_id, password_hash) VALUES (:u, :h)"),
                {"u": user_id, "h": passwords.hash_password(body.password or "")},
            )
        role_id = inv.company_role_id if is_company else inv.org_role_id
        role_key = conn.execute(text("SELECT key FROM roles WHERE id = :r"), {"r": role_id}).scalar_one()
        already_counted = conn.execute(
            text(
                "SELECT 1 FROM organization_memberships WHERE user_id = :u AND organization_id = :o AND status = 'active'"
                " UNION ALL SELECT 1 FROM company_memberships WHERE user_id = :u AND organization_id = :o"
                " AND status = 'active'"
            ),
            {"u": user_id, "o": inv.organization_id},
        ).first()
        if already_counted is None:
            entitlements.guard(conn, inv.organization_id, "limit.users")
        if role_key in ("accountant", "staff"):
            entitlements.guard(conn, inv.organization_id, "limit.accountants")
        if is_company:
            conn.execute(
                text(
                    "INSERT INTO company_memberships (id, organization_id, company_id, user_id, role_id)"
                    " VALUES (:id, :o, :c, :u, :r)"
                    " ON CONFLICT (company_id, user_id) DO UPDATE SET role_id = EXCLUDED.role_id, status = 'active'"
                ),
                {"id": new_id(), "o": inv.organization_id, "c": inv.company_id, "u": user_id, "r": role_id},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO organization_memberships (id, organization_id, user_id, role_id)"
                    " VALUES (:id, :o, :u, :r)"
                    " ON CONFLICT (organization_id, user_id) DO UPDATE SET role_id = EXCLUDED.role_id,"
                    " status = 'active', updated_at = now()"
                ),
                {"id": new_id(), "o": inv.organization_id, "u": user_id, "r": role_id},
            )
        conn.execute(text("UPDATE invitations SET accepted_at = now() WHERE id = :i"), {"i": inv.id})
        for key in ("limit.users", "limit.accountants"):
            entitlements.refresh_counter(conn, inv.organization_id, key)
        audit.write_audit(
            conn,
            action="invitation.accepted",
            actor_id=user_id,
            org_id=inv.organization_id,
            company_id=inv.company_id,
            entity_type="invitation",
            entity_id=inv.id,
            summary={"role": role_key},
            meta=meta,
        )
    return MessageOut(status="accepted")


__all__ = ["DbContext", "router", "to_login_response", "to_token_response"]
