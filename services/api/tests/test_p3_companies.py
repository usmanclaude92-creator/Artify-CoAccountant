"""Firm Control Center: company onboarding, profile vs accounting settings, lifecycle, history, configuration ownership."""

from __future__ import annotations

from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text

FULL = {
    "name": "Gamma Trading",
    "legal_name": "Gamma Trading LLC",
    "code": "GAMMA",
    "registration_number": "CR-1234",
    "company_type": "LLC",
    "industry": "Wholesale",
    "country": "OM",
    "city": "Muscat",
    "address_line": "Way 12, Building 4",
    "contact_email": "info@gamma.example.com",
    "contact_phone": "+968 5555 0100",
    "base_currency": "OMR",
    "financial_year_start_month": 4,
    "financial_year_start_day": 1,
    "accounting_basis": "accrual",
    "vat_registered": True,
    "vat_number": "OM1234567890",
    "tax_jurisdiction": "OM",
}


def _create(owner, **over):  # type: ignore[no-untyped-def]
    return owner.post(f"/orgs/{owner.org_id}/companies", {**FULL, **over})


def test_wizard_creates_profile_settings_configuration_and_assignments_atomically(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Wizard Firm")
    acc = make_member(owner, mailer, "accountant")
    r = _create(
        owner, assignments=[{"user_id": uid(acc), "assignment_type": "primary", "responsibility": "Monthly close"}]
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["legal_name"] == "Gamma Trading LLC" and c["name"] == "Gamma Trading"
    assert (
        c["financial_year_start_month"],
        c["accounting_basis"],
        c["vat_registered"],
        c["vat_number"],
        c["tax_jurisdiction"],
    ) == (4, "accrual", True, "OM1234567890", "OM")
    cfg = owner.get(f"/companies/{c['id']}/configuration").json()
    assert cfg["workspace_state"] == "not_configured" and cfg["config_version"] == 1
    assert "Workspace sections and nested folders" in cfg["future_areas"]
    assignments = owner.get(f"/companies/{c['id']}/assignments").json()
    assert [(a["display_name"], a["assignment_type"], a["responsibility"], a["active"]) for a in assignments] == [
        ("Accountant", "primary", "Monthly close", True)
    ]
    assert acc.get(f"/companies/{c['id']}").status_code == 200  # assignment grants access at once
    with (
        owner_engine.connect() as conn
    ):  # Phase 4 tables exist, but a new company gets NO sections, folders or categories
        for table in (
            "workspace_nodes",
            "document_categories",
            "custom_field_definitions",
            "naming_rules",
            "filing_rules",
        ):
            n = conn.execute(text(f"SELECT count(*) FROM {table} WHERE company_id = :c"), {"c": c["id"]}).scalar_one()
            assert n == 0, table


def test_each_company_owns_its_settings_and_organization_settings_never_overwrite_them(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Isolation Of Settings Firm")
    a = _create(owner).json()
    b = make_company(owner, "PLAIN")  # created with defaults only
    assert (b["financial_year_start_month"], b["vat_registered"], b["vat_number"], b["accounting_basis"]) == (
        1,
        False,
        None,
        None,
    )
    assert (
        owner.patch(
            f"/companies/{b['id']}/settings", {"base_currency": "USD", "financial_year_start_month": 7}
        ).status_code
        == 200
    )
    a_after = owner.get(f"/companies/{a['id']}").json()
    assert (a_after["base_currency"], a_after["financial_year_start_month"]) == ("OMR", 4)  # untouched
    # organization-level defaults do not rewrite any company
    assert (
        owner.patch(
            f"/orgs/{owner.org_id}/settings", {"default_currency": "EUR", "timezone": "Asia/Muscat"}
        ).status_code
        == 200
    )
    assert owner.get(f"/companies/{a['id']}").json()["base_currency"] == "OMR"
    assert owner.get(f"/companies/{b['id']}").json()["base_currency"] == "USD"


def test_company_validation(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Validation Firm")
    assert _create(owner, vat_registered=False).status_code == 422  # a VAT number needs registration
    assert _create(owner, financial_year_start_month=2, financial_year_start_day=30).status_code == 422
    assert _create(owner, contact_email="not-an-email").status_code == 422
    assert _create(owner, accounting_basis="barter").status_code == 422
    assert _create(owner, base_currency="ZZZ").status_code == 409
    assert _create(owner, code="has space").status_code == 422
    ok = _create(owner)
    assert ok.status_code == 201
    assert _create(owner).status_code == 409  # duplicate code
    # assigning someone who cannot be assigned (the owner reaches every company already)
    r = _create(owner, code="OTHER", assignments=[{"user_id": uid(owner)}])
    assert r.status_code == 409
    assert owner.get(f"/orgs/{owner.org_id}/companies").json().__len__() == 1  # the failed wizard left nothing behind
    # VAT consistency on settings: switching registration off clears the number
    cid = ok.json()["id"]
    assert owner.patch(f"/companies/{cid}/settings", {"vat_registered": False}).json()["vat_number"] is None
    assert owner.patch(f"/companies/{cid}/settings", {"vat_number": "X1"}).status_code == 422
    assert owner.patch(f"/companies/{cid}/settings", {}).status_code == 422


def test_profile_and_settings_have_separate_permissions(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Permission Split Firm")
    c = _create(owner).json()
    manager = make_member(owner, mailer, "practice_manager")
    admin = make_member(owner, mailer, "practice_admin")
    acc = make_member(owner, mailer, "accountant", company_ids=[c["id"]])
    for actor in (owner, admin, manager):
        assert actor.patch(f"/companies/{c['id']}", {"industry": f"Trade {actor.email[:5]}"}).status_code == 200
        assert actor.patch(f"/companies/{c['id']}/settings", {"accounting_basis": "cash"}).status_code == 200
    # an assigned accountant can read but edit neither profile nor settings
    assert acc.get(f"/companies/{c['id']}").status_code == 200
    assert acc.patch(f"/companies/{c['id']}", {"industry": "Hack"}).status_code == 403
    assert acc.patch(f"/companies/{c['id']}/settings", {"accounting_basis": "accrual"}).status_code == 403
    # `status` is not a profile field any more: it cannot be smuggled in through the edit endpoint
    assert owner.patch(f"/companies/{c['id']}", {"status": "archived"}).json()["status"] == "active"


def test_base_currency_locks_once_configured(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Currency Lock Firm")
    c = _create(owner).json()
    assert owner.patch(f"/companies/{c['id']}/settings", {"base_currency": "USD"}).status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE company_configurations SET workspace_state = 'configured' WHERE company_id = :c"),
            {"c": c["id"]},
        )
    locked = owner.patch(f"/companies/{c['id']}/settings", {"base_currency": "OMR"})
    assert locked.status_code == 409
    assert (
        owner.patch(f"/companies/{c['id']}/settings", {"vat_registered": False}).status_code == 200
    )  # other settings stay editable


def test_lifecycle_activate_deactivate_archive_restore(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Lifecycle Co Firm")
    c = _create(owner).json()
    p = f"/companies/{c['id']}/status"
    assert owner.post(p, {"action": "activate"}).status_code == 409  # already active
    assert owner.post(p, {"action": "restore"}).status_code == 409
    assert owner.post(p, {"action": "deactivate", "reason": "Client paused"}).json()["status"] == "suspended"
    assert owner.post(p, {"action": "deactivate"}).status_code == 409
    assert owner.post(p, {"action": "activate"}).json()["status"] == "active"
    assert owner.post(p, {"action": "archive", "reason": "Client left"}).json()["status"] == "archived"
    # archived: nothing is deleted, but it is frozen until restored
    assert owner.get(f"/companies/{c['id']}").status_code == 200
    assert owner.patch(f"/companies/{c['id']}", {"industry": "x"}).status_code == 409
    assert owner.patch(f"/companies/{c['id']}/settings", {"accounting_basis": "cash"}).status_code == 409
    assert owner.post(p, {"action": "deactivate"}).status_code == 409
    assert owner.post(p, {"action": "restore"}).json()["status"] == "active"
    assert owner.patch(f"/companies/{c['id']}", {"industry": "Back in business"}).status_code == 200
    hist = owner.get(f"/companies/{c['id']}/history").json()
    kinds = [(e["action"], e["summary"].get("action")) for e in hist["items"]]
    assert (
        ("company.status_changed", "restore") in kinds
        and ("company.status_changed", "archive") in kinds
        and ("company.created", None) in kinds
    )
    assert hist["items"][0]["actor_name"] == "Owner" and hist["total"] >= 6


def test_archive_permission_and_restore_respects_the_subscription_limit(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Archive Rights Firm")
    c1 = make_company(owner, "A1")
    manager = make_member(owner, mailer, "practice_manager")
    admin = make_member(owner, mailer, "practice_admin")
    assert (
        manager.post(f"/companies/{c1['id']}/status", {"action": "archive"}).status_code == 403
    )  # managers edit, they do not archive
    assert admin.post(f"/companies/{c1['id']}/status", {"action": "archive"}).status_code == 200
    set_plan_limit(owner_engine, owner.org_id, "limit.companies", 1)
    make_company(owner, "A2")  # takes the single slot the archived company freed
    blocked = owner.post(f"/companies/{c1['id']}/status", {"action": "restore"})
    assert blocked.status_code == 402 and blocked.json()["key"] == "limit.companies"
    assert owner.get(f"/companies/{c1['id']}").json()["status"] == "archived"  # nothing was lost


def test_overview_search_filter_sort_pagination_and_visibility(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Directory Firm")
    ids = {code: make_company(owner, code, name=f"Co {code}")["id"] for code in ("ALPHA", "BRAVO", "CHARLIE")}
    owner.post(f"/companies/{ids['CHARLIE']}/status", {"action": "archive"})
    owner.post(f"/companies/{ids['BRAVO']}/status", {"action": "deactivate"})
    acc = make_member(owner, mailer, "accountant", company_ids=[ids["ALPHA"]])
    base = f"/orgs/{owner.org_id}/companies/overview"
    everything = owner.get(base).json()
    assert everything["total"] == 3 and [i["code"] for i in everything["items"]] == ["ALPHA", "BRAVO", "CHARLIE"]
    assert owner.get(f"{base}?status=open").json()["total"] == 2
    assert owner.get(f"{base}?status=archived").json()["items"][0]["code"] == "CHARLIE"
    assert owner.get(f"{base}?q=brav").json()["total"] == 1
    assert owner.get(f"{base}?q=%25").json()["total"] == 0  # wildcards are literal
    desc = owner.get(f"{base}?sort=name&order=desc&limit=2").json()
    assert [i["code"] for i in desc["items"]] == ["CHARLIE", "BRAVO"] and desc["total"] == 3
    assert owner.get(f"{base}?limit=2&offset=2").json()["items"][0]["code"] == "CHARLIE"
    mine = acc.get(f"{base}").json()
    assert mine["total"] == 1 and mine["items"][0]["code"] == "ALPHA" and mine["items"][0]["assigned_to_me"] is True
    assert mine["items"][0]["accountant_count"] == 1
    assert owner.get(f"{base}?assigned_to_me=true").json()["total"] == 0  # the owner reaches all, assigned to none
    assert owner.get(f"{base}?limit=0").status_code == 422


def test_company_history_and_configuration_are_company_scoped(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="History Scope Firm")
    a, b = make_company(owner, "HA"), make_company(owner, "HB")
    acc = make_member(owner, mailer, "accountant", company_ids=[a["id"]])
    assert acc.get(f"/companies/{a['id']}/history").status_code == 200
    assert acc.get(f"/companies/{a['id']}/configuration").status_code == 200
    for path in (
        f"/companies/{b['id']}/history",
        f"/companies/{b['id']}/configuration",
        f"/companies/{b['id']}/assignments/history",
    ):
        assert acc.get(path).status_code == 404  # an unassigned company does not exist for them
    assert acc.post(f"/companies/{a['id']}/status", {"action": "archive"}).status_code == 403
    # events of one company never appear in another's history
    owner.patch(f"/companies/{b['id']}", {"industry": "Only B"})
    assert all(
        e["summary"].get("fields") != ["industry"] for e in acc.get(f"/companies/{a['id']}/history").json()["items"]
    )


def test_other_organizations_cannot_touch_these_endpoints(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Tenant A"), new_owner(client, mailer, org_name="Tenant B")
    ca = make_company(a, "ONLYA")
    for method, path, body in (
        ("get", f"/companies/{ca['id']}/history", None),
        ("get", f"/companies/{ca['id']}/configuration", None),
        ("patch", f"/companies/{ca['id']}/settings", {"accounting_basis": "cash"}),
        ("post", f"/companies/{ca['id']}/status", {"action": "archive"}),
        ("patch", f"/companies/{ca['id']}", {"industry": "x"}),
    ):
        for org in (a.org_id, b.org_id):  # claiming either organization with B's token
            r = getattr(b, method)(path, body, org=org) if body is not None else getattr(b, method)(path, org=org)
            assert r.status_code == 404, (method, path, org, r.status_code)
    assert b.get(f"/orgs/{a.org_id}/companies/overview", org=a.org_id).status_code == 404
    assert a.get(f"/companies/{ca['id']}").json()["status"] == "active"
