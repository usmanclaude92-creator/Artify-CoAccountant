"""Document security, authorization, entitlements and storage accounting (Phase 6)."""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import io
import uuid

import pytest
import samples
from artify_api import scanner
from artify_api.storage import get_storage
from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text
from test_p4_workspace import W, assign, mk
from test_p6_documents import keys_of, stored, up


@pytest.fixture
def firm(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Docs Security Firm")
    c = make_company(owner, "DS")["id"]
    return owner, c, mk(owner, c, "Finance")["id"]


def two_companies(owner):  # type: ignore[no-untyped-def]
    a, b = make_company(owner, "SA")["id"], make_company(owner, "SB")["id"]
    return a, mk(owner, a, "Docs")["id"], b, mk(owner, b, "Docs")["id"]


# ================================================================== isolation
def test_other_organizations_cannot_touch_any_document_endpoint(client, mailer) -> None:  # type: ignore[no-untyped-def]
    a = new_owner(client, mailer, org_name="Org A Docs")
    b = new_owner(client, mailer, org_name="Org B Docs")
    ca, na = make_company(a, "XA")["id"], None
    na = mk(a, ca, "Docs")["id"]
    d = stored(a, ca, "secret.pdf", samples.pdf(), na)
    did = d["id"]
    calls = [
        ("get", f"/companies/{ca}/documents", None),
        ("get", f"/companies/{ca}/documents/counts", None),
        ("get", f"/companies/{ca}/documents/storage", None),
        ("get", f"/companies/{ca}/documents/limits", None),
        ("get", f"/companies/{ca}/documents/{did}", None),
        ("get", f"/companies/{ca}/documents/{did}/content", None),
        ("get", f"/companies/{ca}/documents/{did}/versions", None),
        ("get", f"/companies/{ca}/documents/{did}/events", None),
        ("patch", f"/companies/{ca}/documents/{did}", {"display_name": "hacked"}),
        ("post", f"/companies/{ca}/documents/{did}/move", {"node_id": na}),
        ("post", f"/companies/{ca}/documents/{did}/archive", None),
        ("post", f"/companies/{ca}/documents/{did}/delete", {"reason": "attack"}),
        ("post", f"/companies/{ca}/documents/{did}/purge", {"confirm": "secret.pdf"}),
        ("post", f"/companies/{ca}/documents/{did}/versions/1/restore", {}),
        ("post", f"/companies/{ca}/documents/check-duplicate", {"sha256": hashlib.sha256(b"x").hexdigest()}),
        ("post", f"/companies/{ca}/uploads", {"filename": "x.pdf", "size": 5, "node_id": na}),
        ("get", f"/orgs/{a.org_id}/storage", None),
        ("post", f"/orgs/{a.org_id}/storage/reconcile", {"dry_run": True}),
    ]
    for method, path, body in calls:
        for org in (a.org_id, b.org_id):  # claiming A's organization, or claiming its own
            hdr = {"Idempotency-Key": uuid.uuid4().hex}
            r = (
                b.get(path, org=org)
                if method == "get"
                else b.patch(path, body or {}, org=org)
                if method == "patch"
                else b.post(path, body, org=org, **hdr)
            )
            assert r.status_code in (403, 404) and "secret.pdf" not in r.text, (
                method,
                path,
                org == a.org_id,
                r.status_code,
            )
    assert get_storage().exists(keys_of_one(a, ca, did))  # untouched


def keys_of_one(actor, company, did):  # type: ignore[no-untyped-def]
    from artify_api.db import app_engine

    with app_engine().begin() as c:
        c.execute(
            text("SELECT set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"),
            {"o": actor.org_id},
        )
        return str(
            c.execute(text("SELECT storage_key FROM document_versions WHERE document_id = :d"), {"d": did}).scalar_one()
        )


def test_an_accountant_sees_only_documents_of_assigned_companies_and_ids_cannot_be_swapped(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Scope Docs Firm")
    a, na, b, nb = two_companies(owner)
    doc_a, doc_b = stored(owner, a, "a.pdf", samples.pdf(), na), stored(owner, b, "b.pdf", samples.pdf(), nb)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    assert acc.get(f"/companies/{a}/documents/{doc_a['id']}").status_code == 200
    assert acc.get(f"/companies/{a}/documents").json()["total"] == 1
    for path in (
        f"/companies/{b}/documents",
        f"/companies/{b}/documents/{doc_b['id']}",
        f"/companies/{b}/documents/{doc_b['id']}/content",
        f"/companies/{b}/documents/counts",
    ):
        assert acc.get(path).status_code == 404, path
    # manipulated ids: B's document through A's path, random ids, A's folder for B's company
    assert acc.get(f"/companies/{a}/documents/{doc_b['id']}").status_code == 404
    assert acc.get(f"/companies/{a}/documents/{uuid.uuid4()}").status_code == 404
    assert acc.get(f"/companies/{a}/documents/{doc_b['id']}/content").status_code == 404
    assert acc.patch(f"/companies/{a}/documents/{doc_b['id']}", {"display_name": "x"}).status_code == 404
    assert up(acc, b, "x.pdf", samples.pdf(), nb)[1].status_code == 404
    assert up(acc, a, "x.pdf", samples.pdf(), nb)[1].status_code == 404  # B's folder id inside A's company
    # removing the assignment removes access on the very next request
    assert owner.delete(f"/companies/{a}/assignments/{uid(acc)}").status_code == 204
    assert acc.get(f"/companies/{a}/documents/{doc_a['id']}").status_code == 404
    assert acc.get(f"/companies/{a}/documents/{doc_a['id']}/content").status_code == 404


def test_folder_permissions_apply_to_documents_and_change_immediately(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Folder Perm Docs")
    c = make_company(owner, "FP")["id"]
    open_n, secret = mk(owner, c, "Open")["id"], mk(owner, c, "Payroll")["id"]
    d_open, d_secret = (
        stored(owner, c, "open.pdf", samples.pdf(), open_n),
        stored(owner, c, "pay.pdf", samples.pdf(), secret),
    )
    acc = make_member(owner, mailer, "accountant")
    assign(owner, c, acc)
    assert acc.get(f"/companies/{c}/documents").json()["total"] == 2
    grant = owner.post(
        W(c, "/permissions"),
        {"node_id": secret, "subject_type": "user", "user_id": uid(acc), "permission": "view", "effect": "deny"},
    )
    assert grant.status_code == 201
    listing = acc.get(f"/companies/{c}/documents").json()
    assert [x["id"] for x in listing["items"]] == [d_open["id"]] and "pay.pdf" not in str(listing)
    assert "Payroll" not in str(acc.get(f"/companies/{c}/documents/counts").json())
    for path in (
        f"/companies/{c}/documents/{d_secret['id']}",
        f"/companies/{c}/documents/{d_secret['id']}/content",
        f"/companies/{c}/documents/{d_secret['id']}/versions",
        f"/companies/{c}/documents/{d_secret['id']}/events",
    ):
        assert acc.get(path).status_code == 404, path
    assert acc.get(f"/companies/{c}/documents", params={"node_id": secret}).status_code == 404
    assert acc.get(f"/companies/{c}/documents", params={"q": "pay"}).json()["total"] == 0
    assert up(acc, c, "n.pdf", samples.pdf(), secret)[1].status_code == 404
    gid = next(g["id"] for g in owner.get(W(c, "/permissions")).json()["items"] if g["permission"] == "view")
    assert owner.delete(W(c, f"/permissions/{gid}")).status_code == 200
    assert acc.get(f"/companies/{c}/documents/{d_secret['id']}").status_code == 200  # revoked: effective at once
    # restricted mode: nothing without an explicit allow
    owner.patch(W(c, "/settings"), {"access_mode": "restricted"})
    assert acc.get(f"/companies/{c}/documents").json()["total"] == 0
    owner.post(
        W(c, "/permissions"),
        {"node_id": open_n, "subject_type": "user", "user_id": uid(acc), "permission": "view", "effect": "allow"},
    )
    assert acc.get(f"/companies/{c}/documents").json()["total"] == 1


def test_who_may_upload_change_remove_and_purge(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Roles Docs Firm")
    c = make_company(owner, "RD")["id"]
    n = mk(owner, c, "Docs")["id"]
    doc = stored(owner, c, "base.pdf", samples.pdf(), n)
    members = {
        r: make_member(owner, mailer, r)
        for r in ("accountant", "assistant_accountant", "document_operator", "read_only", "staff")
    }
    for m in members.values():
        assign(owner, c, m)
    for role, m in members.items():
        assert m.get(f"/companies/{c}/documents/{doc['id']}/content").status_code == 200, (
            role
        )  # everyone assigned can read
    for role in ("accountant", "assistant_accountant", "document_operator"):  # contributors
        m = members[role]
        d = stored(m, c, f"{role}.pdf", samples.pdf(), n)
        assert m.patch(f"/companies/{c}/documents/{d['id']}", {"display_name": f"{role} renamed"}).status_code == 200
        assert m.post(f"/companies/{c}/documents/{d['id']}/archive").status_code == 200
        assert (
            m.post(f"/companies/{c}/documents/{d['id']}/delete", {"reason": "not allowed"}).status_code == 403
        )  # removing needs more
        assert m.post(f"/companies/{c}/documents/{d['id']}/purge", {"confirm": "x"}).status_code == 403
        assert m.post(f"/companies/{c}/documents/{d['id']}/legal-hold", {"hold": True}).status_code == 403
    for role in ("read_only", "staff"):  # viewers
        m = members[role]
        assert up(m, c, "x.pdf", samples.pdf(), n)[1].status_code == 403, role
        assert m.patch(f"/companies/{c}/documents/{doc['id']}", {"display_name": "x"}).status_code == 403, role
        assert m.post(f"/companies/{c}/documents/{doc['id']}/move", {"node_id": n}).status_code == 403, role
        assert m.post(f"/companies/{c}/documents/{doc['id']}/archive").status_code == 403, role
    mgr = make_member(owner, mailer, "practice_manager")
    removed = mgr.post(f"/companies/{c}/documents/{doc['id']}/delete", {"reason": "duplicate upload"})
    assert removed.status_code == 200  # a manager may remove...
    assert (
        mgr.post(f"/companies/{c}/documents/{doc['id']}/purge", {"confirm": "base.pdf"}).status_code == 403
    )  # ...but not purge


def test_suspended_members_lose_document_access_immediately(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Suspend Docs Firm")
    c = make_company(owner, "SD")["id"]
    n = mk(owner, c, "Docs")["id"]
    doc = stored(owner, c, "s.pdf", samples.pdf(), n)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, c, acc)
    assert acc.get(f"/companies/{c}/documents/{doc['id']}/content").status_code == 200
    owner.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"status": "suspended"})
    assert acc.get(f"/companies/{c}/documents/{doc['id']}").status_code in (403, 404)
    assert acc.get(f"/companies/{c}/documents/{doc['id']}/content").status_code in (403, 404)
    assert up(acc, c, "n.pdf", samples.pdf(), n)[1].status_code in (403, 404)


def test_a_revoked_device_can_no_longer_upload_or_download(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Device Docs Firm")
    c = make_company(owner, "DD")["id"]
    n = mk(owner, c, "Docs")["id"]
    doc = stored(owner, c, "d.pdf", samples.pdf(), n)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, c, acc)
    dev = acc.post("/devices", {"platform": "windows", "name": "PC-1", "app_version": "0.1.0"}).json()
    init, _ = up(acc, c, "p.pdf", samples.pdf(), n, complete=False)
    assert init.status_code == 201
    assert acc.delete(f"/devices/{dev['id']}").status_code == 204
    assert acc.get(f"/companies/{c}/documents/{doc['id']}/content").status_code == 401
    assert up(acc, c, "q.pdf", samples.pdf(), n)[1].status_code == 401
    assert acc.post(f"/companies/{c}/uploads/{init.json()['id']}/complete").status_code == 401


def test_uploads_belong_to_the_person_who_started_them(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Own Uploads Firm")
    c = make_company(owner, "OU")["id"]
    n = mk(owner, c, "Docs")["id"]
    acc = make_member(owner, mailer, "accountant")
    assign(owner, c, acc)
    init, _ = up(acc, c, "mine.pdf", samples.pdf(), n, complete=False)
    sid = init.json()["id"]
    for call in (
        lambda: owner.get(f"/companies/{c}/uploads/{sid}"),
        lambda: owner.post(f"/companies/{c}/uploads/{sid}/complete"),
        lambda: owner.post(f"/companies/{c}/uploads/{sid}/cancel"),
        lambda: owner.put_bytes(f"/companies/{c}/uploads/{sid}/content?offset=0", b"x"),
        lambda: owner.patch(f"/companies/{c}/uploads/{sid}", {"display_name": "x"}),
    ):
        assert call().status_code == 404
    assert owner.get(f"/companies/{c}/uploads").json()["total"] == 0  # the list shows one's own uploads


def test_hostile_file_and_folder_names_cannot_reach_storage_or_display(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    for raw in (
        "../../../etc/passwd.pdf",
        "..\\..\\windows\\system32\\x.pdf",
        "C:\\Users\\a\\b.pdf",
        "/abs/path/c.pdf",
    ):
        d = stored(owner, c, raw, samples.pdf(), node)
        assert "/" not in d["display_name"] and "\\" not in d["display_name"] and ".." not in d["display_name"], raw
        assert "/" not in d["original_filename"] and "\\" not in d["original_filename"]
        (key,) = keys_of(owner_engine_of(), d["id"]) if False else (keys_of_one(owner, c, d["id"]),)
        assert key.startswith("o/") and "passwd" not in key and ".." not in key
    r = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "a\x00b.pdf", "size": 5, "node_id": node},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_filename"
    nasty = stored(owner, c, "x.pdf", samples.pdf(), node, display_name="..\\..\\boot.ini")
    assert "\\" not in nasty["display_name"] and nasty["display_name"].endswith(".pdf")


def owner_engine_of():  # type: ignore[no-untyped-def]
    raise AssertionError


# ================================================================== malware scanning
class FakeScanner:
    name = "fake"

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def scan(self, fh: io.BufferedReader):  # type: ignore[no-untyped-def]
        if self.verdict == "down":
            raise scanner.ScanUnavailable("down")
        return (self.verdict == "clean", None if self.verdict == "clean" else "Test.Signature")


@pytest.fixture
def scanning():  # type: ignore[no-untyped-def]
    yield scanner.set_scanner
    scanner.reset_scanner()


def test_scanning_is_only_claimed_when_it_ran(firm, scanning) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    assert stored(owner, c, "off.pdf", samples.pdf(), node)["scan_status"] == "not_scanned"
    scanning(FakeScanner("clean"))
    assert stored(owner, c, "clean.pdf", samples.pdf(), node)["scan_status"] == "clean"
    scanning(FakeScanner("infected"))
    _, bad = up(owner, c, "bad.pdf", samples.pdf(), node)
    assert (
        bad.status_code == 422
        and bad.json()["code"] == "malware_detected"
        and bad.json()["session"]["status"] == "quarantined"
    )
    assert owner.get(f"/companies/{c}/documents", params={"q": "bad"}).json()["total"] == 0
    scanning(FakeScanner("down"))
    init, down = up(owner, c, "later.pdf", samples.pdf(), node)
    assert down.status_code == 503 and down.json()["code"] == "scan_unavailable"  # fails closed, nothing stored
    assert owner.get(f"/companies/{c}/documents", params={"q": "later"}).json()["total"] == 0
    scanning(FakeScanner("clean"))
    sid = init.json()["id"]
    assert owner.post(f"/companies/{c}/uploads/{sid}/retry").json()["received_bytes"] > 0
    ok = owner.post(f"/companies/{c}/uploads/{sid}/complete")
    assert ok.status_code == 200 and ok.json()["document"]["scan_status"] == "clean"


# ================================================================== entitlements and storage accounting
def test_storage_quota_is_enforced_on_the_backend_and_never_deletes_data(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Quota Docs Firm")
    c = make_company(owner, "Q1")["id"]
    n = mk(owner, c, "Docs")["id"]
    one = samples.pdf("q") * 20
    d1 = stored(owner, c, "one.pdf", one, n)
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", len(one) + 100)
    over = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "two.pdf", "size": len(one), "node_id": n},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert over.status_code == 402 and over.json()["key"] == "quota.storage_bytes"
    assert (
        over.json()["limit"] == len(one) + 100
        and over.json()["current"] == len(one)
        and over.json()["requested"] == len(one)
    )
    # plan shrinks below current usage: existing files stay readable, only growth is refused
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", 10)
    assert owner.get(f"/companies/{c}/documents/{d1['id']}/content").content == one
    assert owner.post(f"/companies/{c}/documents/{d1['id']}/archive").status_code == 200  # still manageable
    usage = owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]
    assert usage["used_bytes"] == len(one) and usage["limit_bytes"] == 10 and usage["available_bytes"] == 0
    # raising the limit again re-enables uploads
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", None, unlimited=True)
    assert up(owner, c, "two.pdf", samples.pdf("q2") * 20, n)[1].status_code == 200


def test_concurrent_uploads_cannot_oversubscribe_the_storage_quota(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Race Docs Firm")
    c = make_company(owner, "RC")["id"]
    n = mk(owner, c, "Docs")["id"]
    data = [samples.pdf(f"race{i}") * 10 for i in range(6)]
    set_plan_limit(
        owner_engine, owner.org_id, "quota.storage_bytes", max(len(x) for x in data) * 2 + 50
    )  # room for two

    def go(i: int) -> int:
        return up(owner, c, f"r{i}.pdf", data[i], n)[1].status_code

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(go, range(6)))
    assert results.count(200) == 2 and results.count(402) == 4, results
    assert (
        owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]["used_bytes"] <= max(len(x) for x in data) * 2 + 50
    )


def test_file_size_and_batch_limits_come_from_the_plan_and_only_lower_the_central_ceiling(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    lim = owner.get(f"/companies/{c}/documents/limits").json()
    assert (
        lim["max_file_size_bytes"] == 200 * 1024 * 1024
        and "pdf" in lim["allowed_extensions"]
        and lim["import_batch_max_files"] == 100
    )
    set_plan_limit(owner_engine, owner.org_id, "limit.max_file_size_bytes", 1000)
    set_plan_limit(owner_engine, owner.org_id, "limit.import_batch_files", 2)
    lim = owner.get(f"/companies/{c}/documents/limits").json()
    assert lim["max_file_size_bytes"] == 1000 and lim["import_batch_max_files"] == 2
    big = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "big.pdf", "size": 1001, "node_id": node},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert big.status_code == 413 and big.json()["code"] == "file_too_large" and big.json()["limit"] == 1000
    assert owner.post(f"/companies/{c}/document-batches", {"file_count": 3}).status_code == 413
    ok = owner.post(f"/companies/{c}/document-batches", {"file_count": 2, "label": "March"})
    assert ok.status_code == 201
    set_plan_limit(owner_engine, owner.org_id, "limit.max_file_size_bytes", 10**12)  # a plan cannot raise the ceiling
    assert owner.get(f"/companies/{c}/documents/limits").json()["max_file_size_bytes"] == 200 * 1024 * 1024


def test_monthly_document_quota_counts_documents_not_versions(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Monthly Docs Firm")
    c = make_company(owner, "MQ")["id"]
    n = mk(owner, c, "Docs")["id"]
    set_plan_limit(owner_engine, owner.org_id, "quota.documents_per_month", 1)
    doc = stored(owner, c, "first.pdf", samples.pdf(), n)
    second = up(owner, c, "second.pdf", samples.pdf(), n)[1]
    assert second.status_code == 402 and second.json()["key"] == "quota.documents_per_month"
    assert (
        up(owner, c, "first.pdf", samples.pdf("v2"), None, document_id=doc["id"])[1].status_code == 200
    )  # a version is not a new document


def test_a_suspended_subscription_blocks_uploads_but_not_reading(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Suspended Docs Firm")
    c = make_company(owner, "SS")["id"]
    n = mk(owner, c, "Docs")["id"]
    doc = stored(owner, c, "keep.pdf", samples.pdf(), n)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE subscriptions SET status = 'suspended' WHERE organization_id = :o"), {"o": owner.org_id}
        )
    blocked = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "x.pdf", "size": 5, "node_id": n},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").status_code == 200


def test_usage_reporting_per_company_type_and_organization(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Usage Docs Firm")
    a, na, b, nb = two_companies(owner)
    pdf1, png1 = samples.pdf("u1") * 5, samples.png()
    stored(owner, a, "a.pdf", pdf1, na)
    d2 = stored(owner, a, "b.png", png1, na)
    stored(owner, b, "c.pdf", samples.pdf("u3"), nb)
    owner.post(f"/companies/{a}/documents/{d2['id']}/archive")
    org = owner.get(f"/orgs/{owner.org_id}/storage").json()
    by = {x["company_id"]: x for x in org["companies"]}
    assert by[a]["stored_bytes"] == len(pdf1) + len(png1) and by[a]["documents"] == 2 and by[b]["documents"] == 1
    assert (
        org["storage"]["used_bytes"] == by[a]["stored_bytes"] + by[b]["stored_bytes"]
        and org["documents_this_month"]["used"] == 3
    )
    assert "Archived" in org["accounting_model"]
    co = owner.get(f"/companies/{a}/documents/storage").json()
    assert co["documents"] == {"active": 1, "archived": 1, "removed": 0} and co["stored_bytes"] == len(pdf1) + len(png1)
    assert {t["extension"] for t in co["by_type"]} == {"pdf", "png"} and co["uploads_last_7_days"]["completed"] == 2
    failed = up(owner, a, "bad.pdf", b"not a pdf", na)[1]
    assert failed.status_code == 422
    assert {x["company_id"]: x for x in owner.get(f"/orgs/{owner.org_id}/storage").json()["companies"]}[a][
        "failed_uploads"
    ] == 1
    dash = owner.get(f"/orgs/{owner.org_id}/dashboard").json()["usage"]
    assert next(u for u in dash if u["key"] == "quota.storage_bytes")["current"] == org["storage"]["used_bytes"]
    sub = owner.get(f"/orgs/{owner.org_id}/subscription").json()["entitlements"]
    assert next(e for e in sub if e["key"] == "quota.storage_bytes")["enforced_now"] is True


def test_storage_usage_is_visible_only_with_the_entitlements_permission(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Usage Perm Firm")
    acc = make_member(owner, mailer, "accountant")
    assert acc.get(f"/orgs/{owner.org_id}/storage").status_code == 403
    assert acc.post(f"/orgs/{owner.org_id}/storage/reconcile", {"dry_run": True}).status_code == 403
    mgr = make_member(owner, mailer, "practice_manager")
    assert mgr.get(f"/orgs/{owner.org_id}/storage").status_code == 200
    assert mgr.post(f"/orgs/{owner.org_id}/storage/reconcile", {"dry_run": True}).status_code == 403  # owner/admin only


# ================================================================== audit
def test_every_important_action_is_recorded(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    other = mk(owner, c, "Other")["id"]
    doc = stored(owner, c, "audit.pdf", samples.pdf("a"), node)
    did = doc["id"]
    owner.get(f"/companies/{c}/documents/{did}/content")
    owner.get(f"/companies/{c}/documents/{did}/content", params={"inline": "true"})
    owner.patch(f"/companies/{c}/documents/{did}", {"display_name": "Renamed"})
    owner.post(f"/companies/{c}/documents/{did}/move", {"node_id": other})
    up(owner, c, "audit.pdf", samples.pdf("b"), None, document_id=did, change_reason="fix")
    owner.post(f"/companies/{c}/documents/{did}/versions/1/restore", {})
    owner.post(f"/companies/{c}/documents/{did}/archive")
    owner.post(f"/companies/{c}/documents/{did}/restore")
    owner.post(f"/companies/{c}/documents/{did}/delete", {"reason": "cleanup"})
    kinds = {
        e["event_type"]
        for e in owner.get(f"/companies/{c}/documents/{did}/events", params={"limit": 100}).json()["items"]
    }
    assert kinds >= {
        "uploaded",
        "downloaded",
        "previewed",
        "renamed",
        "moved",
        "version_created",
        "version_restored",
        "archived",
        "restored",
        "deleted",
    }
    acts = {
        e["action"]
        for e in owner.get(f"/orgs/{owner.org_id}/activity", params={"company_id": c, "limit": 200}).json()["items"]
    }
    assert acts >= {
        "document.uploaded",
        "document.downloaded",
        "document.renamed",
        "document.moved",
        "document.version_created",
        "document.version_restored",
        "document.archived",
        "document.restored",
        "document.deleted",
    }
    assert "audit.pdf" not in str(
        owner.get(f"/orgs/{owner.org_id}/activity", params={"company_id": c, "action": "document.downloaded"}).json()
    )  # no names or content in download events


def test_batches_group_uploads_and_report_their_status(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    batch = owner.post(f"/companies/{c}/document-batches", {"file_count": 3, "label": "Import 1"}).json()
    for name, data in (("one.pdf", samples.pdf()), ("two.png", samples.png()), ("bad.pdf", b"nope")):
        up(owner, c, name, data, node, batch_id=batch["id"])
    sessions = owner.get(f"/companies/{c}/uploads", params={"batch_id": batch["id"]}).json()
    assert sessions["total"] == 3 and sorted(s["status"] for s in sessions["items"]) == [
        "completed",
        "completed",
        "failed",
    ]
    assert (
        owner.get(f"/companies/{c}/uploads", params={"batch_id": batch["id"], "status": "failed"}).json()["total"] == 1
    )
    assert owner.get(f"/companies/{c}/documents").json()["total"] == 2
