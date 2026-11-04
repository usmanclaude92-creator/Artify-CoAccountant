"""API-level tenant and company isolation (TS-ISO-01, 02, 08, 09, 10)."""

from __future__ import annotations

import uuid

from artify_api.context import PlatformRequires, Public, Requires
from conftest import PASSWORD, Actor, invite_and_accept, login, make_company, new_owner
from fastapi.routing import APIRoute
from sqlalchemy import text


def _world(client, mailer):  # type: ignore[no-untyped-def]
    """Org A: owner, companies A1/A2, accountant assigned to A1 only, employee of A1.
    Org B: owner with a company B1."""
    a, b = new_owner(client, mailer, org_name="Firm A"), new_owner(client, mailer, org_name="Firm B")
    a1, a2, b1 = make_company(a, "A1"), make_company(a, "A2"), make_company(b, "B1")
    acc = invite_and_accept(a, mailer, role="accountant")
    acc_id = acc.get("/me").json()["user_id"]
    r = a.post(f"/companies/{a1['id']}/assignments", {"user_id": acc_id, "assignment_type": "primary"})
    assert r.status_code == 201, r.text
    emp = invite_and_accept(a, mailer, role="company_employee", path=f"/companies/{a1['id']}/invitations")
    return a, b, acc, emp, a1, a2, b1


