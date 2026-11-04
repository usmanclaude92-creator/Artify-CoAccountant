"""Super Admin authorization: realm separation, role matrix, denial auditing (TS-ISO-06, TS-SEC platform)."""

from __future__ import annotations

import pytest
from artify_api.permissions import PERMISSIONS, PLATFORM_ROLE_RANK, PLATFORM_ROLES, SYSTEM_ROLES
from conftest import invite_and_accept, make_company, new_owner, new_platform_admin
from sqlalchemy import text

PLATFORM_PERMS = {p.key for p in PERMISSIONS if p.key.startswith("platform.")}


def test_platform_role_catalog_is_least_privilege() -> None:
    assert set(PLATFORM_ROLES) == set(PLATFORM_ROLE_RANK)
    for role, perms in PLATFORM_ROLES.items():
        assert perms <= PLATFORM_PERMS, role
        assert "platform.self.read" in perms
    owner = PLATFORM_ROLES["platform_owner"]
    assert owner == PLATFORM_PERMS
    # nobody except the owner can manage administrators or settings
    for role, perms in PLATFORM_ROLES.items():
        if role != "platform_owner":
            assert "platform.admins.manage" not in perms and "platform.settings.manage" not in perms, role
    # support has no finance or administration power; finance has no support/data power
    support, billing = PLATFORM_ROLES["platform_support"], PLATFORM_ROLES["platform_billing"]
    assert not {p for p in support if p.startswith("platform.billing.") and p.endswith("manage")}
    assert "platform.billing.manage" not in support and "platform.plans.manage" not in support
    assert not {"platform.support.view_data", "platform.support.request", "platform.organizations.suspend"} & billing
    # read-only really is read-only
    ro = PLATFORM_ROLES["platform_readonly"]
    assert not {p for p in ro if p.endswith((".manage", ".suspend", ".override", ".request", ".revoke", ".view_data"))}
    # tenant roles never hold a platform permission
    for _key, (_scope, _name, perms) in SYSTEM_ROLES.items():
        assert not {p for p in perms if p.startswith("platform.")}


def test_customer_tokens_cannot_reach_super_admin(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Not Platform")
    c1 = make_company(owner, "NP1")
    accountant = invite_and_accept(owner, mailer, role="accountant")
    employee = invite_and_accept(owner, mailer, role="company_employee", path=f"/companies/{c1['id']}/invitations")
    probes = [
        ("get", "/platform/me"),
        ("get", "/platform/dashboard"),
        ("get", "/platform/organizations"),
        ("get", "/platform/subscriptions"),
        ("get", "/platform/admins"),
        ("get", "/platform/audit-events"),
        ("post", "/platform/billing/invoices"),
    ]
    for actor in (owner, accountant, employee):
        for method, path in probes:
            r = getattr(client, method)(f"/api/v1{path}", headers={"Authorization": f"Bearer {actor.access}"})
            assert r.status_code == 401, (method, path, r.status_code)  # wrong audience/secret: not even a 403
    # and no token at all
    for _method, path in probes:
        assert client.get(f"/api/v1{path}").status_code in (401, 405)


def test_platform_token_is_useless_on_customer_routes(client, owner_engine, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Tenant Only")
    admin = new_platform_admin(client, owner_engine)
    for path in (f"/orgs/{owner.org_id}", "/me", f"/orgs/{owner.org_id}/members"):
        r = client.get(f"/api/v1{path}", headers={**admin.headers(), "X-Organization-Id": owner.org_id})
        assert r.status_code == 401, path


def test_unenrolled_admin_is_blocked_everywhere_but_enrollment(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from artify_api.security import passwords
    from conftest import PASSWORD, uniq

    email = uniq("unenrolled")
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_admins (id, email, display_name, role_key, password_hash) VALUES (gen_random_uuid(), :e, 'U', 'platform_owner', :h)"
            ),
            {"e": email, "h": passwords.hash_password(PASSWORD)},
        )
    tok = client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD}).json()["tokens"][
        "access_token"
    ]
    h = {"Authorization": f"Bearer {tok}"}
    for path in ("/dashboard", "/organizations", "/plans", "/admins", "/health", "/settings"):
        r = client.get(f"/api/v1/platform{path}", headers=h)
        assert r.status_code == 403 and r.json()["code"] == "mfa_required", path


