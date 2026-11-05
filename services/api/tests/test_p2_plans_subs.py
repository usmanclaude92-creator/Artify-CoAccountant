"""Plans, versions, subscriptions, entitlement enforcement and history integrity (TS-ENT-03/04/07)."""

from __future__ import annotations

import uuid

import pytest
from conftest import make_company, new_owner, new_platform_admin
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

LIMITS = {
    "limit.companies": {"value": 2},
    "limit.users": {"value": 10},
    "limit.accountants": {"value": 5},
    "limit.devices": {"value": 5},
    "limit.workspace_sections_per_company": {"value": 8},
    "quota.storage_bytes": {"value": 1073741824},
    "feature.api_access": {"enabled": False},
}


def _plan_body(key: str, **over) -> dict:  # type: ignore[no-untyped-def]
    body = {
        "key": key,
        "name": f"Plan {key}",
        "description": "test plan",
        "version": {
            "billing_interval": "monthly",
            "price_amount": "49.500",
            "price_currency": "OMR",
            "support_level": "priority",
            "trial_days": 14,
            "entitlements": LIMITS,
        },
    }
    body.update(over)
    return body


def _make_plan(admin, key: str | None = None, companies: int = 2, activate: bool = True, price: str = "49.500") -> dict:  # type: ignore[no-untyped-def]
    key = key or f"t_{uuid.uuid4().hex[:8]}"
    body = _plan_body(key)
    body["version"]["entitlements"] = {**LIMITS, "limit.companies": {"value": companies}}
    body["version"]["price_amount"] = price
    r = admin.post("/plans", body)
    assert r.status_code == 201, r.text
    plan = r.json()
    if activate:
        assert admin.post(f"/plans/{plan['plan']['id']}/activate").status_code == 200
        plan = admin.get(f"/plans/{plan['plan']['id']}").json()
    return plan


def _org(admin, name: str) -> dict:  # type: ignore[no-untyped-def]
    return next(o for o in admin.get("/organizations", q=name).json()["items"] if o["display_name"] == name)


def _sub(admin, org_id: str) -> dict:  # type: ignore[no-untyped-def]
    return admin.get(f"/organizations/{org_id}").json()["subscription"]


