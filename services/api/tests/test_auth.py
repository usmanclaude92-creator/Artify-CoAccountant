"""Authentication behaviour: TS-SEC-01 (rotation/reuse), TS-SEC-02 (MFA), TS-SEC-06 (rate limits),
password reset, enumeration resistance, platform realm."""

from __future__ import annotations

import uuid

from artify_api.security import passwords
from conftest import (
    PASSWORD,
    Actor,
    login,
    new_owner,
    signup_and_verify,
    totp_at_step,
    totp_now,
    uniq,
)
from sqlalchemy import text


def test_signup_response_is_identical_for_existing_email(client, mailer) -> None:  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    body = {
        "email": email,
        "password": PASSWORD,
        "display_name": "Dup",
        "organization_name": "Dup Org",
        "organization_type": "independent_accountant",
        "country": "OM",
        "default_currency": "OMR",
    }
    first = client.post("/api/v1/auth/signup", json={**body, "email": uniq("fresh")})
    again = client.post("/api/v1/auth/signup", json=body)
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()
    assert mailer.last("signup_existing_account", email)


def test_login_errors_do_not_reveal_whether_account_exists(client, mailer) -> None:  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    wrong_pw = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong password!!"})
    no_user = client.post("/api/v1/auth/login", json={"email": uniq("ghost"), "password": "wrong password!!"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]
    assert wrong_pw.json()["code"] == no_user.json()["code"]


def test_unverified_email_cannot_log_in(client, mailer) -> None:  # type: ignore[no-untyped-def]
    email = uniq("unv")
    client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "U",
            "organization_name": "Unv Org",
            "organization_type": "independent_accountant",
            "country": "OM",
            "default_currency": "OMR",
        },
    )
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401 and r.json()["code"] == "email_not_verified"


def test_password_policy_is_enforced_on_signup(client) -> None:  # type: ignore[no-untyped-def]
    for bad in ("short", "password", "x" * 300):
        r = client.post(
            "/api/v1/auth/signup",
            json={
                "email": uniq(),
                "password": bad,
                "display_name": "U",
                "organization_name": "Pw Org",
                "organization_type": "independent_accountant",
                "country": "OM",
                "default_currency": "OMR",
            },
        )
        assert r.status_code in (422,), (bad[:10], r.status_code)


def test_passwords_are_stored_as_argon2id(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    with owner_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT password_hash FROM auth_credentials c JOIN users u ON u.id=c.user_id WHERE u.email=:e"),
            {"e": email},
        ).scalar_one()
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    assert passwords.verify_password(stored, PASSWORD) and not passwords.verify_password(stored, "nope")


def test_privileged_roles_require_mfa_before_org_access(client, mailer) -> None:  # TS-SEC-02  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    body = login(client, email)
    assert body["mfa_enrollment_required"] is True
    a = Actor(client, email, body["tokens"]["access_token"], body["tokens"]["refresh_token"])
    org = a.get("/me").json()["memberships"][0]["organization_id"]
    r = a.get(f"/orgs/{org}")
    assert r.status_code == 403 and r.json()["code"] == "mfa_required"
    # after enrollment the same user passes
    secret = client.post("/api/v1/auth/mfa/enroll", headers=a.headers()).json()["secret"]
    ok = client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=a.headers())
    assert ok.status_code == 200 and len(ok.json()["recovery_codes"]) == 10
    a.access = ok.json()["access_token"]
    assert a.get(f"/orgs/{org}").status_code == 200


def test_mfa_login_challenge_replay_and_recovery_codes(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    assert a.mfa_secret
    with owner_engine.connect() as conn:
        used = conn.execute(
            text("SELECT f.last_used_step FROM mfa_factors f JOIN users u ON u.id = f.user_id WHERE u.email = :e"),
            {"e": a.email},
        ).scalar_one()
    first = client.post("/api/v1/auth/login", json={"email": a.email, "password": PASSWORD}).json()
    assert first["status"] == "mfa_required" and first["tokens"] is None
    # Replay protection, deterministic: the exact step consumed at enrollment can never be used again,
    # however much wall-clock time has passed.
    replay = client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": totp_at_step(a.mfa_secret, used)}
    )
    assert replay.status_code == 401
    nxt = totp_at_step(a.mfa_secret, used + 1)  # a later step inside the server's +-1 step tolerance
    good = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": nxt})
    assert good.status_code == 200 and good.json()["mfa"] is True
    again = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": nxt})
    assert again.status_code == 401  # same step used twice
    assert (
        client.post("/api/v1/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": "000000"}).status_code
        == 401
    )


def test_recovery_code_works_once(client, mailer) -> None:  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    body = login(client, email)
    a = Actor(client, email, body["tokens"]["access_token"], body["tokens"]["refresh_token"])
    secret = client.post("/api/v1/auth/mfa/enroll", headers=a.headers()).json()["secret"]
    codes = client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=a.headers()).json()[
        "recovery_codes"
    ]
    for expected in (200, 401):
        challenge = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["mfa_token"]
        r = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": codes[0]})
        assert r.status_code == expected


