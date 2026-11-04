"""User-level security events are visible to the tenants the user belongs to (and only those)."""

from __future__ import annotations

import os

import pytest
from conftest import PASSWORD, Actor, invite_and_accept, new_owner, totp_now
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


def _actions(actor: Actor) -> list[dict]:
    return actor.get(f"/orgs/{actor.org_id}/audit-events?limit=200").json()


def test_member_login_and_failures_appear_in_tenant_audit(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    staff = invite_and_accept(a, mailer, role="staff")
    client.post("/api/v1/auth/login", json={"email": staff.email, "password": "wrong password!!"})
    client.post("/api/v1/auth/login", json={"email": staff.email, "password": PASSWORD})
    events = _actions(a)
    staff_id = staff.get("/me").json()["user_id"]
    mine = [e for e in events if e["actor_id"] == staff_id]
    kinds = {e["action"] for e in mine}
    assert {"auth.login_failed", "auth.login"} <= kinds
    # owner's own MFA enrollment/login events are there too
    assert {"auth.mfa_enabled"} <= {e["action"] for e in events}
    # nothing sensitive is stored: no email, password or token in any summary
    blob = str([e["summary"] for e in events]).lower()
    assert staff.email.lower() not in blob and "password" not in blob


def test_events_do_not_leak_across_tenants(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer), new_owner(client, mailer)
    a_id = a.get("/me").json()["user_id"]
    assert not [e for e in _actions(b) if e["actor_id"] == a_id]


def test_user_in_two_organizations_is_audited_in_both(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer), new_owner(client, mailer)
    staff = invite_and_accept(a, mailer, role="staff")
    assert b.post(f"/orgs/{b.org_id}/invitations", {"email": staff.email, "role": "accountant"}).status_code == 201
    token = mailer.last("invitation", staff.email).context["token"]
    assert (
        client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": token},
            headers={"Authorization": f"Bearer {staff.access}"},
        ).status_code
        == 200
    )
    client.post("/api/v1/auth/login", json={"email": staff.email, "password": PASSWORD})
    staff_id = staff.get("/me").json()["user_id"]
    for owner in (a, b):
        assert any(e["actor_id"] == staff_id and e["action"] == "auth.login" for e in _actions(owner))


def test_password_reset_events_are_recorded(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    client.post("/api/v1/auth/password-reset/request", json={"email": a.email})
    token = mailer.last("password_reset", a.email).context["token"]
    client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "a brand new passphrase!"})
    # the old session is gone; read the log with a fresh MFA login
    ch = client.post("/api/v1/auth/login", json={"email": a.email, "password": "a brand new passphrase!"}).json()
    t = client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": totp_now(a.mfa_secret, 1)}
    ).json()
    a.access = t["access_token"]
    actions = {e["action"] for e in _actions(a)}
    assert {"auth.password_reset_requested", "auth.password_reset"} <= actions


def test_audit_function_cannot_forge_another_users_events(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer), new_owner(client, mailer)
    b_id = b.get("/me").json()["user_id"]
    a_id = a.get("/me").json()["user_id"]
    eng = create_engine(os.environ["ARTIFY_DATABASE_URL_APP"])
    with pytest.raises(DBAPIError) as exc, eng.connect() as conn, conn.begin():
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": a_id})
        conn.execute(text("SELECT app.audit_user_event(:b, 'auth.login', '{}'::jsonb, NULL, NULL, NULL)"), {"b": b_id})
    assert "authenticated subject" in str(exc.value)
    eng.dispose()
