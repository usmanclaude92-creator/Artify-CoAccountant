"""Phase 12 synchronisation foundation: the change feed and the preflight check, against PostgreSQL. Synthetic data only."""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from conftest import make_member, new_owner, uid
from test_p6_documents import W, assign, backend, mk, pdf, upload  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import ACC, books, contact, draft, taxes  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import BK, DAY, full_close, quiet, start, txn, validate  # noqa: F401


def SY(cid: str, path: str) -> str:
    return f"/companies/{cid}/sync{path}"


def pull_all(actor, cid: str, **params) -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    items, cursor = [], None
    for _ in range(50):
        q = "&".join(f"{k}={v}" for k, v in {**params, **({"cursor": cursor} if cursor else {})}.items())
        r = actor.get(SY(cid, "/changes" + (f"?{q}" if q else "")))
        assert r.status_code == 200, r.text
        body = r.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not body["has_more"]:
            return items
    raise AssertionError("feed did not end")


def test_the_feed_lists_every_kind_in_order_with_server_revisions_and_continues_from_a_cursor(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    quiet(o, a)
    doc = upload(o, a, pdf("feed"), "feed.pdf", books["node"])
    tid = txn(books)
    closed = full_close(books)
    first = pull_all(o, a)
    kinds = {i["kind"] for i in first}
    assert {"document", "transaction", "closing", "workspace"} <= kinds
    ts = [i["changed_at"] for i in first]
    assert ts == sorted(ts), "oldest first, by the database's own clock"
    by = {(i["kind"], i["id"]): i for i in first}
    assert by[("document", doc["id"])]["revision"] >= 1 and by[("document", doc["id"])]["data"]["sha256"]
    assert by[("transaction", tid)]["status"] == "approved" and by[("transaction", tid)]["data"]["number"]
    assert by[("closing", closed["id"])]["status"] == "closed"
    assert len(by) == len(first), "no item twice"
    # a cursor taken now returns nothing until something changes
    cur = o.get(SY(a, "/changes?limit=500")).json()["next_cursor"]
    assert o.get(SY(a, f"/changes?cursor={cur}")).json()["items"] == []
    assert (
        o.patch(
            f"/companies/{a}/documents/{doc['id']}", {"display_name": "Renamed", "base_row_version": doc["row_version"]}
        ).status_code
        == 200
    )
    after = o.get(SY(a, f"/changes?cursor={cur}")).json()
    assert [(i["kind"], i["id"]) for i in after["items"]] == [("document", doc["id"])]
    assert after["items"][0]["revision"] == doc["row_version"] + 1


def test_paging_with_a_small_limit_visits_everything_once(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    for n in range(5):
        upload(o, a, pdf(f"p{n}" * (n + 3)), f"p{n}.pdf", books["node"])
    page = o.get(SY(a, "/changes?limit=2&kind=document")).json()
    assert len(page["items"]) == 2 and page["has_more"] is True
    everything = pull_all(o, a, limit=2, kind="document")
    assert len(everything) == 5 and len({i["id"] for i in everything}) == 5
    assert o.get(SY(a, "/changes?kind=nonsense")).json()["code"] == "unknown_kind"
    assert o.get(SY(a, "/changes?cursor=not-a-cursor")).json()["code"] == "invalid_cursor"


def test_each_person_sees_only_what_their_permissions_and_folders_allow(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    secret = mk(o, a, "Salaries")["id"]
    shown = upload(o, a, pdf("shown-doc"), "shown.pdf", books["node"])
    hidden = upload(o, a, pdf("hidden-doc-longer" * 3), "hidden.pdf", secret)
    assert o.post(
        W(a, "/permissions"),
        {"node_id": secret, "subject_type": "user", "user_id": uid(prep), "permission": "view", "effect": "deny"},
    ).status_code in (200, 201)
    txn(books)
    seen = {(i["kind"], i["id"]) for i in pull_all(prep, a)}
    assert ("document", shown["id"]) in seen and ("document", hidden["id"]) not in seen
    assert ("document", hidden["id"]) in {(i["kind"], i["id"]) for i in pull_all(o, a)}
    # a document operator has no accounting or bookkeeping rights: only documents (and the workspace) appear
    op = make_member(o, mailer, "document_operator")
    assign(o, a, op)
    got = o.client.get(f"/api/v1{SY(a, '/changes')}", headers=op.headers()).json()
    assert set(got["kinds"]) == {"workspace", "document"} and {i["kind"] for i in got["items"]} <= {
        "workspace",
        "document",
    }


def test_another_company_or_organization_never_appears(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    doc_b = upload(o, b, pdf("only-b"), "b.pdf", books["node_b"])
    assert doc_b["id"] not in {i["id"] for i in pull_all(o, a)}
    stranger = new_owner(client, mailer, org_name="Stranger Firm")
    assert stranger.get(SY(a, "/changes")).status_code == 404
    assert stranger.post(SY(a, "/check"), {"items": [{"kind": "document", "id": doc_b["id"]}]}).status_code == 404
    r = o.post(SY(a, "/check"), {"items": [{"kind": "document", "id": doc_b["id"], "base_revision": 1}]}).json()[
        "items"
    ][0]
    assert r["state"] == "missing" and r["current_revision"] is None, (
        "company A cannot learn that company B's document exists"
    )


def test_preflight_says_current_stale_locked_or_missing_and_why(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    quiet(o, a)
    doc = upload(o, a, pdf("check"), "check.pdf", books["node"])
    pending = txn(books, approve=False)
    approved = txn(books)

    def one(kind: str, ident: str, base: int | None):  # type: ignore[no-untyped-def]
        return o.post(SY(a, "/check"), {"items": [{"kind": kind, "id": ident, "base_revision": base}]}).json()["items"][
            0
        ]

    assert one("document", doc["id"], doc["row_version"])["state"] == "current"
    o.patch(
        f"/companies/{a}/documents/{doc['id']}",
        {"display_name": "Changed elsewhere", "base_row_version": doc["row_version"]},
    )
    st = one("document", doc["id"], doc["row_version"])
    assert st["state"] == "stale" and st["current_revision"] == doc["row_version"] + 1 and "Refresh" in st["reason"]
    assert one("document", doc["id"], None)["state"] == "current", "no base revision: just report the current one"
    cur_txn = acct.get(ACC(a, f"/transactions/{pending}")).json()["row_version"]
    assert one("transaction", pending, cur_txn)["state"] == "current"
    locked = one("transaction", approved, 1)
    assert locked["state"] == "locked" and "correction or reversal" in locked["reason"]
    # a draft dated on a closed day is locked because of the day, not because of its own status
    s = start(books["prep"], a, "2025-03-12")
    validate(books["prep"], a, s["id"])
    books["prep"].post(BK(a, f"/closings/{s['id']}/submit"), {})
    acct.post(BK(a, f"/closings/{s['id']}/close"), {})
    late = draft(o, a, "purchase_invoice", date="2025-03-12", lines=[{"amount": "3"}]).json()["id"]
    r = one("transaction", late, 1)
    assert r["state"] == "locked" and "day is closed" in r["reason"]
    assert one("closing", s["id"], 1)["state"] == "locked"
    assert one("workspace", a, 0)["state"] in ("stale", "current")
    cfg = o.get(f"/companies/{a}/workspace/upload-config").json()
    assert one("workspace", a, cfg["version"])["state"] == "current"
    mk(o, a, "A new section")
    assert one("workspace", a, cfg["version"])["state"] == "stale"
    assert one("document", str(uuid.uuid4()), 1)["state"] == "missing"
    assert one("document", "not-a-uuid", 1)["state"] == "missing"


def test_the_feed_never_trusts_a_device_clock(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    upload(o, a, pdf("clock"), "clock.pdf", books["node"])
    r = o.client.get(
        f"/api/v1{SY(a, '/changes')}",
        headers={**o.headers(), "Date": "Mon, 01 Jan 1990 00:00:00 GMT", "X-Client-Time": "1990-01-01T00:00:00Z"},
    ).json()
    now = datetime.now(UTC)
    assert all(datetime.fromisoformat(i["changed_at"]) <= now for i in r["items"])
    assert abs((datetime.fromisoformat(r["server_time"]) - now).total_seconds()) < 60
