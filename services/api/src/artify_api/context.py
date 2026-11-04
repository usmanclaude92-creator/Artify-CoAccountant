"""Per-request authentication, membership resolution and authorization.

One database transaction per request (committed BEFORE the response is sent: dependencies use
scope="function"). The RLS context is derived server-side from validated memberships; client
supplied organization/company ids are only *claims* that must match a membership.
"""

from __future__ import annotations

import ipaddress
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request
from sqlalchemy import Connection, text

from artify_api import audit
from artify_api.db import DbContext, app_engine, apply_context, platform_engine, transaction
from artify_api.errors import Forbidden, MfaRequired, NotFound, Unauthenticated
from artify_api.permissions import PLATFORM_ROLES, SYSTEM_ROLES
from artify_api.policy import Principal, authorize
from artify_api.security.tokens import AccessClaims, decode_token

log = logging.getLogger("artify.context")


def _valid_ip(value: str | None) -> str | None:
    """Only well-formed IP literals reach the inet column (unix sockets / test clients give none).
    Behind a reverse proxy the deployment must configure trusted proxy headers (Phase 12)."""
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def request_meta(request: Request) -> audit.RequestMeta:
    client = _valid_ip(request.client.host if request.client else None)
    return audit.RequestMeta(
        ip=client,
        user_agent=request.headers.get("user-agent"),
        correlation_id=getattr(request.state, "correlation_id", None),
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("Missing bearer token.")
    return token.strip()


# ---------------------------------------------------------------- transactions
def tenant_conn() -> Iterator[Connection]:
    with app_engine().connect() as conn, conn.begin():
        yield conn


def platform_conn() -> Iterator[Connection]:
    with platform_engine().connect() as conn, conn.begin():
        yield conn


def record_denial(
    action: str,
    *,
    user_id: uuid.UUID | None,
    org_id: uuid.UUID | None,
    meta: audit.RequestMeta,
    summary: dict[str, object],
) -> None:
    """Persist a denied-access event in its OWN transaction (the request transaction rolls back).
    If `org_id` is a real organization of the caller the event is tenant-visible; otherwise it is
    stored unattributed (organization_id NULL) for the platform security view."""
    try:
        ctx = DbContext(user_id=user_id, org_id=org_id, company_scope="all" if org_id else None)
        with transaction(app_engine(), ctx) as conn:
            audit.write_audit(
                conn, action=action, actor_type="user", actor_id=user_id, org_id=org_id, summary=summary, meta=meta
            )
    except Exception:  # auditing must never turn a denial into a 500
        log.exception("failed to record denial audit")


def record_platform_denial(
    admin_id: uuid.UUID, meta: audit.RequestMeta, permission: str, role_key: str, path: str
) -> None:
    """Persist a denied platform action in its OWN transaction (the request transaction rolls back)."""
    try:
        with transaction(platform_engine(), DbContext(user_id=admin_id)) as conn:
            audit.write_platform_audit(
                conn,
                action="authz.denied",
                actor_id=admin_id,
                summary={"permission": permission, "role": role_key, "path": path},
                meta=meta,
                outcome="denied",
            )
    except Exception:  # auditing must never turn a denial into a 500
        log.exception("failed to record platform denial audit")


# ---------------------------------------------------------------- tenant context
@dataclass
class RequestCtx:
    conn: Connection
    user_id: uuid.UUID
    session_id: uuid.UUID
    mfa: bool
    meta: audit.RequestMeta
    org_id: uuid.UUID | None = None
    principal: Principal | None = None
    company_id: uuid.UUID | None = None
    method: str = "GET"


def authenticate_tenant(request: Request, conn: Connection) -> tuple[AccessClaims, bool]:
    claims = decode_token("tenant", _bearer(request))
    apply_context(conn, DbContext(user_id=claims.subject))
    row = conn.execute(
        text(
            "SELECT s.mfa_verified_at IS NOT NULL AS mfa, u.status AS user_status FROM sessions s"
            " JOIN users u ON u.id = s.user_id"
            " WHERE s.id = :sid AND s.user_id = :u AND s.revoked_at IS NULL AND s.expires_at > now()"
        ),
        {"sid": claims.session_id, "u": claims.subject},
    ).first()
    if row is None or row.user_status != "active":
        raise Unauthenticated("Session is no longer valid.")
    return claims, bool(row.mfa)


def _parse_uuid(raw: str | None, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError) as exc:
        raise NotFound(f"{what} not found.") from exc


def resolve_principal(conn: Connection, user_id: uuid.UUID, org_id: uuid.UUID, mfa: bool) -> Principal | None:
    """Load the caller's roles in `org_id`. Returns None when they have no membership at all."""
    org_role = conn.execute(
        text(
            "SELECT r.key FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
            " WHERE m.user_id = :u AND m.organization_id = :o AND m.status = 'active'"
        ),
        {"u": user_id, "o": org_id},
    ).scalar()
    company_roles = {
        row.company_id: row.key
        for row in conn.execute(
            text(
                "SELECT cm.company_id, r.key FROM company_memberships cm JOIN roles r ON r.id = cm.role_id"
                " WHERE cm.user_id = :u AND cm.organization_id = :o AND cm.status = 'active'"
            ),
            {"u": user_id, "o": org_id},
        )
    }
    # Assignments grant company visibility only while the person holds an ACTIVE organization membership and the
    # assignment window (starts_on .. ends_on) is open. Removed/suspended members keep no access through old rows.
    assigned = (
        frozenset(
            conn.execute(
                text(
                    "SELECT company_id FROM accountant_assignments WHERE user_id = :u AND organization_id = :o"
                    " AND starts_on <= current_date AND (ends_on IS NULL OR ends_on >= current_date)"
                ),
                {"u": user_id, "o": org_id},
            ).scalars()
        )
        if org_role is not None
        else frozenset()
    )
    if org_role is None and not company_roles:
        return None
    return Principal(
        user_id=user_id,
        org_id=org_id,
        org_role=org_role,
        company_roles=company_roles,
        assigned_company_ids=assigned,
        mfa_verified=mfa,
    )


class Public:
    """Marks a route as intentionally unauthenticated. The route-table test requires every route
    to carry either `Public` or `Requires`/`PlatformRequires`."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __call__(self) -> None:
        return None


class Requires:
    """Declares and enforces the permission a tenant-realm route needs.

    permission=None means "any authenticated tenant user" (profile, MFA, sessions); `reason`
    documents why no organization permission applies.
    org_source: 'path' (/orgs/{org_id}/...), 'header' (X-Organization-Id) or 'none'.
    """

    def __init__(
        self,
        permission: str | None = None,
        *,
        org_source: Literal["path", "header", "none"] = "path",
        company: bool = False,
        reason: str = "",
    ) -> None:
        if permission is None and not reason:
            raise ValueError("authenticated-only routes must document a reason")
        if company and org_source == "path":
            org_source = "header"
        self.permission = permission
        self.org_source = org_source if permission else "none"
        self.company = company
        self.reason = reason

    def __call__(self, request: Request, conn: Connection = Depends(tenant_conn, scope="function")) -> RequestCtx:
        meta = request_meta(request)
        claims, mfa = authenticate_tenant(request, conn)
        ctx = RequestCtx(
            conn=conn, user_id=claims.subject, session_id=claims.session_id, mfa=mfa, meta=meta, method=request.method
        )
        if self.permission is None:
            return ctx

        if self.org_source == "path":
            claimed = request.path_params.get("org_id")
        else:
            claimed = request.headers.get("x-organization-id")
        org_id = _parse_uuid(claimed, "Organization")
        principal = resolve_principal(conn, claims.subject, org_id, mfa)
        if principal is None:
            record_denial(
                "authz.denied",
                user_id=claims.subject,
                org_id=None,
                meta=meta,
                summary={"reason": "no_membership", "claimed_org": str(org_id), "path": request.url.path},
            )
            raise NotFound("Organization not found.")

        apply_context(
            conn,
            DbContext(
                user_id=claims.subject,
                org_id=org_id,
                company_scope=principal.company_scope,
                company_ids=sorted(principal.visible_company_ids, key=str),
            ),
        )
        ctx.org_id, ctx.principal = org_id, principal

        org_status = conn.execute(text("SELECT status FROM organizations WHERE id = :o"), {"o": org_id}).scalar()
        if org_status in (None, "closed"):
            raise NotFound("Organization not found.")
        if org_status == "suspended" and request.method not in ("GET", "HEAD", "OPTIONS"):
            raise Forbidden(
                "The organization is suspended; changes are not permitted.", extra={"organization_status": "suspended"}
            )

        company_id: uuid.UUID | None = None
        if self.company:
            company_id = _parse_uuid(request.path_params.get("company_id"), "Company")
        decision = authorize(principal, self.permission, company_id)
        if not decision.allowed:
            record_denial(
                "authz.denied",
                user_id=claims.subject,
                org_id=org_id,
                meta=meta,
                summary={
                    "permission": self.permission,
                    "reason": decision.reason,
                    "path": request.url.path,
                    "company_id": str(company_id) if company_id else None,
                },
            )
            if decision.reason == "company_out_of_scope":
                raise NotFound("Company not found.")
            if decision.reason == "mfa_required":
                raise MfaRequired("Complete multi-factor authentication to use this role.")
            raise Forbidden("You do not have permission to perform this action.", extra={"permission": self.permission})
        if company_id is not None:
            exists = conn.execute(text("SELECT 1 FROM companies WHERE id = :c"), {"c": company_id}).scalar()
            if not exists:  # RLS hides companies of other tenants/scope: same 404 as a real miss
                raise NotFound("Company not found.")
            ctx.company_id = company_id
        return ctx


# ---------------------------------------------------------------- platform context
@dataclass
class PlatformCtx:
    conn: Connection
    admin_id: uuid.UUID
    session_id: uuid.UUID
    role_key: str
    mfa: bool
    meta: audit.RequestMeta

    def can(self, permission: str) -> bool:
        return permission in PLATFORM_ROLES[self.role_key]


class PlatformRequires:
    """Platform-realm equivalent. Platform admins authenticate against a different issuer and
    audience, use a different DB role, and have no policy access to tenant business tables."""

    def __init__(self, permission: str | None = None, *, allow_unenrolled: bool = False, reason: str = "") -> None:
        if permission is None and not reason:
            raise ValueError("authenticated-only platform routes must document a reason")
        self.permission = permission
        self.allow_unenrolled = allow_unenrolled
        self.reason = reason

    def __call__(self, request: Request, conn: Connection = Depends(platform_conn, scope="function")) -> PlatformCtx:
        meta = request_meta(request)
        claims = decode_token("platform", _bearer(request))
        apply_context(conn, DbContext(user_id=claims.subject))
        row = conn.execute(
            text(
                "SELECT s.mfa_verified_at IS NOT NULL AS mfa, a.role_key, a.status FROM sessions s"
                " JOIN platform_admins a ON a.id = s.platform_admin_id"
                " WHERE s.id = :sid AND s.platform_admin_id = :a AND s.revoked_at IS NULL"
                " AND s.expires_at > now()"
            ),
            {"sid": claims.session_id, "a": claims.subject},
        ).first()
        if row is None or row.status != "active":
            raise Unauthenticated("Session is no longer valid.")
        if not row.mfa and not self.allow_unenrolled:
            raise MfaRequired("Platform administrators must complete multi-factor authentication.")
        ctx = PlatformCtx(
            conn=conn,
            admin_id=claims.subject,
            session_id=claims.session_id,
            role_key=row.role_key,
            mfa=bool(row.mfa),
            meta=meta,
        )
        if self.permission and self.permission not in PLATFORM_ROLES[row.role_key]:
            record_platform_denial(claims.subject, meta, self.permission, row.role_key, request.url.path)
            raise Forbidden("You do not have permission to perform this action.", extra={"permission": self.permission})
        return ctx


def role_permissions(role_key: str) -> list[str]:
    return sorted(SYSTEM_ROLES[role_key][2])
