"""Dashboard (real data only), usage accuracy and isolation, health, audit viewer, activity feed, settings."""

from __future__ import annotations

import uuid

import pytest
from conftest import PASSWORD, make_company, new_owner, new_platform_admin, signup_and_verify, uniq
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _org(admin, name):  # type: ignore[no-untyped-def]
    return next(o for o in admin.get("/organizations", q=name).json()["items"] if o["display_name"] == name)


def test_dashboard_numbers_match_the_database_and_unmeasured_things_say_so(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    firm = new_owner(client, mailer, org_name="Dash Firm")
    new_owner(client, mailer, org_name="Dash Solo", org_type="independent_accountant")
    make_company(firm, "DASH1")
    d = admin.get("/dashboard").json()
    with owner_engine.connect() as conn:
        orgs = conn.execute(text("SELECT count(*) FROM organizations")).scalar()
        by_type = {r[0]: r[1] for r in conn.execute(text("SELECT type, count(*) FROM organizations GROUP BY type"))}
        companies = conn.execute(text("SELECT count(*) FROM companies")).scalar()
        users = conn.execute(text("SELECT count(*) FROM users")).scalar()
        subs = {r[0]: r[1] for r in conn.execute(text("SELECT status, count(*) FROM subscriptions GROUP BY status"))}
    assert d["organizations"]["total"] == orgs and d["organization_types"] == by_type
    assert d["companies"]["total"] == companies and d["users"]["total"] == users
    for status in ("trialing", "active", "past_due", "suspended", "cancelled", "expired"):
        assert d["subscriptions"]["breakdown"][status] == subs.get(status, 0)
    # nothing is measured for these yet: marked unavailable with a reason, never a number
    for key in ("documents", "storage", "ai_usage"):
        assert d[key]["available"] is False and d[key]["note"] and "value" not in d[key]
    assert d["usage_trend"]["available"] is False
    assert d["environment"] == "ci" and len(d["organization_growth"]) == 12
    assert sum(p["count"] for p in d["organization_growth"]) == orgs
    assert d["recent_registrations"][0]["display_name"] in {"Dash Solo", "Dash Firm"}
    assert d["revenue"]["available"] is False or all(
        set(t) == {"currency", "amount"} for t in d["revenue"]["collected_last_30_days"]
    )


def test_dashboard_sections_respect_permissions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    new_owner(client, mailer, org_name="Dash Perm Firm")
    support = new_platform_admin(client, owner_engine, "platform_support").get("/dashboard").json()
    assert support["revenue"] is None and support["recent_admin_activity"] is None  # no billing / audit permission
    assert support["recent_subscription_changes"] is not None
    billing = new_platform_admin(client, owner_engine, "platform_billing").get("/dashboard").json()
    assert billing["revenue"] is not None and billing["recent_admin_activity"] is None
    assert new_platform_admin(client, owner_engine, "platform_auditor").get("/dashboard").status_code == 403


def test_dashboard_revenue_only_reflects_recorded_payments(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Revenue Firm")
    org = _org(admin, "Revenue Firm")
    before = {t["currency"]: t["amount"] for t in admin.get("/dashboard").json()["revenue"]["collected_last_30_days"]}
    inv = admin.post(
        "/billing/invoices", {"organization_id": org["id"], "amount_total": "75.500", "currency": "USD", "issue": True}
    ).json()
    admin.post(f"/billing/invoices/{inv['id']}/payments", {"amount": "75.500", "method": "bank_transfer"})
    after = {t["currency"]: t["amount"] for t in admin.get("/dashboard").json()["revenue"]["collected_last_30_days"]}
    assert float(after["USD"]) - float(before.get("USD", "0")) == pytest.approx(75.5)


def test_dashboard_alerts_come_from_real_conditions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Alert Firm")
    org = _org(admin, "Alert Firm")
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO outbox_events (id, event_type, state, attempts) VALUES (gen_random_uuid(), 'test.failed', 'failed', 5)"
            )
        )
        conn.execute(text("UPDATE subscriptions SET status = 'past_due' WHERE organization_id = :o"), {"o": org["id"]})
    try:
        d = admin.get("/dashboard").json()
        kinds = {a["kind"]: a for a in d["alerts"]}
        assert kinds["failed_jobs"]["count"] >= 1 and kinds["failed_jobs"]["severity"] == "critical"
        assert kinds["past_due"]["count"] >= 1 and d["jobs"]["failed"] >= 1
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM outbox_events WHERE event_type = 'test.failed'"))
            conn.execute(
                text("UPDATE subscriptions SET status = 'trialing' WHERE organization_id = :o"), {"o": org["id"]}
            )


