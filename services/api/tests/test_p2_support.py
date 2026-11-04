"""Controlled support access: request -> customer approval -> scoped, time-limited, audited use -> revoke (TS-ISO-07)."""

from __future__ import annotations

import uuid

from conftest import invite_and_accept, new_owner, new_platform_admin
from sqlalchemy import text

REASON = "Customer reported missing members after an import; need to verify roles."


def _org_id(admin, name):  # type: ignore[no-untyped-def]
    return next(o for o in admin.get("/organizations", q=name).json()["items"] if o["display_name"] == name)["id"]


def _request(support, org_id, **over):  # type: ignore[no-untyped-def]
    return support.post("/support/grants", {"organization_id": org_id, "reason": REASON, "requested_hours": 8, **over})


def _setup(client, mailer, owner_engine, name):  # type: ignore[no-untyped-def]
    firm = new_owner(client, mailer, org_name=name)
    invite_and_accept(firm, mailer, role="accountant")
    support = new_platform_admin(client, owner_engine, "platform_support")
    return firm, support, _org_id(support, name)


def _set_ctx(conn, admin_id):  # type: ignore[no-untyped-def]
    conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(admin_id)})


def test_full_lifecycle_with_customer_approval_and_double_audit(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "Support Lifecycle Firm")
    req = _request(support, org_id)
    assert req.status_code == 201, req.text
    g = req.json()
    assert g["status"] == "requested" and g["approved_level"] is None and g["expires_at"] is None
    assert "Support Lifecycle Firm" in mailer.last("support_access_request", firm.email).context["organization"]
    # until the customer approves, nothing is readable - not even by the requester
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 403
    # the customer sees the request, with the reason and who asked
    seen = firm.get(f"/orgs/{org_id}/support-grants").json()
    assert (
        seen[0]["id"] == g["id"]
        and seen[0]["reason"] == REASON
        and seen[0]["requester_name"] == "Admin platform_support"
    )
    ok = firm.post(
        f"/orgs/{org_id}/support-grants/{g['id']}/approve",
        {"access_level": "metadata", "hours": 4, "note": "ok for 4h"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "active" and ok.json()["approved_level"] == "metadata"
    members = support.get(f"/support/grants/{g['id']}/members")
    assert members.status_code == 200 and len(members.json()) == 2
    assert {m["role"] for m in members.json()} == {"firm_owner", "accountant"}
    assert set(members.json()[0]) == {"user_id", "email", "display_name", "user_status", "role", "membership_status"}
    assert support.get(f"/support/grants/{g['id']}/audit-events").status_code == 200
    # BOTH logs record the use: Artify's platform audit and the customer's own audit log
    with owner_engine.connect() as conn:
        pa = [
            r[0]
            for r in conn.execute(
                text("SELECT action FROM platform_audit_events WHERE target_id = :g ORDER BY created_at"),
                {"g": g["id"]},
            )
        ]
    # the pre-approval attempt above is itself on record as a denial
    assert pa == ["support.requested", "support.access_denied", "support.members_viewed", "support.audit_viewed"]
    tenant = [
        e for e in firm.get(f"/orgs/{org_id}/audit-events?limit=100").json() if e["action"].startswith("support.")
    ]
    assert {e["action"] for e in tenant} >= {"support.grant_approved", "support.members_viewed", "support.audit_viewed"}
    assert any(e["actor_type"] == "platform_admin" for e in tenant)
    # the customer revokes; access ends at once
    rv = firm.post(f"/orgs/{org_id}/support-grants/{g['id']}/revoke", {"reason": "Issue resolved, thank you"})
    assert rv.status_code == 200 and rv.json()["status"] == "revoked" and rv.json()["revoked_by_type"] == "customer"
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 403
    assert support.get(f"/support/grants/{g['id']}").json()["status"] == "revoked"
    assert firm.post(f"/orgs/{org_id}/support-grants/{g['id']}/revoke", {"reason": "again"}).status_code == 409


def test_grant_scoping_is_enforced_by_row_level_security_in_the_database(
    client, mailer, owner_engine, platform_db
) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "RLS Grant Firm")
    other = new_owner(client, mailer, org_name="RLS Other Firm")
    g = _request(support, org_id).json()

    def visible(table: str) -> int:
        # audit_events: ignore the Phase 1 "unattributed security events" policy (organization_id IS NULL)
        where = " WHERE organization_id IS NOT NULL" if table == "audit_events" else ""
        with platform_db.connect() as conn, conn.begin():
            _set_ctx(conn, support.id)
            return conn.execute(text(f"SELECT count(*) FROM {table}{where}")).scalar_one()

    assert (
        visible("organization_memberships") == 0 and visible("users") == 0 and visible("audit_events") == 0
    )  # no grant yet
    firm.post(f"/orgs/{org_id}/support-grants/{g['id']}/approve", {"hours": 2})
    with platform_db.connect() as conn, conn.begin():
        _set_ctx(conn, support.id)
        orgs = {str(r[0]) for r in conn.execute(text("SELECT DISTINCT organization_id FROM organization_memberships"))}
        emails = {r[0] for r in conn.execute(text("SELECT email::text FROM users"))}
    assert orgs == {org_id}  # exactly the granted organization; the other firm stays invisible
    assert firm.email in emails and other.email not in emails
    # a DIFFERENT platform administrator has no grant, hence sees nothing
    stranger = new_platform_admin(client, owner_engine, "platform_support")
    with platform_db.connect() as conn, conn.begin():
        _set_ctx(conn, stranger.id)
        assert conn.execute(text("SELECT count(*) FROM organization_memberships")).scalar() == 0
    # expiry is evaluated by the database, with no job needed
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE support_access_grants SET expires_at = now() - interval '1 second' WHERE id = :g"),
            {"g": g["id"]},
        )
    assert visible("organization_memberships") == 0 and visible("users") == 0
    assert support.get(f"/support/grants/{g['id']}").json()["status"] == "expired"
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 403
    # even with a grant, tenant business tables stay closed to the platform role
    import pytest
    from sqlalchemy.exc import DBAPIError

    for table in ("companies", "company_memberships", "invitations", "devices"):
        with pytest.raises(DBAPIError) as exc, platform_db.connect() as conn, conn.begin():
            _set_ctx(conn, support.id)
            conn.execute(text(f"SELECT count(*) FROM {table}"))
        assert "permission denied" in str(exc.value).lower(), table


