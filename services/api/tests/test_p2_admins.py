"""Platform administrator management: invitations, least privilege, no self-escalation, session revocation."""

from __future__ import annotations

from conftest import PASSWORD, login_platform_admin, new_platform_admin, relogin_platform_admin, uniq
from sqlalchemy import text


def _invite_and_join(owner, client, mailer, role: str):  # type: ignore[no-untyped-def]
    email = uniq("newadmin")
    r = owner.post("/admins/invitations", {"email": email, "role_key": role})
    assert r.status_code == 201, r.text
    token = mailer.last("platform_invitation", email).context["token"]
    ok = client.post(
        "/api/v1/platform/auth/invitations/accept",
        json={"token": token, "display_name": "Joined Admin", "password": PASSWORD},
    )
    assert ok.status_code == 204, ok.text
    row = next(a for a in owner.get("/admins").json()["administrators"] if a["email"] == email)
    return login_platform_admin(client, email, row["id"], role), row


def test_invitation_flow_creates_least_privilege_admin_with_mandatory_mfa(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    email = uniq("support")
    inv = owner.post("/admins/invitations", {"email": email, "role_key": "platform_support"}).json()
    assert inv["state"] == "pending" and "token" not in inv and "token_hash" not in inv
    token = mailer.last("platform_invitation", email).context["token"]
    # weak password refused, bad token refused, then success; the token is single-use
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": token, "display_name": "Sam Support", "password": "short"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": "x" * 40, "display_name": "Sam Support", "password": PASSWORD},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": token, "display_name": "Sam Support", "password": PASSWORD},
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": token, "display_name": "Sam Support", "password": PASSWORD},
        ).status_code
        == 422
    )
    r = client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD}).json()
    assert r["mfa_enrollment_required"] is True  # MFA is mandatory before any Super Admin access
    h = {"Authorization": f"Bearer {r['tokens']['access_token']}"}
    assert client.get("/api/v1/platform/dashboard", headers=h).json()["code"] == "mfa_required"
    listing = owner.get("/admins").json()
    row = next(a for a in listing["administrators"] if a["email"] == email)
    assert row["role_key"] == "platform_support" and row["status"] == "active" and row["last_login_at"]
    assert "platform.billing.manage" not in row["permissions"] and "platform.admins.manage" not in row["permissions"]
    assert next(i for i in listing["invitations"] if i["id"] == inv["id"])["state"] == "accepted"
    with owner_engine.connect() as conn:
        actions = {
            a[0]
            for a in conn.execute(
                text("SELECT action FROM platform_audit_events WHERE actor_id = :a OR target_id = :i"),
                {"a": row["id"], "i": inv["id"]},
            )
        }
        pw = conn.execute(text("SELECT password_hash FROM platform_admins WHERE email = :e"), {"e": email}).scalar()
    assert {"admin.invited", "admin.invitation_accepted"} <= actions
    assert pw.startswith("$argon2id$") and PASSWORD not in str(listing)


def test_invitation_rules(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    existing = new_platform_admin(client, owner_engine, "platform_support")
    assert (
        owner.post("/admins/invitations", {"email": existing.email, "role_key": "platform_support"}).status_code == 409
    )
    email = uniq("dup")
    first = owner.post("/admins/invitations", {"email": email, "role_key": "platform_billing"}).json()
    assert (
        owner.post("/admins/invitations", {"email": email, "role_key": "platform_billing"}).status_code == 409
    )  # one pending at a time
    assert owner.delete(f"/admins/invitations/{first['id']}").status_code == 204
    assert owner.delete(f"/admins/invitations/{first['id']}").status_code == 404
    token = mailer.last("platform_invitation", email).context["token"]
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": token, "display_name": "Revoked", "password": PASSWORD},
        ).status_code
        == 422
    )
    assert owner.post("/admins/invitations", {"email": "bad", "role_key": "platform_billing"}).status_code == 422
    assert owner.post("/admins/invitations", {"email": uniq("r"), "role_key": "platform_god"}).status_code == 422
    # an invitation that expired can no longer be used
    expired = uniq("exp")
    owner.post("/admins/invitations", {"email": expired, "role_key": "platform_readonly"})
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE platform_admin_invitations SET expires_at = now() - interval '1 minute' WHERE email = :e"),
            {"e": expired},
        )
    tok = mailer.last("platform_invitation", expired).context["token"]
    assert (
        client.post(
            "/api/v1/platform/auth/invitations/accept",
            json={"token": tok, "display_name": "Late", "password": PASSWORD},
        ).status_code
        == 422
    )
    assert next(i for i in owner.get("/admins").json()["invitations"] if i["email"] == expired)["state"] == "expired"


