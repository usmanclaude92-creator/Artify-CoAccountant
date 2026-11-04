"""Firm Control Center: invitations, team, accountants, assignments, role rules, ownership transfer."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from conftest import PASSWORD, Actor, login, make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text

ACCEPT = "/api/v1/auth/invitations/accept"


def _invite(owner, role="accountant", email=None, **extra):  # type: ignore[no-untyped-def]
    email = email or f"inv-{role}-{abs(hash(str(extra))) % 10**8}@example.com"
    return email, owner.post(f"/orgs/{owner.org_id}/invitations", {"email": email, "role": role, **extra})


def test_invitation_assigns_companies_on_acceptance_and_reports_email_honestly(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Invite Assign Firm")
    a, b = make_company(owner, "IA"), make_company(owner, "IB")
    email, r = _invite(owner, "assistant_accountant", company_ids=[a["id"]])
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["state"] == "pending" and inv["company_ids"] == [a["id"]]
    assert (
        inv["email_delivery"]["configured"] is False
    )  # tests run without SMTP: the API must not pretend a mail was sent
    assert "nothing is sent" in inv["email_delivery"]["note"]
    token = mailer.last("invitation", email).context["token"]
    assert client.post(ACCEPT, json={"token": token, "display_name": "Ann", "password": PASSWORD}).status_code == 200
    who = Actor(client, email, login(client, email)["tokens"]["access_token"], "", owner.org_id)
    assert who.get(f"/companies/{a['id']}").status_code == 200
    assert who.get(f"/companies/{b['id']}").status_code == 404  # only the invited company
    listing = owner.get(f"/orgs/{owner.org_id}/invitations").json()
    assert next(i for i in listing if i["id"] == inv["id"])["state"] == "accepted"
    assert any(
        e["action"] == "invitation.accepted" and e["summary"]["companies_assigned"] == 1
        for e in owner.get(f"/orgs/{owner.org_id}/activity").json()["items"]
    )


def test_invitation_rules_rank_duplicates_and_company_access(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Invite Rules Firm")
    c = make_company(owner, "IR")
    manager = make_member(owner, mailer, "practice_manager")
    accountant = make_member(owner, mailer, "accountant")
    # nobody invites above their own rank; the owner role is never invitable
    assert (
        manager.post(
            f"/orgs/{owner.org_id}/invitations", {"email": "x1@example.com", "role": "practice_admin"}
        ).status_code
        == 403
    )
    assert (
        manager.post(f"/orgs/{owner.org_id}/invitations", {"email": "x2@example.com", "role": "read_only"}).status_code
        == 201
    )
    assert (
        owner.post(f"/orgs/{owner.org_id}/invitations", {"email": "x3@example.com", "role": "firm_owner"}).status_code
        == 422
    )
    assert (
        accountant.post(
            f"/orgs/{owner.org_id}/invitations", {"email": "x4@example.com", "role": "read_only"}
        ).status_code
        == 403
    )
    # company access only for roles that are assigned to companies (owners/managers already reach everything)
    assert (
        owner.post(
            f"/orgs/{owner.org_id}/invitations",
            {"email": "x5@example.com", "role": "practice_manager", "company_ids": [c["id"]]},
        ).status_code
        == 422
    )
    assert (
        owner.post(
            f"/orgs/{owner.org_id}/invitations",
            {"email": "x6@example.com", "role": "accountant", "company_ids": ["00000000-0000-0000-0000-000000000000"]},
        ).status_code
        == 404
    )
    # duplicates: active member, pending invitation
    assert (
        owner.post(f"/orgs/{owner.org_id}/invitations", {"email": accountant.email, "role": "staff"}).status_code == 409
    )
    assert (
        owner.post(f"/orgs/{owner.org_id}/invitations", {"email": "x2@example.com", "role": "read_only"}).status_code
        == 409
    )
    # an invitation for a role above yours cannot be resent by you
    pending = owner.post(
        f"/orgs/{owner.org_id}/invitations", {"email": "x7@example.com", "role": "practice_admin"}
    ).json()
    assert manager.post(f"/orgs/{owner.org_id}/invitations/{pending['id']}/resend").status_code == 403


def test_resend_cancel_decline_and_expiry(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Invite Lifecycle Firm")
    email, r = _invite(owner, "accountant", f"life1-{uuid.uuid4().hex[:6]}@example.com")
    inv = r.json()
    first = mailer.last("invitation", email).context["token"]
    again = owner.post(f"/orgs/{owner.org_id}/invitations/{inv['id']}/resend")
    assert again.status_code == 200 and again.json()["resent_count"] == 1
    second = mailer.last("invitation", email).context["token"]
    assert second != first
    assert (
        client.post(ACCEPT, json={"token": first, "display_name": "x", "password": PASSWORD}).status_code == 422
    )  # the old link is dead
    # cancel
    assert owner.delete(f"/orgs/{owner.org_id}/invitations/{inv['id']}").status_code == 204
    assert client.post(ACCEPT, json={"token": second, "display_name": "x", "password": PASSWORD}).status_code == 422
    assert (
        owner.post(f"/orgs/{owner.org_id}/invitations/{inv['id']}/resend").status_code == 404
    )  # cancelled invitations cannot be resent
    states = {i["id"]: i["state"] for i in owner.get(f"/orgs/{owner.org_id}/invitations").json()}
    assert states[inv["id"]] == "revoked"
    # decline by the invitee
    e2, r2 = _invite(owner, "staff", f"life2-{uuid.uuid4().hex[:6]}@example.com")
    tok = mailer.last("invitation", e2).context["token"]
    assert client.post("/api/v1/auth/invitations/decline", json={"token": tok}).json()["status"] == "declined"
    assert client.post("/api/v1/auth/invitations/decline", json={"token": tok}).status_code == 422
    assert client.post(ACCEPT, json={"token": tok, "display_name": "x", "password": PASSWORD}).status_code == 422
    assert (
        next(i for i in owner.get(f"/orgs/{owner.org_id}/invitations").json() if i["id"] == r2.json()["id"])["state"]
        == "declined"
    )
    assert any(
        e["action"] == "invitation.declined" for e in owner.get(f"/orgs/{owner.org_id}/activity").json()["items"]
    )
    # expiry
    e3, r3 = _invite(owner, "read_only", f"life3-{uuid.uuid4().hex[:6]}@example.com")
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE invitations SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": r3.json()["id"]},
        )
    t3 = mailer.last("invitation", e3).context["token"]
    assert client.post(ACCEPT, json={"token": t3, "display_name": "x", "password": PASSWORD}).status_code == 422
    assert (
        next(i for i in owner.get(f"/orgs/{owner.org_id}/invitations").json() if i["id"] == r3.json()["id"])["state"]
        == "expired"
    )
    assert owner.get(f"/orgs/{owner.org_id}/invitations?state=pending").json() == []


def test_only_the_invited_person_can_accept_and_never_twice(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Invite Security Firm")
    other = new_owner(client, mailer, org_name="Invite Security Other")
    existing_member = make_member(owner, mailer, "accountant")
    # an existing account must authenticate AS the invited address; someone else's token does not do
    email = other.email
    _, r = _invite(owner, "staff", email)
    assert r.status_code == 201
    token = mailer.last("invitation", email).context["token"]
    assert client.post(ACCEPT, json={"token": token}).status_code == 401  # no session at all
    wrong = client.post(ACCEPT, json={"token": token}, headers={"Authorization": f"Bearer {existing_member.access}"})
    assert wrong.status_code == 401  # signed in as a different user
    right = client.post(ACCEPT, json={"token": token}, headers={"Authorization": f"Bearer {other.access}"})
    assert right.status_code == 200
    assert (
        client.post(ACCEPT, json={"token": token}, headers={"Authorization": f"Bearer {other.access}"}).status_code
        == 422
    )  # single use
    # someone who is already an active member cannot be re-invited through a stale invitation
    assert owner.post(f"/orgs/{owner.org_id}/invitations", {"email": other.email, "role": "staff"}).status_code == 409
    me = other.get("/me").json()
    assert {m["organization_id"] for m in me["memberships"]} == {
        other.org_id,
        owner.org_id,
    }  # a user may belong to several organizations


def test_team_list_filters_profiles_and_last_activity(client, mailer) -> None:  # type: ignore[no-untyped-def]
    tag = uuid.uuid4().hex[:6]
    owner = new_owner(client, mailer, org_name="Team View Firm")
    c = make_company(owner, "TV")
    acc = make_member(owner, mailer, "accountant", email=f"team-acc-{tag}@example.com", company_ids=[c["id"]])
    make_member(owner, mailer, "document_operator", email=f"team-ops-{tag}@example.com")
    base = f"/orgs/{owner.org_id}/team"
    allrows = owner.get(base).json()
    assert allrows["total"] == 3 and {m["role"] for m in allrows["items"]} == {
        "firm_owner",
        "accountant",
        "document_operator",
    }
    row = next(m for m in allrows["items"] if m["email"] == f"team-acc-{tag}@example.com")
    assert row["assigned_company_count"] == 1 and row["status"] == "active" and row["last_activity_at"]
    assert owner.get(f"{base}?role=accountant").json()["total"] == 1
    assert owner.get(f"{base}?q=team-ops-{tag}").json()["items"][0]["role"] == "document_operator"
    assert owner.get(f"{base}?sort=name&order=desc&limit=1").json()["total"] == 3
    # professional profile: separate from the role
    assert (
        owner.patch(
            f"/orgs/{owner.org_id}/members/{uid(acc)}/profile",
            {"job_title": "Senior Accountant", "license_number": "LIC-9"},
        ).status_code
        == 200
    )
    assert (
        acc.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}/profile", {"phone": "+968 1"}).status_code == 200
    )  # your own
    assert (
        acc.patch(f"/orgs/{owner.org_id}/members/{uid(owner)}/profile", {"job_title": "Hack"}).status_code == 403
    )  # someone else's
    shown = next(m for m in owner.get(base).json()["items"] if m["email"] == f"team-acc-{tag}@example.com")
    assert (shown["job_title"], shown["phone"], shown["license_number"], shown["role"]) == (
        "Senior Accountant",
        "+968 1",
        "LIC-9",
        "accountant",
    )
    # a title never grants access
    assert acc.get(f"/orgs/{owner.org_id}/subscription").status_code == 403
    # an accountant sees the team but not the whole picture of assignments counts
    assert all(m["assigned_company_count"] is None for m in acc.get(base).json()["items"])
    for forbidden_viewer in (
        make_member(owner, mailer, "assistant_accountant"),
        make_member(owner, mailer, "read_only"),
    ):
        assert forbidden_viewer.get(base).status_code == 403


def test_accountants_assignments_matrix_and_history(client, mailer) -> None:  # type: ignore[no-untyped-def]
    """The brief's example: A -> acc1; B -> acc1+acc2; C -> acc3."""
    tag = uuid.uuid4().hex[:6]
    owner = new_owner(client, mailer, org_name="Matrix Firm")
    ca, cb, cc = (make_company(owner, k, name=f"Company {k}") for k in ("CA", "CB", "CC"))
    a1, a2, a3 = (make_member(owner, mailer, "accountant", email=f"matrix{i}-{tag}@example.com") for i in (1, 2, 3))
    pairs = [(ca, a1), (cb, a1), (cb, a2), (cc, a3)]
    for comp, who in pairs:
        r = owner.post(
            f"/companies/{comp['id']}/assignments",
            {
                "user_id": uid(who),
                "assignment_type": "primary" if who is a1 else "secondary",
                "responsibility": f"Books of {comp['code']}",
            },
        )
        assert r.status_code == 201, r.text
    listing = owner.get(f"/orgs/{owner.org_id}/assignments?limit=10").json()
    assert listing["total"] == 4 and {(r["company_name"], r["display_name"]) for r in listing["items"]} == {
        ("Company CA", "Accountant"),
        ("Company CB", "Accountant"),
        ("Company CC", "Accountant"),
    }
    by_company = {}
    for r in listing["items"]:
        by_company.setdefault(r["company_name"], []).append(r["email"])
    assert sorted(by_company["Company CB"]) == [
        f"matrix1-{tag}@example.com",
        f"matrix2-{tag}@example.com",
    ] and by_company["Company CC"] == [f"matrix3-{tag}@example.com"]
    assert owner.get(f"/orgs/{owner.org_id}/assignments?company_id={cb['id']}").json()["total"] == 2
    assert owner.get(f"/orgs/{owner.org_id}/assignments?user_id={uid(a1)}").json()["total"] == 2
    people = {p["email"]: p for p in owner.get(f"/orgs/{owner.org_id}/accountants").json()["items"]}
    assert (
        people[f"matrix1-{tag}@example.com"]["active_company_count"] == 2
        and people[f"matrix3-{tag}@example.com"]["active_company_count"] == 1
    )
    assert {a["company_name"] for a in people[f"matrix1-{tag}@example.com"]["assignments"]} == {
        "Company CA",
        "Company CB",
    }
    detail = owner.get(f"/orgs/{owner.org_id}/accountants/{uid(a1)}").json()
    assert detail["role"] == "accountant" and len(detail["assignments"]) == 2
    # access follows the assignment, and only the assignment
    assert a2.get(f"/companies/{cb['id']}").status_code == 200 and a2.get(f"/companies/{ca['id']}").status_code == 404
    # change, end, remove, history
    assert (
        owner.patch(
            f"/companies/{cb['id']}/assignments/{uid(a2)}",
            {"assignment_type": "reviewer", "responsibility": "Quarterly review"},
        ).json()["assignment_type"]
        == "reviewer"
    )
    assert owner.patch(f"/companies/{cb['id']}/assignments/{uid(a2)}", {}).status_code == 422
    assert owner.delete(f"/companies/{cb['id']}/assignments/{uid(a2)}").status_code == 204
    assert owner.delete(f"/companies/{cb['id']}/assignments/{uid(a2)}").status_code == 404
    assert a2.get(f"/companies/{cb['id']}").status_code == 404  # removal ends access at once
    hist = owner.get(f"/companies/{cb['id']}/assignments/history").json()
    assert [e["action"] for e in hist["items"]] == [
        "assignment.removed",
        "assignment.updated",
        "assignment.set",
        "assignment.set",
    ]
    assert hist["items"][0]["actor_name"] == "Owner" and hist["items"][1]["summary"]["assignment_type"] == "reviewer"


