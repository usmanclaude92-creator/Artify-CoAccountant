"""Super Admin organization management: listing, detail, provisioning, suspension, companies overview."""

from __future__ import annotations

from conftest import (
    PASSWORD,
    Actor,
    invite_and_accept,
    login,
    make_company,
    new_owner,
    new_platform_admin,
    totp_now,
    uniq,
)
from sqlalchemy import text


def _find(admin, name: str) -> dict:  # type: ignore[no-untyped-def]
    r = admin.get("/organizations", q=name)
    assert r.status_code == 200, r.text
    items = [o for o in r.json()["items"] if o["display_name"] == name]
    assert items, name
    return items[0]


def test_list_filter_sort_paginate_and_search(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    tag = uniq("z").split("-")[1][:6]
    firm = new_owner(client, mailer, org_name=f"Zeta{tag} Audit Firm")
    solo = new_owner(client, mailer, org_name=f"Zeta{tag} Solo Practice", org_type="independent_accountant")
    make_company(firm, "Z1")
    make_company(firm, "Z2")

    r = admin.get("/organizations", q=f"Zeta{tag}")
    body = r.json()
    assert body["total"] == 2 and {o["display_name"] for o in body["items"]} == {
        f"Zeta{tag} Audit Firm",
        f"Zeta{tag} Solo Practice",
    }
    firm_row = next(o for o in body["items"] if o["display_name"] == f"Zeta{tag} Audit Firm")
    assert firm_row["type"] == "accounting_firm" and firm_row["company_count"] == 2 and firm_row["user_count"] == 1
    assert firm_row["owner_email"] == firm.email and firm_row["subscription_status"] == "trialing"
    # type filter = the "Independent accountants" module
    ind = admin.get("/organizations", q=f"Zeta{tag}", type="independent_accountant").json()
    assert [o["display_name"] for o in ind["items"]] == [f"Zeta{tag} Solo Practice"]
    assert admin.get("/organizations", q=f"Zeta{tag}", type="accounting_firm").json()["total"] == 1
    # sorting and pagination
    asc = [
        o["display_name"] for o in admin.get("/organizations", q=f"Zeta{tag}", sort="name", order="asc").json()["items"]
    ]
    assert asc == sorted(asc)
    page = admin.get("/organizations", q=f"Zeta{tag}", sort="name", order="asc", limit=1, offset=1).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["display_name"] == asc[1]
    # owner e-mail search, and LIKE wildcards in input are literal
    assert admin.get("/organizations", q=solo.email).json()["total"] == 1
    assert admin.get("/organizations", q="%").json()["total"] == 0
    assert admin.get("/organizations", status="suspended", q="Zeta").json()["total"] == 0
    assert admin.get("/organizations", limit=0).status_code == 422


def test_detail_contains_aggregates_and_no_business_data(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Detail Firm")
    c = make_company(firm, "D1", tax_registration="TAX-SECRET-123")
    invite_and_accept(firm, mailer, role="accountant")
    org = _find(admin, "Detail Firm")
    d = admin.get(f"/organizations/{org['id']}").json()
    assert d["organization"]["accountant_count"] == 1 and d["organization"]["user_count"] == 2
    assert d["subscription"]["status"] == "trialing" and d["subscription"]["plan_key"]
    keys = {u["key"]: u for u in d["usage"]}
    assert keys["limit.companies"]["current"] == 1 and keys["limit.companies"]["enforced_now"] is True
    assert (
        keys["quota.storage_bytes"]["current"] == 0 and keys["quota.storage_bytes"]["status"] == "ok"
    )  # Phase 6 meters it
    assert (
        keys["quota.ai_units_per_month"]["current"] is None
        and keys["quota.ai_units_per_month"]["status"] == "not_metered"
    )
    flat = str(d)
    assert "TAX-SECRET-123" not in flat and c["id"] not in flat  # no company row data in the organization view
    assert admin.get("/organizations/00000000-0000-0000-0000-000000000000").status_code == 404


def test_company_overview_is_metadata_only(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_support")
    firm = new_owner(client, mailer, org_name="Company View Firm")
    c = make_company(firm, "CV1", name="Gamma Trading LLC", tax_registration="TAX-HIDDEN-9")
    acc = invite_and_accept(firm, mailer, role="accountant")
    firm.post(f"/companies/{c['id']}/assignments", {"user_id": acc.get("/me").json()["user_id"]})
    invite_and_accept(firm, mailer, role="company_employee", path=f"/companies/{c['id']}/invitations")
    r = admin.get("/companies", q="Gamma")
    row = r.json()["items"][0]
    assert row["name"] == "Gamma Trading LLC" and row["organization_name"] == "Company View Firm"
    assert row["accountant_count"] == 1 and row["user_count"] == 1
    assert row["documents_processed"] is None and row["storage_bytes"] is None  # not metered yet, never a fake 0
    assert "tax_registration" not in row and "TAX-HIDDEN-9" not in r.text
    assert admin.get("/companies", organization_id=row["organization_id"]).json()["total"] == 1
    assert admin.get("/companies", status="archived", q="Gamma").json()["total"] == 0
    assert admin.get("/companies", country="ZZ").json()["total"] == 0


def test_provision_organization_owner_accepts_and_signs_in(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    owner_email = uniq("newowner")
    r = admin.post(
        "/organizations",
        {
            "type": "independent_accountant",
            "name": "Provisioned Practice",
            "country": "OM",
            "default_currency": "OMR",
            "owner_email": owner_email,
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["organization"]["owner_email"] is None  # nobody accepted yet; no password was ever set by Artify
    assert d["subscription"]["status"] == "trialing" and d["organization"]["type"] == "independent_accountant"
    org_id = d["organization"]["id"]
    token = mailer.last("invitation", owner_email).context["token"]
    ok = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "display_name": "New Owner", "password": PASSWORD}
    )
    assert ok.status_code == 200, ok.text
    again = client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "display_name": "x", "password": PASSWORD}
    )
    assert again.status_code == 422  # single use
    body = login(client, owner_email)
    actor = Actor(client, owner_email, body["tokens"]["access_token"], body["tokens"]["refresh_token"], org_id)
    me = actor.get("/me").json()
    assert (
        me["memberships"][0]["organization_id"] == org_id and me["memberships"][0]["organization_role"] == "firm_owner"
    )
    # firm owners must still enroll MFA before touching the organization
    assert actor.get(f"/orgs/{org_id}").status_code == 403
    enroll = client.post("/api/v1/auth/mfa/enroll", headers=actor.headers()).json()["secret"]
    confirm = client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(enroll)}, headers=actor.headers())
    actor.access = confirm.json()["access_token"]
    assert actor.get(f"/orgs/{org_id}").status_code == 200
    assert _find(admin, "Provisioned Practice")["owner_email"] == owner_email
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM platform_audit_events WHERE action='organization.provisioned' AND target_organization=:o"
                ),
                {"o": org_id},
            ).scalar()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM subscription_events WHERE organization_id=:o AND event_type='created'"),
                {"o": org_id},
            ).scalar()
            == 1
        )