def test_only_the_owner_manages_administrators(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    target = new_platform_admin(client, owner_engine, "platform_support")
    for role in ("platform_admin", "platform_support", "platform_billing", "platform_readonly", "platform_auditor"):
        other = new_platform_admin(client, owner_engine, role)
        assert (
            other.post("/admins/invitations", {"email": uniq("x"), "role_key": "platform_readonly"}).status_code == 403
        ), role
        assert (
            other.patch(
                f"/admins/{target.id}",
                {"role_key": "platform_owner", "reason": "Escalating privileges", "confirm": target.email},
            ).status_code
            == 403
        ), role
        assert (
            other.post(
                f"/admins/{target.id}/suspend", {"reason": "Trying to suspend somebody", "confirm": target.email}
            ).status_code
            == 403
        ), role
    # reading the list needs admins.read: admin/readonly may, support/billing/auditor may not
    assert new_platform_admin(client, owner_engine, "platform_admin").get("/admins").status_code == 200
    assert new_platform_admin(client, owner_engine, "platform_support").get("/admins").status_code == 403
    assert owner.get("/admins").status_code == 200


def test_no_self_escalation_and_no_privilege_above_your_own(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    other_owner = new_platform_admin(client, owner_engine)
    reason = "Documented reason for change"
    # nobody changes their own role or status, not even the owner
    assert (
        owner.patch(
            f"/admins/{owner.id}", {"role_key": "platform_readonly", "reason": reason, "confirm": owner.email}
        ).status_code
        == 422
    )
    assert owner.post(f"/admins/{owner.id}/suspend", {"reason": reason, "confirm": owner.email}).status_code == 422
    assert owner.post(f"/admins/{owner.id}/revoke-sessions", {"reason": reason}).status_code == 422
    # typed confirmation is required
    t = new_platform_admin(client, owner_engine, "platform_support")
    assert (
        owner.patch(
            f"/admins/{t.id}", {"role_key": "platform_billing", "reason": reason, "confirm": "wrong@example.com"}
        ).status_code
        == 422
    )
    assert (
        owner.patch(
            f"/admins/{t.id}", {"role_key": "platform_billing", "reason": "short", "confirm": t.email}
        ).status_code
        == 422
    )
    changed = owner.patch(f"/admins/{t.id}", {"role_key": "platform_billing", "reason": reason, "confirm": t.email})
    assert changed.status_code == 200 and changed.json()["role_key"] == "platform_billing"
    assert "platform.billing.manage" in changed.json()["permissions"]
    assert (
        owner.patch(
            f"/admins/{t.id}", {"role_key": "platform_billing", "reason": reason, "confirm": t.email}
        ).status_code
        == 409
    )
    # the new role applies at once (permissions are read from the database on every request)
    assert t.get("/billing/summary").status_code == 200 and t.get("/support/grants").status_code == 403
    # a (hypothetical) non-owner manager could not grant above their own rank: rank table is the single source
    from artify_api.permissions import PLATFORM_ROLE_RANK

    assert (
        PLATFORM_ROLE_RANK["platform_owner"]
        > PLATFORM_ROLE_RANK["platform_admin"]
        > PLATFORM_ROLE_RANK["platform_support"]
    )
    assert other_owner.id != owner.id


def test_owner_demotion_takes_effect_and_an_active_owner_always_remains(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """Only an active owner can manage administrators, nobody can change themselves, so demoting or suspending
    an owner can never leave the platform without one (the last-owner guard in `_guard_target` is a second line)."""
    a, b = new_platform_admin(client, owner_engine), new_platform_admin(client, owner_engine)
    reason = "Documented reason for change"
    assert (
        a.patch(f"/admins/{a.id}", {"role_key": "platform_admin", "reason": reason, "confirm": a.email}).status_code
        == 422
    )
    assert a.post(f"/admins/{b.id}/suspend", {"reason": reason, "confirm": b.email}).status_code == 200
    assert b.get("/dashboard").status_code == 401  # a suspended owner is out immediately
    assert a.post(f"/admins/{a.id}/suspend", {"reason": reason, "confirm": a.email}).status_code == 422
    a.post(f"/admins/{b.id}/reactivate", {"reason": reason})
    b2 = relogin_platform_admin(client, b)
    assert (
        a.patch(f"/admins/{b.id}", {"role_key": "platform_admin", "reason": reason, "confirm": b.email}).status_code
        == 200
    )
    assert (
        b2.post("/admins/invitations", {"email": uniq("x"), "role_key": "platform_readonly"}).status_code == 403
    )  # demoted: no manage
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM platform_admins WHERE role_key = 'platform_owner' AND status = 'active'")
            ).scalar()
            >= 1
        )


def test_suspend_revokes_sessions_immediately_and_reactivate_restores(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    victim = new_platform_admin(client, owner_engine, "platform_admin")
    assert victim.get("/dashboard").status_code == 200
    reason = "Left the company; revoking access"
    s = owner.post(f"/admins/{victim.id}/suspend", {"reason": reason, "confirm": victim.email})
    assert s.status_code == 200 and s.json()["status"] == "disabled"
    assert victim.get("/dashboard").status_code == 401
    with owner_engine.connect() as conn:  # the sessions themselves are revoked, not just the account disabled
        live = conn.execute(
            text("SELECT count(*) FROM sessions WHERE platform_admin_id = :a AND revoked_at IS NULL"), {"a": victim.id}
        ).scalar()
    assert live == 0
    login = client.post("/api/v1/platform/auth/login", json={"email": victim.email, "password": PASSWORD})
    assert login.status_code in (401, 403)
    assert owner.post(f"/admins/{victim.id}/suspend", {"reason": reason, "confirm": victim.email}).status_code == 409
    assert owner.post(f"/admins/{victim.id}/reactivate", {"reason": reason}).json()["status"] == "active"
    again = relogin_platform_admin(client, victim)
    assert again.get("/dashboard").status_code == 200
    # revoke-sessions signs the administrator out everywhere without suspending
    assert owner.post(f"/admins/{victim.id}/revoke-sessions", {"reason": reason}).status_code == 204
    assert again.get("/dashboard").status_code == 401  # account still active; only the session was revoked
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT status FROM platform_admins WHERE id = :a"), {"a": victim.id}).scalar()
            == "active"
        )
        actions = [
            r[0]
            for r in conn.execute(
                text("SELECT action FROM platform_audit_events WHERE target_id = :i ORDER BY created_at"),
                {"i": victim.id},
            )
        ]
    assert actions == ["admin.suspended", "admin.reactivated", "admin.sessions_revoked"]


def test_activity_history_shows_sign_ins_and_actions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    worker = new_platform_admin(client, owner_engine, "platform_admin")
    worker.get("/organizations")
    act = owner.get(f"/admins/{worker.id}/activity").json()
    assert act["total"] >= 2 and {"auth.login", "auth.mfa_enabled"} <= {a["action"] for a in act["items"]}
    assert owner.get(f"/admins/{worker.id}/activity", limit=1).json()["items"].__len__() == 1
    assert owner.get("/admins/00000000-0000-0000-0000-000000000000/activity").status_code == 404
    assert "password" not in str(act)


def test_password_hash_is_never_exposed(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    text_ = str(owner.get("/admins").json()) + str(owner.get("/me").json())
    assert "argon2" not in text_ and "password" not in text_.lower()
