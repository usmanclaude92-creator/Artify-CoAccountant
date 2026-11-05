"""Phase 6 review findings (added after an independent review of the document engine against the invariants):
database-level immutability of stored binaries, who may read storage totals, and the quota under concurrency."""

from __future__ import annotations

import concurrent.futures as cf

import pytest
from conftest import make_member, set_plan_limit
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p6_documents import D, assign, backend, firm, init, objects, pdf, put, upload  # noqa: F401


def test_stored_versions_and_storage_objects_are_immutable_in_the_database(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("immutable"), "orig.pdf", node)
    new_hash = "a" * 64
    for stmt in (
        "UPDATE document_versions SET sha256 = :h WHERE document_id = :d",
        "UPDATE document_versions SET size_bytes = size_bytes + 1 WHERE document_id = :d",
        "UPDATE document_versions SET storage_object_id = gen_random_uuid() WHERE document_id = :d",
        "UPDATE document_versions SET original_filename = 'other.pdf' WHERE document_id = :d",
        "DELETE FROM document_versions WHERE document_id = :d",
        "UPDATE storage_objects SET sha256 = :h WHERE id IN (SELECT storage_object_id FROM document_versions WHERE document_id = :d)",
        "UPDATE storage_objects SET size_bytes = 1 WHERE id IN (SELECT storage_object_id FROM document_versions WHERE document_id = :d)",
        "UPDATE storage_objects SET storage_key = storage_key || 'x' WHERE id IN (SELECT storage_object_id FROM document_versions WHERE document_id = :d)",
        "DELETE FROM storage_objects WHERE id IN (SELECT storage_object_id FROM document_versions WHERE document_id = :d)",
    ):
        # even the table owner is refused: the guard is a trigger, not just a missing grant
        with owner_engine.connect() as conn, pytest.raises(DBAPIError, match="immutable|never deleted"), conn.begin():
            conn.execute(text(stmt), {"h": new_hash, "d": doc["id"]})
    with owner_engine.begin() as conn:  # the lifecycle columns still work, and the scan result may be recorded
        conn.execute(
            text(
                "UPDATE storage_objects SET state = 'verified', verified_at = now() WHERE id IN"
                " (SELECT storage_object_id FROM document_versions WHERE document_id = :d)"
            ),
            {"d": doc["id"]},
        )
        conn.execute(
            text("UPDATE document_versions SET scan_status = 'clean' WHERE document_id = :d"), {"d": doc["id"]}
        )
        conn.execute(
            text(
                "UPDATE storage_objects SET state = 'purged', purged_at = now() WHERE id IN"
                " (SELECT storage_object_id FROM document_versions WHERE document_id = :d)"
            ),
            {"d": doc["id"]},
        )
    with owner_engine.connect() as conn, pytest.raises(DBAPIError, match="stays purged"), conn.begin():
        conn.execute(
            text(
                "UPDATE storage_objects SET state = 'stored' WHERE id IN"
                " (SELECT storage_object_id FROM document_versions WHERE document_id = :d)"
            ),
            {"d": doc["id"]},
        )


def test_storage_totals_and_reconciliation_are_for_storage_managers_only(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    upload(owner, a, pdf("totals"), "t.pdf", node)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    assert owner.get("/storage/overview").status_code == 200
    for method, path, body in (
        ("get", "/storage/overview", None),
        ("post", "/storage/reconcile", {"repair": False}),
    ):
        r = acc.get(path) if method == "get" else acc.post(path, body)
        assert r.status_code == 403, (path, r.status_code)
        assert "totals" not in r.text and "bytes" not in r.text


def test_concurrent_uploads_cannot_oversubscribe_the_storage_quota(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    contents = [pdf(f"race{i}") for i in range(4)]  # all the same size
    ops = []
    for i, c in enumerate(contents):
        r = init(owner, a, c, f"race{i}.pdf", node)
        assert r.status_code == 201, r.text
        ops.append(r.json()["id"])
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", len(contents[0]) * 2)  # room for exactly two
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda p: put(owner, a, p[0], p[1]).status_code, zip(ops, contents, strict=True)))
    stored = sum(o["size_bytes"] for o in objects(owner_engine, a) if o["state"] != "purged")
    assert stored <= len(contents[0]) * 2, (codes, stored)
    assert codes.count(200) == 2 and all(c in (200, 402) for c in codes), codes


def test_company_wide_totals_and_the_activity_feed_never_include_hidden_folders(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    from conftest import uid
    from test_p6_documents import W, mk, sha

    owner, a, _b, node, _ = firm
    secret = mk(owner, a, "Payroll")["id"]
    visible_doc = upload(owner, a, pdf("visible-doc"), "visible.pdf", node)
    hidden_content = pdf("hidden-doc-with-a-longer-body" * 3)
    hidden_doc = upload(owner, a, hidden_content, "salaries.pdf", secret)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    g = owner.post(
        W(a, "/permissions"),
        {"node_id": secret, "subject_type": "user", "user_id": uid(acc), "permission": "view", "effect": "deny"},
    )
    assert g.status_code == 201
    counts = acc.get(D(a, "/counts")).json()
    assert secret not in counts["nodes"]
    assert counts["totals"]["active"] == 1, counts  # the hidden document is not even counted
    assert counts["totals"]["bytes"] == visible_doc["size_bytes"], counts  # nor are its bytes
    feed = acc.get(D(a, "/events")).json()
    ids = {e["document_id"] for e in feed["items"]}
    leaks = [e for e in feed["items"] if "salaries" in str(e)]
    assert visible_doc["id"] in ids and hidden_doc["id"] not in ids and not leaks, [
        (e["event_type"], e["summary"]) for e in leaks
    ]
    full = owner.get(D(a, "/events")).json()
    assert hidden_doc["id"] in {e["document_id"] for e in full["items"]} and full["total"] > feed["total"]
    assert sha(hidden_content) not in acc.get(D(a)).text