def test_provisioning_validations_and_permissions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    bad_plan = admin.post(
        "/organizations",
        {
            "type": "accounting_firm",
            "name": "X Firm",
            "country": "OM",
            "default_currency": "OMR",
            "owner_email": uniq("o"),
            "plan_key": "nope",
        },
    )
    assert bad_plan.status_code == 422
    bad_ccy = admin.post(
        "/organizations",
        {
            "type": "accounting_firm",
            "name": "X Firm",
            "country": "OM",
            "default_currency": "ZZZ",
            "owner_email": uniq("o") + "",
        },
    )
    assert bad_ccy.status_code in (409, 422)
    for role in ("platform_support", "platform_billing", "platform_readonly"):
        denied = new_platform_admin(client, owner_engine, role).post(
            "/organizations",
            {
                "type": "accounting_firm",
                "name": "Nope Firm",
                "country": "OM",
                "default_currency": "OMR",
                "owner_email": "a@example.com",
            },
        )
        assert denied.status_code == 403, role


def test_suspend_requires_confirmation_reason_and_is_reversible(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Suspend Me Ltd")
    make_company(firm, "S1")
    org = _find(admin, "Suspend Me Ltd")
    path = f"/organizations/{org['id']}"
    long_reason = "Unpaid invoices after three reminders"
    assert admin.post(f"{path}/suspend", {"reason": "short", "confirm": org["slug"]}).status_code == 422
    wrong = admin.post(f"{path}/suspend", {"reason": long_reason, "confirm": "wrong-slug"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirmation_mismatch"
    assert _find(admin, "Suspend Me Ltd")["status"] == "active"
    ok = admin.post(f"{path}/suspend", {"reason": long_reason, "confirm": org["slug"].upper()})
    assert ok.status_code == 200 and ok.json()["status"] == "suspended"
    # documented restricted behaviour: read-only for the customer, data preserved
    assert firm.get(f"/orgs/{firm.org_id}/companies").status_code == 200
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "S2", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 403
    )
    assert (
        admin.post(f"{path}/suspend", {"reason": long_reason, "confirm": org["slug"]}).status_code == 409
    )  # already suspended
    assert (
        admin.post(f"{path}/reactivate", {"reason": long_reason, "confirm": org["slug"]}).json()["status"] == "active"
    )
    assert admin.post(f"{path}/reactivate", {"reason": long_reason, "confirm": org["slug"]}).status_code == 409
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "S3", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 201
    )
    hist = admin.get(f"{path}/history").json()
    titles = [h["title"] for h in hist]
    assert any("active → suspended" in t for t in titles) and any("suspended → active" in t for t in titles)
    assert any(h["kind"] == "registered" for h in hist)
    detail = admin.get(path).json()
    assert [e["to_status"] for e in detail["status_history"]] == ["active", "suspended"]
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, reason FROM platform_audit_events WHERE target_organization=:o AND action LIKE 'organization.%' ORDER BY created_at"
            ),
            {"o": org["id"]},
        ).all()
    assert [r.action for r in rows] == ["organization.suspended", "organization.reactivated"] and rows[
        0
    ].reason == long_reason
    # support/billing/auditor roles cannot suspend
    for role in ("platform_support", "platform_billing", "platform_readonly", "platform_auditor"):
        assert (
            new_platform_admin(client, owner_engine, role)
            .post(f"{path}/suspend", {"reason": long_reason, "confirm": org["slug"]})
            .status_code
            == 403
        )


