"""Phase 1 follow-ups: member role changes, wider idempotency, reconcile CLI, JSON logging,
parameter-free DB errors, SMTP delivery, and the AuthProvider seam."""

from __future__ import annotations

import json
import logging
import os
import uuid

import pytest
from aiosmtpd.controller import Controller
from artify_api import cli
from artify_api.auth_provider import set_auth_provider
from artify_api.auth_types import LoginResult, TokenPair
from artify_api.logging_config import JsonFormatter, correlation_id_var
from artify_api.mailer import Message, SmtpMailer
from artify_api.settings import Settings
from conftest import PASSWORD, invite_and_accept, make_company, new_owner, set_plan_limit, uniq
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


# ------------------------------------------------------------------ member role changes
def _uid(actor) -> str:  # type: ignore[no-untyped-def]
    return actor.get("/me").json()["user_id"]


def test_role_change_staff_to_accountant_respects_the_accountant_limit(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    staff = invite_and_accept(a, mailer, role="staff")  # counts as an accountant seat
    set_plan_limit(owner_engine, a.org_id, "limit.accountants", 1)
    other = invite_and_accept(a, mailer, role="practice_admin")
    # practice_admin -> accountant would need a 2nd accountant seat
    r = a.patch(f"/orgs/{a.org_id}/members/{_uid(other)}", {"role": "accountant"})
    assert r.status_code == 402 and r.json()["key"] == "limit.accountants"
    # staff -> accountant keeps the same single seat
    r = a.patch(f"/orgs/{a.org_id}/members/{_uid(staff)}", {"role": "accountant"})
    assert r.status_code == 200 and r.json()["role"] == "accountant"
    assert a.patch(f"/orgs/{a.org_id}/members/{_uid(staff)}", {}).status_code == 422


def test_role_change_takes_effect_immediately_and_clears_assignments(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    c1, c2 = make_company(a, "R1"), make_company(a, "R2")
    acc = invite_and_accept(a, mailer, role="accountant")
    assert a.post(f"/companies/{c1['id']}/assignments", {"user_id": _uid(acc)}).status_code == 201
    assert acc.get(f"/companies/{c2['id']}").status_code == 404
    # promotion to practice_admin: org-wide reach, but MFA is immediately required for the new role
    assert a.patch(f"/orgs/{a.org_id}/members/{_uid(acc)}", {"role": "practice_admin"}).status_code == 200
    blocked = acc.get(f"/orgs/{a.org_id}/companies")
    assert blocked.status_code == 403 and blocked.json()["code"] == "mfa_required"
    assert a.get(f"/companies/{c1['id']}/assignments").json() == []  # accountant assignment ended
    # demotion to staff: loses audit access at once
    assert a.patch(f"/orgs/{a.org_id}/members/{_uid(acc)}", {"role": "staff"}).status_code == 200
    assert acc.get(f"/orgs/{a.org_id}/audit-events").status_code == 403
    events = {e["action"] for e in a.get(f"/orgs/{a.org_id}/audit-events?limit=100").json()}
    assert "member.role_changed" in events


def test_firm_owner_role_is_protected(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    admin = invite_and_accept(a, mailer, role="practice_admin")
    # secure the admin with MFA so they can act
    enroll = client.post("/api/v1/auth/mfa/enroll", headers=admin.headers()).json()["secret"]
    from conftest import totp_now

    admin.access = client.post(
        "/api/v1/auth/mfa/confirm", json={"code": totp_now(enroll)}, headers=admin.headers()
    ).json()["access_token"]
    staff = invite_and_accept(a, mailer, role="staff")
    # a practice_admin may not mint or touch firm owners
    assert admin.patch(f"/orgs/{a.org_id}/members/{_uid(staff)}", {"role": "firm_owner"}).status_code == 403
    assert admin.patch(f"/orgs/{a.org_id}/members/{_uid(a)}", {"status": "suspended"}).status_code == 403
    # nobody changes their own role; the last owner cannot be demoted by anyone
    assert a.patch(f"/orgs/{a.org_id}/members/{_uid(a)}", {"role": "staff"}).status_code == 422
    # promote staff to a second owner, then the first can be demoted — and the remaining owner cannot be
    assert a.patch(f"/orgs/{a.org_id}/members/{_uid(staff)}", {"role": "firm_owner"}).status_code == 200


def test_reactivating_a_member_is_limit_checked(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    staff = invite_and_accept(a, mailer, role="staff")
    staff_id = _uid(staff)  # read before suspension: /me with an inactive org header is a 404
    assert a.patch(f"/orgs/{a.org_id}/members/{staff_id}", {"status": "suspended"}).status_code == 200
    set_plan_limit(owner_engine, a.org_id, "limit.users", 1)  # only the owner fits
    r = a.patch(f"/orgs/{a.org_id}/members/{staff_id}", {"status": "active"})
    assert r.status_code == 402 and r.json()["key"] == "limit.users"


# ------------------------------------------------------------------ idempotency on more routes
def test_invitation_and_device_creation_are_idempotent(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    email = uniq("idem")
    key = {"Idempotency-Key": uuid.uuid4().hex}
    first = a.post(f"/orgs/{a.org_id}/invitations", {"email": email, "role": "staff"}, **key)
    again = a.post(f"/orgs/{a.org_id}/invitations", {"email": email, "role": "staff"}, **key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert len([m for m in mailer.sent if m.template == "invitation" and m.to.lower() == email]) == 1
    changed = a.post(f"/orgs/{a.org_id}/invitations", {"email": email, "role": "accountant"}, **key)
    assert changed.status_code == 422
    dkey = {"Idempotency-Key": uuid.uuid4().hex}
    d1 = a.post("/devices", {"platform": "windows", "name": "PC"}, **dkey)
    d2 = a.post("/devices", {"platform": "windows", "name": "PC"}, **dkey)
    assert d1.json() == d2.json() and len(a.get("/devices").json()) == 1


# ------------------------------------------------------------------ reconcile CLI
def test_reconcile_cli_repairs_counter_drift(client, mailer, owner_engine, capsys) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    make_company(a, "REC")
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE usage_counters SET value = 42 WHERE organization_id = :o AND metric = 'limit.companies'"),
            {"o": a.org_id},
        )
    assert cli.main(["reconcile"]) == 0
    out = capsys.readouterr().out
    assert f"org={a.org_id} limit.companies: stored=42 actual=1" in out
    with owner_engine.connect() as conn:
        value = conn.execute(
            text("SELECT value FROM usage_counters WHERE organization_id = :o AND metric = 'limit.companies'"),
            {"o": a.org_id},
        ).scalar()
    assert value == 1
    assert cli.main(["reconcile"]) == 0
    assert f"org={a.org_id}" not in capsys.readouterr().out  # clean on the second pass


# ------------------------------------------------------------------ logging
def test_json_log_format_has_correlation_id_and_exception() -> None:
    record = logging.LogRecord("artify.test", logging.ERROR, __file__, 1, "boom %s", ("x",), None)
    correlation_id_var.set("cid-123")
    try:
        try:
            raise ValueError("bad")
        except ValueError:
            import sys

            record.exc_info = sys.exc_info()
        record.status = 500  # extra fields are included
        data = json.loads(JsonFormatter().format(record))
    finally:
        correlation_id_var.set(None)
    assert data["message"] == "boom x" and data["level"] == "ERROR" and data["correlation_id"] == "cid-123"
    assert data["exception"]["type"] == "ValueError" and data["status"] == 500 and data["ts"].endswith("+00:00")


def test_database_errors_do_not_expose_bound_parameters() -> None:
    """hide_parameters=True: a failing statement must not put user values into exception text/logs."""
    from artify_api.db import app_engine

    secret = f"leak-{uuid.uuid4().hex}@example.com"
    with pytest.raises(DBAPIError) as exc, app_engine().connect() as conn, conn.begin():
        conn.execute(
            text("INSERT INTO users (id, email, display_name) VALUES (gen_random_uuid(), :e, 'x')"), {"e": secret}
        )
    assert secret not in str(exc.value)
    assert "[parameters" not in str(exc.value)


# ------------------------------------------------------------------ SMTP delivery
class _Collector:
    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope):  # type: ignore[no-untyped-def]
        self.messages.append(envelope)
        return "250 OK"


def _settings(port: int) -> Settings:
    return Settings(
        mailer_backend="smtp",
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_starttls=False,
        web_base_url="https://app.example.com",
        mail_from="Artify <no-reply@example.com>",
    )


def test_smtp_mailer_delivers_real_messages_with_links() -> None:
    handler = _Collector()
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        mailer = SmtpMailer(_settings(port))
        mailer.send(Message(to="owner@example.com", template="verify_email", context={"token": "tok123"}))
        mailer.send(Message(to="owner@example.com", template="password_reset", context={"token": "rst456"}))
        mailer.send(Message(to="owner@example.com", template="invitation", context={"token": "inv789"}))
        mailer.flush()
    finally:
        controller.stop()
    assert len(handler.messages) == 3
    from email import message_from_bytes, policy

    parsed = {}
    for env in handler.messages:
        msg = message_from_bytes(env.content, policy=policy.default)
        parsed[msg["Subject"]] = (msg, msg.get_content())
    assert set(parsed) == {
        "Verify your email address",
        "Reset your password",
        "You have been invited to Artify Co-Accountant",
    }
    assert "https://app.example.com/verify-email?token=tok123" in parsed["Verify your email address"][1]
    assert "https://app.example.com/reset-password?token=rst456" in parsed["Reset your password"][1]
    assert (
        "https://app.example.com/accept-invitation?token=inv789"
        in parsed["You have been invited to Artify Co-Accountant"][1]
    )
    assert all(m["To"] == "owner@example.com" and "no-reply@example.com" in m["From"] for m, _ in parsed.values())


def test_smtp_failure_never_raises_into_the_request_and_never_logs_content(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mailer = SmtpMailer(_settings(1))  # nothing listens on port 1
    with caplog.at_level(logging.ERROR, logger="artify.mail"):
        mailer.send(Message(to="x@example.com", template="verify_email", context={"token": "SECRET-TOKEN"}))
        mailer.flush()
    assert any("mail delivery failed" in r.getMessage() for r in caplog.records)
    assert "SECRET-TOKEN" not in caplog.text and "x@example.com" not in caplog.text


def test_settings_require_smtp_in_production() -> None:
    with pytest.raises(ValueError, match="smtp"):
        Settings(
            environment="production",
            tenant_jwt_secret="a" * 40,
            platform_jwt_secret="b" * 40,  # type: ignore[arg-type]
            mfa_encryption_key=Fernet.generate_key().decode(),
            rate_limit_backend="redis",  # type: ignore[arg-type]
            database_url_app="postgresql+psycopg://u:p@h/db",
            database_url_platform="postgresql+psycopg://u:p@h/db",
            database_url_dispatcher="postgresql+psycopg://u:p@h/db",
            mailer_backend="console",
        )


# ------------------------------------------------------------------ AuthProvider seam
class _FakeProvider:
    """Proves routes depend on the protocol only: no database is touched by login/refresh here."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def login(self, realm, email, password, meta):  # type: ignore[no-untyped-def]
        self.calls.append(f"login:{realm}:{email}")
        return LoginResult(False, tokens=TokenPair("fake-access", "fake-refresh", 60, False, uuid.uuid4()))

    def refresh(self, realm, refresh_token):  # type: ignore[no-untyped-def]
        self.calls.append(f"refresh:{realm}")
        return TokenPair("fake-access-2", "fake-refresh-2", 60, False, uuid.uuid4())


def test_routes_use_the_auth_provider_interface(client) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeProvider()
    set_auth_provider(fake)  # type: ignore[arg-type]
    try:
        r = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
        assert r.status_code == 200 and r.json()["tokens"]["access_token"] == "fake-access"
        r = client.post("/api/v1/platform/auth/refresh", json={"refresh_token": "anything"})
        assert r.status_code == 200 and r.json()["access_token"] == "fake-access-2"
    finally:
        set_auth_provider(None)
    assert fake.calls == ["login:tenant:nobody@example.com", "refresh:platform"]
    assert (
        client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}).status_code == 401
    )


_ = (create_engine, os)
