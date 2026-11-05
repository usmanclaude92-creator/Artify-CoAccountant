"""Phase 12: synchronization conflicts recorded by the server and resolved by people, with an immutable audited history."""

# ruff: noqa: F811
from __future__ import annotations

import uuid

from conftest import make_member, new_owner
from sqlalchemy import text
from test_p6_documents import assign, backend, pdf, upload  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import ACC, books  # noqa: F401  (books is a fixture)
from test_p10_bookkeeping import quiet, txn


def SY(cid: str, path: str) -> str:
    return f"/companies/{cid}/sync{path}"


def stale_doc(books):  # type: ignore[no-untyped-def]
    """A document the owner renamed after a device last saw it. Returns (document, revision the device holds)."""
    o, a = books["owner"], books["a"]
    doc = upload(o, a, pdf("conflict"), "c.pdf", books["node"])
    held = doc["row_version"]
    r = o.patch(f"/companies/{a}/documents/{doc['id']}", {"display_name": "Server rename", "base_row_version": held})
    assert r.status_code == 200, r.text
    return doc, held


def report(actor, cid, kind, ident, base, client="laptop-1", **kw):  # type: ignore[no-untyped-def]
    return actor.post(
        SY(cid, "/conflicts"),
        {
            "kind": kind,
            "id": ident,
            "client_id": client,
            "base_revision": base,
            "local_summary": {"display_name": "Local"},
            **kw,
        },
    )