def test_status_history_is_append_only(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Immutable History Ltd")
    org = _find(admin, "Immutable History Ltd")
    admin.post(
        f"/organizations/{org['id']}/suspend", {"reason": "Testing the append-only history", "confirm": org["slug"]}
    )
    import pytest
    from sqlalchemy.exc import DBAPIError

    for sql in (
        "UPDATE organization_status_events SET reason = 'tampered tampered'",
        "DELETE FROM organization_status_events",
    ):
        with pytest.raises(DBAPIError), owner_engine.begin() as conn:
            conn.execute(text(sql))


def test_profile_update_and_unauthorized_update_rejected(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, "platform_admin")
    new_owner(client, mailer, org_name="Rename Me Ltd")
    org = _find(admin, "Rename Me Ltd")
    r = admin.patch(f"/organizations/{org['id']}", {"display_name": "Renamed Ltd"})
    assert r.status_code == 200 and r.json()["display_name"] == "Renamed Ltd"
    assert admin.patch(f"/organizations/{org['id']}", {}).status_code == 422
    assert (
        new_platform_admin(client, owner_engine, "platform_support")
        .patch(f"/organizations/{org['id']}", {"display_name": "Hack"})
        .status_code
        == 403
    )
    assert _find(admin, "Renamed Ltd")["id"] == org["id"]


def test_global_search_respects_permissions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner_admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Searchable Firm")
    make_company(firm, "SRCH", name="Searchable Holdings")
    hits = owner_admin.get("/search", q="Searchable").json()
    assert {h["kind"] for h in hits} >= {"organization", "company"}
    billing = new_platform_admin(client, owner_engine, "platform_billing").get("/search", q="Searchable").json()
    assert {h["kind"] for h in billing} == {"organization"}  # no companies / administrators for finance
    assert owner_admin.get("/search", q="a").status_code == 422
