"""Authentication flows shared by both realms: sessions, refresh rotation with reuse detection,
MFA enrollment/verification, password reset, e-mail verification.

Pre-authentication operations open their own short transactions; the context (RLS) is set to the
subject only after the credential/token has been verified."""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from artify_api import audit
from artify_api.db import DbContext, app_engine, platform_engine, transaction
from artify_api.errors import Conflict, Unauthenticated, Unprocessable
from artify_api.ids import new_id
from artify_api.mailer import Message, get_mailer
from artify_api.ratelimit import enforce
from artify_api.security import passwords, totp
from artify_api.security.tokens import (
    Realm,
    decode_token,
    hash_token,
    issue_token,
    new_opaque_token,
)
from artify_api.settings import get_settings

_ENGINE = {"tenant": app_engine, "platform": platform_engine}
_COL = {"tenant": "user_id", "platform": "platform_admin_id"}


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    mfa: bool
    session_id: uuid.UUID


def _engine(realm: Realm):  # type: ignore[no-untyped-def]
    return _ENGINE[realm]()


def _refresh_ttl(realm: Realm) -> int:
    s = get_settings()
    return s.tenant_refresh_ttl_seconds if realm == "tenant" else s.platform_refresh_ttl_seconds


# ------------------------------------------------------------------ sessions
def issue_session(
    conn: Connection,
    realm: Realm,
    subject: uuid.UUID,
    meta: audit.RequestMeta,
    *,
    mfa_verified: bool,
    device_id: uuid.UUID | None = None,
) -> TokenPair:
    """Create a session + first refresh token. `conn` must already carry the subject's context."""
    ttl = _refresh_ttl(realm)
    session_id = new_id()
    col = _COL[realm]
    conn.execute(
        text(
            f"INSERT INTO sessions (id, realm, {col}, device_id, ip, user_agent, mfa_verified_at, expires_at)"
            " VALUES (:id, :realm, :sub, :dev, CAST(:ip AS inet), :ua,"
            " CASE WHEN :mfa THEN now() END, now() + make_interval(secs => :ttl))"
        ),
        {
            "id": session_id,
            "realm": realm,
            "sub": subject,
            "dev": device_id,
            "ip": meta.ip,
            "ua": (meta.user_agent or "")[:300],
            "mfa": mfa_verified,
            "ttl": ttl,
        },
    )
    return _new_refresh(conn, realm, subject, session_id, mfa_verified, ttl)


def _new_refresh(
    conn: Connection, realm: Realm, subject: uuid.UUID, session_id: uuid.UUID, mfa: bool, ttl: int
) -> TokenPair:
    plaintext, digest = new_opaque_token()
    conn.execute(
        text(
            "INSERT INTO refresh_tokens (id, session_id, token_hash, expires_at)"
            " SELECT :id, s.id, :h, s.expires_at FROM sessions s WHERE s.id = :sid"
        ),
        {"id": new_id(), "sid": session_id, "h": digest},
    )
    access, expires_in = issue_token(realm, subject, session_id, mfa=mfa)
    return TokenPair(access, plaintext, expires_in, mfa, session_id)


