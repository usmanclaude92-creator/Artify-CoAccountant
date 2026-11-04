"""Backend entitlement enforcement (TS-ENT-01, TS-ENT-02) and the policy engine (unit)."""

from __future__ import annotations

import concurrent.futures as cf
import uuid

import pytest
from artify_api import entitlements
from artify_api.policy import Principal, authorize
from conftest import PASSWORD, invite_and_accept, make_company, new_owner, set_plan_limit
from sqlalchemy import text


def _company_body(code: str) -> dict:
    return {"name": f"Co {code}", "code": code, "country": "OM", "base_currency": "OMR"}


def test_company_limit_enforced_by_raw_api(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """TS-ENT-01: no UI involved; the API refuses with a structured error."""
    a = new_owner(client, mailer)
    set_plan_limit(owner_engine, a.org_id, "limit.companies", 2)
    make_company(a, "C1"), make_company(a, "C2")
    r = a.post(f"/orgs/{a.org_id}/companies", _company_body("C3"))
    assert r.status_code == 402
    body = r.json()
    assert body["code"] == "entitlement_exceeded"
    assert (body["key"], body["limit"], body["current"], body["requested"]) == ("limit.companies", 2, 2, 1)
    assert len(a.get(f"/orgs/{a.org_id}/companies").json()) == 2
    # archiving frees a slot; reactivating is also limit-checked
    c1 = a.get(f"/orgs/{a.org_id}/companies").json()[0]
    assert a.post(f"/companies/{c1['id']}/status", {"action": "archive"}).status_code == 200
    assert a.post(f"/orgs/{a.org_id}/companies", _company_body("C3")).status_code == 201
    assert a.post(f"/companies/{c1['id']}/status", {"action": "restore"}).status_code == 402


def test_user_accountant_and_device_limits(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    set_plan_limit(owner_engine, a.org_id, "limit.users", 2)
    set_plan_limit(owner_engine, a.org_id, "limit.accountants", 1)
    set_plan_limit(owner_engine, a.org_id, "limit.devices", 1)
    invite_and_accept(a, mailer, role="accountant")  # users 2/2, accountants 1/1
    # third user would exceed limit.users; the invitation is created but acceptance is refused
    email = f"x-{uuid.uuid4().hex[:6]}@example.com"
    assert a.post(f"/orgs/{a.org_id}/invitations", {"email": email, "role": "practice_admin"}).status_code == 201
    token = mailer.last("invitation", email).context["token"]
    r = client.post("/api/v1/auth/invitations/accept", json={"token": token, "display_name": "X", "password": PASSWORD})
    assert r.status_code == 402 and r.json()["key"] == "limit.users"
    # raise users but not accountants: an accountant is still refused on limit.accountants
    set_plan_limit(owner_engine, a.org_id, "limit.users", 5)
    email2 = f"y-{uuid.uuid4().hex[:6]}@example.com"
    assert a.post(f"/orgs/{a.org_id}/invitations", {"email": email2, "role": "staff"}).status_code == 201
    token2 = mailer.last("invitation", email2).context["token"]
    r = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token2, "display_name": "Y", "password": PASSWORD}
    )
    assert r.status_code == 402 and r.json()["key"] == "limit.accountants"
    # devices
    d = {"platform": "windows", "name": "PC-1"}
    first = a.post("/devices", d)
    assert first.status_code == 201
    assert a.post("/devices", {**d, "name": "PC-2"}).json()["key"] == "limit.devices"
    assert a.delete(f"/devices/{first.json()['id']}").status_code == 204
    assert a.post("/devices", {**d, "name": "PC-3"}).status_code == 201


def test_concurrent_creation_cannot_oversubscribe(client, mailer, owner_engine) -> None:  # TS-ENT-02  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    set_plan_limit(owner_engine, a.org_id, "limit.companies", 3)
    make_company(a, "SEED1"), make_company(a, "SEED2")  # 2 of 3 used

    def create(i: int) -> int:
        return a.post(f"/orgs/{a.org_id}/companies", _company_body(f"RACE{i}")).status_code

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert sorted(results).count(201) == 1, results
    assert results.count(402) == 7, results
    assert len(a.get(f"/orgs/{a.org_id}/companies").json()) == 3