def test_assignment_windows_control_access(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Window Firm")
    c = make_company(owner, "WIN")
    acc = make_member(owner, mailer, "accountant")
    today = date.today()
    # not started yet: no access, shown as inactive
    r = owner.post(
        f"/companies/{c['id']}/assignments", {"user_id": uid(acc), "starts_on": str(today + timedelta(days=5))}
    )
    assert r.status_code == 201 and r.json()["active"] is False
    assert acc.get(f"/companies/{c['id']}").status_code == 404
    # open window
    assert (
        owner.patch(
            f"/companies/{c['id']}/assignments/{uid(acc)}", {"starts_on": str(today - timedelta(days=2))}
        ).json()["active"]
        is True
    )
    assert acc.get(f"/companies/{c['id']}").status_code == 200
    # ended yesterday: the row stays as history, access is gone
    ended = owner.patch(f"/companies/{c['id']}/assignments/{uid(acc)}", {"ends_on": str(today - timedelta(days=1))})
    assert ended.json()["active"] is False
    assert acc.get(f"/companies/{c['id']}").status_code == 404
    assert owner.get(f"/orgs/{owner.org_id}/assignments?active=false").json()["total"] == 1
    assert owner.get(f"/orgs/{owner.org_id}/assignments?active=true").json()["total"] == 0
    assert owner.get(f"/orgs/{owner.org_id}/accountants/{uid(acc)}").json()["active_company_count"] == 0
    assert (
        owner.patch(
            f"/companies/{c['id']}/assignments/{uid(acc)}", {"ends_on": str(today - timedelta(days=30))}
        ).status_code
        == 422
    )  # before it started
    assert (
        owner.post(
            f"/companies/{c['id']}/assignments",
            {"user_id": uid(acc), "starts_on": "2026-02-01", "ends_on": "2026-01-01"},
        ).status_code
        == 422
    )


def test_assignment_authorization_and_eligibility(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Assign Authz Firm")
    other = new_owner(client, mailer, org_name="Assign Authz Other")
    c = make_company(owner, "AZ")
    acc = make_member(owner, mailer, "accountant")
    reader = make_member(owner, mailer, "read_only")
    manager = make_member(owner, mailer, "practice_manager")
    target = uid(acc)
    # who may assign
    for who in (acc, reader):
        assert who.post(f"/companies/{c['id']}/assignments", {"user_id": target}).status_code in (403, 404), who.email
    assert manager.post(f"/companies/{c['id']}/assignments", {"user_id": target}).status_code == 201
    assert manager.delete(f"/companies/{c['id']}/assignments/{target}").status_code == 204
    # who can be assigned: assignable roles only (owner/admin/manager reach every company already); other tenants' users never
    for not_assignable in (uid(owner), uid(manager)):
        assert owner.post(f"/companies/{c['id']}/assignments", {"user_id": not_assignable}).status_code == 409
    assert owner.post(f"/companies/{c['id']}/assignments", {"user_id": uid(other)}).status_code == 409
    assert (
        owner.post(f"/companies/{c['id']}/assignments", {"user_id": uid(reader)}).status_code == 201
    )  # read-only users are assignable
    # another tenant cannot use my company id, and I cannot assign into theirs
    assert other.post(f"/companies/{c['id']}/assignments", {"user_id": uid(other)}, org=owner.org_id).status_code == 404
    # archived companies are frozen
    owner.post(f"/companies/{c['id']}/status", {"action": "archive"})
    assert owner.post(f"/companies/{c['id']}/assignments", {"user_id": target}).status_code == 409


def test_membership_changes_remove_access_and_assignments_do_not_leak(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Membership Firm")
    c = make_company(owner, "MS")
    acc = make_member(owner, mailer, "accountant", company_ids=[c["id"]])
    assert acc.get(f"/companies/{c['id']}").status_code == 200
    for state in ("suspended", "removed"):
        assert owner.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"status": state}).status_code == 200
        assert (
            acc.get(f"/companies/{c['id']}").status_code == 404
        )  # an inactive membership gives nothing, assignment row or not
        assert acc.get(f"/orgs/{owner.org_id}/dashboard").status_code == 404
    assert owner.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"status": "active"}).status_code == 200
    assert (
        acc.get(f"/companies/{c['id']}").status_code == 200
    )  # reactivation restores exactly the assignments that existed
    # role changes: rank rule, no self-promotion
    manager = make_member(owner, mailer, "practice_manager")
    admin = make_member(owner, mailer, "practice_admin")
    assert (
        manager.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"role": "staff"}).status_code == 403
    )  # no members.manage
    assert admin.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"role": "practice_manager"}).status_code == 200
    # a promoted user holds a privileged role now: MFA is mandatory before anything works
    assert acc.get(f"/orgs/{owner.org_id}/companies/overview").json()["code"] == "mfa_required"
    from conftest import enroll_actor_mfa

    enroll_actor_mfa(acc)
    assert acc.get(f"/orgs/{owner.org_id}/companies/overview").json()["total"] == 1  # promoted: reaches all companies
    assert admin.patch(f"/orgs/{owner.org_id}/members/{uid(admin)}", {"role": "firm_owner"}).status_code == 422  # self
    assert (
        admin.patch(f"/orgs/{owner.org_id}/members/{uid(manager)}", {"role": "firm_owner"}).status_code == 403
    )  # only an owner makes owners
    assert (
        admin.patch(f"/orgs/{owner.org_id}/members/{uid(owner)}", {"status": "removed"}).status_code == 403
    )  # never above your rank