def refresh(realm: Realm, refresh_token: str) -> TokenPair:
    """Rotate a refresh token. Presenting an already-used token revokes the whole session family."""
    digest = hash_token(refresh_token)
    reuse_of: tuple[uuid.UUID, uuid.UUID] | None = None
    with transaction(_engine(realm)) as conn:
        row = conn.execute(text("SELECT * FROM app.auth_refresh_token_by_hash(:h)"), {"h": digest}).first()
        if row is None:
            raise Unauthenticated("Invalid refresh token.")
        subject = row.user_id if realm == "tenant" else row.platform_admin_id
        if row.realm != realm or subject is None:
            raise Unauthenticated("Invalid refresh token.")
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(subject)})
        now_ok = conn.execute(
            text(
                "SELECT (CAST(:te AS timestamptz) > now()) AND (CAST(:se AS timestamptz) > now())"
                " AND CAST(:rv AS timestamptz) IS NULL"
            ),
            {"te": row.token_expires_at, "se": row.session_expires_at, "rv": row.session_revoked_at},
        ).scalar()
        if not now_ok:
            raise Unauthenticated("Refresh token expired or revoked.")
        claimed = conn.execute(
            text("UPDATE refresh_tokens SET used_at = now() WHERE id = :id AND used_at IS NULL RETURNING id"),
            {"id": row.token_id},
        ).first()
        if claimed is None:
            conn.execute(
                text(
                    "UPDATE sessions SET revoked_at = now(), revoke_reason = 'refresh_reuse'"
                    " WHERE id = :s AND revoked_at IS NULL"
                ),
                {"s": row.session_id},
            )
            reuse_of = (row.session_id, subject)
        else:
            active = conn.execute(
                text("SELECT status FROM " + ("users" if realm == "tenant" else "platform_admins") + " WHERE id = :u"),
                {"u": subject},
            ).scalar()
            if active != "active":
                raise Unauthenticated("Account is disabled.")
            mfa = bool(
                conn.execute(
                    text("SELECT mfa_verified_at IS NOT NULL FROM sessions WHERE id = :s"), {"s": row.session_id}
                ).scalar()
            )
            conn.execute(text("UPDATE sessions SET last_used_at = now() WHERE id = :s"), {"s": row.session_id})
            ttl = int(
                conn.execute(
                    text(
                        "SELECT GREATEST(1, EXTRACT(EPOCH FROM (expires_at - now()))::int) FROM sessions WHERE id = :s"
                    ),
                    {"s": row.session_id},
                ).scalar_one()
            )
            return _new_refresh(conn, realm, subject, row.session_id, mfa, ttl)
    # Outside the transaction so the revocation above is committed before we raise.
    assert reuse_of is not None
    _audit_security(realm, "auth.refresh_reuse_detected", reuse_of[1], {"session_id": str(reuse_of[0])})
    raise Unauthenticated("Refresh token reuse detected; the session has been revoked.")


def revoke_session(conn: Connection, session_id: uuid.UUID, reason: str) -> bool:
    res = conn.execute(
        text("UPDATE sessions SET revoked_at = now(), revoke_reason = :r WHERE id = :s AND revoked_at IS NULL"),
        {"s": session_id, "r": reason},
    )
    return bool(res.rowcount)


def _audit_security(
    realm: Realm, action: str, subject: uuid.UUID | None, summary: dict[str, Any], meta: audit.RequestMeta | None = None
) -> None:
    """Security events outside any tenant: organization_id NULL (tenant realm) or platform audit."""
    try:
        with transaction(_engine(realm), DbContext(user_id=subject)) as conn:
            if realm == "tenant":
                audit.write_audit(conn, action=action, actor_id=subject, summary=summary, meta=meta)
            else:
                audit.write_platform_audit(conn, action=action, actor_id=subject, summary=summary, meta=meta)
    except Exception:  # never let auditing mask the original outcome
        import logging

        logging.getLogger("artify.auth").exception("audit write failed")


# ------------------------------------------------------------------ login (both realms)
@dataclass(frozen=True)
class LoginResult:
    mfa_required: bool
    mfa_token: str | None = None
    tokens: TokenPair | None = None
    mfa_enrollment_required: bool = False


def login(realm: Realm, email: str, password: str, meta: audit.RequestMeta) -> LoginResult:
    ip = meta.ip or "unknown"
    enforce(f"login:{realm}:ip:{ip}", 30, 60)
    enforce(f"login:{realm}:email:{email.lower()}", 6, 300)
    engine = _engine(realm)
    with transaction(engine) as conn:
        fn = "auth_user_by_email" if realm == "tenant" else "auth_platform_admin_by_email"
        row = conn.execute(text(f"SELECT * FROM app.{fn}(:e)"), {"e": email}).first()
    ok = passwords.verify_password(row.password_hash if row else None, password)
    if not ok or row is None or row.status != "active":
        _audit_security(
            realm,
            "auth.login_failed",
            row.id if row else None,
            {"email_hash": hash_token(email.lower()).hex()[:16]},
            meta,
        )
        raise Unauthenticated("Invalid email or password.")
    if realm == "tenant" and row.email_verified_at is None:
        raise EmailNotVerified("Verify your email address before signing in.")
    subject: uuid.UUID = row.id
    if row.mfa_enabled:
        token, _ = issue_token(realm, subject, new_id(), mfa=False, typ="mfa_challenge")
        return LoginResult(mfa_required=True, mfa_token=token)
    with transaction(engine, DbContext(user_id=subject)) as conn:
        tokens = issue_session(conn, realm, subject, meta, mfa_verified=False)
        enroll = True if realm == "platform" else _holds_mfa_role(conn, subject)
        if realm == "tenant":
            audit.write_audit(conn, action="auth.login", actor_id=subject, summary={"mfa": False}, meta=meta)
        else:
            audit.write_platform_audit(conn, action="auth.login", actor_id=subject, summary={"mfa": False}, meta=meta)
    return LoginResult(mfa_required=False, tokens=tokens, mfa_enrollment_required=enroll)


