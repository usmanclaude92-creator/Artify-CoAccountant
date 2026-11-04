"""Document management API: uploads, integrity, workspace integration, operations, versions, metadata, search, naming and
filing, duplicates, entitlements and storage accounting. Real PostgreSQL, real filesystem storage in a temp directory."""

from __future__ import annotations

import hashlib
import os
import re
import uuid

import pytest
import samples
from artify_api.storage import FilesystemStorage, StorageError, get_storage
from conftest import make_company, make_member, new_owner, uid
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p4_workspace import W, assign, mk

KEY_RE = re.compile(r"^o/[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}$")


def up(actor, company, name, data, node=None, chunk=None, *, complete=True, **init):  # type: ignore[no-untyped-def]
    """init -> chunked PUT -> complete. Returns (init_response, last_response)."""
    body = {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **init}
    if node is not None:
        body["node_id"] = node
    key = init.pop("_key", None) or uuid.uuid4().hex
    r = actor.post(f"/companies/{company}/uploads", body, **{"Idempotency-Key": key})
    if r.status_code not in (200, 201):
        return r, r
    sid = r.json()["id"]
    step = chunk or len(data)
    for off in range(0, len(data), step):
        put = actor.put_bytes(f"/companies/{company}/uploads/{sid}/content?offset={off}", data[off : off + step])
        assert put.status_code == 200, put.text
    if not complete:
        return r, put
    return r, actor.post(f"/companies/{company}/uploads/{sid}/complete")


def stored(actor, company, name, data, node, **kw):  # type: ignore[no-untyped-def]
    _, r = up(actor, company, name, data, node, **kw)
    assert r.status_code == 200, r.text
    return r.json()["document"]


@pytest.fixture
def firm(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Docs Firm")
    a = make_company(owner, "DA")["id"]
    node = mk(owner, a, "Finance")["id"]
    return owner, a, node


def keys_of(owner_engine, doc_id):  # type: ignore[no-untyped-def]
    with owner_engine.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text("SELECT storage_key FROM document_versions WHERE document_id = :d ORDER BY version_number"),
                {"d": doc_id},
            )
        ]