def test_only_the_grant_holder_can_use_it_and_misuse_is_audited(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "Holder Only Firm")
    g = _request(support, org_id).json()
    firm.post(f"/orgs/{org_id}/support-grants/{g['id']}/approve", {"hours": 2})
    colleague = new_platform_admin(client, owner_engine, "platform_support")
    r = colleague.get(f"/support/grants/{g['id']}/members")
    assert r.status_code == 403 and r.json()["reason"] == "not_grant_holder"
    with owner_engine.connect() as conn:
        denied = conn.execute(
            text(
                "SELECT count(*) FROM platform_audit_events WHERE action='support.access_denied' AND actor_id=:a AND outcome='denied'"
            ),
            {"a": colleague.id},
        ).scalar()
    assert denied == 1
    # roles without view_data cannot use even their own grant
    owner_admin = new_platform_admin(client, owner_engine, "platform_admin")
    g2 = _request(owner_admin, org_id)
    assert g2.status_code == 201
    firm.post(f"/orgs/{org_id}/support-grants/{g2.json()['id']}/approve", {"hours": 2})
    assert owner_admin.get(f"/support/grants/{g2.json()['id']}/members").status_code == 403


def test_customer_authorization_rules(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "Authz Support Firm")
    stranger_firm = new_owner(client, mailer, org_name="Stranger Firm")
    g = _request(support, org_id).json()
    path = f"/orgs/{org_id}/support-grants/{g['id']}"
    accountant = invite_and_accept(firm, mailer, role="accountant")
    assert accountant.get(f"/orgs/{org_id}/support-grants").status_code == 403  # only the firm owner
    assert accountant.post(f"{path}/approve", {"hours": 1}).status_code == 403
    # another customer cannot see or decide this request (404, no existence leak)
    assert stranger_firm.get(f"/orgs/{org_id}/support-grants", org=org_id).status_code == 404
    assert stranger_firm.post(f"{path}/approve", {"hours": 1}, org=org_id).status_code == 404
    assert (
        stranger_firm.post(f"/orgs/{stranger_firm.org_id}/support-grants/{g['id']}/approve", {"hours": 1}).status_code
        == 404
    )
    # cannot approve more than was requested, nor sensitive access that was not requested
    assert firm.post(f"{path}/approve", {"hours": 9}).status_code == 422
    assert (
        firm.post(f"{path}/approve", {"hours": 2, "access_level": "read", "confirm_sensitive": True}).status_code == 422
    )
    assert firm.post(f"{path}/approve", {"hours": 0}).status_code == 422
    assert firm.post(f"{path}/deny", {"note": "not needed"}).json()["status"] == "denied"
    assert firm.post(f"{path}/approve", {"hours": 2}).status_code == 409  # decided requests are final
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 403
    # a stale request (> 14 days) can no longer be approved
    old = _request(support, org_id).json()
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE support_access_grants SET created_at = now() - interval '15 days' WHERE id = :g"),
            {"g": old["id"]},
        )
    assert firm.post(f"/orgs/{org_id}/support-grants/{old['id']}/approve", {"hours": 1}).status_code == 409