def test_refresh_rotation_and_reuse_detection(client, mailer) -> None:  # TS-SEC-01  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    challenge = client.post("/api/v1/auth/login", json={"email": a.email, "password": PASSWORD}).json()["mfa_token"]
    t0 = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": totp_now(a.mfa_secret, 1)}).json()
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": t0["refresh_token"]})
    assert r1.status_code == 200
    t1 = r1.json()
    assert t1["refresh_token"] != t0["refresh_token"] and t1["mfa"] is True
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {t1['access_token']}"}).status_code == 200
    # presenting the OLD token again = theft signal: family is revoked, new tokens die too
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": t0["refresh_token"]})
    assert reuse.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {t1['access_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-token"}).status_code == 401


def test_logout_revokes_the_session_immediately(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    assert a.get("/me").status_code == 200
    assert client.post("/api/v1/auth/logout", headers=a.headers()).status_code == 204
    assert a.get("/me").status_code == 401


def test_session_list_and_remote_revocation(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    sessions = a.get("/auth/sessions").json()
    assert any(s["current"] for s in sessions)
    other = login(client, a.email)  # a second login (MFA challenge only); create a real second session
    ch = other["mfa_token"]
    t = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch, "code": totp_now(a.mfa_secret, 1)}).json()
    second = {"Authorization": f"Bearer {t['access_token']}"}
    sid = next(s["id"] for s in a.get("/auth/sessions").json() if not s["current"])
    assert a.delete(f"/auth/sessions/{sid}").status_code == 204
    assert client.get("/api/v1/me", headers=second).status_code == 401
    assert a.delete(f"/auth/sessions/{uuid.uuid4()}").status_code == 404


def test_password_reset_flow_revokes_sessions(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    r = client.post("/api/v1/auth/password-reset/request", json={"email": a.email})
    ghost = client.post("/api/v1/auth/password-reset/request", json={"email": uniq("ghost")})
    assert r.status_code == ghost.status_code == 202 and r.json() == ghost.json()
    token = mailer.last("password_reset", a.email).context["token"]
    new_pw = "an entirely different passphrase"
    assert (
        client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "short"}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": new_pw}).status_code
        == 204
    )
    assert (
        client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": new_pw}).status_code
        == 422
    )  # single use
    assert a.get("/me").status_code == 401  # old session revoked
    assert client.post("/api/v1/auth/login", json={"email": a.email, "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": a.email, "password": new_pw}).status_code == 200


def test_login_is_rate_limited(client, mailer) -> None:  # TS-SEC-06  # type: ignore[no-untyped-def]
    email = signup_and_verify(client, mailer)
    codes = [
        client.post("/api/v1/auth/login", json={"email": email, "password": "wrong password!!"}).status_code
        for _ in range(8)
    ]
    assert codes[:6] == [401] * 6 and 429 in codes[6:]
    limited = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert limited.status_code == 429 and "retry-after" in limited.headers  # even the right password is throttled


def test_disabled_user_loses_access_on_refresh(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    challenge = client.post("/api/v1/auth/login", json={"email": a.email, "password": PASSWORD}).json()["mfa_token"]
    t = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": totp_now(a.mfa_secret, 1)}).json()
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE users SET status='disabled' WHERE email=:e"), {"e": a.email})
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {t['access_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401


# ------------------------------------------------------------------ platform realm
def _make_admin(owner_engine, role: str = "platform_owner") -> tuple[str, str]:  # type: ignore[no-untyped-def]
    email = uniq("admin")
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_admins (id, email, display_name, role_key, password_hash)"
                " VALUES (gen_random_uuid(), :e, 'Admin', :r, :h)"
            ),
            {"e": email, "r": role, "h": passwords.hash_password(PASSWORD)},
        )
    return email, role


def test_platform_admin_must_enroll_mfa_then_sees_metadata_only(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    new_owner(client, mailer, org_name="Visible Org")
    email, _ = _make_admin(owner_engine)
    r = client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD}).json()
    assert r["status"] == "ok" and r["mfa_enrollment_required"] is True
    h = {"Authorization": f"Bearer {r['tokens']['access_token']}"}
    blocked = client.get("/api/v1/platform/me", headers=h)
    assert blocked.status_code == 403 and blocked.json()["code"] == "mfa_required"
    secret = client.post("/api/v1/platform/auth/mfa/enroll", headers=h).json()["secret"]
    ok = client.post("/api/v1/platform/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=h)
    assert ok.status_code == 200
    h = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert client.get("/api/v1/platform/me", headers=h).json()["role"] == "platform_owner"
    orgs = client.get("/api/v1/platform/organizations", headers=h)
    assert orgs.status_code == 200 and any(o["display_name"] == "Visible Org" for o in orgs.json())
    assert set(orgs.json()[0]) == {"id", "type", "display_name", "country", "status"}  # no business data
    # a platform token is useless on tenant routes (separate audience and key)
    assert client.get("/api/v1/me", headers=h).status_code == 401
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM platform_audit_events WHERE action='auth.mfa_enabled'")).scalar()
            >= 1
        )


def test_platform_roles_are_enforced(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    email, _ = _make_admin(owner_engine, "platform_auditor")
    r = client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {r['tokens']['access_token']}"}
    secret = client.post("/api/v1/platform/auth/mfa/enroll", headers=h).json()["secret"]
    h = {
        "Authorization": "Bearer "
        + client.post("/api/v1/platform/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=h).json()[
            "access_token"
        ]
    }
    assert client.get("/api/v1/platform/me", headers=h).status_code == 200
    assert client.get("/api/v1/platform/organizations", headers=h).status_code == 403  # auditor lacks it