# ================================================================== upload, integrity, storage
def test_resumable_upload_stores_verifies_and_serves_the_exact_bytes(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf("invoice") * 40
    init, done = up(owner, c, "Supplier Invoice 1025.pdf", data, node, chunk=len(data) // 3 + 1)
    assert init.status_code == 201 and done.status_code == 200, done.text
    session, doc = done.json()["session"], done.json()["document"]
    assert session["status"] == "completed" and session["received_bytes"] == len(data)
    assert (
        doc["display_name"] == "Supplier Invoice 1025.pdf" and doc["original_filename"] == "Supplier Invoice 1025.pdf"
    )
    assert (
        doc["size_bytes"] == len(data)
        and doc["sha256"] == hashlib.sha256(data).hexdigest()
        and doc["mime_type"] == "application/pdf"
    )
    assert doc["node_id"] == node and doc["filed_path_snapshot"] == "Finance" and doc["current_version_number"] == 1
    assert "storage_key" not in str(doc) and doc["scan_status"] == "not_scanned"  # honest: scanning is not configured
    (key,) = keys_of(owner_engine, doc["id"])
    assert KEY_RE.fullmatch(key) and "Invoice" not in key and "pdf" not in key  # opaque: no name, no path, no extension
    assert get_storage().exists(key) and get_storage().size(key) == len(data)
    got = owner.get(f"/companies/{c}/documents/{doc['id']}/content")
    assert got.status_code == 200 and got.content == data and got.headers["x-content-sha256"] == doc["sha256"]
    assert (
        got.headers["content-disposition"].startswith("attachment")
        and got.headers["x-content-type-options"] == "nosniff"
    )
    part = owner.client.get(
        f"/api/v1/companies/{c}/documents/{doc['id']}/content", headers={**owner.headers(), "Range": "bytes=5-14"}
    )
    assert (
        part.status_code == 206
        and part.content == data[5:15]
        and part.headers["content-range"] == f"bytes 5-14/{len(data)}"
    )
    assert (
        owner.client.get(
            f"/api/v1/companies/{c}/documents/{doc['id']}/content",
            headers={**owner.headers(), "Range": "bytes=999999-"},
        ).status_code
        == 416
    )
    inline = owner.get(f"/companies/{c}/documents/{doc['id']}/content", params={"inline": "true"})
    assert (
        inline.headers["content-disposition"].startswith("inline")
        and "default-src 'none'" in inline.headers["content-security-policy"]
    )


def test_unauthenticated_and_foreign_access_to_content_is_refused(client, firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    doc = stored(owner, c, "a.pdf", samples.pdf(), node)
    assert client.get(f"/api/v1/companies/{c}/documents/{doc['id']}/content").status_code == 401
    # there is no URL-with-token way to fetch bytes at all
    assert client.get(f"/api/v1/downloads/{uuid.uuid4().hex}").status_code == 404


@pytest.mark.parametrize("name", list(samples.ACCEPTED))
def test_every_supported_format_uploads_and_downloads_unchanged(firm, name: str) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.ACCEPTED[name]()
    doc = stored(owner, c, name, data, node)
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").content == data


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("virus.exe", b"MZ" + b"\0" * 80, "unsupported_type"),
        ("archive.zip", b"PK\x03\x04" + b"\0" * 40, "unsupported_type"),
        ("cut.pdf", samples.pdf()[:-10], "invalid_file"),
        ("fake.pdf", b"not a pdf at all", "invalid_file"),
        ("png-as-pdf.pdf", samples.png(), "invalid_file"),
        ("bad.docx", b"PK\x03\x04garbage", "invalid_file"),
        ("page.txt", b"<script>x</script>", "invalid_file"),
    ],
)
def test_bad_files_are_rejected_with_clear_errors_and_nothing_is_stored(firm, owner_engine, name, data, code) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    _, r = up(owner, c, name, data, node)
    assert r.status_code in (415, 422) and r.json()["code"] == code and r.json()["detail"]
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM documents WHERE company_id = :c"), {"c": c}).scalar_one() == 0
    sid = r.json().get("session", {}).get("id")
    if sid:
        assert get_storage().stage_size(owner.get(f"/companies/{c}/uploads/{sid}").json()["id"].replace("-", "")) == 0


def test_empty_file_active_content_and_quarantine(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    r = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "e.pdf", "size": 0, "node_id": node},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 422
    _, js = up(owner, c, "js.pdf", samples.pdf_with_javascript(), node)
    assert (
        js.status_code == 422 and js.json()["code"] == "quarantined" and js.json()["session"]["status"] == "quarantined"
    )
    _, macro = up(owner, c, "m.docx", samples.docx({"word/vbaProject.bin": b"x"}), node)
    assert macro.json()["code"] == "quarantined"
    assert owner.get(f"/companies/{c}/documents").json()["total"] == 0  # held, never filed


def test_checksum_mismatch_fails_and_a_restart_works(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf()
    init, done = up(owner, c, "a.pdf", data, node, sha256="0" * 64)
    assert done.status_code == 422 and done.json()["code"] == "checksum_mismatch"
    sid = init.json()["id"]
    assert owner.get(f"/companies/{c}/uploads/{sid}").json()["status"] == "failed"
    again = owner.post(f"/companies/{c}/uploads/{sid}/retry")
    assert again.json()["status"] == "open" and again.json()["received_bytes"] == 0  # the bad bytes were discarded


def test_interrupted_upload_resumes_at_the_servers_offset(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf() * 30
    init, _ = up(owner, c, "r.pdf", data, node, chunk=100, complete=False)  # all chunks sent but never completed
    sid = init.json()["id"]
    assert owner.get(f"/companies/{c}/uploads/{sid}").json()["received_bytes"] == len(data)
    # a fresh session, interrupted half way
    half = len(data) // 2
    r = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "half.pdf", "size": len(data), "node_id": node},
        **{"Idempotency-Key": "key-half-0001"},
    )
    sid2 = r.json()["id"]
    assert owner.put_bytes(f"/companies/{c}/uploads/{sid2}/content?offset=0", data[:half]).status_code == 200
    bad = owner.put_bytes(f"/companies/{c}/uploads/{sid2}/content?offset=0", data[:50])  # replay of old bytes
    assert bad.status_code == 409 and bad.json()["code"] == "offset_mismatch" and bad.json()["received_bytes"] == half
    early = owner.post(f"/companies/{c}/uploads/{sid2}/complete")
    assert (
        early.status_code == 409 and early.json()["code"] == "size_mismatch"
    )  # not complete: nothing is reported stored
    assert owner.put_bytes(f"/companies/{c}/uploads/{sid2}/content?offset={half}", data[half:]).status_code == 200
    assert owner.post(f"/companies/{c}/uploads/{sid2}/complete").status_code == 200


def test_too_much_data_and_oversized_chunks_are_refused(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    r = owner.post(
        f"/companies/{c}/uploads",
        {"filename": "s.pdf", "size": 10, "node_id": node},
        **{"Idempotency-Key": "key-small-0001"},
    )
    sid = r.json()["id"]
    over = owner.put_bytes(f"/companies/{c}/uploads/{sid}/content?offset=0", b"x" * 11)
    assert over.status_code == 422 and over.json()["code"] == "too_much_data"
    assert owner.put_bytes(f"/companies/{c}/uploads/{sid}/content?offset=0", b"").status_code == 422


def test_cancel_discards_the_data_and_is_final(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    init, _ = up(owner, c, "c.pdf", samples.pdf(), node, complete=False)
    sid = init.json()["id"]
    assert owner.post(f"/companies/{c}/uploads/{sid}/cancel").json()["status"] == "cancelled"
    after = owner.put_bytes(f"/companies/{c}/uploads/{sid}/content?offset=0", b"x")
    assert after.status_code == 409 and after.json()["code"] == "session_closed"
    assert owner.post(f"/companies/{c}/uploads/{sid}/complete").status_code == 409


def test_init_is_idempotent_and_completion_cannot_create_two_documents(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf()
    body = {"filename": "i.pdf", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "node_id": node}
    h = {"Idempotency-Key": "same-key-12345"}
    first = owner.post(f"/companies/{c}/uploads", body, **h)
    second = owner.post(f"/companies/{c}/uploads", body, **h)
    assert first.status_code == 201 and second.status_code == 200 and first.json()["id"] == second.json()["id"]
    clash = owner.post(f"/companies/{c}/uploads", {**body, "filename": "other.pdf"}, **h)
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_mismatch"
    assert owner.post(f"/companies/{c}/uploads", body).status_code == 422  # a key is mandatory
    sid = first.json()["id"]
    owner.put_bytes(f"/companies/{c}/uploads/{sid}/content?offset=0", data)
    r1 = owner.post(f"/companies/{c}/uploads/{sid}/complete")
    r2 = owner.post(f"/companies/{c}/uploads/{sid}/complete")  # a retry after a lost response
    assert r1.status_code == r2.status_code == 200 and r1.json()["document"]["id"] == r2.json()["document"]["id"]
    assert owner.get(f"/companies/{c}/documents").json()["total"] == 1


def test_storage_failure_keeps_the_data_and_a_retry_succeeds_without_duplicates(
    firm, monkeypatch, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf()
    storage = get_storage()
    real = storage.commit_stage

    def broken(ref, key):  # type: ignore[no-untyped-def]
        raise StorageError("disk full")

    monkeypatch.setattr(storage, "commit_stage", broken)
    init, done = up(owner, c, "f.pdf", data, node)
    assert done.status_code == 503 and done.json()["code"] == "storage_error"
    sid = init.json()["id"]
    assert owner.get(f"/companies/{c}/uploads/{sid}").json()["status"] == "failed"
    assert owner.get(f"/companies/{c}/documents").json()["total"] == 0  # nothing is claimed stored
    monkeypatch.setattr(storage, "commit_stage", real)
    retry = owner.post(f"/companies/{c}/uploads/{sid}/retry").json()
    assert retry["status"] == "open" and retry["received_bytes"] == len(data)  # the staged bytes were kept
    assert owner.post(f"/companies/{c}/uploads/{sid}/complete").status_code == 200
    assert owner.get(f"/companies/{c}/documents").json()["total"] == 1


def test_a_database_failure_after_storing_removes_the_object(firm, monkeypatch, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    from artify_api import documents as docmod

    monkeypatch.setattr(docmod, "_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")))
    before = set(get_storage().list_keys())
    _, done = up(owner, c, "boom.pdf", samples.pdf(), node)
    assert done.status_code == 500
    assert set(get_storage().list_keys()) == before  # no orphan object
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM documents WHERE company_id = :c"), {"c": c}).scalar_one() == 0


def test_reconciliation_finds_orphans_and_missing_objects(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    doc = stored(owner, c, "keep.pdf", samples.pdf(), node)
    gone = stored(owner, c, "gone.pdf", samples.pdf(), node)
    (gone_key,) = keys_of(owner_engine, gone["id"])
    get_storage().delete(gone_key)
    storage = get_storage()
    assert isinstance(storage, FilesystemStorage)
    orphan = storage.new_key(uuid.UUID(owner.org_id), uuid.UUID(c))
    storage.stage_append(uuid.uuid4().hex, 0, b"")  # an empty stage is harmless
    ref = uuid.uuid4().hex
    storage.stage_append(ref, 0, b"orphaned bytes")
    storage.commit_stage(ref, orphan)
    dry = owner.post(f"/orgs/{owner.org_id}/storage/reconcile", {"dry_run": True}).json()
    assert dry["orphan_objects"] == [orphan] and dry["missing_objects"] == [gone_key] and dry["orphans_removed"] == 0
    assert storage.exists(orphan)
    old = 1_600_000_000
    os.utime(storage._path(orphan), (old, old))  # past the grace period
    applied = owner.post(f"/orgs/{owner.org_id}/storage/reconcile", {"dry_run": False}).json()
    assert applied["orphans_removed"] == 1 and not storage.exists(orphan)
    assert keys_of(owner_engine, doc["id"])[0] in set(storage.list_keys())  # real documents are never touched


# ================================================================== workspace integration
def test_two_companies_with_different_structures_use_their_own_folders(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Two Shapes Firm")
    a, b = make_company(owner, "SA")["id"], make_company(owner, "SB")["id"]
    four = [mk(owner, a, n)["id"] for n in ("Bank Statements", "Receivables", "Payables", "Expenses")]
    ten = {
        n: mk(owner, b, n)["id"]
        for n in ("Finance", "Sales", "Purchases", "Payroll", "VAT", "Projects", "HR", "Legal", "Tax", "Archive")
    }
    banking = mk(owner, b, "Banking", ten["Finance"])["id"]
    recon = mk(owner, b, "Reconciliation", banking)["id"]
    da = stored(owner, a, "stmt.pdf", samples.pdf(), four[0])
    db = stored(owner, b, "deep.pdf", samples.pdf(), recon)
    assert (
        da["filed_path_snapshot"] == "Bank Statements" and db["filed_path_snapshot"] == "Finance/Banking/Reconciliation"
    )
    # a company's documents never go into another company's folder, even with a valid id
    _, r = up(owner, a, "x.pdf", samples.pdf(), ten["Sales"])
    assert r.status_code == 404
    # listing by folder: direct and including subfolders
    r = owner.get(f"/companies/{b}/documents", params={"node_id": ten["Finance"]})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert (
        owner.get(
            f"/companies/{b}/documents", params={"node_id": ten["Finance"], "include_subfolders": "false"}
        ).json()["total"]
        == 0
    )
    assert owner.get(f"/companies/{b}/documents", params={"node_id": banking}).json()["total"] == 1
    counts = owner.get(f"/companies/{b}/documents/counts").json()
    assert counts["by_node"][recon]["active"] == 1 and counts["totals"]["active"] == 1


def test_renaming_or_moving_a_folder_never_touches_documents_or_objects(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    inbox = mk(owner, c, "Inbox", node)["id"]
    other = mk(owner, c, "Elsewhere")["id"]
    doc = stored(owner, c, "stay.pdf", samples.pdf(), inbox)
    (key,) = keys_of(owner_engine, doc["id"])
    assert owner.patch(W(c, f"/nodes/{node}"), {"name": "Finance Renamed"}).status_code == 200
    moved = owner.post(W(c, f"/nodes/{inbox}/move"), {"parent_id": other})
    assert moved.status_code == 200, moved.text
    d = owner.get(f"/companies/{c}/documents/{doc['id']}").json()
    assert d["node_id"] == inbox and d["path"] == "Elsewhere/Inbox"
    assert d["filed_path_snapshot"] == "Finance/Inbox"  # the snapshot is history, the id is the reference
    assert keys_of(owner_engine, doc["id"]) == [key] and get_storage().exists(key)
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").content == b"".join(get_storage().read(key))


def test_archived_folders_keep_their_documents_but_take_no_new_ones(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    doc = stored(owner, c, "old.pdf", samples.pdf(), node)
    assert owner.post(W(c, f"/nodes/{node}/archive"), {"confirm_impact": True}).status_code == 200
    assert owner.get(f"/companies/{c}/documents/{doc['id']}").status_code == 200
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").status_code == 200
    _, r = up(owner, c, "new.pdf", samples.pdf(), node)
    assert r.status_code == 409 and r.json()["code"] == "target_unavailable"
    again = owner.post(f"/companies/{c}/documents/{doc['id']}/archive").status_code
    assert again == 200
    assert owner.post(f"/companies/{c}/documents/{doc['id']}/restore").json()["code"] == "target_unavailable"


def test_a_folder_that_holds_documents_cannot_be_deleted(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    stored(owner, c, "held.pdf", samples.pdf(), node)
    r = owner.delete(W(c, f"/nodes/{node}"))
    assert r.status_code == 409 and r.json()["impact"]["documents"] == 1
    imp = owner.get(W(c, f"/nodes/{node}/impact")).json()
    assert imp["documents"] == 1


def test_a_folder_that_became_unavailable_during_the_upload_is_reported_and_correctable(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    spare = mk(owner, c, "Spare")["id"]
    data = samples.pdf()
    init, _ = up(owner, c, "late.pdf", data, node, complete=False)
    sid = init.json()["id"]
    owner.post(W(c, f"/nodes/{node}/archive"), {"confirm_impact": True})
    r = owner.post(f"/companies/{c}/uploads/{sid}/complete")
    assert r.status_code == 409 and r.json()["code"] == "target_unavailable"
    fixed = owner.patch(f"/companies/{c}/uploads/{sid}", {"node_id": spare})
    assert fixed.status_code == 200 and fixed.json()["status"] == "open" and fixed.json()["node_id"] == spare
    done = owner.post(f"/companies/{c}/uploads/{sid}/complete")
    assert done.status_code == 200 and done.json()["document"]["node_id"] == spare


# ================================================================== operations
def test_rename_metadata_archive_delete_and_purge_rules(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    d1 = stored(owner, c, "one.pdf", samples.pdf(), node)
    d2 = stored(owner, c, "two.pdf", samples.pdf(), node)
    r = owner.patch(f"/companies/{c}/documents/{d1['id']}", {"display_name": "Renamed One"})
    assert r.status_code == 200 and r.json()["display_name"] == "Renamed One.pdf"  # the type stays with the content
    clash = owner.patch(f"/companies/{c}/documents/{d2['id']}", {"display_name": "renamed one.pdf"})
    assert clash.status_code == 409 and clash.json()["code"] == "name_taken"
    assert (
        owner.patch(f"/companies/{c}/documents/{d2['id']}", {"display_name": "a/b"}).json()["display_name"] == "a_b.pdf"
    )
    stale = owner.patch(f"/companies/{c}/documents/{d1['id']}", {"display_name": "X", "row_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "document_changed"
    assert owner.post(f"/companies/{c}/documents/{d1['id']}/archive").json()["status"] == "archived"
    assert (
        owner.patch(f"/companies/{c}/documents/{d1['id']}", {"display_name": "Nope"}).status_code == 409
    )  # restore first
    assert d1["id"] not in [x["id"] for x in owner.get(f"/companies/{c}/documents").json()["items"]]
    assert owner.get(f"/companies/{c}/documents", params={"status": "archived"}).json()["total"] == 1
    assert owner.post(f"/companies/{c}/documents/{d1['id']}/restore").json()["status"] == "active"
    # removing is not purging
    gone = owner.post(f"/companies/{c}/documents/{d1['id']}/delete", {"reason": "uploaded by mistake"})
    assert gone.status_code == 200 and gone.json()["status"] == "deleted"
    (key,) = keys_of(owner_engine, d1["id"])
    assert get_storage().exists(key)  # the original is still there
    assert owner.get(f"/companies/{c}/documents/{d1['id']}/content").status_code == 410
    early = owner.post(f"/companies/{c}/documents/{d1['id']}/purge", {"confirm": "Renamed One.pdf"})
    assert early.status_code == 409 and early.json()["code"] == "retention_period"
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE documents SET deleted_at = now() - interval '40 days' WHERE id = :d"), {"d": d1["id"]}
        )
    assert (
        owner.post(f"/companies/{c}/documents/{d1['id']}/purge", {"confirm": "wrong"}).json()["code"]
        == "confirmation_mismatch"
    )
    assert owner.post(f"/companies/{c}/documents/{d1['id']}/legal-hold", {"hold": True}).status_code == 200
    held = owner.post(f"/companies/{c}/documents/{d1['id']}/purge", {"confirm": "Renamed One.pdf"})
    assert held.status_code == 409 and held.json()["code"] == "legal_hold"
    assert owner.post(f"/companies/{c}/documents/{d1['id']}/legal-hold", {"hold": False}).status_code == 200
    used_before = owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]["used_bytes"]
    done = owner.post(f"/companies/{c}/documents/{d1['id']}/purge", {"confirm": "Renamed One.pdf"})
    assert done.status_code == 200 and done.json()["versions_removed"] == 1
    assert not get_storage().exists(key)
    assert owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]["used_bytes"] < used_before
    with owner_engine.connect() as conn:  # a tombstone and its history remain
        assert (
            conn.execute(text("SELECT status FROM documents WHERE id = :d"), {"d": d1["id"]}).scalar_one() == "purged"
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM document_events WHERE document_id = :d AND event_type = 'purged'"),
                {"d": d1["id"]},
            ).scalar_one()
            == 1
        )
    assert owner.get(f"/companies/{c}/documents/{d1['id']}").status_code == 404


def test_removed_documents_can_be_brought_back_and_are_never_hard_deleted_by_the_app_role(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    d = stored(owner, c, "back.pdf", samples.pdf(), node)
    owner.post(f"/companies/{c}/documents/{d['id']}/delete", {"reason": "oops removed"})
    assert owner.post(f"/companies/{c}/documents/{d['id']}/undelete").json()["status"] == "archived"
    app = __import__("sqlalchemy").create_engine(os.environ["ARTIFY_DATABASE_URL_APP"])
    for stmt in (
        "DELETE FROM documents",
        "DELETE FROM document_versions",
        "DELETE FROM document_events",
        "UPDATE document_events SET event_type = 'purged'",
    ):
        with pytest.raises(DBAPIError), app.connect() as conn, conn.begin():
            conn.execute(
                text("SELECT set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"),
                {"o": owner.org_id},
            )
            conn.execute(text(stmt))
    # even the table owner cannot alter a stored version's identity
    with owner_engine.connect() as conn, pytest.raises(DBAPIError, match="immutable"), conn.begin():
        conn.execute(
            text("UPDATE document_versions SET sha256 = repeat('a', 64) WHERE document_id = :d"), {"d": d["id"]}
        )
    app.dispose()


def test_move_between_folders_keeps_history_and_validates_the_target(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    target = mk(owner, c, "Target")["id"]
    d = stored(owner, c, "m.pdf", samples.pdf(), node)
    clash = stored(owner, c, "m.pdf", samples.pdf(), target)  # same display name already in the target folder
    r = owner.post(f"/companies/{c}/documents/{d['id']}/move", {"node_id": target})
    assert r.status_code == 200 and r.json()["node_id"] == target and r.json()["display_name"] != clash["display_name"]
    assert r.json()["filed_path_snapshot"] == "Target"
    ev = owner.get(f"/companies/{c}/documents/{d['id']}/events").json()["items"]
    assert [e["event_type"] for e in ev][:2] == ["moved", "uploaded"] and ev[0]["detail"]["from"] == "Finance"
    assert owner.post(f"/companies/{c}/documents/{d['id']}/move", {"node_id": str(uuid.uuid4())}).status_code == 404


# ================================================================== versions
def test_versions_preserve_the_original_and_restore_only_moves_the_pointer(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    v1, v2, v3 = samples.pdf("one"), samples.pdf("two") * 3, samples.pdf("three")
    doc = stored(owner, c, "Supplier-Invoice-1025.pdf", v1, node)
    for data, reason in ((v2, "Corrected document"), (v3, "Updated supplier invoice")):
        _, r = up(owner, c, "Supplier-Invoice-1025.pdf", data, None, document_id=doc["id"], change_reason=reason)
        assert r.status_code == 200, r.text
    vs = owner.get(f"/companies/{c}/documents/{doc['id']}/versions").json()["items"]
    assert [v["version_number"] for v in vs] == [3, 2, 1] and [v["is_current"] for v in vs] == [True, False, False]
    assert vs[0]["change_reason"] == "Updated supplier invoice" and vs[2]["change_reason"] is None
    assert len(set(keys_of(owner_engine, doc["id"]))) == 3  # three distinct objects, none overwritten
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").content == v3
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content", params={"version": 1}).content == v1
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content", params={"version": 9}).status_code == 404
    used = owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]["used_bytes"]
    assert used == len(v1) + len(v2) + len(v3)  # every retained version counts, once
    r = owner.post(f"/companies/{c}/documents/{doc['id']}/versions/2/restore", {"reason": "v3 was wrong"})
    assert r.status_code == 200 and r.json()["current_version_number"] == 2
    assert owner.get(f"/companies/{c}/documents/{doc['id']}/content").content == v2
    assert (
        owner.get(f"/orgs/{owner.org_id}/storage").json()["storage"]["used_bytes"] == used
    )  # restoring added no bytes
    assert (
        owner.post(f"/companies/{c}/documents/{doc['id']}/versions/2/restore", {}).json()["code"] == "already_current"
    )
    evs = owner.get(f"/companies/{c}/documents/{doc['id']}/events").json()["items"]
    kinds = [e["event_type"] for e in evs if e["event_type"] not in ("downloaded", "previewed")]
    assert kinds[:3] == ["version_restored", "version_created", "version_created"] and kinds[-1] == "uploaded"
    # the next upload continues the numbering, it never reuses a number
    _, r4 = up(owner, c, "Supplier-Invoice-1025.pdf", samples.pdf("four"), None, document_id=doc["id"])
    assert r4.json()["document"]["latest_version_number"] == 4


def test_version_upload_rules(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    data = samples.pdf()
    doc = stored(owner, c, "v.pdf", data, node)
    same = up(owner, c, "v.pdf", data, None, document_id=doc["id"])[1]
    assert same.status_code == 409 and same.json()["code"] == "identical_to_current_version"
    other_type = up(owner, c, "v.png", samples.png(), None, document_id=doc["id"])[1]
    assert other_type.status_code == 422 and other_type.json()["code"] == "extension_changed"
    owner.post(f"/companies/{c}/documents/{doc['id']}/archive")
    archived = up(owner, c, "v.pdf", samples.pdf(), None, document_id=doc["id"])[1]
    assert archived.status_code == 409 and archived.json()["code"] == "document_not_active"


# ================================================================== metadata, search, naming, filing
def make_fields(owner, c, cat=None):  # type: ignore[no-untyped-def]
    f = {}
    for label, t, extra in (
        ("Supplier", "short_text", {"required": True}),
        ("Invoice Number", "short_text", {}),
        ("Quantity", "number", {"validation": {"min": 0}}),
        ("Total", "currency", {"validation": {"allowed_currencies": ["OMR"]}}),
        ("Invoice Date", "date", {}),
        (
            "Cost Center",
            "dropdown",
            {"options": [{"value": "ops", "label": "Operations"}, {"value": "hq", "label": "HQ"}]},
        ),
    ):
        r = owner.post(
            W(c, "/fields"), {"label": label, "data_type": t, **extra, **({"category_ids": [cat]} if cat else {})}
        )
        assert r.status_code == 201, r.text
        f[label] = r.json()["item"]["key"]
    return f


def test_company_specific_metadata_is_validated_stored_filtered_and_audited(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    keys = make_fields(owner, c)
    ok = {
        keys["Supplier"]: "Acme LLC",
        keys["Invoice Number"]: "INV-1",
        keys["Quantity"]: 3,
        keys["Total"]: {"amount": "10.500", "currency": "OMR"},
        keys["Invoice Date"]: "2026-03-04",
        keys["Cost Center"]: "ops",
    }
    doc = stored(owner, c, "m.pdf", samples.pdf(), node, field_values=ok)
    d = owner.get(f"/companies/{c}/documents/{doc['id']}").json()
    assert (
        d["field_values"][keys["Total"]] == {"amount": "10.500", "currency": "OMR"}
        and d["field_values"][keys["Quantity"]] == 3
    )
    # required, wrong type, bad option, unknown key, bad currency
    _, missing = up(owner, c, "n.pdf", samples.pdf(), node, field_values={keys["Invoice Number"]: "x"})
    assert (
        missing.status_code == 422
        and missing.json()["code"] == "invalid_metadata"
        and any("required" in p for p in missing.json()["problems"])
    )
    for bad in (
        {keys["Quantity"]: "three"},
        {keys["Cost Center"]: "nope"},
        {keys["Total"]: {"amount": "1", "currency": "USD"}},
        {"unknown_key": "x"},
        {keys["Invoice Date"]: "04/03/2026"},
    ):
        r = owner.post(
            f"/companies/{c}/uploads",
            {"filename": "b.pdf", "size": 5, "node_id": node, "field_values": bad},
            **{"Idempotency-Key": uuid.uuid4().hex},
        )
        assert r.status_code == 422 and r.json()["code"] == "invalid_metadata", bad
    # filter and search by metadata
    assert owner.get(f"/companies/{c}/documents", params={"field": f"{keys['Cost Center']}:ops"}).json()["total"] == 1
    assert owner.get(f"/companies/{c}/documents", params={"field": f"{keys['Cost Center']}:hq"}).json()["total"] == 0
    assert owner.get(f"/companies/{c}/documents", params={"field": f"{keys['Quantity']}:3"}).json()["total"] == 1
    assert owner.get(f"/companies/{c}/documents", params={"q": "acme"}).json()["total"] == 1
    # edit with history
    r = owner.patch(f"/companies/{c}/documents/{doc['id']}", {"field_values": {keys["Cost Center"]: "hq"}})
    assert r.status_code == 200 and r.json()["field_values"][keys["Cost Center"]] == "hq"
    ev = owner.get(f"/companies/{c}/documents/{doc['id']}/events").json()["items"][0]
    assert ev["event_type"] == "metadata_changed" and ev["detail"]["fields"][keys["Cost Center"]] == {
        "from": "ops",
        "to": "hq",
    }
    assert (
        owner.patch(f"/companies/{c}/documents/{doc['id']}", {"field_values": {keys["Cost Center"]: "zzz"}}).status_code
        == 422
    )


def test_another_company_has_other_fields_and_is_unaffected(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Fields Firm")
    a, b = make_company(owner, "FA")["id"], make_company(owner, "FB")["id"]
    na, nb = mk(owner, a, "Docs")["id"], mk(owner, b, "Docs")["id"]
    keys = make_fields(owner, a)
    stored(owner, a, "a.pdf", samples.pdf(), na, field_values={keys["Supplier"]: "Acme"})
    free = stored(owner, b, "b.pdf", samples.pdf(), nb)  # company B has no required fields
    assert owner.get(f"/companies/{b}/documents/{free['id']}").json()["field_values"] == {}
    _, r = up(owner, b, "c.pdf", samples.pdf(), nb, field_values={keys["Supplier"]: "Acme"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_metadata"  # A's field does not exist in B


def test_naming_rule_names_documents_and_missing_metadata_is_never_invented(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    cat = owner.post(W(c, "/categories"), {"name": "Supplier invoice", "document_kind": "purchase_invoice"}).json()[
        "item"
    ]
    keys = make_fields(owner, c, cat["id"])
    assert (
        owner.post(
            W(c, "/naming-rules"),
            {"name": "inv", "pattern": "{Supplier}_{InvoiceNumber}_{InvoiceDate}", "category_id": cat["id"]},
        ).status_code
        == 201
    )
    full = {keys["Supplier"]: "Acme", keys["Invoice Number"]: "1025", keys["Invoice Date"]: "2026-03-04"}
    sug = owner.post(
        f"/companies/{c}/documents/suggest",
        {"filename": "scan0001.pdf", "category_id": cat["id"], "node_id": node, "field_values": full},
    ).json()
    assert (
        sug["naming"]["name"] == "Acme_1025_2026-03-04.pdf"
        and sug["naming"]["complete"]
        and sug["original_filename"] == "scan0001.pdf"
    )
    doc = stored(owner, c, "scan0001.pdf", samples.pdf(), node, category_id=cat["id"], field_values=full)
    assert doc["display_name"] == "Acme_1025_2026-03-04.pdf" and doc["original_filename"] == "scan0001.pdf"
    assert doc["kind_at_filing"] == "purchase_invoice"
    # same values again: a different document, so the name is made unique and nothing is overwritten
    dup = stored(owner, c, "scan0002.pdf", samples.pdf(), node, category_id=cat["id"], field_values=full)
    assert dup["display_name"] != doc["display_name"] and dup["display_name"].startswith("Acme_1025_2026-03-04")
    # incomplete metadata: the original name is kept instead of inventing "NA"
    part = stored(
        owner, c, "scan0003.pdf", samples.pdf(), node, category_id=cat["id"], field_values={keys["Supplier"]: "Acme"}
    )
    assert part["display_name"] == "scan0003.pdf" and "NA" not in part["display_name"]
    inc = owner.post(
        f"/companies/{c}/documents/suggest",
        {"filename": "x.pdf", "category_id": cat["id"], "node_id": node, "field_values": {keys["Supplier"]: "Acme"}},
    ).json()
    assert not inc["naming"]["complete"] and inc["naming"]["warnings"]
    # a user-chosen name wins; hostile characters are neutralised
    own = stored(
        owner,
        c,
        "scan0004.pdf",
        samples.pdf(),
        node,
        category_id=cat["id"],
        field_values=full,
        display_name="../../evil:name",
    )
    assert own["display_name"] == ".._.._evil_name.pdf".strip(".") or "/" not in own["display_name"]
    assert re.fullmatch(r"[^/\\:*?\"<>|]+", own["display_name"])


def test_filing_rules_suggest_a_destination_only_from_configured_evidence(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    banks = mk(owner, c, "Bank Statements", node)["id"]
    muscat = mk(owner, c, "Bank of Muscat", banks)["id"]
    cat = owner.post(W(c, "/categories"), {"name": "Statements", "document_kind": "bank_statement"}).json()["item"]
    rule = owner.post(
        W(c, "/filing-rules"),
        {
            "name": "Statements",
            "conditions": [{"field": "document_kind", "operator": "equals", "value": "bank_statement"}],
            "target_node_id": muscat,
            "priority": 10,
        },
    ).json()["item"]
    s = owner.post(f"/companies/{c}/documents/suggest", {"filename": "s.pdf", "category_id": cat["id"]}).json()[
        "filing"
    ]
    assert (
        s["rule"]["id"] == rule["id"]
        and s["target_node_id"] == muscat
        and s["path"] == "Finance/Bank Statements/Bank of Muscat"
    )
    none = owner.post(f"/companies/{c}/documents/suggest", {"filename": "s.pdf"}).json()["filing"]
    assert none["rule"] is None and none["target_node_id"] is None  # no evidence, no guess
    doc = stored(owner, c, "s.pdf", samples.pdf(), muscat, category_id=cat["id"], filing_rule_id=rule["id"])
    assert doc["filing_rule_id"] == rule["id"]
    wrong = stored(owner, c, "t.pdf", samples.pdf(), banks, filing_rule_id=rule["id"])
    assert wrong["filing_rule_id"] is None  # the rule does not target the chosen folder, so it is not recorded
    owner.post(W(c, f"/nodes/{muscat}/archive"), {"confirm_impact": True})
    after = owner.post(f"/companies/{c}/documents/suggest", {"filename": "s.pdf", "category_id": cat["id"]}).json()[
        "filing"
    ]
    assert after["rule"] is None  # archived targets are never suggested


def test_search_filters_sorting_and_pagination(firm) -> None:  # type: ignore[no-untyped-def]
    owner, c, node = firm
    sub = mk(owner, c, "Sub", node)["id"]
    stored(owner, c, "alpha.pdf", samples.pdf(), node)
    stored(owner, c, "beta.png", samples.png(), node)
    stored(owner, c, "gamma.xlsx", samples.xlsx(), sub)
    stored(owner, c, "delta.docx", samples.docx(), sub)
    g = lambda **p: owner.get(f"/companies/{c}/documents", params=p).json()  # noqa: E731
    assert g()["total"] == 4 and g(q="alp")["total"] == 1 and g(q="%")["total"] == 0 and g(q="_")["total"] == 0
    assert (
        [x["extension"] for x in g(family="image")["items"]] == ["png"]
        and g(family="excel")["total"] == 1
        and g(family="word")["total"] == 1
    )
    assert (
        g(ext="pdf")["total"] == 1
        and g(node_id=sub)["total"] == 2
        and g(node_id=node, include_subfolders="false")["total"] == 2
    )
    page = g(sort="name", order="asc", limit=2, offset=1)
    assert [x["display_name"] for x in page["items"]] == ["beta.png", "delta.docx"] and page["total"] == 4
    assert g(created_from="2999-01-01T00:00:00Z")["total"] == 0 and g(uploaded_by=uid(owner))["total"] == 4
    assert g(q="no such thing")["items"] == []
    assert owner.get(f"/companies/{c}/documents", params={"limit": 500}).status_code == 422


# ================================================================== duplicates
def test_duplicates_are_detected_by_content_inside_one_company_only(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Dup Firm")
    a, b = make_company(owner, "UA")["id"], make_company(owner, "UB")["id"]
    na, nb = mk(owner, a, "Docs")["id"], mk(owner, b, "Docs")["id"]
    data = samples.pdf("same")
    first = stored(owner, a, "original.pdf", data, na)
    # same bytes, different name: a duplicate
    _, dup = up(owner, a, "renamed-copy.pdf", data, na)
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_detected"
    assert dup.json()["document"]["id"] == first["id"] and dup.json()["document"]["path"] == "Docs"
    # same name, different bytes: not a duplicate
    assert up(owner, a, "original.pdf", samples.pdf("other"), na)[1].status_code == 200
    # another company may hold the same bytes, and learns nothing about company A
    _, other = up(owner, b, "original.pdf", data, nb)
    assert other.status_code == 200
    assert owner.post(
        f"/companies/{b}/documents/check-duplicate",
        {"sha256": hashlib.sha256(samples.pdf("only-in-a-xyz")).hexdigest()},
    ).json() == {"exists": False, "document": None}
    # an explicit decision keeps a separate copy and records it
    r = stored(owner, a, "renamed-copy.pdf", data, na, duplicate_policy="allow_copy")
    assert r["id"] != first["id"]
    ev = owner.get(f"/companies/{a}/documents/{r['id']}/events").json()["items"]
    assert any(e["event_type"] == "duplicate_kept" and e["detail"]["duplicate_of"] == first["id"] for e in ev)
    # a pre-check says so before any bytes move
    pre = owner.post(f"/companies/{a}/documents/check-duplicate", {"sha256": hashlib.sha256(data).hexdigest()}).json()
    assert pre["exists"] and pre["document"]["id"] == first["id"]
    init = owner.post(
        f"/companies/{a}/uploads",
        {"filename": "z.pdf", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "node_id": na},
        **{"Idempotency-Key": uuid.uuid4().hex},
    )
    assert init.status_code == 409 and init.json()["code"] == "duplicate_detected"


def test_a_duplicate_in_a_hidden_folder_is_reported_without_a_reference(firm, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, c, _ = firm
    secret = mk(owner, c, "Confidential")["id"]
    open_node = mk(owner, c, "Open")["id"]
    data = samples.pdf("secret")
    stored(owner, c, "s.pdf", data, secret)
    owner.post(
        W(c, "/permissions"),
        {
            "node_id": secret,
            "subject_type": "role",
            "role": "document_operator",
            "permission": "view",
            "effect": "deny",
        },
    )
    op = make_member(owner, mailer, "document_operator")
    assign(owner, c, op)
    _, r = up(op, c, "mine.pdf", data, open_node)
    assert r.status_code == 409 and r.json()["code"] == "duplicate_detected"
    assert r.json()["exists"] is True and r.json()["document"] is None  # exists, but the operator may not see where
    assert "Confidential" not in r.text and secret not in r.text