def test_plan_create_validation_and_money_as_strings(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    plan = _make_plan(admin, activate=False)
    v = plan["versions"][0]
    assert plan["plan"]["status"] == "inactive" and v["version"] == 1  # a new plan is never live until activated
    assert v["price_amount"] == "49.500" and isinstance(v["price_amount"], str)  # money is a string in JSON
    assert v["entitlements"]["limit.companies"] == {"value": 2, "unlimited": False, "enabled": None}
    assert v["entitlements"]["feature.api_access"]["enabled"] is False
    key = plan["plan"]["key"]
    assert admin.post("/plans", _plan_body(key)).status_code == 409
    for bad in (
        {"key": "Bad Key", "name": "x" * 3},
        {"version": {"entitlements": {"limit.nonsense": {"value": 1}}}},
        {"version": {"entitlements": {"limit.companies": {"enabled": True}}}},
        {"version": {"entitlements": {"feature.api_access": {"value": 3}}}},
        {"version": {"entitlements": {"limit.companies": {"value": 1, "unlimited": True}}}},
        {"version": {"entitlements": {"limit.companies": {"value": -1}}}},
        {"version": {"price_amount": "10", "price_currency": None, "entitlements": {}}},
        {"version": {"price_amount": "-1", "price_currency": "OMR", "entitlements": {}}},
        {"version": {"price_amount": "1", "price_currency": "ZZZ", "entitlements": {}}},
    ):
        body = _plan_body(f"t_{uuid.uuid4().hex[:8]}")
        for k, val in bad.items():
            body[k] = val if k != "version" else {**body["version"], **val}
        assert admin.post("/plans", body).status_code == 422, bad
    catalog = {c["key"]: c for c in admin.get("/entitlements").json()}
    assert (
        catalog["limit.companies"]["enforced_now"] is True
        and catalog["limit.workspace_sections_per_company"]["enforced_now"]
        is True  # enforced per company since Phase 4
        and catalog["limit.excel_templates_per_company"]["enforced_now"] is True  # enforced since Phase 9
    )


def test_versions_are_immutable_once_used_and_plans_can_be_compared(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    plan = _make_plan(admin)
    pid, v1 = plan["plan"]["id"], plan["versions"][0]["id"]
    # edit = new version; both exist, subscriptions keep what they pinned
    new_owner(client, mailer, org_name="Pinned Firm")
    org = _org(admin, "Pinned Firm")
    sub = _sub(admin, org["id"])
    assert (
        admin.post(
            f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": v1, "reason": "Move onto the new test plan"}
        ).status_code
        == 200
    )
    spec = {
        **plan["versions"][0],
        "entitlements": {**plan["versions"][0]["entitlements"], "limit.companies": {"value": 9}},
    }
    spec = {
        k: spec[k]
        for k in (
            "billing_interval",
            "price_amount",
            "price_currency",
            "support_level",
            "trial_days",
            "notes",
            "entitlements",
        )
    }
    v2 = admin.post(f"/plans/{pid}/versions", spec)
    assert v2.status_code == 201 and [v["version"] for v in v2.json()["versions"]] == [2, 1]
    assert v2.json()["versions"][1]["subscriptions"] == 1 and v2.json()["plan"]["latest_version"] == 2
    assert _sub(admin, org["id"])["plan_version"] == 1  # grandfathered
    # the database itself refuses to edit a version a subscription uses
    for sql in (
        "UPDATE plan_entitlements SET int_value = 99 WHERE plan_version_id = :v",
        "DELETE FROM plan_entitlements WHERE plan_version_id = :v",
        "UPDATE subscription_plan_versions SET support_level = 'community' WHERE id = :v",
        "DELETE FROM subscription_plan_versions WHERE id = :v",
    ):
        with pytest.raises(DBAPIError) as exc, owner_engine.begin() as conn:
            conn.execute(text(sql), {"v": v1})
        assert "immutable" in str(exc.value)
    other = _make_plan(admin, companies=4)
    cmp = admin.get("/plans/compare", plan_id=[pid, other["plan"]["id"]]).json()
    row = next(r for r in cmp["rows"] if r["key"] == "limit.companies")
    assert [v["value"] for v in row["values"]] == [9, 4] and len(cmp["plans"]) == 2
    assert admin.get("/plans/compare", plan_id=[pid]).status_code == 422


def test_plan_lifecycle_activate_deactivate_archive(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    plan = _make_plan(admin)
    pid, key = plan["plan"]["id"], plan["plan"]["key"]
    assert admin.post(f"/plans/{pid}/activate").status_code == 409  # already active
    assert admin.post(f"/plans/{pid}/deactivate").json()["status"] == "inactive"
    assert admin.post(f"/plans/{pid}/activate").json()["status"] == "active"
    assert (
        admin.post(f"/plans/{pid}/archive", {"reason": "Superseded by a newer plan", "confirm": "wrong"}).status_code
        == 422
    )
    assert (
        admin.post(f"/plans/{pid}/archive", {"reason": "Superseded by a newer plan", "confirm": key}).json()["status"]
        == "retired"
    )
    assert admin.post(f"/plans/{pid}/activate").status_code == 409  # archived is final
    spec = {k: plan["versions"][0][k] for k in ("billing_interval", "support_level", "entitlements")}
    assert admin.post(f"/plans/{pid}/versions", spec).status_code == 409
    assert admin.patch(f"/plans/{pid}", {"name": "Renamed plan"}).json()["name"] == "Renamed plan"
    listed = {p["key"]: p for p in admin.get("/plans").json()}
    assert listed[key]["status"] == "retired"
    assert key not in {p["key"] for p in admin.get("/plans", status="active").json()}
    # the default sign-up plan cannot be deactivated or archived out from under new customers
    default = next(p for p in admin.get("/plans").json() if p["is_default"])
    assert admin.post(f"/plans/{default['id']}/deactivate").status_code == 409
    assert (
        admin.post(
            f"/plans/{default['id']}/archive", {"reason": "Trying to break sign-up", "confirm": default["key"]}
        ).status_code
        == 409
    )


def test_plan_change_is_enforced_by_the_customer_api(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """Acceptance 1: a plan assigned in Super Admin changes the tenant's effective limit with no deploy."""
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Limit Firm")
    org = _org(admin, "Limit Firm")
    sub = _sub(admin, org["id"])
    small = _make_plan(admin, companies=1, price="10.000")["versions"][0]["id"]
    big = _make_plan(admin, companies=4, price="99.000")["versions"][0]["id"]
    r = admin.post(
        f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": small, "reason": "Starter customer, one company"}
    )
    assert r.status_code == 200 and r.json()["plan_version_id"] == small
    make_company(firm, "L1")
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "L2", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 402
    )
    assert (
        admin.post(
            f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": big, "reason": "Upgraded after sales call"}
        ).status_code
        == 200
    )
    for code in ("L2", "L3", "L4"):
        make_company(firm, code)
    blocked = firm.post(
        f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "L5", "country": "OM", "base_currency": "OMR"}
    )
    assert blocked.status_code == 402 and blocked.json()["limit"] == 4 and blocked.json()["current"] == 4
    ev = admin.get(f"/subscriptions/{sub['id']}").json()["events"]
    assert [e["event_type"] for e in ev][:2] == ["plan_changed", "plan_changed"] and ev[0]["details"][
        "direction"
    ] == "upgrade"
    assert (
        admin.post(
            f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": big, "reason": "Same plan again please"}
        ).status_code
        == 409
    )


def test_downgrade_below_usage_needs_acknowledgement_and_preserves_data(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Downgrade Firm")
    for c in ("D1", "D2"):
        make_company(firm, c)
    org = _org(admin, "Downgrade Firm")
    sub = _sub(admin, org["id"])
    mid = _make_plan(admin, companies=5, price="100.000")["versions"][0]["id"]
    assert (
        admin.post(
            f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": mid, "reason": "Moved onto a priced plan"}
        ).status_code
        == 200
    )
    tiny = _make_plan(admin, companies=1, price="10.000")["versions"][0]["id"]
    refused = admin.post(
        f"/subscriptions/{sub['id']}/change-plan", {"plan_version_id": tiny, "reason": "Customer asked to downgrade"}
    )
    assert refused.status_code == 409 and refused.json()["code"] == "over_limit"
    assert refused.json()["violations"] == [{"key": "limit.companies", "limit": 1, "current": 2}]
    assert _sub(admin, org["id"])["plan_version_id"] == mid  # nothing changed
    done = admin.post(
        f"/subscriptions/{sub['id']}/change-plan",
        {"plan_version_id": tiny, "reason": "Customer asked to downgrade", "acknowledge_over_limit": True},
    )
    assert done.status_code == 200
    # existing data untouched and readable; further creation blocked
    assert len(firm.get(f"/orgs/{firm.org_id}/companies").json()) == 2
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "D3", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 402
    )
    usage = admin.get("/usage", q="Downgrade Firm").json()["items"][0]
    assert usage["worst"] == "exceeded"
    row = next(r for r in usage["rows"] if r["key"] == "limit.companies")
    assert row["current"] == 2 and row["limit"] == 1 and row["remaining"] == -1 and row["percent"] == 200.0
    ev = admin.get(f"/subscriptions/{sub['id']}").json()["events"][0]
    assert ev["details"]["direction"] == "downgrade" and ev["details"]["over_limit"][0]["key"] == "limit.companies"


def test_extend_suspend_reactivate_status_and_restrictions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Lifecycle Firm")
    org = _org(admin, "Lifecycle Firm")
    sub = _sub(admin, org["id"])
    sid = sub["id"]
    reason = "Documented administrative reason"
    ext = admin.post(f"/subscriptions/{sid}/extend", {"days": 10, "reason": reason}).json()
    assert ext["trial_ends_at"] > sub["trial_ends_at"]
    assert admin.post(f"/subscriptions/{sid}/extend", {"days": 0, "reason": reason}).status_code == 422
    assert admin.post(f"/subscriptions/{sid}/suspend", {"reason": reason, "confirm": "nope"}).status_code == 422
    s = admin.post(f"/subscriptions/{sid}/suspend", {"reason": reason, "confirm": org["slug"]})
    assert s.status_code == 200 and s.json()["status"] == "suspended"
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "LC1", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 403
    )
    assert firm.get(f"/orgs/{firm.org_id}/companies").status_code == 200  # reads keep working
    assert admin.post(f"/subscriptions/{sid}/extend", {"days": 3, "reason": reason}).status_code == 409
    back = admin.post(f"/subscriptions/{sid}/reactivate", {"reason": reason}).json()
    assert back["status"] == "trialing"  # restored to what it was before the suspension
    make_company(firm, "LC2")
    assert admin.post(f"/subscriptions/{sid}/reactivate", {"reason": reason}).status_code == 409
    # explicit transitions with a matrix
    assert (
        admin.post(
            f"/subscriptions/{sid}/status", {"status": "active", "reason": reason, "confirm": org["slug"]}
        ).json()["status"]
        == "active"
    )
    assert (
        admin.post(
            f"/subscriptions/{sid}/status", {"status": "past_due", "reason": reason, "confirm": org["slug"]}
        ).json()["status"]
        == "past_due"
    )
    assert (
        admin.post(
            f"/subscriptions/{sid}/status", {"status": "expired", "reason": reason, "confirm": org["slug"]}
        ).json()["status"]
        == "expired"
    )
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "LC3", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 403
    )
    assert (
        admin.post(
            f"/subscriptions/{sid}/status", {"status": "past_due", "reason": reason, "confirm": org["slug"]}
        ).status_code
        == 409
    )  # not allowed from expired
    assert (
        admin.post(
            f"/subscriptions/{sid}/status", {"status": "active", "reason": reason, "confirm": org["slug"]}
        ).json()["status"]
        == "active"
    )
    cancelled = admin.post(
        f"/subscriptions/{sid}/status", {"status": "cancelled", "reason": reason, "confirm": org["slug"]}
    ).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancelled_at"]
    assert (
        admin.post(
            f"/subscriptions/{sid}/change-plan", {"plan_version_id": sub["plan_version_id"], "reason": reason}
        ).status_code
        == 409
    )
    assert (
        admin.post(f"/subscriptions/{sid}/cancel-at-period-end", {"enabled": True, "reason": reason}).status_code == 409
    )
    # history: every transition recorded, in order, append-only
    events = admin.get(f"/subscriptions/{sid}").json()["events"]
    kinds = [(e["event_type"], e["to_status"]) for e in reversed(events)]
    assert (
        kinds[0] == ("created", "trialing") and ("status_changed", "cancelled") in kinds and ("extended", None) in kinds
    )
    with pytest.raises(DBAPIError), owner_engine.begin() as conn:
        conn.execute(text("UPDATE subscription_events SET reason = 'tampered' WHERE subscription_id = :s"), {"s": sid})
    with pytest.raises(DBAPIError), owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM subscription_events WHERE subscription_id = :s"), {"s": sid})