ROLE_CASES = [
    # role, method, path, body, expected status family for a role WITHOUT the permission
    (
        "platform_support",
        "post",
        "/billing/invoices",
        {"organization_id": "00000000-0000-0000-0000-000000000000", "amount_total": "1", "currency": "OMR"},
    ),
    ("platform_support", "post", "/plans", {"key": "nope_plan", "name": "Nope", "version": {"entitlements": {}}}),
    (
        "platform_billing",
        "post",
        "/organizations/00000000-0000-0000-0000-000000000000/suspend",
        {"reason": "x" * 12, "confirm": "x"},
    ),
    (
        "platform_billing",
        "post",
        "/support/grants",
        {"organization_id": "00000000-0000-0000-0000-000000000000", "reason": "x" * 25},
    ),
    (
        "platform_readonly",
        "post",
        "/subscriptions/00000000-0000-0000-0000-000000000000/extend",
        {"days": 3, "reason": "x" * 12},
    ),
    ("platform_readonly", "put", "/settings/trial_days", {"value": 10, "reason": "x" * 12}),
    ("platform_auditor", "get", "/organizations", None),
    ("platform_auditor", "get", "/billing/invoices", None),
    ("platform_admin", "post", "/admins/invitations", {"email": "x@example.com", "role_key": "platform_support"}),
    ("platform_admin", "put", "/settings/trial_days", {"value": 10, "reason": "x" * 12}),
    (
        "platform_admin",
        "post",
        "/billing/invoices",
        {"organization_id": "00000000-0000-0000-0000-000000000000", "amount_total": "1", "currency": "OMR"},
    ),
    ("platform_admin", "get", "/support/grants/00000000-0000-0000-0000-000000000000/members", None),
]


@pytest.mark.parametrize(("role", "method", "path", "body"), ROLE_CASES)
def test_role_matrix_denials_are_403_and_audited(client, owner_engine, role, method, path, body) -> None:  # type: ignore[no-untyped-def]
    admin = new_platform_admin(client, owner_engine, role)
    r = getattr(admin, method)(path, body) if body is not None else getattr(admin, method)(path)
    assert r.status_code == 403, (role, path, r.status_code, r.text)
    assert r.json()["code"] == "forbidden"
    with owner_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM platform_audit_events WHERE action = 'authz.denied' AND actor_id = :a AND outcome = 'denied'"
            ),
            {"a": admin.id},
        ).scalar()
    assert n >= 1  # denials are recorded in their own transaction, so they survive the rollback


def test_every_platform_role_can_read_its_own_profile_and_permissions(client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    for role, perms in PLATFORM_ROLES.items():
        admin = new_platform_admin(client, owner_engine, role)
        me = admin.get("/me").json()
        assert me["role"] == role and set(me["permissions"]) == set(perms)


def test_allowed_paths_work_for_the_right_roles(client, owner_engine, mailer) -> None:  # type: ignore[no-untyped-def]
    new_owner(client, mailer, org_name="Visible To Finance")
    assert new_platform_admin(client, owner_engine, "platform_billing").get("/billing/summary").status_code == 200
    assert new_platform_admin(client, owner_engine, "platform_support").get("/support/grants").status_code == 200
    assert new_platform_admin(client, owner_engine, "platform_auditor").get("/audit-events").status_code == 200
    assert new_platform_admin(client, owner_engine, "platform_readonly").get("/organizations").status_code == 200


def test_every_platform_route_declares_a_permission_that_matches_its_verb() -> None:
    """Static guard against permission drift: reads need a read-type permission, mutations a write-type one.
    (Authentication routes and the documented exceptions below are the only ones that differ.)"""
    from artify_api.context import PlatformRequires, Public
    from conftest import iter_api_routes, walk_dependencies

    # POST routes that only READ (search is a GET-like query; none here) or whose finer check is inside the handler
    inner_checked = {
        ("POST", "/api/v1/platform/support/grants/{grant_id}/revoke")
    }  # requester may withdraw; support.revoke for others
    write_suffixes = (".manage", ".suspend", ".override", ".request", ".revoke")
    read_suffixes = (".read", ".view_data")
    checked = 0
    for path, route in iter_api_routes():
        if not path.startswith("/api/v1/platform"):
            continue
        calls = list(walk_dependencies(route.dependant))
        declared = [c for c in calls if isinstance(c, PlatformRequires)]
        public = [c for c in calls if isinstance(c, Public)]
        assert declared or public, f"{path} declares no authorization"
        if public or "/platform/auth/" in path:
            continue  # sign-in, refresh, MFA enrolment, invitation acceptance: authentication endpoints
        perm = declared[0].permission
        assert perm or path.endswith("/auth/logout"), (
            f"{path} is authenticated-only; Super Admin routes must name a permission"
        )
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, path) in inner_checked:
                continue
            suffixes = read_suffixes if method == "GET" else write_suffixes
            assert perm.endswith(suffixes), (
                f"{method} {route.path} needs a {'read' if method == 'GET' else 'write'} permission, has {perm}"
            )
            checked += 1
    assert checked >= 60  # the whole Super Admin surface was inspected, not an empty set