def test_usage_is_accurate_isolated_and_flags_limits(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    tag = uuid.uuid4().hex[:6]
    a = new_owner(client, mailer, org_name=f"Usage{tag} A")
    b = new_owner(client, mailer, org_name=f"Usage{tag} B")
    for code in ("U1", "U2", "U3", "U4"):  # dev plan allows 5 companies: 4 of 5 = 80% -> approaching
        make_company(a, code)
    make_company(b, "UB1")
    rows = {r["organization_name"]: r for r in admin.get("/usage", q=f"Usage{tag}").json()["items"]}
    ua = {x["key"]: x for x in rows[f"Usage{tag} A"]["rows"]}
    ub = {x["key"]: x for x in rows[f"Usage{tag} B"]["rows"]}
    assert ua["limit.companies"]["current"] == 4 and ua["limit.companies"]["limit"] == 5
    assert (
        ua["limit.companies"]["remaining"] == 1
        and ua["limit.companies"]["percent"] == 80.0
        and ua["limit.companies"]["status"] == "approaching"
    )
    assert (
        ub["limit.companies"]["current"] == 1 and ub["limit.companies"]["status"] == "ok"
    )  # no leakage between tenants
    assert ua["limit.users"]["current"] == 1 and ua["limit.devices"]["current"] == 0
    assert rows[f"Usage{tag} A"]["worst"] == "approaching" and rows[f"Usage{tag} B"]["worst"] == "ok"
    assert ua["quota.storage_bytes"]["current"] == 0 and ua["quota.storage_bytes"]["status"] == "ok"  # metered since Phase 6
    assert ua["quota.storage_bytes"]["enforced_now"] is True and ua["limit.companies"]["enforced_now"] is True
    assert ua["quota.ai_units_per_month"]["current"] is None and ua["quota.ai_units_per_month"]["enforced_now"] is False
    assert ua["feature.api_access"]["status"] == "flag" and ua["feature.api_access"]["enabled"] is False
    only = admin.get("/usage", q=f"Usage{tag}", attention="approaching").json()
    assert [i["organization_name"] for i in only["items"]] == [f"Usage{tag} A"] and only["total"] == 1
    # the figures equal what the customer's own entitlement endpoint reports
    own = {e["key"]: e for e in a.get(f"/orgs/{a.org_id}/entitlements").json()}
    assert (
        own["limit.companies"]["current"] == ua["limit.companies"]["current"]
        and own["limit.companies"]["limit"] == ua["limit.companies"]["limit"]
    )
    hist = admin.get(f"/organizations/{_org(admin, f'Usage{tag} A')['id']}/usage-history").json()
    assert hist["points"] == [] and "No metered usage" in hist["note"]
    assert new_platform_admin(client, owner_engine, "platform_auditor").get("/usage").status_code == 403


def test_usage_history_reports_real_records(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="History Firm")
    org = _org(admin, "History Firm")
    with owner_engine.begin() as conn:
        for q in (100, 50):
            conn.execute(
                text(
                    "INSERT INTO usage_records (id, organization_id, metric, quantity) VALUES (gen_random_uuid(), :o, 'quota.ai_units_per_month', :q)"
                ),
                {"o": org["id"], "q": q},
            )
    pts = admin.get(f"/organizations/{org['id']}/usage-history").json()["points"]
    assert len(pts) == 1 and pts[0]["quantity"] == 150 and pts[0]["metric"] == "quota.ai_units_per_month"


def test_health_reports_real_checks_and_does_not_overclaim(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    h = admin.get("/health").json()
    checks = {c["name"]: c for c in h["checks"]}
    assert (
        checks["api"]["status"] == "ok"
        and checks["database"]["status"] == "ok"
        and checks["database"]["latency_ms"] is not None
    )
    assert checks["redis"]["status"] in ("ok", "down")  # a real ping, never assumed
    assert checks["workers"]["status"] == "unknown" and checks["object_storage"]["status"] == "not_configured"
    assert checks["backups"]["status"] == "not_configured"
    assert h["environment"] == "ci" and "Automated test" in h["environment_note"] and h["version"]
    assert "no external monitoring" in h["monitoring_note"].lower()
    assert h["response_counts_since_start"]["2xx"] >= 1 and h["uptime_seconds"] >= 0
    assert h["overall"] in ("ok", "degraded")
    assert new_platform_admin(client, owner_engine, "platform_billing").get("/health").status_code == 403


def test_health_flags_a_stuck_queue(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO outbox_events (id, event_type, state, created_at) VALUES (gen_random_uuid(), 'test.stuck', 'pending', now() - interval '1 hour')"
            )
        )
    try:
        queue = next(c for c in admin.get("/health").json()["checks"] if c["name"] == "outbox_queue")
        assert queue["status"] == "degraded" and "dispatcher" in queue["detail"]
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM outbox_events WHERE event_type = 'test.stuck'"))


def test_audit_viewer_filters_paginates_and_is_append_only(client, mailer, owner_engine, platform_db) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    worker = new_platform_admin(client, owner_engine, "platform_admin")
    new_owner(client, mailer, org_name="Audit Subject Ltd")
    org = _org(owner, "Audit Subject Ltd")
    worker.patch(f"/organizations/{org['id']}", {"display_name": "Audit Subject Renamed"})
    worker.post(f"/organizations/{org['id']}/suspend", {"reason": "Audit trail demonstration", "confirm": org["slug"]})
    worker.post("/admins/invitations", {"email": uniq("x"), "role_key": "platform_readonly"})  # denied: admins.manage
    by_org = owner.get("/audit-events", organization_id=org["id"]).json()
    assert [e["action"] for e in by_org["items"]][:2] == ["organization.suspended", "organization.updated"]
    e = by_org["items"][0]
    assert e["actor_id"] == worker.id and e["actor_name"] == "Admin platform_admin" and e["outcome"] == "success"
    assert (
        e["reason"] == "Audit trail demonstration"
        and e["organization_name"] == "Audit Subject Renamed"
        and e["target_type"] == "organization"
    )
    assert owner.get(f"/audit-events/{e['id']}").json()["id"] == e["id"]
    assert owner.get(f"/audit-events/{uuid.uuid4()}").status_code == 404
    denied = owner.get("/audit-events", actor_id=worker.id, outcome="denied").json()
    assert denied["total"] >= 1 and denied["items"][0]["action"] == "authz.denied"
    assert owner.get("/audit-events", action="organization.").json()["total"] >= 2
    assert owner.get("/audit-events", action="organization.sus", organization_id=org["id"]).json()["total"] == 1
    assert owner.get("/audit-events", q="demonstration").json()["total"] >= 1
    assert owner.get("/audit-events", since="2999-01-01T00:00:00Z").json()["total"] == 0
    page = owner.get("/audit-events", actor_id=worker.id, limit=2, offset=1).json()
    assert len(page["items"]) <= 2 and page["total"] >= 3
    assert owner.get("/audit-events", outcome="bogus").status_code == 422
    # ordinary role gating
    assert new_platform_admin(client, owner_engine, "platform_support").get("/audit-events").status_code == 403
    assert new_platform_admin(client, owner_engine, "platform_auditor").get("/audit-events").status_code == 200
    # append-only: neither the platform role nor the schema owner can edit or remove records
    for sql in (
        "UPDATE platform_audit_events SET action = 'tampered'",
        "DELETE FROM platform_audit_events",
        "TRUNCATE platform_audit_events",
    ):
        with pytest.raises(DBAPIError), platform_db.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": owner.id})
            conn.execute(text(sql))
    for sql in ("UPDATE platform_audit_events SET action = 'tampered'", "DELETE FROM platform_audit_events"):
        with pytest.raises(DBAPIError), owner_engine.begin() as conn:
            conn.execute(text(sql))


def test_audit_records_never_contain_secrets(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_platform_admin(client, owner_engine)
    email = uniq("secret")
    owner.post("/admins/invitations", {"email": email, "role_key": "platform_readonly"})
    token = mailer.last("platform_invitation", email).context["token"]
    client.post(
        "/api/v1/platform/auth/invitations/accept",
        json={"token": token, "display_name": "Secret Holder", "password": PASSWORD},
    )
    with owner_engine.connect() as conn:
        blob = " ".join(
            str(r) for r in conn.execute(text("SELECT summary, reason, target_id FROM platform_audit_events"))
        )
    assert PASSWORD not in blob and token not in blob and "argon2" not in blob


def test_activity_feed_merges_real_sources(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    new_owner(client, mailer, org_name="Feed Firm")
    org = _org(admin, "Feed Firm")
    admin.post(f"/organizations/{org['id']}/suspend", {"reason": "Feed demonstration only", "confirm": org["slug"]})
    feed = admin.get("/activity", limit=100).json()
    kinds = {i["kind"] for i in feed}
    assert {"registration", "subscription", "admin"} <= kinds
    assert any(i["title"].startswith("Feed Firm registered") for i in feed)
    assert any("organization.suspended" in i["title"] for i in feed)
    times = [i["at"] for i in feed]
    assert times == sorted(times, reverse=True)
    assert new_platform_admin(client, owner_engine, "platform_support").get("/activity").status_code == 403


def test_settings_are_validated_audited_and_used(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine)
    reason = "Adjusting the platform defaults"
    keys = {s["key"]: s for s in admin.get("/settings").json()}
    assert set(keys) == {"default_plan_key", "trial_days", "support.grant_max_hours", "billing.invoice_prefix"}
    old_trial = keys["trial_days"]["value"]
    try:
        for key, value in (
            ("trial_days", -1),
            ("trial_days", 999),
            ("trial_days", "abc"),
            ("trial_days", True),
            ("billing.invoice_prefix", "lower"),
            ("billing.invoice_prefix", "TOOLONGPREFIX"),
            ("default_plan_key", "no_such_plan"),
            ("support.grant_max_hours", 0),
        ):
            assert admin.put(f"/settings/{key}", {"value": value, "reason": reason}).status_code == 422, (key, value)
        assert admin.put("/settings/unknown", {"value": 1, "reason": reason}).status_code == 404
        assert admin.put("/settings/trial_days", {"value": 7, "reason": "short"}).status_code == 422
        assert admin.put("/settings/trial_days", {"value": 7, "reason": reason}).json()["value"] == 7
        # new sign-ups pick the new trial length up with no deploy
        signup_and_verify(client, mailer, email=uniq("trial"), org_name="Trial Seven Ltd")
        sub = admin.get(f"/organizations/{_org(admin, 'Trial Seven Ltd')['id']}").json()["subscription"]
        days = (_parse(sub["trial_ends_at"]) - _parse(sub["created_at"])).days
        assert days in (6, 7)
        # invoice numbers use the prefix setting
        admin.put("/settings/billing.invoice_prefix", {"value": "ART", "reason": reason})
        org = _org(admin, "Trial Seven Ltd")
        inv = admin.post(
            "/billing/invoices", {"organization_id": org["id"], "amount_total": "1", "currency": "OMR"}
        ).json()
        assert inv["number"].startswith("ART-")
    finally:
        admin.put("/settings/trial_days", {"value": old_trial, "reason": "Restoring the test default"})
        admin.put("/settings/billing.invoice_prefix", {"value": "INV", "reason": "Restoring the test default"})
    assert new_platform_admin(client, owner_engine, "platform_admin").get("/settings").status_code == 200
    assert (
        new_platform_admin(client, owner_engine, "platform_admin")
        .put("/settings/trial_days", {"value": 3, "reason": reason})
        .status_code
        == 403
    )
    assert new_platform_admin(client, owner_engine, "platform_support").get("/settings").status_code == 403
    with owner_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM platform_audit_events WHERE action='settings.updated' AND target_id='trial_days'"
            )
        ).scalar()
    assert n >= 2


def _parse(value: str):  # type: ignore[no-untyped-def]
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))