def test_ownership_transfer_is_the_only_way_to_hand_over(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Transfer Firm")
    admin = make_member(owner, mailer, "practice_admin")
    acc = make_member(owner, mailer, "accountant")
    slug = owner.get(f"/orgs/{owner.org_id}/settings").json()["slug"]
    url = f"/orgs/{owner.org_id}/ownership-transfer"
    # the last owner cannot be removed or demoted in any other way
    assert admin.patch(f"/orgs/{owner.org_id}/members/{uid(owner)}", {"role": "practice_admin"}).status_code == 403
    # only the owner transfers; confirmation and an eligible target are required
    assert admin.post(url, {"new_owner_user_id": uid(admin), "confirm": slug}).status_code == 403
    assert owner.post(url, {"new_owner_user_id": uid(admin), "confirm": "wrong"}).status_code == 422
    assert owner.post(url, {"new_owner_user_id": uid(owner), "confirm": slug}).status_code == 422
    assert (
        owner.post(url, {"new_owner_user_id": "00000000-0000-0000-0000-000000000000", "confirm": slug}).status_code
        == 404
    )
    owner.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"status": "suspended"})
    assert owner.post(url, {"new_owner_user_id": uid(acc), "confirm": slug}).status_code == 404  # must be active
    done = owner.post(url, {"new_owner_user_id": uid(admin), "confirm": slug})
    assert done.status_code == 200 and done.json()["role"] == "firm_owner"
    roles = {m["email"]: m["role"] for m in admin.get(f"/orgs/{owner.org_id}/team").json()["items"]}
    assert roles[admin.email] == "firm_owner" and roles[owner.email] == "practice_admin"
    assert list(roles.values()).count("firm_owner") == 1  # exactly one owner at every moment
    assert (
        owner.post(url, {"new_owner_user_id": uid(admin), "confirm": slug}).status_code == 403
    )  # the former owner can no longer transfer
    assert any(
        e["action"] == "organization.ownership_transferred"
        for e in admin.get(f"/orgs/{owner.org_id}/activity").json()["items"]
    )