def test_cross_tenant_access_is_denied_with_404_and_audited(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-01"""
    a, b, _acc, _emp, a1, _a2, _b1 = _world(client, mailer)
    probes = [
        f"/orgs/{a.org_id}",
        f"/orgs/{a.org_id}/companies",
        f"/orgs/{a.org_id}/members",
        f"/orgs/{a.org_id}/audit-events",
        f"/orgs/{a.org_id}/entitlements",
    ]
    for path in probes:
        r = b.get(path, org=a.org_id)
        assert r.status_code == 404, (path, r.status_code, r.text)
        assert str(a1["name"]) not in r.text
    # company routes: B's token with A's org header and A's company id
    for method, path in (
        ("get", f"/companies/{a1['id']}"),
        ("get", f"/companies/{a1['id']}/members"),
        ("get", f"/companies/{a1['id']}/assignments"),
    ):
        r = getattr(b, method)(path, org=a.org_id)
        assert r.status_code == 404, (path, r.status_code)
    # B claiming its OWN org but A's company id: also 404 (RLS hides it; no existence leak)
    assert b.get(f"/companies/{a1['id']}", org=b.org_id).status_code == 404
    # writes
    assert (
        b.post(
            f"/orgs/{a.org_id}/companies",
            {"name": "x", "code": "X", "country": "OM", "base_currency": "OMR"},
            org=a.org_id,
        ).status_code
        == 404
    )
    assert (
        b.post(f"/orgs/{a.org_id}/invitations", {"email": "z@example.com", "role": "staff"}, org=a.org_id).status_code
        == 404
    )
    with owner_engine.connect() as conn:
        denied = conn.execute(
            text(
                "SELECT count(*) FROM audit_events WHERE action='authz.denied' AND organization_id IS NULL"
                " AND summary->>'claimed_org' = :o"
            ),
            {"o": a.org_id},
        ).scalar()
    assert denied >= len(probes)


def test_unauthenticated_requests_are_rejected(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    for path in (f"/orgs/{a.org_id}", "/me", f"/orgs/{a.org_id}/companies"):
        assert client.get(f"/api/v1{path}").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_accountant_sees_only_assigned_companies(client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-02"""
    a, _b, acc, _emp, a1, a2, _b1 = _world(client, mailer)
    listed = acc.get(f"/orgs/{a.org_id}/companies").json()
    assert [c["id"] for c in listed] == [a1["id"]]
    assert acc.get(f"/companies/{a1['id']}").status_code == 200
    assert acc.get(f"/companies/{a2['id']}").status_code == 404  # exists, but out of scope
    assert acc.get(f"/companies/{uuid.uuid4()}").status_code == 404  # does not exist: same answer
    assert acc.get(f"/companies/{a2['id']}/members").status_code == 404
    # owner sees both
    assert {c["id"] for c in a.get(f"/orgs/{a.org_id}/companies").json()} == {a1["id"], a2["id"]}
    # least privilege: accountant cannot create companies, invite or read the audit log
    assert (
        acc.post(
            f"/orgs/{a.org_id}/companies", {"name": "n", "code": "N", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 403
    )
    assert acc.post(f"/orgs/{a.org_id}/invitations", {"email": "q@example.com", "role": "staff"}).status_code == 403
    assert acc.get(f"/orgs/{a.org_id}/audit-events").status_code == 403
    # removing the assignment takes effect on the next request
    assert a.delete(f"/companies/{a1['id']}/assignments/{acc.get('/me').json()['user_id']}").status_code == 204
    assert acc.get(f"/companies/{a1['id']}").status_code == 404
    assert acc.get(f"/orgs/{a.org_id}/companies").json() == []


def test_company_employee_is_confined_to_own_company(client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-08"""
    a, b, _acc, emp, a1, a2, b1 = _world(client, mailer)
    assert emp.get(f"/companies/{a1['id']}").status_code == 200
    assert emp.get(f"/companies/{a2['id']}").status_code == 404
    assert [c["id"] for c in emp.get(f"/orgs/{a.org_id}/companies").json()] == [a1["id"]]
    for forbidden in (
        f"/orgs/{a.org_id}/members",
        f"/orgs/{a.org_id}/audit-events",
        f"/orgs/{a.org_id}/invitations",
        f"/orgs/{a.org_id}/entitlements",
    ):
        assert emp.get(forbidden).status_code == 403, forbidden
    assert emp.get(f"/companies/{a1['id']}/members").status_code == 403  # employees cannot list members
    assert (
        emp.post(
            f"/companies/{a1['id']}/invitations", {"email": "x@example.com", "role": "company_employee"}
        ).status_code
        == 403
    )
    assert emp.get(f"/companies/{b1['id']}", org=b.org_id).status_code == 404  # other tenant
    # a company_owner CAN manage their own company's members, but nothing else
    owner_c = invite_and_accept(a, mailer, role="company_owner", path=f"/companies/{a1['id']}/invitations")
    assert owner_c.get(f"/companies/{a1['id']}/members").status_code == 200
    assert (
        owner_c.post(
            f"/companies/{a1['id']}/invitations", {"email": "y@example.com", "role": "company_employee"}
        ).status_code
        == 201
    )
    assert owner_c.get(f"/companies/{a2['id']}/members").status_code == 404


def test_realms_are_separate(client, mailer) -> None:  # type: ignore[no-untyped-def]
    """TS-ISO-09"""
    a = new_owner(client, mailer)
    assert client.get("/api/v1/platform/me", headers=a.headers()).status_code == 401
    assert client.get("/api/v1/platform/organizations", headers=a.headers()).status_code == 401
    # an MFA challenge token is not an access token
    email = a.email
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()
    assert r["status"] == "mfa_required"
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {r['mfa_token']}"}).status_code == 401


def test_every_route_declares_its_authorization() -> None:
    """TS-ISO-10: fail if any route lacks Public(...)/Requires(...)/PlatformRequires(...)."""
    from artify_api.main import create_app

    def walk(dependant):  # type: ignore[no-untyped-def]
        for d in dependant.dependencies:
            yield d.call
            yield from walk(d)

    undeclared = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue  # docs/openapi endpoints are framework-provided and added only in non-production
        calls = list(walk(route.dependant))
        if not any(isinstance(c, (Public, Requires, PlatformRequires)) for c in calls):
            undeclared.append(f"{sorted(route.methods)} {route.path}")
        for c in calls:
            if isinstance(c, Requires) and c.permission is not None:
                from artify_api.permissions import PERMISSION_KEYS

                assert c.permission in PERMISSION_KEYS, c.permission
    assert not undeclared, undeclared


def test_membership_status_changes_take_effect_immediately(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a, _b, acc, _emp, a1, _a2, _b1 = _world(client, mailer)
    acc_id = acc.get("/me").json()["user_id"]
    assert a.patch(f"/orgs/{a.org_id}/members/{acc_id}", {"status": "suspended"}).status_code == 200
    assert acc.get(f"/companies/{a1['id']}").status_code == 404
    assert acc.get(f"/orgs/{a.org_id}").status_code == 404
    assert a.patch(f"/orgs/{a.org_id}/members/{acc_id}", {"status": "active"}).status_code == 200
    assert acc.get(f"/companies/{a1['id']}").status_code == 200


def test_last_firm_owner_cannot_be_deactivated(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer)
    me = a.get("/me").json()["user_id"]
    r = a.patch(f"/orgs/{a.org_id}/members/{me}", {"status": "suspended"})
    assert r.status_code == 409


__all__ = ["Actor", "login"]
