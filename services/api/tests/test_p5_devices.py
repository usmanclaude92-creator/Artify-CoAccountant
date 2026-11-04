"""Phase 5 backend support for the Windows desktop: device-bound sessions, heartbeat, revocation."""

from __future__ import annotations

from conftest import PASSWORD, Actor, login, make_member, new_owner, set_plan_limit, totp_now, uid


def _register(actor: Actor, name: str = "ACCOUNTING-PC-01") -> dict:  # type: ignore[type-arg]
    r = actor.post("/devices", {"platform": "windows", "name": name, "app_version": "0.1.0"})
    assert r.status_code == 201, r.text
    return r.json()


def test_registering_binds_the_session_and_revocation_ends_it_and_blocks_refresh(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Desktop Firm")
    acc = make_member(owner, mailer, "accountant")
    dev = _register(acc)
    assert dev["status"] == "active" and dev["registered_at"] and dev["last_seen_at"]
    assert acc.get("/me").status_code == 200
    assert acc.delete(f"/devices/{dev['id']}").status_code == 204
    # the access token's session was bound to the device, so it stops working at once...
    assert acc.get("/me").status_code == 401
    # ...and its refresh token is dead too
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": acc.refresh})
    assert r.status_code == 401


def test_a_new_login_can_rebind_to_its_active_device_but_never_to_a_revoked_or_foreign_one(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Desktop Firm 2")
    acc = make_member(owner, mailer, "accountant")
    other = make_member(owner, mailer, "assistant_accountant")
    dev = _register(acc)
    foreign = _register(other, "OTHER-PC")
    # a fresh sign-in (new session) re-attaches itself to the same installation and records activity
    body = login(client, acc.email)
    fresh = Actor(client, acc.email, body["tokens"]["access_token"], body["tokens"]["refresh_token"], owner.org_id)
    bound = fresh.post(f"/devices/{dev['id']}/session")
    assert bound.status_code == 200 and bound.json()["id"] == dev["id"]
    assert fresh.post(f"/devices/{foreign['id']}/session").status_code == 404  # somebody else's device
    # revoking now also ends the re-bound session
    assert owner.get("/me").status_code == 200
    assert fresh.delete(f"/devices/{dev['id']}").status_code == 204
    assert fresh.get("/me").status_code == 401
    # a revoked installation cannot attach any new session to itself
    body = login(client, acc.email)
    again = Actor(client, acc.email, body["tokens"]["access_token"], body["tokens"]["refresh_token"], owner.org_id)
    assert again.post(f"/devices/{dev['id']}/session").status_code == 404
    assert again.get("/me").status_code == 200  # the account itself is still fine; only the device is dead


def test_device_listing_shows_only_own_devices_and_the_limit_still_applies(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Desktop Firm 3")
    set_plan_limit(owner_engine, owner.org_id, "limit.devices", 1)
    acc = make_member(owner, mailer, "accountant")
    _register(acc)
    over = acc.post("/devices", {"platform": "windows", "name": "SECOND-PC"})
    assert over.status_code == 402 and over.json()["key"] == "limit.devices"
    names = [d["name"] for d in acc.get("/devices").json()]
    assert names == ["ACCOUNTING-PC-01"] and owner.get("/devices").json() == []
    assert uid(acc) != uid(owner) and PASSWORD and totp_now  # fixtures used elsewhere; keeps imports honest