def test_cancel_at_period_end_flag_and_subscription_list_filters(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    tag = uuid.uuid4().hex[:6]
    new_owner(client, mailer, org_name=f"Listing{tag} Alpha")
    new_owner(client, mailer, org_name=f"Listing{tag} Beta")
    org = _org(admin, f"Listing{tag} Alpha")
    sid = _sub(admin, org["id"])["id"]
    r = admin.post(f"/subscriptions/{sid}/cancel-at-period-end", {"enabled": True, "reason": "Customer will not renew"})
    assert r.status_code == 200 and r.json()["cancel_at_period_end"] is True
    admin.post(f"/subscriptions/{sid}/suspend", {"reason": "Payment dispute under review", "confirm": org["slug"]})
    listing = admin.get("/subscriptions", q=f"Listing{tag}", sort="organization", order="asc").json()
    assert listing["total"] == 2 and listing["items"][0]["organization_name"] == f"Listing{tag} Alpha"
    assert listing["items"][0]["latest_invoice_status"] is None and listing["items"][0]["organization_slug"]
    only = admin.get("/subscriptions", q=f"Listing{tag}", status="suspended").json()
    assert only["total"] == 1 and only["items"][0]["id"] == sid
    assert admin.get("/subscriptions", plan="no_such_plan").json()["total"] == 0
    assert admin.get("/subscriptions", status="bogus").status_code == 422


def test_overrides_raise_limits_expire_and_are_audited(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Override Firm")
    org = _org(admin, "Override Firm")
    small = _make_plan(admin, companies=1)["versions"][0]["id"]
    admin.post(
        f"/subscriptions/{_sub(admin, org['id'])['id']}/change-plan",
        {"plan_version_id": small, "reason": "Starter customer with one company"},
    )
    make_company(firm, "O1")
    body = {"entitlement_key": "limit.companies", "value": {"value": 3}, "reason": "Negotiated exception for migration"}
    assert (
        admin.post(f"/organizations/{org['id']}/overrides", {**body, "expires_at": "2000-01-01T00:00:00Z"}).status_code
        == 422
    )
    assert (
        admin.post(f"/organizations/{org['id']}/overrides", {**body, "entitlement_key": "limit.bogus"}).status_code
        == 422
    )
    assert admin.post(f"/organizations/{org['id']}/overrides", {**body, "value": {"enabled": True}}).status_code == 422
    created = admin.post(f"/organizations/{org['id']}/overrides", body)
    assert created.status_code == 201 and created.json()["active"] is True
    make_company(firm, "O2")  # override beats the plan
    usage = admin.get(f"/organizations/{org['id']}").json()["usage"]
    assert next(u for u in usage if u["key"] == "limit.companies")["source"] == "override"
    assert (
        admin.post(
            f"/organizations/{org['id']}/overrides/{created.json()['id']}/expire",
            {"reason": "Migration finished, exception ends"},
        ).json()["active"]
        is False
    )
    assert (
        firm.post(
            f"/orgs/{firm.org_id}/companies", {"name": "n", "code": "O3", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 402
    )
    assert (
        admin.post(
            f"/organizations/{org['id']}/overrides/{created.json()['id']}/expire",
            {"reason": "Expiring it a second time"},
        ).status_code
        == 404
    )
    # only roles with the override permission (and not even support) may create one
    for role in ("platform_support", "platform_billing", "platform_readonly"):
        assert (
            new_platform_admin(client, owner_engine, role)
            .post(f"/organizations/{org['id']}/overrides", body)
            .status_code
            == 403
        )
    with owner_engine.connect() as conn:
        actions = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT action FROM platform_audit_events WHERE target_organization=:o AND action LIKE 'entitlement.%' ORDER BY created_at"
                ),
                {"o": org["id"]},
            )
        ]
    assert actions == ["entitlement.override_added", "entitlement.override_expired"]


def test_subscription_endpoints_are_permission_gated(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Gated Firm")
    org = _org(admin, "Gated Firm")
    sid = _sub(admin, org["id"])["id"]
    support = new_platform_admin(client, owner_engine, "platform_support")
    assert support.get(f"/subscriptions/{sid}").status_code == 200  # may read
    assert (
        support.post(f"/subscriptions/{sid}/extend", {"days": 5, "reason": "Trying without permission"}).status_code
        == 403
    )
    assert (
        support.post(
            f"/subscriptions/{sid}/change-plan",
            {"plan_version_id": str(uuid.uuid4()), "reason": "Trying without permission"},
        ).status_code
        == 403
    )
    assert new_platform_admin(client, owner_engine, "platform_auditor").get("/subscriptions").status_code == 403
    assert new_platform_admin(client, owner_engine, "platform_billing").get("/plans").status_code == 200
    assert (
        new_platform_admin(client, owner_engine, "platform_billing")
        .post("/plans", _plan_body("nope_billing"))
        .status_code
        == 403
    )