def test_new_roles_have_exactly_their_documented_powers(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Roles Firm")
    c1, c2 = make_company(owner, "R1"), make_company(owner, "R2")
    manager = make_member(owner, mailer, "practice_manager")
    assistant = make_member(owner, mailer, "assistant_accountant", company_ids=[c1["id"]])
    operator = make_member(owner, mailer, "document_operator", company_ids=[c1["id"]])
    reader = make_member(owner, mailer, "read_only", company_ids=[c1["id"]])
    org = owner.org_id
    # practice manager: reaches every company, creates and edits, assigns, invites below its rank - but not roles, settings, archive, ownership
    assert manager.get(f"/companies/{c2['id']}").status_code == 200
    assert (
        manager.post(
            f"/orgs/{org}/companies", {"name": "M", "code": "MGR", "country": "OM", "base_currency": "OMR"}
        ).status_code
        == 201
    )
    assert manager.patch(f"/orgs/{org}/settings", {"city": "x"}).status_code == 403
    assert manager.patch(f"/orgs/{org}/members/{uid(reader)}", {"status": "suspended"}).status_code == 403
    assert (
        manager.post(f"/orgs/{org}/ownership-transfer", {"new_owner_user_id": uid(reader), "confirm": "x"}).status_code
        == 403
    )
    assert (
        manager.get(f"/orgs/{org}/activity").status_code == 200
        and manager.get(f"/orgs/{org}/subscription").status_code == 200
    )
    # assistant accountant / document operator / read-only: assigned companies only, read-only, no team view
    for who in (assistant, operator, reader):
        assert who.get(f"/companies/{c1['id']}").status_code == 200
        assert who.get(f"/companies/{c2['id']}").status_code == 404
        assert who.patch(f"/companies/{c1['id']}", {"industry": "x"}).status_code == 403
        assert (
            who.post(
                f"/orgs/{org}/companies", {"name": "N", "code": "NOPE", "country": "OM", "base_currency": "OMR"}
            ).status_code
            == 403
        )
        assert who.get(f"/orgs/{org}/team").status_code == 403
        assert (
            who.get(f"/orgs/{org}/activity").status_code == 403
            and who.get(f"/orgs/{org}/subscription").status_code == 403
        )
        assert who.get(f"/orgs/{org}/dashboard").status_code == 200
    roles = {r["key"]: r for r in owner.get("/roles").json()}
    assert (
        roles["practice_manager"]["reaches_all_companies"]
        and roles["practice_manager"]["mfa_required"]
        and roles["practice_manager"]["rank"] == 60
    )
    assert roles["read_only"]["assignable_to_companies"] and roles["read_only"]["invitable"] is not False
    assert (
        roles["assistant_accountant"]["counts_as_accountant"] and not roles["document_operator"]["counts_as_accountant"]
    )


def test_practice_manager_must_complete_mfa(client, mailer) -> None:  # type: ignore[no-untyped-def]
    from conftest import invite_and_accept

    owner = new_owner(client, mailer, org_name="Manager MFA Firm")
    manager = invite_and_accept(owner, mailer, role="practice_manager")  # no MFA yet
    r = manager.get(f"/orgs/{owner.org_id}/dashboard")
    assert r.status_code == 403 and r.json()["code"] == "mfa_required"


def test_assistant_accountants_count_against_the_accountant_limit(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Seat Limit Firm")
    set_plan_limit(owner_engine, owner.org_id, "limit.accountants", 1)
    make_member(owner, mailer, "assistant_accountant")
    email, r = _invite(owner, "accountant", f"seat2-{uuid.uuid4().hex[:6]}@example.com")
    assert r.status_code == 201
    token = mailer.last("invitation", email).context["token"]
    full = client.post(ACCEPT, json={"token": token, "display_name": "Second", "password": PASSWORD})
    assert full.status_code == 402 and full.json()["key"] == "limit.accountants"
    # roles that are not professional staff do not use accountant seats
    make_member(owner, mailer, "document_operator")
    make_member(owner, mailer, "read_only")
    usage = owner.get(f"/orgs/{owner.org_id}/subscription").json()["entitlements"]
    acc = next(u for u in usage if u["key"] == "limit.accountants")
    assert acc["current"] == 1 and acc["limit"] == 1  # only the one real accountant seat is used


@pytest.mark.parametrize(
    "path",
    [
        "team",
        "members",
        "invitations",
        "accountants",
        "assignments",
        "activity",
        "subscription",
        "settings",
        "dashboard",
    ],
)
def test_other_tenants_get_404_on_every_organization_endpoint(client, mailer, path) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name=f"Iso A {path}"), new_owner(client, mailer, org_name=f"Iso B {path}")
    make_company(a, "ISO")
    r = b.get(f"/orgs/{a.org_id}/{path}", org=a.org_id)
    assert r.status_code == 404 and "Iso A" not in r.text
    assert a.get(f"/orgs/{a.org_id}/{path}").status_code == 200