def test_a_stale_write_is_recorded_once_per_device_and_the_server_checks_the_claim(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    doc, held = stale_doc(books)
    r = report(o, a, "document", doc["id"], held)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["conflict_type"] == "stale" and c["state"] == "open" and c["base_revision"] == held
    assert c["server_revision"] == held + 1 and c["server_summary"]["state"] == "stale"
    # reporting again from the same device is idempotent; another device gets its own conflict
    again = report(o, a, "document", doc["id"], held)
    assert again.status_code == 200 and again.json()["id"] == c["id"]
    other = report(o, a, "document", doc["id"], held, client="phone-1")
    assert other.status_code == 201 and other.json()["id"] != c["id"]
    # the claim is verified: a current copy is not a conflict, and a made-up id is 404
    cur = report(o, a, "document", doc["id"], held + 1)
    assert cur.status_code == 422 and cur.json()["code"] == "no_conflict"
    assert report(o, a, "document", str(uuid.uuid4()), 1).status_code == 404
    assert report(o, a, "document", doc["id"], held, local_summary={"x": "y" * 5000}).status_code == 422


def test_resolution_is_recorded_audited_and_final(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    doc, held = stale_doc(books)
    cid = report(o, a, "document", doc["id"], held).json()["id"]
    assert o.get(SY(a, "/status")).json()["open_conflicts"] == 1
    # unknown resolution and a missing explanation are refused
    assert o.post(SY(a, f"/conflicts/{cid}/resolve"), {"resolution": "apply_local"}).json()["code"] == "note_required"
    ok = o.post(SY(a, f"/conflicts/{cid}/resolve"), {"resolution": "apply_local", "note": "My edit is the right title"})
    assert ok.status_code == 200, ok.text
    r = ok.json()
    assert r["state"] == "resolved" and r["resolution"] == "apply_local" and r["resolved_by"] and r["resolved_at"]
    assert r["next_base_revision"] == held + 1  # the client must now write on top of the current server revision
    assert o.get(SY(a, "/status")).json()["open_conflicts"] == 0
    # final: a second decision is refused, and the database refuses any change to a resolved row
    again = o.post(SY(a, f"/conflicts/{cid}/resolve"), {"resolution": "keep_server"})
    assert again.status_code == 409 and again.json()["code"] == "already_resolved"
    with owner_engine.begin() as conn:
        for sql in (
            "UPDATE sync_conflicts SET resolution_note = 'x' WHERE id = :i",
            "DELETE FROM sync_conflicts WHERE id = :i",
        ):
            try:
                conn.execute(text("SAVEPOINT s"))
                conn.execute(text(sql), {"i": cid})
                raise AssertionError("the database allowed a change to a resolved conflict")
            except AssertionError:
                raise
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT s"))
        n = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE entity_id = :i AND action LIKE 'sync.conflict.%'"), {"i": cid}
        ).scalar_one()
    assert n == 2  # detected + resolved


def test_a_locked_record_can_only_keep_the_server_copy(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    quiet(o, a)
    tid = txn(books)  # approved
    rev = o.get(ACC(a, f"/transactions/{tid}")).json()["row_version"]
    r = report(o, a, "transaction", tid, rev - 1 if rev > 1 else 0)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["conflict_type"] == "locked"
    for bad in ("apply_local", "merged"):
        x = o.post(SY(a, f"/conflicts/{c['id']}/resolve"), {"resolution": bad, "note": "force it through"})
        assert x.status_code in (409, 422), x.text
    keep = o.post(SY(a, f"/conflicts/{c['id']}/resolve"), {"resolution": "keep_server"})
    assert keep.status_code == 200 and keep.json()["next_base_revision"] is None


def test_only_the_finder_or_someone_with_the_permission_can_decide(books, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, acct, prep = books["owner"], books["a"], books["acct"], books["prep"]
    other = make_member(o, mailer, "assistant_accountant")
    assign(o, a, other)
    doc, held = stale_doc(books)
    cid = report(prep, a, "document", doc["id"], held).json()["id"]
    # a colleague who neither found it nor holds sync.resolve may look but not decide
    assert other.get(SY(a, f"/conflicts/{cid}")).status_code == 200
    denied = other.post(SY(a, f"/conflicts/{cid}/resolve"), {"resolution": "keep_server"})
    assert denied.status_code == 403
    assert o.get(SY(a, f"/conflicts/{cid}")).json()["state"] == "open"
    # an accountant holds sync.resolve and may decide someone else's conflict
    done = acct.post(SY(a, f"/conflicts/{cid}/resolve"), {"resolution": "discarded", "note": "Not needed any more"})
    assert done.status_code == 200 and done.json()["resolved_by"] != done.json()["detected_by"]
    # the finder can decide their own
    cid2 = report(prep, a, "document", doc["id"], held, client="phone-9").json()["id"]
    assert prep.post(SY(a, f"/conflicts/{cid2}/resolve"), {"resolution": "keep_server"}).status_code == 200
    lst = o.get(SY(a, "/conflicts?state=resolved")).json()["items"]
    assert {x["id"] for x in lst} == {cid, cid2}


def test_hidden_folders_hide_their_conflicts(books, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    doc, held = stale_doc(books)
    cid = report(o, a, "document", doc["id"], held).json()["id"]
    assert prep.get(SY(a, f"/conflicts/{cid}")).status_code == 200
    assert o.patch(f"/companies/{a}/workspace/settings", {"access_mode": "restricted"}).status_code == 200
    assert prep.get(SY(a, f"/conflicts/{cid}")).status_code == 404
    assert cid not in [x["id"] for x in prep.get(SY(a, "/conflicts")).json()["items"]]
    assert o.get(SY(a, f"/conflicts/{cid}")).status_code == 200


def test_conflicts_never_cross_companies_or_organizations(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    doc, held = stale_doc(books)
    cid = report(o, a, "document", doc["id"], held).json()["id"]
    assert o.get(SY(b, f"/conflicts/{cid}")).status_code == 404
    assert o.post(SY(b, f"/conflicts/{cid}/resolve"), {"resolution": "keep_server"}).status_code == 404
    assert o.get(SY(b, "/conflicts")).json()["items"] == []
    assert report(o, b, "document", doc["id"], held).status_code == 404
    stranger = new_owner(client, mailer)
    assert stranger.get(SY(a, f"/conflicts/{cid}")).status_code == 404
    assert stranger.get(SY(a, "/conflicts")).status_code in (403, 404)