class EmailNotVerified(Unauthenticated):
    code, title = "email_not_verified", "Email address not verified"


def _holds_mfa_role(conn: Connection, user_id: uuid.UUID) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM organization_memberships m JOIN roles r ON r.id = m.role_id"
                " WHERE m.user_id = :u AND m.status = 'active' AND r.key IN ('firm_owner', 'practice_admin')"
            ),
            {"u": user_id},
        ).scalar()
    )


def verify_mfa(realm: Realm, mfa_token: str, code: str, meta: audit.RequestMeta) -> TokenPair:
    claims = decode_token(realm, mfa_token, expected_typ="mfa_challenge")
    subject = claims.subject
    enforce(f"mfa:{realm}:{subject}", 6, 300)
    col = _COL[realm]
    accepted = False
    with transaction(_engine(realm), DbContext(user_id=subject)) as conn:
        factor = conn.execute(
            text(
                f"SELECT id, secret_encrypted, last_used_step FROM mfa_factors WHERE {col} = :s"
                " AND confirmed_at IS NOT NULL"
            ),
            {"s": subject},
        ).first()
        if factor is not None:
            step = totp.verify_code(totp.decrypt_secret(factor.secret_encrypted), code, factor.last_used_step)
            if step is not None:
                conn.execute(
                    text("UPDATE mfa_factors SET last_used_step = :st WHERE id = :id"), {"st": step, "id": factor.id}
                )
                accepted = True
            else:
                rc = conn.execute(
                    text(
                        f"UPDATE mfa_recovery_codes SET used_at = now() WHERE {col} = :s AND code_hash = :h"
                        " AND used_at IS NULL RETURNING id"
                    ),
                    {"s": subject, "h": totp.hash_recovery_code(code)},
                ).first()
                accepted = rc is not None
        if accepted:
            tokens = issue_session(conn, realm, subject, meta, mfa_verified=True)
            if realm == "tenant":
                audit.write_audit(conn, action="auth.login", actor_id=subject, summary={"mfa": True}, meta=meta)
            else:
                audit.write_platform_audit(
                    conn, action="auth.login", actor_id=subject, summary={"mfa": True}, meta=meta
                )
            return tokens
    _audit_security(realm, "auth.mfa_failed", subject, {}, meta)
    raise Unauthenticated("Invalid verification code.")


def mfa_enroll(conn: Connection, realm: Realm, subject: uuid.UUID, account_label: str) -> tuple[str, str]:
    col = _COL[realm]
    existing = conn.execute(text(f"SELECT id, confirmed_at FROM mfa_factors WHERE {col} = :s"), {"s": subject}).first()
    if existing is not None and existing.confirmed_at is not None:
        raise Conflict("Multi-factor authentication is already enabled.")
    secret = totp.new_secret()
    enc = totp.encrypt_secret(secret)
    if existing is None:
        conn.execute(
            text(f"INSERT INTO mfa_factors (id, {col}, secret_encrypted) VALUES (:id, :s, :e)"),
            {"id": new_id(), "s": subject, "e": enc},
        )
    else:
        conn.execute(text("UPDATE mfa_factors SET secret_encrypted = :e WHERE id = :id"), {"e": enc, "id": existing.id})
    return secret, totp.provisioning_uri(secret, account_label)


