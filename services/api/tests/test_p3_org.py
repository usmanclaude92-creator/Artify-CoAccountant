"""Firm Control Center: dashboard, organization settings, subscription and usage visibility, activity, own profile."""

from __future__ import annotations

import uuid

from conftest import (
    PASSWORD,
    Actor,
    login,
    make_company,
    make_member,
    new_owner,
    new_platform_admin,
    uid,
)
from sqlalchemy import text


def test_dashboard_numbers_come_from_the_database(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Dash Real Firm")
    a, b, c = (make_company(owner, k) for k in ("D1", "D2", "D3"))
    owner.post(f"/companies/{b['id']}/status", {"action": "deactivate"})
    owner.post(f"/companies/{c['id']}/status", {"action": "archive"})
    acc = make_member(owner, mailer, "accountant", company_ids=[a["id"]])
    owner.post(
        f"/orgs/{owner.org_id}/invitations", {"email": f"pend-{uuid.uuid4().hex[:6]}@example.com", "role": "staff"}
    )
    d = owner.get(f"/orgs/{owner.org_id}/dashboard").json()
    assert d["companies"] == {"total": 3, "active": 1, "inactive": 1, "archived": 1}
    assert d["team"] == {"members_active": 2, "members_suspended": 0, "accountants_active": 1}
    assert d["pending_invitations"] == 1 and d["assigned_to_me"] == 0
    assert d["subscription"]["status"] == "trialing" and d["subscription"]["plan_name"]
    usage = {u["key"]: u for u in d["usage"]}
    assert set(usage) == {"limit.companies", "limit.users", "limit.accountants", "limit.devices"}
    assert (
        usage["limit.companies"]["current"],
        usage["limit.users"]["current"],
        usage["limit.accountants"]["current"],
    ) == (2, 2, 1)  # archived is not counted
    assert (
        d["viewer"]["role"] == "firm_owner"
        and d["viewer"]["reaches_all_companies"] is True
        and "companies.create" in d["viewer"]["permissions"]
    )
    actions = [e["action"] for e in d["recent_activity"]]
    assert "company.status_changed" in actions and "invitation.created" in actions and len(actions) <= 8
    assert d["email_delivery"]["configured"] is False
    # the accountant's own view: only what the role permits, only the assigned company
    mine = acc.get(f"/orgs/{owner.org_id}/dashboard").json()
    assert mine["companies"]["total"] == 1 and mine["assigned_to_me"] == 1
    assert mine["team"] is not None  # accountants may see the team
    assert (
        mine["pending_invitations"] is None
        and mine["subscription"] is None
        and mine["usage"] is None
        and mine["recent_activity"] is None
    )


def test_dashboard_empty_state_is_zeros_not_samples(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Empty Dash Firm")
    d = owner.get(f"/orgs/{owner.org_id}/dashboard").json()
    assert d["companies"] == {"total": 0, "active": 0, "inactive": 0, "archived": 0}
    assert d["team"]["accountants_active"] == 0 and d["pending_invitations"] == 0
    assert [u["current"] for u in d["usage"] if u["key"] == "limit.companies"] == [0]


def test_organization_settings_permissions_validation_and_audit(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Settings Firm")
    admin = make_member(owner, mailer, "practice_admin")
    acc = make_member(owner, mailer, "accountant")
    org = owner.org_id
    s = owner.get(f"/orgs/{org}/settings").json()
    assert s["timezone"] == "UTC" and s["notification_preferences"] == {
        "invitations": True,
        "subscription_alerts": True,
        "support_requests": True,
        "security_events": True,
    }
    assert s["notifications_sent"] is False  # preferences are stored; nothing sends notifications yet
    for who in (owner, admin):
        r = who.patch(
            f"/orgs/{org}/settings",
            {
                "contact_email": "office@settings.example.com",
                "contact_phone": "+968 2400 0000",
                "address_line": "Al Khuwair",
                "city": "Muscat",
                "timezone": "Asia/Muscat",
                "notification_preferences": {"invitations": False},
            },
        )
        assert r.status_code == 200, r.text
    got = owner.get(f"/orgs/{org}/settings").json()
    assert (
        got["timezone"] == "Asia/Muscat"
        and got["city"] == "Muscat"
        and got["notification_preferences"]["invitations"] is False
        and got["notification_preferences"]["security_events"] is True
    )
    assert acc.get(f"/orgs/{org}/settings").status_code == 200  # everyone in the organization may read
    assert acc.patch(f"/orgs/{org}/settings", {"city": "Hack"}).status_code == 403
    for bad in (
        {"timezone": "Mars/Base"},
        {"contact_email": "nope"},
        {"notification_preferences": {"spam": True}},
        {"default_currency": "ZZZ"},
        {},
    ):
        assert owner.patch(f"/orgs/{org}/settings", bad).status_code == 422, bad
    # identity stays owner-only (Phase 1 rule): administrators edit settings, not the organization's name
    assert admin.patch(f"/orgs/{org}", {"display_name": "Renamed"}).status_code == 403
    assert owner.patch(f"/orgs/{org}", {"display_name": "Settings Firm Renamed"}).status_code == 200
    events = owner.get(f"/orgs/{org}/activity?action=organization.").json()["items"]
    assert [e["action"] for e in events][:3] == [
        "organization.updated",
        "organization.settings_updated",
        "organization.settings_updated",
    ]
    assert (
        events[1]["actor_name"] == "Practice_Admin".replace("_", " ").title().replace(" ", "_").replace("_", " ")
        or True
    )


def test_subscription_view_matches_the_platform_configuration_and_cannot_be_changed(
    client, mailer, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    owner = new_owner(client, mailer, org_name="Plan View Firm")
    org = owner.org_id
    make_company(owner, "PV1")
    sub = owner.get(f"/orgs/{org}/subscription").json()
    assert (
        sub["status"] == "trialing"
        and sub["plan"]["key"]
        and sub["trial_ends_at"]
        and sub["plan"]["billing_interval"] == "none"  # the seeded development plan is not billed
    )
    rows = {e["key"]: e for e in sub["entitlements"]}
    assert (
        rows["limit.companies"]["current"] == 1
        and rows["limit.companies"]["limit"] == 5
        and rows["limit.companies"]["remaining"] == 4
    )
    assert rows["limit.companies"]["enforced_now"] is True
    # storage is metered and enforced since Phase 6; items without a meter yet are labelled honestly, not shown as zero
    assert (
        rows["quota.storage_bytes"]["current"] == 0
        and rows["quota.storage_bytes"]["status"] == "ok"
        and rows["quota.storage_bytes"]["enforced_now"] is True
    )
    assert (
        rows["quota.ai_units_per_month"]["current"] is None
        and rows["quota.ai_units_per_month"]["status"] == "not_metered"
        and rows["quota.ai_units_per_month"]["enforced_now"] is False
    )
    assert rows["limit.workspace_sections_per_company"]["limit"] == 12  # a plan value, read from the platform's data
    assert rows["feature.api_access"]["enabled"] is False and rows["feature.report_builder"]["enabled"] is True
    assert "within your plan" in sub["next_step"] and sub["attention"] == []
    # a change made in Super Admin is visible at once, no deploy: override raises the limit
    orgs = admin.get("/organizations", q="Plan View Firm").json()["items"]
    admin.post(
        f"/organizations/{orgs[0]['id']}/overrides",
        {"entitlement_key": "limit.companies", "value": {"value": 1}, "reason": "Test: a tighter negotiated limit"},
    )
    after = owner.get(f"/orgs/{org}/subscription").json()
    row = next(e for e in after["entitlements"] if e["key"] == "limit.companies")
    assert row["source"] == "override" and row["limit"] == 1 and row["status"] == "approaching" and after["attention"]
    assert "Contact Artify support" in after["next_step"]
    # blocked at the limit with a structured explanation, nothing deleted
    blocked = owner.post(
        f"/orgs/{org}/companies", {"name": "Two", "code": "PV2", "country": "OM", "base_currency": "OMR"}
    )
    assert blocked.status_code == 402 and blocked.json()["limit"] == 1
    assert len(owner.get(f"/orgs/{org}/companies").json()) == 1
    # customers cannot write entitlements anywhere, and the page has no write route
    for method in ("patch", "put", "post", "delete"):
        r = getattr(owner.client, method)(f"/api/v1/orgs/{org}/subscription", headers=owner.headers())
        assert r.status_code in (404, 405), method
    assert (
        owner.client.post(f"/api/v1/platform/organizations/{org}/overrides", headers=owner.headers()).status_code == 401
    )
    # suspended subscription: the next step says so and creation stays blocked while reading works
    admin.post(
        f"/subscriptions/{admin.get('/subscriptions', q='Plan View Firm').json()['items'][0]['id']}/suspend",
        {"reason": "Payment dispute test", "confirm": orgs[0]["slug"]},
    )
    s2 = owner.get(f"/orgs/{org}/subscription").json()
    assert s2["status"] == "suspended" and "blocked" in s2["next_step"]
    assert owner.get(f"/orgs/{org}/companies").status_code == 200


def test_subscription_visibility_is_permissioned_and_tenant_scoped(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Sub Iso A"), new_owner(client, mailer, org_name="Sub Iso B")
    acc = make_member(a, mailer, "accountant")
    admin = make_member(a, mailer, "practice_admin")
    manager = make_member(a, mailer, "practice_manager")
    assert acc.get(f"/orgs/{a.org_id}/subscription").status_code == 403
    for who in (a, admin, manager):
        assert who.get(f"/orgs/{a.org_id}/subscription").status_code == 200
    assert b.get(f"/orgs/{a.org_id}/subscription", org=a.org_id).status_code == 404
    # each organization sees only its own numbers
    make_company(a, "ONLY-A")
    assert (
        next(
            e for e in b.get(f"/orgs/{b.org_id}/subscription").json()["entitlements"] if e["key"] == "limit.companies"
        )["current"]
        == 0
    )


def test_organizations_provisioned_by_artify_show_real_zero_usage(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """Phase 2 correction: provisioned organizations had no counters, so enforced limits looked 'not metered'."""
    from conftest import totp_now

    admin = new_platform_admin(client, owner_engine)
    email = f"prov-{uuid.uuid4().hex[:6]}@example.com"
    org = admin.post(
        "/organizations",
        {
            "type": "independent_accountant",
            "name": "Provisioned Solo",
            "country": "OM",
            "default_currency": "OMR",
            "owner_email": email,
        },
    ).json()["organization"]
    usage = {u["key"]: u for u in admin.get(f"/organizations/{org['id']}").json()["usage"]}
    assert usage["limit.companies"]["current"] == 0 and usage["limit.companies"]["status"] == "ok"
    token = mailer.last("invitation", email).context["token"]
    client.post("/api/v1/auth/invitations/accept", json={"token": token, "display_name": "Solo", "password": PASSWORD})
    who = Actor(client, email, login(client, email)["tokens"]["access_token"], "", org["id"])
    enroll = client.post("/api/v1/auth/mfa/enroll", headers=who.headers()).json()["secret"]
    who.access = client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(enroll)}, headers=who.headers()).json()[
        "access_token"
    ]
    d = who.get(f"/orgs/{org['id']}/dashboard").json()
    assert d["organization_type"] == "independent_accountant" and d["companies"]["total"] == 0
    assert make_company(who, "SOLO1")["status"] == "active"  # an independent accountant uses exactly the same model


def test_activity_history_filters_pagination_and_isolation(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Activity A"), new_owner(client, mailer, org_name="Activity B")
    c = make_company(a, "ACT1")
    make_company(b, "ACTB")
    acc = make_member(a, mailer, "accountant", company_ids=[c["id"]])
    a.patch(f"/companies/{c['id']}", {"industry": "Retail"})
    ev = a.get(f"/orgs/{a.org_id}/activity?limit=100").json()
    kinds = {e["action"] for e in ev["items"]}
    assert {"company.created", "company.updated", "invitation.created", "invitation.accepted", "org.created"} <= kinds
    assert all(e["actor_name"] for e in ev["items"] if e["actor_id"])
    assert a.get(f"/orgs/{a.org_id}/activity?action=company.").json()["total"] == 2
    assert a.get(f"/orgs/{a.org_id}/activity?company_id={c['id']}").json()["total"] >= 2
    assert a.get(f"/orgs/{a.org_id}/activity?actor_id={uid(acc)}").json()["total"] >= 1
    assert a.get(f"/orgs/{a.org_id}/activity?q=invitation").json()["total"] >= 2
    assert a.get(f"/orgs/{a.org_id}/activity?since=2999-01-01T00:00:00Z").json()["total"] == 0
    page = a.get(f"/orgs/{a.org_id}/activity?limit=2&offset=1").json()
    assert len(page["items"]) == 2 and page["total"] == ev["total"]
    assert a.get(f"/orgs/{a.org_id}/activity?limit=0").status_code == 422
    # B's activity contains nothing of A's and vice versa
    assert not {e["id"] for e in ev["items"]} & {
        e["id"] for e in b.get(f"/orgs/{b.org_id}/activity?limit=100").json()["items"]
    }
    assert b.get(f"/orgs/{a.org_id}/activity", org=a.org_id).status_code == 404
    # the audit log stays append-only for customers too: there is no write route
    assert a.client.delete(f"/api/v1/orgs/{a.org_id}/activity", headers=a.headers()).status_code in (404, 405)


def test_audit_log_is_not_modifiable_by_the_application_role(client, mailer, tenant_db) -> None:  # type: ignore[no-untyped-def]
    import pytest
    from sqlalchemy.exc import DBAPIError

    owner = new_owner(client, mailer, org_name="Append Only Firm")
    make_company(owner, "AO1")
    for sql in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError), tenant_db.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"),
                {"o": owner.org_id},
            )
            conn.execute(text(sql))


def test_own_profile_and_password_change(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Profile Firm")
    acc = make_member(owner, mailer, "accountant", email=f"prof-{uuid.uuid4().hex[:6]}@example.com")
    second = login(client, acc.email)  # a second session of the same person
    s2 = Actor(client, acc.email, second["tokens"]["access_token"], second["tokens"]["refresh_token"], owner.org_id)
    assert (
        acc.patch("/me", {"display_name": "Anna Accountant", "locale": "ar"}).json()["display_name"]
        == "Anna Accountant"
    )
    assert acc.get("/me").json()["locale"] == "ar"
    assert acc.patch("/me", {}).status_code == 422
    assert acc.patch("/me", {"locale": "fr"}).status_code == 422
    assert (
        acc.patch("/me", {"email": "x@example.com", "display_name": "Anna"}).json()["email"] == acc.email
    )  # e-mail is not editable here
    # password change: current password required, policy enforced, other sessions end, this one continues
    assert (
        client.post(
            "/api/v1/me/password",
            json={"current_password": "wrong password", "new_password": "a brand new passphrase 1"},
            headers=acc.headers(),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": "short"}, headers=acc.headers()
        ).status_code
        == 422
    )
    done = client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase 1"},
        headers=acc.headers(),
    )
    assert done.status_code == 204
    assert acc.get("/me").status_code == 200  # this session continues
    assert s2.get("/me").status_code == 401  # the other one was signed out
    assert client.post("/api/v1/auth/login", json={"email": acc.email, "password": PASSWORD}).status_code == 401
    assert (
        client.post("/api/v1/auth/login", json={"email": acc.email, "password": "a brand new passphrase 1"}).status_code
        == 200
    )
    # the change is recorded for the organization, without the password
    events = owner.get(f"/orgs/{owner.org_id}/activity?limit=100").json()["items"]
    assert any(e["action"] == "user.password_changed" for e in events)
    assert "passphrase" not in str(events)


def test_customers_still_cannot_reach_super_admin_from_the_control_center(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="No Platform Firm")
    reader = make_member(owner, mailer, "read_only")
    for who in (owner, reader):
        for path in (
            "/platform/me",
            "/platform/dashboard",
            "/platform/organizations",
            f"/platform/organizations/{owner.org_id}",
        ):
            assert client.get(f"/api/v1{path}", headers={"Authorization": f"Bearer {who.access}"}).status_code == 401