def test_sensitive_level_needs_explicit_confirmation_and_unlocks_nothing_extra_yet(
    client, mailer, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "Sensitive Level Firm")
    g = _request(support, org_id, access_level="read").json()
    path = f"/orgs/{org_id}/support-grants/{g['id']}/approve"
    assert firm.post(path, {"hours": 2, "access_level": "read"}).status_code == 422  # explicit confirmation required
    ok = firm.post(path, {"hours": 2, "access_level": "read", "confirm_sensitive": True})
    assert ok.status_code == 200 and ok.json()["approved_level"] == "read"
    # no documents or ledgers exist in Phase 2: the same metadata views, nothing more
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 200
    r = support.get(f"/support/grants/{g['id']}")
    assert r.json()["access_level"] == "read"


def test_request_validation_and_duplicates(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    _firm, support, org_id = _setup(client, mailer, owner_engine, "Request Rules Firm")
    assert _request(support, org_id, reason="too short").status_code == 422
    assert _request(support, org_id, requested_hours=0).status_code == 422
    assert _request(support, org_id, requested_hours=100).status_code == 422  # above the 72h platform cap
    assert _request(support, str(uuid.uuid4())).status_code == 404
    first = _request(support, org_id)
    assert first.status_code == 201
    assert _request(support, org_id).status_code == 409  # one open request per administrator and organization
    # withdrawing frees the slot; only the requester (or a role with support.revoke) may withdraw
    other_support = new_platform_admin(client, owner_engine, "platform_support")
    assert (
        other_support.post(
            f"/support/grants/{first.json()['id']}/revoke", {"reason": "Not mine to withdraw"}
        ).status_code
        == 403
    )
    admin = new_platform_admin(client, owner_engine, "platform_admin")
    assert (
        admin.post(
            f"/support/grants/{first.json()['id']}/revoke", {"reason": "Cancelled by a platform administrator"}
        ).status_code
        == 200
    )
    assert _request(support, org_id).status_code == 201
    readonly = new_platform_admin(client, owner_engine, "platform_readonly")
    assert readonly.get("/support/grants").status_code == 200
    assert _request(readonly, org_id).status_code == 403
    listing = support.get("/support/grants", organization_id=org_id, mine=True).json()
    assert listing["total"] == 2 and {g["status"] for g in listing["items"]} == {"revoked", "requested"}
    assert support.get("/support/grants", status="revoked", organization_id=org_id).json()["total"] == 1


def test_a_suspended_requester_loses_view_access_with_the_session(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    firm, support, org_id = _setup(client, mailer, owner_engine, "Requester Gone Firm")
    g = _request(support, org_id).json()
    firm.post(f"/orgs/{org_id}/support-grants/{g['id']}/approve", {"hours": 2})
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 200
    owner = new_platform_admin(client, owner_engine)
    assert (
        owner.post(
            f"/admins/{support.id}/suspend", {"reason": "Offboarded this support agent", "confirm": support.email}
        ).status_code
        == 200
    )
    assert support.get(f"/support/grants/{g['id']}/members").status_code == 401