def mfa_confirm(
    conn: Connection, realm: Realm, subject: uuid.UUID, session_id: uuid.UUID, code: str
) -> tuple[list[str], TokenPair]:
    col = _COL[realm]
    enforce(f"mfa:{realm}:{subject}", 6, 300)
    factor = conn.execute(
        text(f"SELECT id, secret_encrypted, confirmed_at FROM mfa_factors WHERE {col} = :s"), {"s": subject}
    ).first()
    if factor is None or factor.confirmed_at is not None:
        raise Conflict("No pending MFA enrollment.")
    step = totp.verify_code(totp.decrypt_secret(factor.secret_encrypted), code, None)
    if step is None:
        raise Unprocessable("The verification code is not valid.")
    conn.execute(
        text("UPDATE mfa_factors SET confirmed_at = now(), last_used_step = :st WHERE id = :id"),
        {"st": step, "id": factor.id},
    )
    codes = totp.new_recovery_codes()
    for c in codes:
        conn.execute(
            text(f"INSERT INTO mfa_recovery_codes (id, {col}, code_hash) VALUES (:id, :s, :h)"),
            {"id": new_id(), "s": subject, "h": totp.hash_recovery_code(c)},
        )
    conn.execute(text("UPDATE sessions SET mfa_verified_at = now() WHERE id = :s"), {"s": session_id})
    access, expires_in = issue_token(realm, subject, session_id, mfa=True)
    return codes, TokenPair(access, "", expires_in, True, session_id)


# ------------------------------------------------------------------ signup / e-mail verification
_slug_re = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    base = _slug_re.sub("-", name.lower()).strip("-")[:40] or "org"
    return f"{base}-{secrets.token_hex(3)}"


def _issue_email_token(conn: Connection, user_id: uuid.UUID, purpose: str, ttl: int) -> str:
    plaintext, digest = new_opaque_token()
    conn.execute(
        text(
            "INSERT INTO email_tokens (id, user_id, purpose, token_hash, expires_at)"
            " VALUES (:id, :u, :p, :h, now() + make_interval(secs => :ttl))"
        ),
        {"id": new_id(), "u": user_id, "p": purpose, "h": digest, "ttl": ttl},
    )
    return plaintext


def signup(
    *,
    email: str,
    password: str,
    display_name: str,
    organization_name: str,
    organization_type: str,
    country: str,
    default_currency: str,
    locale: str,
    meta: audit.RequestMeta,
) -> None:
    """Create user + organization + owner membership + trial subscription. The response never
    reveals whether the email was already registered (account-enumeration protection)."""
    passwords.validate_password_policy(password, email=email)
    enforce(f"signup:ip:{meta.ip or 'unknown'}", 10, 3600)
    with transaction(app_engine()) as conn:
        existing = conn.execute(text("SELECT id FROM app.auth_user_by_email(:e)"), {"e": email}).first()
    if existing is not None:
        get_mailer().send(Message(to=email, template="signup_existing_account"))
        return
    user_id, org_id = new_id(), new_id()
    pw_hash = passwords.hash_password(password)
    try:
        with transaction(app_engine(), DbContext(user_id=user_id, org_id=org_id, company_scope="all")) as conn:
            conn.execute(
                text("INSERT INTO users (id, email, display_name, locale) VALUES (:id, :e, :n, :l)"),
                {"id": user_id, "e": email, "n": display_name, "l": locale},
            )
            conn.execute(
                text("INSERT INTO auth_credentials (user_id, password_hash) VALUES (:u, :h)"),
                {"u": user_id, "h": pw_hash},
            )
            conn.execute(
                text(
                    "INSERT INTO organizations (id, type, legal_name, display_name, slug, country,"
                    " default_currency, locale) VALUES (:id, :t, :n, :n, :slug, :c, :cur, :l)"
                ),
                {
                    "id": org_id,
                    "t": organization_type,
                    "n": organization_name,
                    "slug": _slugify(organization_name),
                    "c": country,
                    "cur": default_currency,
                    "l": locale,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO organization_memberships (id, organization_id, user_id, role_id)"
                    " SELECT :id, :o, :u, id FROM roles WHERE key = 'firm_owner' AND organization_id IS NULL"
                ),
                {"id": new_id(), "o": org_id, "u": user_id},
            )
            conn.execute(text("SELECT app.create_trial_subscription(:id, :o)"), {"id": new_id(), "o": org_id})
            from artify_api import entitlements

            for key in ("limit.users", "limit.companies", "limit.accountants", "limit.devices"):
                entitlements.refresh_counter_locked(conn, org_id, key)
            audit.write_audit(
                conn,
                action="org.created",
                actor_id=user_id,
                org_id=org_id,
                entity_type="organization",
                entity_id=org_id,
                summary={"type": organization_type, "country": country},
                meta=meta,
            )
            audit.write_audit(
                conn,
                action="user.signed_up",
                actor_id=user_id,
                org_id=org_id,
                entity_type="user",
                entity_id=user_id,
                meta=meta,
            )
            token = _issue_email_token(conn, user_id, "verify_email", get_settings().email_token_ttl_seconds)
    except IntegrityError:
        # Lost a race with a concurrent signup for the same email: respond identically.
        get_mailer().send(Message(to=email, template="signup_existing_account"))
        return
    get_mailer().send(Message(to=email, template="verify_email", context={"token": token}))


