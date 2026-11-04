"""Endpoints added in the Phase 1 brief gap pass: organization profile update/list, company
member management with role-escalation guards, company_admin role, role/permission catalog."""

from __future__ import annotations

from conftest import invite_and_accept, make_company, new_owner, totp_now


def _enroll_mfa(actor):  # type: ignore[no-untyped-def]
    """Privileged roles (practice_admin) must complete MFA before any organization request."""
    enroll = actor.client.post("/api/v1/auth/mfa/enroll", headers=actor.headers())
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    confirm = actor.client.post("/api/v1/auth/mfa/confirm", json={"code": totp_now(secret)}, headers=actor.headers())
    assert confirm.status_code == 200, confirm.text
    actor.access, actor.mfa_secret = confirm.json()["access_token"], secret
    return actor


def test_owner_updates_organization_profile_and_it_is_audited(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Firm A")
    r = a.patch(f"/orgs/{a.org_id}", {"display_name": "Firm A Renamed", "locale": "ar"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Firm A Renamed" and r.json()["locale"] == "ar"
    events = a.get(f"/orgs/{a.org_id}/audit-events").json()
    assert any(e["action"] == "organization.updated" for e in events)
    # immutable-by-this-endpoint fields are rejected or ignored, never applied
    r = a.patch(f"/orgs/{a.org_id}", {"status": "closed"})
    assert r.status_code == 422
    assert a.get(f"/orgs/{a.org_id}").json()["status"] == "active"


def test_only_owner_can_update_organization_profile(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Firm A")
    admin = _enroll_mfa(invite_and_accept(a, mailer, role="practice_admin"))
    acc = invite_and_accept(a, mailer, role="accountant")
    assert acc.patch(f"/orgs/{a.org_id}", {"display_name": "Hacked"}).status_code == 403
    # practice_admin is deliberately not granted organization.update (see ROLE_AND_PERMISSION_MATRIX)
    assert admin.patch(f"/orgs/{a.org_id}", {"display_name": "Hacked"}).status_code == 403
    assert a.get(f"/orgs/{a.org_id}").json()["display_name"] == "Firm A"


def test_cross_tenant_organization_update_is_404(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Firm A"), new_owner(client, mailer, org_name="Firm B")
    assert b.patch(f"/orgs/{a.org_id}", {"display_name": "x"}, org=a.org_id).status_code == 404
    assert a.get(f"/orgs/{a.org_id}").json()["display_name"] == "Firm A"


def test_list_organizations_returns_only_own_memberships(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Firm A"), new_owner(client, mailer, org_name="Firm B")
    names_a = {o["display_name"] for o in a.get("/orgs").json()}
    names_b = {o["display_name"] for o in b.get("/orgs").json()}
    assert names_a == {"Firm A"} and names_b == {"Firm B"}
    assert client.get("/api/v1/orgs").status_code == 401


def test_company_member_management_and_escalation_guards(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Firm A")
    c1, c2 = make_company(a, "C1"), make_company(a, "C2")
    path = f"/companies/{c1['id']}/invitations"
    owner_c = invite_and_accept(a, mailer, role="company_owner", path=path)
    admin_c = invite_and_accept(a, mailer, role="company_admin", path=path)
    emp = invite_and_accept(a, mailer, role="company_employee", path=path)
    emp_id = emp.get("/me").json()["user_id"]
    admin_id = admin_c.get("/me").json()["user_id"]
    owner_id = owner_c.get("/me").json()["user_id"]
    base = f"/companies/{c1['id']}/members"

    # company_admin manages employees but cannot promote, touch admins/owners, or invite above employee
    assert admin_c.patch(f"{base}/{emp_id}", {"status": "suspended"}).status_code == 200
    assert admin_c.patch(f"{base}/{emp_id}", {"status": "active"}).status_code == 200
    assert admin_c.patch(f"{base}/{emp_id}", {"role": "company_owner"}).status_code == 403
    assert admin_c.patch(f"{base}/{owner_id}", {"status": "removed"}).status_code == 403
    assert admin_c.patch(f"{base}/{admin_id}", {"role": "company_employee"}).status_code == 422  # self
    assert admin_c.post(path, {"email": "esc@example.com", "role": "company_owner"}).status_code == 403
    assert admin_c.post(path, {"email": "ok@example.com", "role": "company_employee"}).status_code == 201

    # employee has no member management
    assert emp.patch(f"{base}/{admin_id}", {"status": "removed"}).status_code == 403

    # company_owner may change roles, but the last owner cannot be removed by company staff
    assert owner_c.patch(f"{base}/{emp_id}", {"role": "company_admin"}).status_code == 200
    assert owner_c.patch(f"{base}/{admin_id}", {"status": "removed"}).status_code == 200
    # a removed member loses access to the company
    assert admin_c.get(f"/companies/{c1['id']}").status_code in (403, 404)

    # nobody reaches another company's members through this route
    assert owner_c.patch(f"/companies/{c2['id']}/members/{emp_id}", {"status": "removed"}).status_code == 404

    # the firm owner (org-level) can always manage
    assert a.patch(f"{base}/{owner_id}", {"role": "company_employee"}).status_code == 200
    events = a.get(f"/orgs/{a.org_id}/audit-events?limit=100").json()
    assert any(e["action"] == "company_member.role_changed" for e in events)


def test_cross_tenant_company_member_update_is_404(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="Firm A"), new_owner(client, mailer, org_name="Firm B")
    a1 = make_company(a, "A1")
    emp = invite_and_accept(a, mailer, role="company_employee", path=f"/companies/{a1['id']}/invitations")
    emp_id = emp.get("/me").json()["user_id"]
    r = b.patch(f"/companies/{a1['id']}/members/{emp_id}", {"status": "removed"}, org=b.org_id)
    assert r.status_code == 404
    r = b.patch(f"/companies/{a1['id']}/members/{emp_id}", {"status": "removed"}, org=a.org_id)
    assert r.status_code == 404
    assert a.get(f"/companies/{a1['id']}/members").json()[0]["status"] == "active"


def test_role_and_permission_catalog_has_no_platform_leakage(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    roles = {r["key"]: r for r in a.get("/roles").json()}
    assert {"firm_owner", "practice_admin", "accountant", "staff"} <= set(roles)
    assert {"company_owner", "company_admin", "company_employee"} <= set(roles)
    assert "manage_everything" not in roles
    perms = a.get("/permissions").json()
    assert perms and all(not p["key"].startswith("platform.") for p in perms)
    assert all(not k.startswith("platform.") for r in roles.values() for k in r["permissions"])
    assert client.get("/api/v1/roles").status_code == 401
    # the accountant is not an administrator
    assert "members.manage" not in roles["accountant"]["permissions"]
    assert "companies.create" not in roles["accountant"]["permissions"]


def test_expired_session_is_rejected_for_access_and_refresh(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    a = new_owner(client, mailer, org_name="Expiry Firm")
    assert a.get("/me").status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE sessions SET expires_at = now() - interval '1 minute'"
                " WHERE user_id = (SELECT id FROM users WHERE email = :e)"
            ),
            {"e": a.email},
        )
    assert a.get("/me").status_code == 401
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": a.refresh})
    assert r.status_code == 401


def test_users_cannot_escalate_their_own_privileges(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Escalation Firm")
    c1 = make_company(a, "E1")
    acc = invite_and_accept(a, mailer, role="accountant")
    acc_id = acc.get("/me").json()["user_id"]
    admin = invite_and_accept(a, mailer, role="practice_admin")
    # without MFA the privileged role is refused outright
    assert admin.get(f"/orgs/{a.org_id}/members").status_code == 403
    _enroll_mfa(admin)
    admin_id = admin.get("/me").json()["user_id"]
    assert admin.get(f"/orgs/{a.org_id}/members").status_code == 200

    # an accountant cannot change roles at all, including their own
    assert acc.patch(f"/orgs/{a.org_id}/members/{acc_id}", {"role": "firm_owner"}).status_code == 403
    assert acc.patch(f"/orgs/{a.org_id}/members/{acc_id}", {"role": "practice_admin"}).status_code == 403
    # a practice admin may manage members, but never promote themselves or anyone to firm_owner
    assert admin.patch(f"/orgs/{a.org_id}/members/{admin_id}", {"role": "firm_owner"}).status_code == 422
    assert admin.patch(f"/orgs/{a.org_id}/members/{acc_id}", {"role": "firm_owner"}).status_code == 403
    # firm_owner cannot be invited, and an accountant cannot invite
    assert a.post(f"/orgs/{a.org_id}/invitations", {"email": "x@example.com", "role": "firm_owner"}).status_code == 422
    assert (
        acc.post(f"/orgs/{a.org_id}/invitations", {"email": "y@example.com", "role": "practice_admin"}).status_code
        == 403
    )
    # an accountant cannot assign themselves to a company
    r = acc.post(f"/companies/{c1['id']}/assignments", {"user_id": acc_id})
    assert r.status_code == 404  # out-of-scope company: indistinguishable from a missing one
    roles = {m["email"]: m["role"] for m in a.get(f"/orgs/{a.org_id}/members").json()}
    assert (
        roles[acc.email] == "accountant" and roles[admin.email] == "practice_admin" and roles[a.email] == "firm_owner"
    )