def test_override_precedence_expiry_and_unlimited(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    key = "limit.companies"
    with owner_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
        assert entitlements.effective_limit(conn, uuid.UUID(a.org_id), key).source == "plan"
    set_plan_limit(owner_engine, a.org_id, key, 0, expires="2000-01-01T00:00:00Z")  # already expired
    with owner_engine.begin() as conn:
        assert entitlements.effective_limit(conn, uuid.UUID(a.org_id), key).source == "plan"
    set_plan_limit(owner_engine, a.org_id, key, None, unlimited=True)
    with owner_engine.begin() as conn:
        lim = entitlements.effective_limit(conn, uuid.UUID(a.org_id), key)
    assert lim.unlimited and lim.source == "override"
    for i in range(7):  # the dev plan allows 5; unlimited override allows more
        make_company(a, f"U{i}")


def test_plan_change_takes_effect_without_deploy(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """A plan version in use is immutable (Phase 2 trigger); a limit changes by publishing a NEW version and
    moving the subscription to it - exactly what Super Admin does. The shared dev plan is restored afterwards."""
    import uuid

    a = new_owner(client, mailer)
    new_version = uuid.uuid4()
    with owner_engine.begin() as conn:
        pv = conn.execute(
            text("SELECT plan_version_id FROM subscriptions WHERE organization_id=:o"), {"o": a.org_id}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO subscription_plan_versions (id, plan_id, version, billing_interval)"
                " SELECT :nv, plan_id, (SELECT max(x.version) + 1 FROM subscription_plan_versions x WHERE x.plan_id = v.plan_id),"
                " billing_interval FROM subscription_plan_versions v WHERE v.id = :p"
            ),
            {"nv": new_version, "p": pv},
        )
        conn.execute(
            text(
                "INSERT INTO plan_entitlements (plan_version_id, entitlement_key, int_value, bool_value, unlimited)"
                " SELECT :nv, entitlement_key, CASE WHEN entitlement_key = 'limit.companies' THEN 1 ELSE int_value END,"
                " bool_value, unlimited FROM plan_entitlements WHERE plan_version_id = :p"
            ),
            {"nv": new_version, "p": pv},
        )
        conn.execute(
            text("UPDATE subscriptions SET plan_version_id=:nv WHERE organization_id=:o"),
            {"nv": new_version, "o": a.org_id},
        )
    try:
        make_company(a, "ONLY")
        assert a.post(f"/orgs/{a.org_id}/companies", _company_body("TWO")).status_code == 402
    finally:  # restore the shared dev plan for other tests
        with owner_engine.begin() as conn:
            conn.execute(
                text("UPDATE subscriptions SET plan_version_id=:p WHERE organization_id=:o"), {"p": pv, "o": a.org_id}
            )
            conn.execute(text("DELETE FROM plan_entitlements WHERE plan_version_id=:nv"), {"nv": new_version})
            conn.execute(text("DELETE FROM subscription_plan_versions WHERE id=:nv"), {"nv": new_version})


def test_suspended_subscription_blocks_creation_but_not_reads(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    make_company(a, "BEFORE")
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE subscriptions SET status='suspended' WHERE organization_id=:o"), {"o": a.org_id})
    r = a.post(f"/orgs/{a.org_id}/companies", _company_body("AFTER"))
    assert r.status_code == 403 and r.json()["code"] == "subscription_restricted"
    assert len(a.get(f"/orgs/{a.org_id}/companies").json()) == 1  # existing data stays readable


def test_organization_suspension_makes_it_read_only(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE organizations SET status='suspended' WHERE id=:o"), {"o": a.org_id})
    assert a.get(f"/orgs/{a.org_id}").status_code == 200
    r = a.post(f"/orgs/{a.org_id}/companies", _company_body("NOPE"))
    assert r.status_code == 403 and r.json()["organization_status"] == "suspended"


def test_entitlement_summary_and_counter_reconciliation(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    make_company(a, "R1")
    summary = {e["key"]: e for e in a.get(f"/orgs/{a.org_id}/entitlements").json()}
    assert summary["limit.companies"]["current"] == 1 and summary["limit.companies"]["limit"] == 5
    assert summary["limit.workspace_sections_per_company"]["current"] is None  # not measurable yet
    with owner_engine.begin() as conn:  # corrupt a counter, reconcile must detect and repair it
        conn.execute(
            text("UPDATE usage_counters SET value = 99 WHERE organization_id=:o AND metric='limit.companies'"),
            {"o": a.org_id},
        )
        conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
        drift = entitlements.reconcile(conn, uuid.UUID(a.org_id))
    assert drift == {"limit.companies": (99, 1)}


def test_guard_rejects_unenforceable_keys() -> None:
    with pytest.raises(ValueError):
        entitlements.guard(None, uuid.uuid4(), "limit.folder_depth")  # type: ignore[arg-type]


# ------------------------------------------------------------------ policy engine (pure)
ORG, USER, C1, C2 = (uuid.uuid4() for _ in range(4))


def _p(role: str | None, **kw) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(user_id=USER, org_id=ORG, org_role=role, mfa_verified=kw.pop("mfa", True), **kw)


def test_policy_matrix() -> None:
    owner, admin = _p("firm_owner"), _p("practice_admin")
    assert authorize(owner, "organization.update").allowed
    assert not authorize(admin, "organization.update").allowed
    assert authorize(admin, "companies.read", C1).allowed and authorize(admin, "companies.read", C2).allowed
    acc = _p("accountant", assigned_company_ids=frozenset({C1}))
    assert authorize(acc, "companies.read", C1).allowed
    assert authorize(acc, "companies.read", C2).reason == "company_out_of_scope"
    assert not authorize(acc, "companies.create").allowed
    assert authorize(acc, "companies.list").allowed and authorize(acc, "devices.manage_own").allowed
    emp = _p(None, company_roles={C1: "company_employee"})
    assert authorize(emp, "companies.read", C1).allowed
    assert authorize(emp, "companies.read", C2).reason == "company_out_of_scope"
    assert not authorize(emp, "company_members.manage", C1).allowed
    assert authorize(_p(None, company_roles={C1: "company_owner"}), "company_members.manage", C1).allowed
    assert authorize(_p("firm_owner", mfa=False), "companies.read", C1).reason == "mfa_required"
    assert authorize(_p("accountant", mfa=False, assigned_company_ids=frozenset({C1})), "companies.read", C1).allowed
    assert authorize(owner, "platform.self.read").reason == "wrong_realm"
    assert authorize(owner, "companies.read").reason == "company_required"