def verify_email(token: str) -> None:
    enforce("verify_email:global", 600, 60)
    digest = hash_token(token)
    with transaction(app_engine()) as conn:
        row = conn.execute(text("SELECT * FROM app.auth_email_token_by_hash(:h)"), {"h": digest}).first()
        if row is None or row.purpose != "verify_email" or row.used_at is not None:
            raise Unprocessable("This verification link is invalid or was already used.")
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(row.user_id)})
        valid = conn.execute(text("SELECT CAST(:e AS timestamptz) > now()"), {"e": row.expires_at}).scalar()
        if not valid:
            raise Unprocessable("This verification link has expired.")
        conn.execute(text("UPDATE email_tokens SET used_at = now() WHERE id = :id"), {"id": row.id})
        conn.execute(
            text("UPDATE users SET email_verified_at = now(), updated_at = now() WHERE id = :u"), {"u": row.user_id}
        )
        audit.write_audit(conn, action="user.email_verified", actor_id=row.user_id)


def request_password_reset(email: str, meta: audit.RequestMeta) -> None:
    enforce(f"pwreset:ip:{meta.ip or 'unknown'}", 10, 3600)
    enforce(f"pwreset:email:{email.lower()}", 3, 3600)
    with transaction(app_engine()) as conn:
        row = conn.execute(text("SELECT id, status FROM app.auth_user_by_email(:e)"), {"e": email}).first()
    if row is None or row.status != "active":
        return  # identical response either way
    with transaction(app_engine(), DbContext(user_id=row.id)) as conn:
        token = _issue_email_token(conn, row.id, "password_reset", 3600)
        audit.write_audit(conn, action="auth.password_reset_requested", actor_id=row.id, meta=meta)
    get_mailer().send(Message(to=email, template="password_reset", context={"token": token}))


def confirm_password_reset(token: str, new_password: str, meta: audit.RequestMeta) -> None:
    enforce(f"pwreset_confirm:ip:{meta.ip or 'unknown'}", 20, 3600)
    digest = hash_token(token)
    with transaction(app_engine()) as conn:
        row = conn.execute(text("SELECT * FROM app.auth_email_token_by_hash(:h)"), {"h": digest}).first()
        if row is None or row.purpose != "password_reset" or row.used_at is not None:
            raise Unprocessable("This reset link is invalid or was already used.")
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(row.user_id)})
        if not conn.execute(text("SELECT CAST(:e AS timestamptz) > now()"), {"e": row.expires_at}).scalar():
            raise Unprocessable("This reset link has expired.")
        email = conn.execute(text("SELECT email FROM users WHERE id = :u"), {"u": row.user_id}).scalar_one()
        passwords.validate_password_policy(new_password, email=str(email))
        conn.execute(text("UPDATE email_tokens SET used_at = now() WHERE id = :id"), {"id": row.id})
        conn.execute(
            text("UPDATE auth_credentials SET password_hash = :h, updated_at = now() WHERE user_id = :u"),
            {"h": passwords.hash_password(new_password), "u": row.user_id},
        )
        # A reset proves mailbox control, so it also verifies the address; all sessions end.
        conn.execute(
            text("UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()) WHERE id = :u"),
            {"u": row.user_id},
        )
        conn.execute(
            text(
                "UPDATE sessions SET revoked_at = now(), revoke_reason = 'password_reset'"
                " WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"u": row.user_id},
        )
        audit.write_audit(conn, action="auth.password_reset", actor_id=row.user_id, meta=meta)
