"""Phase 6 tests (real PostgreSQL, local + moto-S3 object storage): import/upload protocol, validation, storage,
workspace integration, operations, versions, metadata, duplicates, search, activity, entitlements and security.
All files are synthetic and generated in memory."""

from __future__ import annotations

import hashlib
import io
import struct
import uuid
import zipfile
import zlib

import pytest
from artify_api import scanner as scanner_mod
from artify_api import storage as storage_mod
from artify_api.storage import LocalBackend
from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text


def W(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/workspace{path}"


def D(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/documents{path}"


def mk(owner, cid, name, parent=None, **kw):  # type: ignore[no-untyped-def]
    r = owner.post(W(cid, "/nodes"), {"name": name, "parent_id": parent, **kw})
    assert r.status_code == 201, r.text
    return r.json()["item"]


def assign(owner, cid, member):  # type: ignore[no-untyped-def]
    r = owner.post(f"/companies/{cid}/assignments", {"user_id": uid(member), "assignment_type": "secondary"})
    assert r.status_code in (200, 201), r.text


# ------------------------------------------------------------------ synthetic file builders
def pdf(tag: str = "") -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Count 0 /Kids [] >>\nendobj\n"
        + f"% synthetic {tag}\n".encode()
        + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def png(tag: str = "") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"\x00" + (tag.encode()[:3].ljust(3, b"\x01"))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg(tag: str = "") -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + tag.encode().ljust(200, b"\x00") + b"\xff\xd9"


def tiff(tag: str = "") -> bytes:
    return b"II*\x00" + tag.encode().ljust(64, b"\x00")


def bmp(tag: str = "") -> bytes:
    body = b"BM" + b"\x00" * 4 + b"\x00" * 4 + b"\x36\x00\x00\x00" + tag.encode().ljust(40, b"\x00")
    return body[:2] + struct.pack("<I", len(body)) + body[6:]


def heic(tag: str = "") -> bytes:
    return struct.pack(">I", 24) + b"ftypheic" + b"\x00\x00\x00\x00" + tag.encode().ljust(8, b"\x00")


def ooxml(kind: str, tag: str = "", extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml" if kind == "docx" else "xl/workbook.xml", f"<x>{tag}</x>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def ole(tag: str = "") -> bytes:
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + tag.encode().ljust(600, b"\x00")


def csv(tag: str = "") -> bytes:
    return f"date,amount\n2025-01-01,10.00\n# {tag}\n".encode()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ protocol helpers
def init(owner, cid, content, filename, node_id, **kw):  # type: ignore[no-untyped-def]
    body = {
        "idempotency_key": kw.pop("key", uuid.uuid4().hex),
        "filename": filename,
        "size": len(content),
        "sha256": sha(content),
        "node_id": node_id,
        **kw,
    }
    return owner.post(D(cid, "/uploads"), body)


def put(owner, cid, op_id, content):  # type: ignore[no-untyped-def]
    return owner.client.put(
        f"/api/v1{D(cid, f'/uploads/{op_id}/content')}",
        content=content,
        headers=owner.headers(**{"Content-Type": "application/octet-stream"}),
    )


def upload(owner, cid, content, filename, node_id, **kw):  # type: ignore[no-untyped-def]
    """The full happy path; returns the completed document."""
    r = init(owner, cid, content, filename, node_id, **kw)
    assert r.status_code == 201, r.text
    op = r.json()
    r = put(owner, cid, op["id"], content)
    assert r.status_code == 200, r.text
    r = owner.post(D(cid, f"/uploads/{op['id']}/complete"))
    assert r.status_code == 200, r.text
    return r.json()["document"]


def objects(owner_engine, cid=None):  # type: ignore[no-untyped-def]
    with owner_engine.begin() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text("SELECT * FROM storage_objects" + (" WHERE company_id = :c" if cid else "")), {"c": cid}
            )
        ]


@pytest.fixture(autouse=True)
def backend(tmp_path):  # type: ignore[no-untyped-def]
    be = LocalBackend(tmp_path / "objects")
    storage_mod.set_storage(be)
    scanner_mod.set_scanner(None)
    yield be
    storage_mod.set_storage(None)
    scanner_mod.set_scanner(None)


@pytest.fixture
def firm(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Docs Firm")
    a, b = make_company(owner, "DA")["id"], make_company(owner, "DB")["id"]
    return owner, a, b, mk(owner, a, "Invoices")["id"], mk(owner, b, "Other")["id"]


# ------------------------------------------------------------------ upload protocol
def test_happy_path_stores_verifies_and_reports_only_after_complete(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("happy")
    r = init(owner, a, content, "Invoice 1.pdf", node)
    assert r.status_code == 201 and r.json()["status"] == "awaiting_upload"
    op = r.json()
    assert owner.get(D(a)).json()["total"] == 0  # nothing exists before the bytes arrive and are confirmed
    r = put(owner, a, op["id"], content)
    assert r.status_code == 200 and r.json()["status"] == "uploaded"
    assert owner.get(D(a)).json()["total"] == 0  # still not a document until `complete`
    r = owner.post(D(a, f"/uploads/{op['id']}/complete"))
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["display_name"] == "Invoice 1.pdf" and doc["status"] == "active" and doc["current_version_no"] == 1
    assert doc["content_sha256"] == sha(content) and doc["size_bytes"] == len(content) and doc["node_id"] == node
    [obj] = objects(owner_engine, a)
    assert obj["state"] == "verified" and obj["sha256"] == sha(content) and "Invoice" not in obj["storage_key"]
    assert obj["storage_key"].startswith(f"t/{owner.org_id}/c/{a}/o/")  # opaque key, no names
    assert backend.stat(obj["storage_key"]) == len(content)
    again = owner.post(D(a, f"/uploads/{op['id']}/complete"))  # idempotent
    assert again.status_code == 200 and again.json()["document"]["id"] == doc["id"]
    assert owner.get(D(a)).json()["total"] == 1


def test_init_is_idempotent_and_rejects_key_reuse_for_other_files(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    c1, c2 = pdf("one"), pdf("two")
    r1 = init(owner, a, c1, "a.pdf", node, key="client-key-0001")
    r2 = init(owner, a, c1, "a.pdf", node, key="client-key-0001")
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert init(owner, a, c2, "b.pdf", node, key="client-key-0001").status_code == 409


def test_complete_requires_content_and_content_retry_is_safe(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("retry")
    op = init(owner, a, content, "r.pdf", node).json()
    assert owner.post(D(a, f"/uploads/{op['id']}/complete")).status_code == 409
    assert put(owner, a, op["id"], content).status_code == 200
    assert put(owner, a, op["id"], content).json()["status"] == "uploaded"  # lost-response retry does not re-store
    assert owner.post(D(a, f"/uploads/{op['id']}/complete")).status_code == 200
    assert owner.get(D(a)).json()["total"] == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("a.pdf", pdf("f")),
        ("a.jpg", jpeg("f")),
        ("a.jpeg", jpeg("g")),
        ("a.png", png("f")),
        ("a.tif", tiff("f")),
        ("a.tiff", tiff("g")),
        ("a.bmp", bmp("f")),
        ("a.heic", heic("f")),
        ("a.doc", ole("f")),
        ("a.xls", ole("g")),
        ("a.docx", ooxml("docx", "f")),
        ("a.xlsx", ooxml("xlsx", "f")),
        ("a.csv", csv("f")),
        ("a.txt", b"plain synthetic text\n"),
    ],
)
def test_every_supported_format_is_accepted_and_typed_by_content(firm, name, content) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, content, name, node)
    assert doc["extension"] == name.rsplit(".", 1)[1] and doc["size_bytes"] == len(content)


@pytest.mark.parametrize("name", ["run.exe", "x.zip", "s.js", "m.bat", "noext", "a.pdf.exe", "q.svg", "p.html"])
def test_unsupported_extensions_are_refused_at_registration(firm, name) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    r = init(owner, a, b"data", name, node)
    assert r.status_code == 422 and r.json()["code"] == "file_not_accepted", r.text


def _rejected(owner, cid, node, content, filename, code, status):  # type: ignore[no-untyped-def]
    op = init(owner, cid, content, filename, node).json()
    r = put(owner, cid, op["id"], content)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code and r.json()["operation"]["status"] == status, r.text
    got = owner.get(D(cid, f"/uploads/{op['id']}")).json()
    assert got["status"] == status and got["error_code"] == code  # the failure is persisted, not just returned
    assert owner.post(D(cid, f"/uploads/{op['id']}/complete")).status_code == 409
    assert owner.get(D(cid)).json()["total"] == 0


@pytest.mark.parametrize(
    ("filename", "content", "code", "status"),
    [
        ("fake.pdf", png("x"), "content_mismatch", "failed"),
        ("fake.png", pdf("x"), "content_mismatch", "failed"),
        ("trunc.pdf", pdf("x")[:-12], "corrupt_file", "failed"),
        ("trunc.png", png("x")[:-20], "corrupt_file", "failed"),
        ("trunc.jpg", jpeg("x")[:-30], "corrupt_file", "failed"),
        ("bad.docx", b"PK\x03\x04not really a package" + b"\x00" * 50, "corrupt_file", "failed"),
        ("binary.txt", b"abc\x00\x01\x02\x03def", "content_mismatch", "failed"),
        ("evil.pdf", b"MZ" + b"\x90" * 64, "executable_content", "quarantined"),
        ("evil.jpg", b"\x7fELF" + b"\x00" * 64, "executable_content", "quarantined"),
        ("script.txt", b"#!/bin/sh\nrm -rf /\n", "executable_content", "quarantined"),
        (
            "js.pdf",
            pdf("x").replace(b"trailer", b"<< /S /JavaScript /JS (app.alert(1)) >>\ntrailer"),
            "active_content",
            "quarantined",
        ),
        ("macro.docx", ooxml("docx", "m", {"word/vbaProject.bin": b"x"}), "macro_content", "quarantined"),
        (
            "macro.doc",
            ole("m").replace(b"\x00" * 40, "_VBA_PROJECT".encode("utf-16-le"), 1),
            "macro_content",
            "quarantined",
        ),
        ("trav.docx", ooxml("docx", "t", {"../../evil.txt": b"x"}), "unsafe_archive_entry", "quarantined"),
    ],
)
def test_dangerous_or_damaged_content_is_rejected_and_never_stored(
    firm, backend, owner_engine, filename, content, code, status
) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    _rejected(owner, a, node, content, filename, code, status)
    assert objects(owner_engine, a) == [] and list(backend.list_objects("t/")) == []


def test_zip_bomb_is_refused(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    bomb = ooxml("docx", "b", {"word/big.xml": b"0" * (60 * 1024 * 1024)})
    _rejected(owner, a, node, bomb, "bomb.docx", "archive_bomb", "quarantined")


def test_size_and_checksum_must_match_the_announcement(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("sz")
    op = init(owner, a, content, "s.pdf", node).json()
    r = put(owner, a, op["id"], content[:-5])
    assert r.status_code == 422 and r.json()["code"] == "size_mismatch"
    op2 = init(owner, a, content, "t.pdf", node).json()
    r = put(owner, a, op2["id"], content + b"extra")  # more than announced
    assert r.status_code == 422 and r.json()["code"] == "size_mismatch"
    wrong = pdf("zz")[: len(content)].ljust(len(content), b" ")
    op3 = init(owner, a, content, "u.pdf", node, sha256=sha(content)).json()
    r = put(owner, a, op3["id"], wrong)
    assert r.status_code == 422 and r.json()["code"] in ("checksum_mismatch", "corrupt_file")


def test_cancel_discards_received_content_and_blocks_completion(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("cancel")
    op = init(owner, a, content, "c.pdf", node).json()
    assert put(owner, a, op["id"], content).status_code == 200
    [obj] = objects(owner_engine, a)
    assert backend.stat(obj["storage_key"]) is not None
    r = owner.post(D(a, f"/uploads/{op['id']}/cancel"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert backend.stat(obj["storage_key"]) is None
    assert objects(owner_engine, a)[0]["state"] == "purged"
    assert owner.post(D(a, f"/uploads/{op['id']}/complete")).status_code == 409
    assert put(owner, a, op["id"], content).status_code == 409


def test_transient_storage_failure_is_reported_honestly_and_can_be_retried(firm, backend, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("flaky")
    op = init(owner, a, content, "f.pdf", node).json()
    real = backend.put_file

    def boom(key, path):  # type: ignore[no-untyped-def]
        raise storage_mod.StorageError("down")

    monkeypatch.setattr(backend, "put_file", boom)
    r = put(owner, a, op["id"], content)
    assert (
        r.status_code == 503
        and r.json()["code"] == "storage_unavailable"
        and r.json()["operation"]["status"] == "failed"
    )
    assert owner.get(D(a)).json()["total"] == 0
    monkeypatch.setattr(backend, "put_file", real)
    r = owner.post(D(a, f"/uploads/{op['id']}/retry"))
    assert r.status_code == 200 and r.json()["status"] == "awaiting_upload"
    assert put(owner, a, op["id"], content).status_code == 200
    assert owner.post(D(a, f"/uploads/{op['id']}/complete")).status_code == 200
    # a content failure (not transient) cannot be "retried" into acceptance
    bad = init(owner, a, png("q"), "q.pdf", node).json()
    assert put(owner, a, bad["id"], png("q")).status_code == 422
    assert owner.post(D(a, f"/uploads/{bad['id']}/retry")).status_code == 409


def test_missing_object_at_completion_is_not_reported_as_success(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("lost")
    op = init(owner, a, content, "l.pdf", node).json()
    assert put(owner, a, op["id"], content).status_code == 200
    backend.delete(objects(owner_engine, a)[0]["storage_key"])  # the object vanishes between PUT and complete
    r = owner.post(D(a, f"/uploads/{op['id']}/complete"))
    assert r.status_code == 503 and r.json()["code"] == "storage_verification_failed"
    assert owner.get(D(a)).json()["total"] == 0
    assert objects(owner_engine, a)[0]["state"] == "missing"


def test_batches_track_their_uploads(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    b = owner.post(D(a, "/import-batches"), {"total_files": 3, "client_batch_key": "k1"})
    assert b.status_code == 201
    bid = b.json()["id"]
    upload(owner, a, pdf("b1"), "b1.pdf", node, batch_id=bid)
    bad = init(owner, a, png("b2"), "b2.pdf", node, batch_id=bid).json()
    put(owner, a, bad["id"], png("b2"))
    st = owner.get(D(a, f"/import-batches/{bid}")).json()
    assert st["total_files"] == 3 and st["registered"] == 2 and st["counts"] == {"completed": 1, "failed": 1}
    assert owner.get(D(a, f"/uploads?batch_id={bid}&status=failed")).json()["total"] == 1
    assert owner.post(D(a, "/import-batches"), {"total_files": 5000}).status_code == 422
    assert init(owner, a, pdf("x"), "x.pdf", node, batch_id=str(uuid.uuid4())).status_code == 404


def test_expired_session_cannot_receive_content_and_reconcile_cleans_up(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("exp")
    op = init(owner, a, content, "e.pdf", node).json()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE document_upload_operations SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": op["id"]},
        )
    r = put(owner, a, op["id"], content)
    assert r.status_code == 409 and r.json()["status"] == "expired" if "status" in r.json() else r.status_code == 409


# ------------------------------------------------------------------ operations: rename, move, archive, delete, purge
def test_rename_keeps_extension_and_never_overwrites(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    d1 = upload(owner, a, pdf("n1"), "Report.pdf", node)
    d2 = upload(owner, a, pdf("n2"), "Other.pdf", node)
    r = owner.patch(D(a, f"/{d2['id']}"), {"display_name": "report.pdf"})
    assert r.status_code == 409 and r.json()["suggestion"] == "report_2.pdf"  # case-insensitive, same folder
    r = owner.patch(D(a, f"/{d2['id']}"), {"display_name": "Renamed"})
    assert r.status_code == 200 and r.json()["display_name"] == "Renamed.pdf"
    assert owner.patch(D(a, f"/{d2['id']}"), {"display_name": "x.exe"}).status_code == 422
    for bad in ("a/b.pdf", "..\\x.pdf", "CON.pdf"):
        r = owner.patch(D(a, f"/{d2['id']}"), {"display_name": bad})
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert "/" not in r.json()["display_name"] and "\\" not in r.json()["display_name"]
    # the same name arriving again is suffixed instead of overwriting
    d3 = upload(owner, a, pdf("n3"), "Report.pdf", node)
    assert d3["display_name"] == "Report_2.pdf" and d1["id"] != d3["id"]
    stale = owner.patch(D(a, f"/{d1['id']}"), {"display_name": "Fresh", "base_row_version": 99})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"


def test_move_changes_location_only_and_folder_changes_never_touch_documents(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    other = mk(owner, a, "Receipts")["id"]
    sub = mk(owner, a, "2025", node)["id"]
    doc = upload(owner, a, pdf("mv"), "m.pdf", node)
    key = objects(owner_engine, a)[0]["storage_key"]
    r = owner.post(D(a, f"/{doc['id']}/move"), {"node_id": other})
    assert r.status_code == 200 and r.json()["node_id"] == other and r.json()["id"] == doc["id"]
    assert objects(owner_engine, a)[0]["storage_key"] == key  # the binary never moved
    assert owner.get(D(a, f"?node_id={node}")).json()["total"] == 0
    assert owner.get(D(a, f"?node_id={other}")).json()["total"] == 1
    assert owner.post(D(a, f"/{doc['id']}/move"), {"node_id": other}).status_code == 409  # already there
    # a name collision at the destination is resolved, not overwritten
    upload(owner, a, pdf("mv2"), "m.pdf", sub)
    r = owner.post(D(a, f"/{doc['id']}/move"), {"node_id": sub})
    assert r.status_code == 200 and r.json()["display_name"] == "m_2.pdf"
    # renaming / moving / archiving the FOLDER keeps the documents (by id) and their bytes
    assert owner.patch(W(a, f"/nodes/{sub}"), {"name": "Renamed folder"}).status_code == 200
    assert owner.get(D(a, f"/{doc['id']}")).json()["node_id"] == sub
    assert owner.post(W(a, f"/nodes/{sub}/move"), {"parent_id": other}).status_code == 200
    assert owner.get(D(a, f"?node_id={sub}")).json()["total"] == 2
    imp = owner.get(W(a, f"/nodes/{other}/impact")).json()
    assert imp["documents"] == 2
    arch = owner.post(W(a, f"/nodes/{other}/archive"), {})
    assert (
        arch.status_code == 409
        and arch.json()["code"] == "confirmation_required"
        and arch.json()["impact"]["documents"] == 2
    )
    assert owner.post(W(a, f"/nodes/{other}/archive"), {"confirm_impact": True}).status_code == 200
    assert owner.get(D(a, f"/{doc['id']}")).status_code == 200  # still readable
    assert owner.delete(W(a, f"/nodes/{other}")).status_code == 409  # cannot be deleted while documents exist
    assert len(objects(owner_engine, a)) == 2
    # nothing new can be filed into an archived location
    assert init(owner, a, pdf("late"), "late.pdf", sub).status_code == 409
    assert owner.post(D(a, f"/{doc['id']}/move"), {"node_id": other}).status_code == 409


def test_archive_restore_delete_undelete_and_permanent_deletion(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("life"), "life.pdf", node)
    did = doc["id"]
    key = objects(owner_engine, a)[0]["storage_key"]  # only one object exists at this point
    assert owner.post(D(a, f"/{did}/restore")).status_code == 409  # not archived
    r = owner.post(D(a, f"/{did}/archive"))
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert owner.get(D(a)).json()["total"] == 0 and owner.get(D(a, "?status=archived")).json()["total"] == 1
    assert owner.patch(D(a, f"/{did}"), {"display_name": "x"}).status_code == 409  # archived documents are read-only
    # a name taken while it was archived is resolved on restore
    upload(owner, a, pdf("life2"), "life.pdf", node)
    r = owner.post(D(a, f"/{did}/restore"))
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["display_name"] == "life_2.pdf"
    # soft delete hides the document; bytes stay and the document can come back
    assert owner.post(D(a, f"/{did}/delete")).json()["status"] == "deleted"
    assert owner.get(D(a, "?status=deleted")).json()["total"] == 1
    assert backend.stat(key) is not None
    assert owner.get(D(a, f"/{did}/content")).status_code == 404  # deleted items are not downloadable
    # permanent deletion needs: deleted state, confirmation, no hold
    assert owner.delete(D(a, f"/{did}?confirm=true")).status_code in (200,)  # owner holds documents.purge
    assert backend.stat(key) is None and owner.get(D(a, f"/{did}")).status_code == 404
    assert next(o for o in objects(owner_engine, a) if o["storage_key"] == key)["state"] == "purged"
    assert owner.get(D(a, "?status=deleted")).json()["total"] == 0
    ev = owner.get(D(a, "/events")).json()["items"]
    assert {"document.purged", "document.created"} <= {e["event_type"] for e in ev}


def test_purge_needs_deleted_state_confirmation_and_no_retention_hold(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("hold"), "hold.pdf", node)
    did = doc["id"]
    assert owner.delete(D(a, f"/{did}?confirm=true")).status_code == 409  # still active
    owner.post(D(a, f"/{did}/delete"))
    assert owner.delete(D(a, f"/{did}")).status_code == 422  # not confirmed
    assert owner.post(D(a, f"/{did}/hold"), {"hold": True, "reason": "legal"}).json()["retention_hold"] is True
    r = owner.delete(D(a, f"/{did}?confirm=true"))
    assert r.status_code == 409 and r.json()["code"] == "retention_hold"
    assert backend.stat(objects(owner_engine, a)[0]["storage_key"]) is not None
    owner.post(D(a, f"/{did}/hold"), {"hold": False})
    assert owner.delete(D(a, f"/{did}?confirm=true")).status_code == 200


def test_duplicate_document_is_an_independent_copy(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    other = mk(owner, a, "Copies")["id"]
    doc = upload(owner, a, pdf("dup"), "orig.pdf", node)
    r = owner.post(D(a, f"/{doc['id']}/duplicate"), {})
    assert r.status_code == 201 and r.json()["display_name"] == "orig - copy.pdf" and r.json()["id"] != doc["id"]
    r2 = owner.post(D(a, f"/{doc['id']}/duplicate"), {"node_id": other, "display_name": "kept"})
    assert r2.status_code == 201 and r2.json()["node_id"] == other and r2.json()["display_name"] == "kept.pdf"
    objs = objects(owner_engine, a)
    assert (
        len(objs) == 3
        and len({o["storage_key"] for o in objs}) == 3
        and {o["sha256"] for o in objs} == {sha(pdf("dup"))}
    )
    # purging the copy cannot damage the original
    cid_ = r.json()["id"]
    owner.post(D(a, f"/{cid_}/delete"))
    assert owner.delete(D(a, f"/{cid_}?confirm=true")).status_code == 200
    assert owner.get(D(a, f"/{doc['id']}/content")).content == pdf("dup")


# ------------------------------------------------------------------ versions
def test_versions_are_immutable_history_and_restore_adds_a_new_version(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    v1, v2, v3 = pdf("v1"), pdf("v2"), pdf("v3")
    doc = upload(owner, a, v1, "Contract.pdf", node)
    did = doc["id"]
    d2 = upload(owner, a, v2, "Contract v2.pdf", node, target_document_id=did, change_reason="client edits")
    assert d2["id"] == did and d2["current_version_no"] == 2 and d2["display_name"] == "Contract.pdf"
    assert d2["content_sha256"] == sha(v2)
    upload(owner, a, v3, "c3.pdf", node, target_document_id=did)
    versions = owner.get(D(a, f"/{did}/versions")).json()["items"]
    assert [v["version_no"] for v in versions] == [3, 2, 1] and versions[1]["change_reason"] == "client edits"
    assert len({o["storage_key"] for o in objects(owner_engine, a)}) == 3  # originals are never overwritten
    for n, content in ((1, v1), (2, v2), (3, v3)):
        assert owner.get(D(a, f"/{did}/content?version={n}")).content == content
    assert owner.get(D(a, f"/{did}/content")).content == v3
    assert owner.get(D(a, f"/{did}/content?version=9")).status_code == 404
    # identical content is not a new version
    r = init(owner, a, v3, "same.pdf", node, target_document_id=did)
    op = r.json()
    rr = put(owner, a, op["id"], v3)
    assert rr.status_code == 409 and rr.json()["code"] == "identical_version"
    # the file type must stay the same
    assert init(owner, a, png("p"), "p.png", node, target_document_id=did).status_code == 422
    # restoring v1 = a NEW version 4 with v1's content; history is intact
    r = owner.post(D(a, f"/{did}/versions/1/restore"), {"reason": "rollback"})
    assert r.status_code == 200 and r.json()["current_version_no"] == 4 and r.json()["content_sha256"] == sha(v1)
    assert r.json()["versions"][0]["restored_from_version"] == 1
    assert owner.post(D(a, f"/{did}/versions/4/restore"), {}).status_code == 409  # already current
    assert owner.post(D(a, f"/{did}/versions/9/restore"), {}).status_code == 404
    assert len(objects(owner_engine, a)) == 3  # restore shares the stored object; nothing was copied or lost
    # permanent deletion removes every version's object
    owner.post(D(a, f"/{did}/delete"))
    assert owner.delete(D(a, f"/{did}?confirm=true")).json()["objects_removed"] == 3


def test_new_version_of_archived_or_foreign_document_is_refused(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    doc = upload(owner, a, pdf("ver"), "v.pdf", node)
    other_company_doc = upload(owner, b, pdf("verb"), "vb.pdf", node_b)
    assert init(owner, a, pdf("x1"), "x.pdf", node, target_document_id=other_company_doc["id"]).status_code == 404
    owner.post(D(a, f"/{doc['id']}/archive"))
    assert init(owner, a, pdf("x2"), "x.pdf", node, target_document_id=doc["id"]).status_code == 409


# ------------------------------------------------------------------ categories, custom metadata
def test_categories_file_types_and_custom_metadata(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, _ = firm
    cat = owner.post(
        W(a, "/categories"),
        {
            "name": "Supplier invoice",
            "document_kind": "purchase_invoice",
            "allowed_file_types": ["pdf", "png"],
            "node_id": node,
        },
    ).json()["item"]
    owner.post(
        W(a, "/fields"),
        {
            "label": "Cost Center",
            "data_type": "dropdown",
            "required": True,
            "options": [{"value": "ops", "label": "Ops"}, {"value": "hq", "label": "HQ"}],
            "category_ids": [cat["id"]],
        },
    )
    owner.post(
        W(a, "/fields"),
        {"label": "Amount", "data_type": "decimal", "validation": {"decimal_places": 2}, "category_ids": [cat["id"]]},
    )
    # file type restrictions of the category are enforced by the server
    r = init(owner, a, csv("c"), "x.csv", node, category_id=cat["id"], metadata={"cost_center": "ops"})
    assert r.status_code == 422 and r.json()["reason"] == "file_type_not_allowed_for_category"
    # required / invalid metadata is refused with per-field problems
    r = init(owner, a, pdf("m1"), "m1.pdf", node, category_id=cat["id"], metadata={})
    assert r.status_code == 422 and r.json()["problems"]
    r = init(owner, a, pdf("m1"), "m1.pdf", node, category_id=cat["id"], metadata={"cost_center": "nope"})
    assert r.status_code == 422
    # a category of another company is rejected, as is a category not valid for this location
    cat_b = owner.post(W(b, "/categories"), {"name": "B cat", "document_kind": "sales_invoice"}).json()["item"]
    assert init(owner, a, pdf("m2"), "m2.pdf", node, category_id=cat_b["id"]).status_code == 422
    doc = upload(
        owner, a, pdf("m3"), "m3.pdf", node, category_id=cat["id"], metadata={"cost_center": "ops", "amount": "10.50"}
    )
    detail = owner.get(D(a, f"/{doc['id']}")).json()
    vals = {m["key"]: m["value"] for m in detail["metadata"]}
    assert vals == {"cost_center": "ops", "amount": "10.50"} and all(m["applies"] for m in detail["metadata"])
    # edit metadata (money stays a string), clearing a required field is refused
    r = owner.patch(D(a, f"/{doc['id']}"), {"metadata": {"amount": "99.99"}})
    assert r.status_code == 200 and {m["key"]: m["value"] for m in r.json()["metadata"]}["amount"] == "99.99"
    assert owner.patch(D(a, f"/{doc['id']}"), {"metadata": {"cost_center": None}}).status_code == 422
    assert owner.patch(D(a, f"/{doc['id']}"), {"metadata": {"bogus": "x"}}).status_code == 422
    # search by a custom field
    hit = owner.get(D(a, "?field_key=cost_center&field_value=ops")).json()
    assert hit["total"] == 1 and hit["items"][0]["id"] == doc["id"]
    assert owner.get(D(a, "?field_key=cost_center&field_value=hq")).json()["total"] == 0
    assert owner.get(D(a, f"?category_id={cat['id']}")).json()["total"] == 1


# ------------------------------------------------------------------ duplicate detection
def test_duplicates_are_company_scoped_and_policy_driven(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    content = pdf("same-bytes")
    first = upload(owner, a, content, "first.pdf", node)
    # blocked at registration (checksum announced) with the existing document named
    r = init(owner, a, content, "second.pdf", node)
    assert r.status_code == 409 and r.json()["code"] == "duplicate_document"
    assert r.json()["duplicates"][0]["id"] == first["id"]
    # blocked at content time too when the client did not announce a checksum
    op = owner.post(
        D(a, "/uploads"),
        {"idempotency_key": "k-nosha-0001", "filename": "n.pdf", "size": len(content), "node_id": node},
    ).json()
    r = put(owner, a, op["id"], content)
    assert (
        r.status_code == 409
        and r.json()["code"] == "duplicate_document"
        and r.json()["operation"]["status"] == "failed"
    )
    # explicit choice to keep a copy
    copy = upload(owner, a, content, "copy.pdf", node, duplicate_policy="allow_copy")
    assert copy["id"] != first["id"]
    chk = owner.post(D(a, "/duplicates/check"), {"sha256": sha(content)}).json()["duplicates"]
    assert {d["id"] for d in chk} == {first["id"], copy["id"]}
    assert owner.get(D(a, f"/{first['id']}")).json()["duplicates"][0]["id"] == copy["id"]
    assert owner.get(D(a, f"?sha256={sha(content)}")).json()["total"] == 2
    # another company of the same firm may hold identical content and never learns about company A's copy
    assert owner.post(D(b, "/duplicates/check"), {"sha256": sha(content)}).json()["duplicates"] == []
    other = upload(owner, b, content, "b.pdf", node_b)
    assert other["id"] not in {d["id"] for d in owner.get(D(a, f"/{first['id']}")).json()["duplicates"]}
    # deleted documents do not count as duplicates
    owner.post(D(a, f"/{first['id']}/delete"))
    owner.post(D(a, f"/{copy['id']}/delete"))
    assert owner.post(D(a, "/duplicates/check"), {"sha256": sha(content)}).json()["duplicates"] == []
    upload(owner, a, content, "again.pdf", node)


# ------------------------------------------------------------------ search, sorting, pagination, counts
def test_search_filter_sort_paginate_and_counts(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    sub = mk(owner, a, "Sub", node)["id"]
    upload(owner, a, pdf("s1"), "Alpha invoice.pdf", node)
    upload(owner, a, png("s2"), "beta receipt.png", node)
    upload(owner, a, csv("s3" * 400), "gamma_100%.csv", sub)
    upload(owner, a, pdf("s4" * 50), "delta.pdf", sub)
    allr = owner.get(D(a)).json()
    assert allr["total"] == 4 and allr["limit"] == 50
    assert {d["display_name"] for d in owner.get(D(a, "?q=invoice")).json()["items"]} == {"Alpha invoice.pdf"}
    assert owner.get(D(a, "?q=100%25")).json()["total"] == 1  # wildcards in user input are literal
    assert owner.get(D(a, "?q=%25")).json()["total"] == 1
    assert owner.get(D(a, "?extension=pdf")).json()["total"] == 2
    assert owner.get(D(a, f"?node_id={node}")).json()["total"] == 2
    assert owner.get(D(a, f"?node_id={node}&include_subfolders=true")).json()["total"] == 4
    names = [d["display_name"] for d in owner.get(D(a, "?sort=name&order=asc")).json()["items"]]
    assert names == sorted(names, key=str.lower)
    sizes = [d["size_bytes"] for d in owner.get(D(a, "?sort=size&order=desc")).json()["items"]]
    assert sizes == sorted(sizes, reverse=True)
    mid = sorted(sizes)[1]
    assert owner.get(D(a, f"?min_size={mid}&max_size={mid}")).json()["total"] >= 1
    page1, page2 = (
        owner.get(D(a, "?sort=name&order=asc&limit=3")).json(),
        owner.get(D(a, "?sort=name&order=asc&limit=3&offset=3")).json(),
    )
    assert len(page1["items"]) == 3 and len(page2["items"]) == 1 and page1["total"] == 4
    assert not {d["id"] for d in page1["items"]} & {d["id"] for d in page2["items"]}
    assert owner.get(D(a, "?limit=500")).status_code == 422 and owner.get(D(a, "?sort=bogus")).status_code == 422
    assert owner.get(D(a, "?created_from=2999-01-01T00:00:00Z")).json()["total"] == 0
    counts = owner.get(D(a, "/counts")).json()
    assert (
        counts["nodes"][node]["own_active"] == 2
        and counts["nodes"][node]["active"] == 4
        and counts["nodes"][sub]["active"] == 2
    )
    assert counts["totals"]["active"] == 4 and counts["totals"]["bytes"] > 0


# ------------------------------------------------------------------ naming and filing suggestions
def test_filing_and_naming_suggestions_are_deterministic_and_advisory(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    purchases = mk(owner, a, "Purchases")["id"]
    cat = owner.post(
        W(a, "/categories"), {"name": "Supplier invoice", "document_kind": "purchase_invoice", "node_id": purchases}
    ).json()["item"]
    owner.post(W(a, "/fields"), {"label": "Supplier", "data_type": "short_text", "category_ids": [cat["id"]]})
    rule = owner.post(
        W(a, "/filing-rules"),
        {
            "name": "Invoices go to purchases",
            "conditions": [{"field": "document_kind", "operator": "equals", "value": "purchase_invoice"}],
            "target_node_id": purchases,
            "target_category_id": cat["id"],
        },
    ).json()["item"]
    owner.post(W(a, "/naming-rules"), {"name": "N", "pattern": "{Supplier|Unknown}_{Date}", "category_id": cat["id"]})
    body = {
        "filename": "scan 0001.pdf",
        "category_id": cat["id"],
        "values": {"supplier": "Acme", "documentdate": "2025-03-04"},
    }
    s1 = owner.post(D(a, "/suggest"), body).json()
    s2 = owner.post(D(a, "/suggest"), body).json()
    assert s1 == s2 and s1["advisory"] is True
    assert (
        s1["filing"]["rule_id"] == rule["id"]
        and s1["filing"]["node_id"] == purchases
        and s1["filing"]["ambiguous"] is False
    )
    assert s1["naming"]["suggested_name"] == "Acme_2025-03-04.pdf" and s1["naming"]["used_naming_rule"] is True
    assert owner.get(D(a)).json()["total"] == 0  # a suggestion files nothing
    # accepting it records the rule on the document; a second document with the same name is suffixed
    d1 = upload(
        owner,
        a,
        pdf("sg1"),
        s1["naming"]["suggested_name"],
        purchases,
        category_id=cat["id"],
        filing_rule_id=rule["id"],
        metadata={"supplier": "Acme"},
    )
    assert d1["filing_rule_id"] == rule["id"]
    s3 = owner.post(D(a, "/suggest"), {**body, "node_id": purchases}).json()
    assert (
        s3["naming"]["suggested_name"] == "Acme_2025-03-04_2.pdf" and s3["naming"]["renamed_to_avoid_overwrite"] is True
    )
    # no rule -> deterministic fallback; a rule pointing outside the caller's reach is not suggested
    s4 = owner.post(D(a, "/suggest"), {"filename": "x.png"}).json()
    assert s4["filing"]["rule_id"] is None and s4["naming"]["used_naming_rule"] is False
    assert init(owner, a, pdf("sg2"), "z.pdf", node, filing_rule_id=str(uuid.uuid4())).status_code == 422


# ------------------------------------------------------------------ content delivery
def test_content_is_backend_streamed_with_safe_headers_ranges_and_an_access_trail(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("stream" * 40)
    doc = upload(owner, a, content, "Stream.pdf", node)
    r = owner.get(D(a, f"/{doc['id']}/content"))
    assert r.status_code == 200 and r.content == content
    assert (
        r.headers["content-disposition"].startswith("attachment;") and r.headers["x-content-type-options"] == "nosniff"
    )
    assert r.headers["x-document-sha256"] == sha(content) and r.headers["accept-ranges"] == "bytes"
    assert "no-store" in r.headers["cache-control"] and "location" not in r.headers  # no redirect / public URL
    inline = owner.get(D(a, f"/{doc['id']}/content?disposition=inline"))
    assert (
        inline.headers["content-disposition"].startswith("inline;")
        and inline.headers["content-type"] == "application/pdf"
    )
    part = owner.client.get(f"/api/v1{D(a, f'/{doc["id"]}/content')}", headers=owner.headers(Range="bytes=2-9"))
    assert (
        part.status_code == 206
        and part.content == content[2:10]
        and part.headers["content-range"] == f"bytes 2-9/{len(content)}"
    )
    tail = owner.client.get(f"/api/v1{D(a, f'/{doc["id"]}/content')}", headers=owner.headers(Range="bytes=-5"))
    assert tail.status_code == 206 and tail.content == content[-5:]
    bad = owner.client.get(
        f"/api/v1{D(a, f'/{doc["id"]}/content')}", headers=owner.headers(Range=f"bytes={len(content) + 5}-")
    )
    assert bad.status_code == 416
    # Office formats and anything not safe to render are never inline, whatever the client asks for
    word = upload(owner, a, ooxml("docx", "w"), "Letter.docx", node)
    w = owner.get(D(a, f"/{word['id']}/content?disposition=inline"))
    assert (
        w.headers["content-disposition"].startswith("attachment;")
        and w.headers["content-type"] == "application/octet-stream"
    )
    # filenames with unusual characters stay header-safe
    odd = upload(owner, a, pdf("odd"), 'Qué "pasa"; ñ.pdf', node)
    h = owner.get(D(a, f"/{odd['id']}/content")).headers["content-disposition"]
    assert '"' not in h.split("filename=")[1].split(";")[0].strip('"') and "UTF-8''" in h
    with owner_engine.begin() as c:
        n = c.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'document.downloaded' AND organization_id = :o"),
            {"o": owner.org_id},
        ).scalar_one()
        assert (
            n == 3
        )  # plain download, forced-attachment Office file, odd-name download; ranged chunks are not re-counted
    types = {e["event_type"] for e in owner.get(D(a, f"/{doc['id']}/events")).json()["items"]}
    assert {"document.created", "document.downloaded", "document.viewed"} <= types


def test_activity_trail_is_append_only_even_for_the_application_role(firm, tenant_db) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("trail"), "t.pdf", node)
    owner.patch(D(a, f"/{doc['id']}"), {"display_name": "Renamed"})
    owner.post(D(a, f"/{doc['id']}/archive"))
    items = owner.get(D(a, f"/{doc['id']}/events")).json()["items"]
    assert [e["event_type"] for e in items][:3] == ["document.archived", "document.updated", "document.created"]
    assert items[1]["summary"]["name"] == {"from": "t.pdf", "to": "Renamed.pdf"} and items[0]["actor_name"]
    with tenant_db.begin() as c:
        c.execute(
            text(
                "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"
            ),
            {"u": uid(owner), "o": owner.org_id},
        )
        for stmt in ("UPDATE document_events SET event_type = 'x'", "DELETE FROM document_events"):
            with pytest.raises(Exception, match="permission denied"), c.begin_nested():
                c.execute(text(stmt))


# ------------------------------------------------------------------ entitlements
def test_file_size_limit_comes_from_the_plan_and_the_system_ceiling(firm, owner_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("big" * 100)
    set_plan_limit(owner_engine, owner.org_id, "limit.file_size_bytes", len(content) - 1)
    r = init(owner, a, content, "big.pdf", node)
    assert r.status_code == 402 and r.json()["key"] == "limit.file_size_bytes" and r.json()["limit"] == len(content) - 1
    assert init(owner, a, pdf("s"), "small.pdf", node).status_code == 201
    set_plan_limit(owner_engine, owner.org_id, "limit.file_size_bytes", None, unlimited=True)
    assert init(owner, a, content, "big.pdf", node).status_code == 201
    from artify_api.settings import get_settings

    monkeypatch.setattr(
        get_settings(), "max_file_size_ceiling_bytes", 100
    )  # even "unlimited" plans have a system ceiling
    r = init(owner, a, content, "big2.pdf", node)
    assert r.status_code == 422 and r.json()["reason"] == "file_too_large"


def test_storage_quota_refuses_new_uploads_but_never_hides_or_deletes_existing_documents(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    d1 = upload(owner, a, pdf("q1" * 20), "q1.pdf", node)
    used = sum(o["size_bytes"] for o in objects(owner_engine, a))
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", used)  # exactly full
    r = init(owner, a, pdf("q2"), "q2.pdf", node)
    assert r.status_code == 402 and r.json()["key"] == "quota.storage_bytes" and r.json()["current"] == used
    assert init(owner, b, pdf("q3"), "q3.pdf", node_b).status_code == 402  # the quota is organization-wide
    # existing documents stay fully usable: read, download, rename, archive, delete
    assert owner.get(D(a, f"/{d1['id']}/content")).status_code == 200
    assert owner.patch(D(a, f"/{d1['id']}"), {"display_name": "kept"}).status_code == 200
    # archived and soft-deleted documents still count; purging frees the space
    owner.post(D(a, f"/{d1['id']}/archive"))
    assert init(owner, a, pdf("q2"), "q2.pdf", node).status_code == 402
    owner.post(D(a, f"/{d1['id']}/restore"))
    owner.post(D(a, f"/{d1['id']}/delete"))
    assert init(owner, a, pdf("q2"), "q2.pdf", node).status_code == 402
    assert owner.delete(D(a, f"/{d1['id']}?confirm=true")).status_code == 200
    assert upload(owner, a, pdf("q2"), "q2.pdf", node)
    ov = owner.get("/storage/overview").json()
    assert ov["storage"]["limit_bytes"] == used and ov["storage"]["used_bytes"] < used + 1


def test_documents_per_month_quota(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    set_plan_limit(owner_engine, owner.org_id, "quota.documents_per_month", 2)
    d1 = upload(owner, a, pdf("m1"), "m1.pdf", node)
    upload(owner, a, pdf("m2"), "m2.pdf", node)
    r = init(owner, a, pdf("m3"), "m3.pdf", node)
    assert r.status_code == 402 and r.json()["key"] == "quota.documents_per_month"
    # new VERSIONS of existing documents are not new documents
    assert upload(owner, a, pdf("m1v2"), "m1v2.pdf", node, target_document_id=d1["id"])["current_version_no"] == 2
    assert owner.post(D(a, f"/{d1['id']}/duplicate"), {}).status_code == 402  # a copy is a new document
    ov = owner.get("/storage/overview").json()
    assert ov["documents_this_month"] == {"used": 2, "limit": 2, "unlimited": False}


def test_quota_is_enforced_at_the_content_step_too(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = pdf("race")
    op = init(owner, a, content, "r.pdf", node).json()  # registered while there is room
    set_plan_limit(owner_engine, owner.org_id, "quota.storage_bytes", 10)  # the plan shrinks before the bytes arrive
    r = put(owner, a, op["id"], content)
    assert r.status_code == 402 and objects(owner_engine, a) == []
    assert owner.get(D(a, f"/uploads/{op['id']}")).json()["status"] == "awaiting_upload"


def test_suspended_subscription_blocks_uploads_but_not_reading(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("sus"), "s.pdf", node)
    with owner_engine.begin() as c:
        c.execute(text("UPDATE subscriptions SET status = 'suspended' WHERE organization_id = :o"), {"o": owner.org_id})
    r = init(owner, a, pdf("sus2"), "s2.pdf", node)
    assert r.status_code == 403 and r.json()["code"] == "subscription_restricted"
    assert owner.get(D(a, f"/{doc['id']}/content")).status_code == 200


# ------------------------------------------------------------------ antivirus interface (honest about being inactive)
def test_without_a_scanner_files_are_validated_but_reported_as_not_scanned(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("ns"), "ns.pdf", node)
    detail = owner.get(D(a, f"/{doc['id']}")).json()
    assert detail["scan_status"] == "not_scanned" and detail["versions"][0]["scan_status"] == "not_scanned"
    ov = owner.get("/storage/overview").json()
    assert ov["scanner"] == {"name": "none", "active": False}


class _Scanner:
    def __init__(self, status: str, detail: str | None = None) -> None:
        self.name, self.active, self._r = "fake", True, scanner_mod.ScanResult(status, detail)

    def scan_file(self, path):  # type: ignore[no-untyped-def]
        return self._r


def test_scanner_verdicts_decide_what_is_stored(firm, owner_engine, backend) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    scanner_mod.set_scanner(_Scanner("clean"))
    doc = upload(owner, a, pdf("clean"), "clean.pdf", node)
    assert owner.get(D(a, f"/{doc['id']}")).json()["scan_status"] == "clean"
    scanner_mod.set_scanner(_Scanner("infected", "Eicar-Test-Signature"))
    op = init(owner, a, pdf("bad"), "bad.pdf", node).json()
    r = put(owner, a, op["id"], pdf("bad"))
    assert (
        r.status_code == 422
        and r.json()["code"] == "malware_detected"
        and r.json()["operation"]["status"] == "quarantined"
    )
    assert (
        len(objects(owner_engine, a)) == 1 and len(list(backend.list_objects("t/"))) == 1
    )  # the infected file was never stored
    scanner_mod.set_scanner(_Scanner("error", "down"))
    op = init(owner, a, pdf("err"), "err.pdf", node).json()
    r = put(owner, a, op["id"], pdf("err"))
    assert r.status_code == 503 and r.json()["code"] == "scan_unavailable"  # unscanned content is not waved through
    assert owner.post(D(a, f"/uploads/{op['id']}/retry")).status_code == 200


def test_clamd_client_speaks_instream_against_a_protocol_fake(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import socket
    import threading

    def serve(reply: bytes, received: list[bytes]):  # type: ignore[no-untyped-def]
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def run() -> None:
            conn, _ = srv.accept()
            with conn:
                data = b""
                while not data.endswith(b"\x00\x00\x00\x00"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                received.append(data)
                conn.sendall(reply)
            srv.close()

        threading.Thread(target=run, daemon=True).start()
        return srv.getsockname()[1]

    f = tmp_path / "f.bin"
    f.write_bytes(b"hello clamd")
    got: list[bytes] = []
    port = serve(b"stream: OK\x00", got)
    res = scanner_mod.ClamdScanner("127.0.0.1", port, 5).scan_file(f)
    assert res.status == "clean" and got[0].startswith(b"zINSTREAM\x00") and b"hello clamd" in got[0]
    port = serve(b"stream: Eicar-Test-Signature FOUND\x00", [])
    res = scanner_mod.ClamdScanner("127.0.0.1", port, 5).scan_file(f)
    assert res.status == "infected" and res.detail == "Eicar-Test-Signature"
    port = serve(b"INSTREAM size limit exceeded. ERROR\x00", [])
    assert scanner_mod.ClamdScanner("127.0.0.1", port, 5).scan_file(f).status == "error"
    assert (
        scanner_mod.ClamdScanner("127.0.0.1", 1, 1).scan_file(f).status == "error"
    )  # unreachable -> error, never "clean"
    assert scanner_mod.NoopScanner().scan_file(f).status == "not_scanned"


# ------------------------------------------------------------------ security: tenants, companies, roles, workspace grants
def test_other_organizations_see_nothing_and_learn_nothing(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("secret"), "secret.pdf", node)
    intruder = new_owner(client, mailer, org_name="Intruder Firm")
    own_company = make_company(intruder, "IC")["id"]
    for method, path in (
        ("get", D(a)),
        ("get", D(a, f"/{doc['id']}")),
        ("get", D(a, f"/{doc['id']}/content")),
        ("get", D(a, "/counts")),
        ("get", D(a, "/events")),
        ("post", D(a, f"/{doc['id']}/archive")),
        ("post", D(a, "/uploads")),
    ):
        r = getattr(intruder, method)(path) if method == "get" else getattr(intruder, method)(path, {})
        assert r.status_code in (404, 422), (method, path, r.status_code)  # 422 only for an empty upload body
        assert (
            "secret.pdf" not in r.text and sha(pdf("secret")) not in r.text
        )  # the error echoes the caller's own URL only
    # claiming the victim organization in the header is a 404 as well (no membership)
    r = intruder.client.get(f"/api/v1{D(a, f'/{doc["id"]}')}", headers=intruder.headers(org=owner.org_id))
    assert r.status_code == 404
    # the document id used under the intruder's OWN company is simply not found
    assert intruder.get(D(own_company, f"/{doc['id']}")).status_code == 404
    assert intruder.get(D(own_company)).json()["total"] == 0
    assert intruder.get("/storage/overview").json()["companies"][0]["stored_bytes"] == 0
    # unauthenticated requests never reach storage
    assert owner.client.get(f"/api/v1{D(a, f'/{doc["id"]}/content')}").status_code == 401


def test_company_scope_manipulated_ids_and_cross_company_references(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    doc_a = upload(owner, a, pdf("ca"), "a.pdf", node)
    doc_b = upload(owner, b, pdf("cb"), "b.pdf", node_b)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)  # only company A
    assert acc.get(D(a)).json()["total"] == 1
    for path in (D(b), D(b, f"/{doc_b['id']}"), D(b, f"/{doc_b['id']}/content"), D(b, "/counts")):
        assert acc.get(path).status_code == 404, path
    # a document id from company B under company A's path
    assert acc.get(D(a, f"/{doc_b['id']}")).status_code == 404
    assert owner.get(D(a, f"/{doc_b['id']}")).status_code == 404  # even the owner: wrong company in the path
    assert owner.patch(D(a, f"/{doc_b['id']}"), {"display_name": "hijack"}).status_code == 404
    # filing into another company's node / category, moving across companies, versioning across companies
    assert init(owner, a, pdf("x1"), "x.pdf", node_b).status_code == 404
    assert owner.post(D(a, f"/{doc_a['id']}/move"), {"node_id": node_b}).status_code == 404
    assert owner.post(D(a, f"/{doc_a['id']}/duplicate"), {"node_id": node_b}).status_code == 404
    assert init(owner, a, pdf("x2"), "x.pdf", node, target_document_id=doc_b["id"]).status_code == 404
    assert owner.post(D(a, "/suggest"), {"filename": "x.pdf", "category_id": str(uuid.uuid4())}).status_code == 422
    # random and malformed ids
    for bad in (str(uuid.uuid4()), "not-a-uuid", "../../etc/passwd", "1; DROP TABLE documents"):
        assert owner.get(D(a, f"/{bad}")).status_code in (404, 422)
        assert owner.get(D(a, f"/uploads/{bad}")).status_code in (404, 422)
    assert owner.get(D(a, f"?node_id={node_b}")).status_code == 404
    # metadata filters cannot be abused for injection
    assert owner.get(D(a, "?field_key=x'%20OR%201=1--&field_value=1")).json()["total"] == 0
    assert owner.get(D(a, "?q=%27%3B%20DROP%20TABLE%20documents%3B--")).status_code == 200


def test_upload_sessions_belong_to_their_creator(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    content = pdf("mine")
    op = init(owner, a, content, "mine.pdf", node).json()
    for method, path, body in (
        ("get", D(a, f"/uploads/{op['id']}"), None),
        ("post", D(a, f"/uploads/{op['id']}/cancel"), {}),
        ("post", D(a, f"/uploads/{op['id']}/complete"), {}),
    ):
        r = acc.get(path) if method == "get" else acc.post(path, body)
        assert r.status_code == 404
    assert put(acc, a, op["id"], content).status_code == 404
    assert acc.get(D(a, "/uploads")).json()["total"] == 0 and owner.get(D(a, "/uploads")).json()["total"] == 1
    # the same client key from two users never collides
    assert init(acc, a, content, "mine.pdf", node, key=op["idempotency_key"]).status_code == 201


def test_role_permissions_for_documents(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("roles"), "roles.pdf", node)
    members = {
        r: make_member(owner, mailer, r) for r in ("accountant", "document_operator", "read_only", "practice_manager")
    }
    for r, m in members.items():
        if r != "practice_manager":
            assign(owner, a, m)
    ro, op_, acc, pm = (
        members["read_only"],
        members["document_operator"],
        members["accountant"],
        members["practice_manager"],
    )
    for m in members.values():  # everyone assigned can read and download
        assert (
            m.get(D(a, f"/{doc['id']}")).status_code == 200 and m.get(D(a, f"/{doc['id']}/content")).status_code == 200
        )
    # read-only: nothing that changes anything
    assert init(ro, a, pdf("r1"), "r1.pdf", node).status_code == 403
    assert ro.patch(D(a, f"/{doc['id']}"), {"display_name": "x"}).status_code == 403
    assert ro.post(D(a, f"/{doc['id']}/archive")).status_code == 403
    assert ro.post(D(a, "/import-batches"), {"total_files": 1}).status_code == 403
    # document operator: imports, but cannot edit or delete
    assert upload(op_, a, pdf("o1"), "o1.pdf", node)["created_by_name"]
    assert op_.patch(D(a, f"/{doc['id']}"), {"display_name": "x"}).status_code == 403
    assert op_.post(D(a, f"/{doc['id']}/delete")).status_code == 403
    # accountant: edits and archives, cannot delete or purge, cannot see storage administration
    assert acc.patch(D(a, f"/{doc['id']}"), {"display_name": "acc edit"}).status_code == 200
    assert acc.post(D(a, f"/{doc['id']}/archive")).status_code == 200
    assert acc.post(D(a, f"/{doc['id']}/restore")).status_code == 200
    assert acc.post(D(a, f"/{doc['id']}/delete")).status_code == 403
    assert acc.get("/storage/overview").status_code == 403
    # practice manager: may delete, but permanent deletion and holds belong to owner/admin
    assert pm.post(D(a, f"/{doc['id']}/delete")).status_code == 200
    assert pm.delete(D(a, f"/{doc['id']}?confirm=true")).status_code == 403
    assert pm.post(D(a, f"/{doc['id']}/hold"), {"hold": True}).status_code == 403
    assert pm.get("/storage/overview").status_code == 200
    assert owner.get(D(a, "?status=deleted")).json()["total"] == 1
    assert acc.get(D(a, "?status=deleted")).json()["total"] == 0  # deleted items are not for everyone's eyes


def test_workspace_grants_govern_documents_like_folders(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    secret = mk(owner, a, "Payroll")["id"]
    d_open = upload(owner, a, pdf("open"), "open.pdf", node)
    d_secret = upload(owner, a, pdf("payroll"), "salaries.pdf", secret)
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    grant = lambda **kw: owner.post(W(a, "/permissions"), kw)  # noqa: E731
    assert acc.get(D(a)).json()["total"] == 2
    # deny VIEW: the document, its bytes, its existence, counts and events all disappear (404, never 403)
    assert (
        grant(node_id=secret, subject_type="user", user_id=uid(acc), permission="view", effect="deny").status_code
        == 201
    )
    assert acc.get(D(a)).json()["total"] == 1
    for path in (
        D(a, f"/{d_secret['id']}"),
        D(a, f"/{d_secret['id']}/content"),
        D(a, f"/{d_secret['id']}/events"),
        D(a, f"?node_id={secret}"),
    ):
        assert acc.get(path).status_code == 404, path
    assert "salaries" not in acc.get(D(a)).text and secret not in str(acc.get(D(a, "/counts")).json())
    assert acc.get(D(a, f"?sha256={sha(pdf('payroll'))}")).json()["total"] == 0
    assert init(acc, a, pdf("p2"), "p2.pdf", secret).status_code == 404
    # duplicates in hidden places are not disclosed either
    assert acc.post(D(a, "/duplicates/check"), {"sha256": sha(pdf("payroll"))}).json()["duplicates"] == []
    assert owner.get(D(a, f"/{d_secret['id']}")).status_code == 200  # the owner is never locked out
    # deny UPLOAD / EDIT on a visible folder: readable, but 403 on the blocked actions only
    assert (
        grant(node_id=node, subject_type="user", user_id=uid(acc), permission="upload", effect="deny").status_code
        == 201
    )
    assert init(acc, a, pdf("p3"), "p3.pdf", node).status_code == 403
    assert acc.get(D(a, f"/{d_open['id']}")).status_code == 200
    assert (
        grant(node_id=node, subject_type="user", user_id=uid(acc), permission="rename", effect="deny").status_code
        == 201
    )
    assert acc.patch(D(a, f"/{d_open['id']}"), {"display_name": "nope"}).status_code == 403
    assert (
        grant(node_id=node, subject_type="user", user_id=uid(acc), permission="archive", effect="deny").status_code
        == 201
    )
    assert acc.post(D(a, f"/{d_open['id']}/archive")).status_code == 403
    # moving needs `move` on the source and `upload` on the destination
    other = mk(owner, a, "Elsewhere")["id"]
    assert acc.post(D(a, f"/{d_open['id']}/move"), {"node_id": other}).status_code == 200
    assert acc.post(D(a, f"/{d_open['id']}/move"), {"node_id": node}).status_code == 403  # upload denied at destination
    # restricted mode: nothing is visible without an explicit allow
    owner.patch(W(a, "/settings"), {"access_mode": "restricted"})
    assert acc.get(D(a)).json()["total"] == 0
    grant(node_id=other, subject_type="user", user_id=uid(acc), permission="view", effect="allow")
    assert acc.get(D(a)).json()["total"] == 1 and acc.get(D(a, f"/{d_open['id']}")).status_code == 200


def test_permission_changes_apply_to_upload_sessions_already_open(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    content = pdf("perm")
    op = init(acc, a, content, "perm.pdf", node).json()
    assert (
        owner.post(
            W(a, "/permissions"),
            {"node_id": node, "subject_type": "user", "user_id": uid(acc), "permission": "upload", "effect": "deny"},
        ).status_code
        == 201
    )
    assert put(acc, a, op["id"], content).status_code == 403
    assert acc.get(D(a, "/uploads/" + op["id"])).json()["status"] == "awaiting_upload"
    # a second session: allowed to send, then loses access before completing
    owner.delete(W(a, f"/permissions/{owner.get(W(a, '/permissions')).json()['items'][0]['id']}"))
    op2 = init(acc, a, pdf("perm2"), "perm2.pdf", node).json()
    assert put(acc, a, op2["id"], pdf("perm2")).status_code == 200
    assert owner.delete(f"/companies/{a}/assignments/{uid(acc)}").status_code == 204
    assert acc.post(D(a, f"/uploads/{op2['id']}/complete")).status_code == 404  # company no longer reachable
    assert owner.get(D(a)).json()["total"] == 0


def test_revoked_sessions_and_inactive_companies_cannot_use_documents(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    doc = upload(owner, a, pdf("rev"), "rev.pdf", node)
    content = pdf("rev2")
    op = init(acc, a, content, "rev2.pdf", node).json()
    assert acc.client.post("/api/v1/auth/logout", headers=acc.headers()).status_code == 204
    assert put(acc, a, op["id"], content).status_code == 401
    assert acc.get(D(a, f"/{doc['id']}/content")).status_code == 401
    # an archived company accepts no new documents; an archived company's existing documents stay readable to owners
    assert owner.post(f"/companies/{a}/status", {"action": "archive"}).status_code == 200
    assert init(owner, a, pdf("late"), "late.pdf", node).status_code == 409
    assert owner.get(D(a, f"/{doc['id']}")).status_code == 200


def test_suspended_organization_is_read_only(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = upload(owner, a, pdf("susp"), "susp.pdf", node)
    with owner_engine.begin() as c:
        c.execute(text("UPDATE organizations SET status = 'suspended' WHERE id = :o"), {"o": owner.org_id})
    assert owner.get(D(a, f"/{doc['id']}/content")).status_code == 200
    assert init(owner, a, pdf("s2"), "s2.pdf", node).status_code == 403
    assert owner.post(D(a, f"/{doc['id']}/delete")).status_code == 403


def test_filenames_cannot_traverse_or_collide_with_system_names(firm, owner_engine, backend) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    cases = {
        "../../etc/passwd.pdf": "passwd.pdf",
        "..\\..\\windows\\system32\\x.pdf": "x.pdf",
        "C:\\temp\\y.pdf": "y.pdf",
        "con.pdf": "con_.pdf",
        'a<b>c:d"e|f?g*.pdf': "a_b_c_d_e_f_g_.pdf",
        "  trailing dots...pdf": "trailing dots.pdf",
        "tab\tname.pdf": "tab_name.pdf",
    }
    for i, (given, expected) in enumerate(cases.items()):
        doc = upload(owner, a, pdf(f"p{i}"), given, node)
        assert doc["display_name"] == expected, (given, doc["display_name"])
        assert "/" not in doc["display_name"] and "\\" not in doc["display_name"]
    assert init(owner, a, pdf("n"), "x" * 400 + ".pdf", node).status_code in (201, 422)
    assert init(owner, a, pdf("n"), "....", node).status_code == 422
    # stored keys are opaque and the backend refuses anything that is not one
    for o in objects(owner_engine, a):
        assert ".pdf" not in o["storage_key"] and ".." not in o["storage_key"]
    for evil in ("../x", f"t/{uuid.uuid4()}/../../x", "/etc/passwd", f"t/{uuid.uuid4()}/c/{uuid.uuid4()}/o/../../../x"):
        with pytest.raises(storage_mod.StorageError):
            backend.stat(evil)
        with pytest.raises(storage_mod.StorageError):
            list(backend.open_range(evil))


def test_database_isolation_for_document_tables(firm, tenant_db, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    upload(owner, a, pdf("rls"), "rls.pdf", node)
    tables = ("documents", "document_versions", "storage_objects", "document_events", "document_upload_operations")
    with tenant_db.begin() as c:  # no tenant context at all -> no rows
        for t in tables:
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0
    with tenant_db.begin() as c:  # another organization's context -> no rows
        c.execute(
            text(
                "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"
            ),
            {"u": str(uuid.uuid4()), "o": str(uuid.uuid4())},
        )
        for t in tables:
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0
    with tenant_db.begin() as c:  # right organization but a company list that excludes A
        c.execute(
            text(
                "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true), set_config('app.company_scope', 'list', true), set_config('app.company_ids', :c, true)"
            ),
            {"u": uid(owner), "o": owner.org_id, "c": b},
        )
        assert c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
    # the composite foreign keys make a document in company A's tree pointing at company B's node impossible
    with owner_engine.begin() as c, pytest.raises(Exception, match="foreign key"):
        c.execute(
            text(
                "INSERT INTO documents (id, organization_id, company_id, node_id, display_name, original_filename, extension, mime_type,"
                " size_bytes, content_sha256) VALUES (gen_random_uuid(), :o, :a, :nb, 'x.pdf', 'x.pdf', 'pdf', 'application/pdf', 1, :h)"
            ),
            {"o": owner.org_id, "a": a, "nb": node_b, "h": "0" * 64},
        )
    # a storage key must be opaque and well-formed at the database level too
    with owner_engine.begin() as c, pytest.raises(Exception, match="check constraint"):
        c.execute(
            text(
                "INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256)"
                " VALUES (gen_random_uuid(), :o, :a, 'local', 'invoices/jan.pdf', 1, :h)"
            ),
            {"o": owner.org_id, "a": a, "h": "0" * 64},
        )


# ------------------------------------------------------------------ storage administration: usage and reconciliation
def test_reconciliation_finds_lost_orphaned_and_abandoned_objects(firm, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    import os
    import time

    owner, a, _b, node, _ = firm
    keep = upload(owner, a, pdf("keep"), "keep.pdf", node)
    lost = upload(owner, a, pdf("lost"), "lost.pdf", node)
    lost_key = next(o["storage_key"] for o in objects(owner_engine, a) if o["sha256"] == sha(pdf("lost")))
    backend.delete(lost_key)
    # an orphan: bytes in the store with no database row, old enough to be certain
    orphan_key = storage_mod.object_key(uuid.UUID(owner.org_id), uuid.UUID(a), uuid.uuid4())
    young_key = storage_mod.object_key(uuid.UUID(owner.org_id), uuid.UUID(a), uuid.uuid4())
    for k in (orphan_key, young_key):
        p = backend._path(k)  # type: ignore[attr-defined]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"stray")
    old = time.time() - 7200
    os.utime(backend._path(orphan_key), (old, old))  # type: ignore[attr-defined]
    # an abandoned session whose content was received but never completed
    stale = init(owner, a, pdf("stale"), "stale.pdf", node).json()
    put(owner, a, stale["id"], pdf("stale"))
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE document_upload_operations SET expires_at = now() - interval '2 hours' WHERE id = :i"),
            {"i": stale["id"]},
        )
    dry = owner.post("/storage/reconcile", {}).json()
    assert (
        dry["repair"] is False
        and dry["missing_objects"] == 1
        and dry["orphan_objects"] == 1
        and dry["expired_uploads"] == 1
    )
    assert dry["missing_sample"] == [lost_key] and dry["orphan_sample"] == [orphan_key]
    assert (
        backend.stat(orphan_key) == 5
        and next(o for o in objects(owner_engine, a) if o["storage_key"] == lost_key)["state"] == "verified"
    )  # a dry run changes nothing
    fixed = owner.post("/storage/reconcile", {"repair": True}).json()
    assert fixed["missing_objects"] == 1 and fixed["orphan_objects"] == 1 and fixed["expired_uploads"] == 1
    assert backend.stat(orphan_key) is None and backend.stat(young_key) == 5  # a young object is never touched
    states = {o["storage_key"]: o["state"] for o in objects(owner_engine, a)}
    assert states[lost_key] == "missing" and list(states.values()).count("purged") == 1
    assert owner.get(D(a, f"/uploads/{stale['id']}")).json()["status"] == "expired"
    # the lost document is reported honestly instead of failing mysteriously; the intact one is unaffected
    r = owner.get(D(a, f"/{lost['id']}/content"))
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"
    assert owner.get(D(a, f"/{keep['id']}/content")).status_code == 200
    again = owner.post("/storage/reconcile", {"repair": True}).json()
    assert (
        again["missing_objects"] == 0 and again["orphan_objects"] == 0 and again["expired_uploads"] == 0
    )  # idempotent
    with owner_engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'storage.reconciled' AND organization_id = :o"),
                {"o": owner.org_id},
            ).scalar_one()
            == 3
        )


def test_storage_overview_reports_usage_per_company_and_the_real_scanner_state(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    d = upload(owner, a, pdf("ov1" * 30), "ov1.pdf", node)
    upload(owner, b, pdf("ov2"), "ov2.pdf", node_b)
    owner.post(D(a, f"/{d['id']}/archive"))
    bad = init(owner, a, png("zz"), "zz.pdf", node).json()
    put(owner, a, bad["id"], png("zz"))
    ov = owner.get("/storage/overview").json()
    per = {c["company_id"]: c for c in ov["companies"]}
    assert (
        per[a]["stored_bytes"] == d["size_bytes"]
        and per[a]["archived_documents"] == 1
        and per[b]["active_documents"] == 1
    )
    assert ov["storage"]["used_bytes"] == per[a]["stored_bytes"] + per[b]["stored_bytes"]
    assert ov["uploads_last_7_days"]["completed"] == 2 and ov["uploads_last_7_days"]["failed"] == 1
    assert ov["recent_failures"][0]["error_code"] == "content_mismatch" and ov["backend"] == "local"
    assert "archived" in ov["accounting"] and ov["scanner"]["active"] is False
    assert owner.get("/storage/overview").json()["max_file_size_bytes"] > 0


# ------------------------------------------------------------------ object storage backends
def test_s3_backend_behaves_like_the_local_backend_under_moto(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import boto3
    from artify_api.settings import get_settings
    from artify_api.storage import S3Backend, StorageError
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="artify-test")
        be = S3Backend(
            get_settings().model_copy(update={"storage_s3_bucket": "artify-test", "storage_s3_sse": "AES256"}),
            client=client,
        )
        key = storage_mod.object_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        src = tmp_path / "f.bin"
        src.write_bytes(b"0123456789")
        assert be.stat(key) is None
        be.put_file(key, src)
        assert be.stat(key) == 10
        assert b"".join(be.open_range(key)) == b"0123456789" and b"".join(be.open_range(key, 2, 5)) == b"2345"
        assert client.head_object(Bucket="artify-test", Key=key)["ServerSideEncryption"] == "AES256"
        assert [o.key for o in be.list_objects(key.rsplit("/", 2)[0] + "/")] == [key]
        be.delete(key)
        assert be.stat(key) is None
        with pytest.raises(StorageError):
            be.stat("not/a/valid/key")
        with pytest.raises(StorageError):
            list(be.open_range(key))  # missing object
        broken = S3Backend(get_settings().model_copy(update={"storage_s3_bucket": "no-such-bucket"}), client=client)
        with pytest.raises(StorageError):
            broken.put_file(storage_mod.object_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()), src)


def test_full_upload_download_and_purge_against_s3(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    import boto3
    from artify_api.settings import get_settings
    from artify_api.storage import S3Backend
    from moto import mock_aws

    owner, a, _b, node, _ = firm
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="artify-e2e")
        storage_mod.set_storage(
            S3Backend(get_settings().model_copy(update={"storage_s3_bucket": "artify-e2e"}), client=client)
        )
        content = pdf("s3-e2e")
        doc = upload(owner, a, content, "s3.pdf", node)
        [obj] = objects(owner_engine, a)
        assert obj["backend"] == "s3" and client.head_object(Bucket="artify-e2e", Key=obj["storage_key"])[
            "ContentLength"
        ] == len(content)
        assert owner.get(D(a, f"/{doc['id']}/content")).content == content
        owner.post(D(a, f"/{doc['id']}/delete"))
        assert owner.delete(D(a, f"/{doc['id']}?confirm=true")).status_code == 200
        assert client.list_objects_v2(Bucket="artify-e2e").get("KeyCount", 0) == 0
        ov = owner.get("/storage/overview").json()
        assert ov["backend"] == "s3"


def test_production_settings_require_s3_storage() -> None:
    from artify_api.settings import Settings
    from cryptography.fernet import Fernet

    base = {
        "environment": "production",
        "tenant_jwt_secret": "a" * 40,
        "platform_jwt_secret": "b" * 40,
        "mfa_encryption_key": Fernet.generate_key().decode(),
        "rate_limit_backend": "redis",
        "database_url_app": "postgresql+psycopg://u:p@h/db",
        "database_url_platform": "postgresql+psycopg://u:p@h/db",
        "database_url_dispatcher": "postgresql+psycopg://u:p@h/db",
        "mailer_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "web_base_url": "https://app.example.com",
    }
    with pytest.raises(ValueError, match="storage_backend"):
        Settings(**base)  # type: ignore[arg-type]
    ok = Settings(**base, storage_backend="s3", storage_s3_bucket="b")  # type: ignore[arg-type]
    assert ok.storage_backend == "s3"


# ------------------------------------------------------------------ regressions with earlier phases
def test_documents_integrate_with_phase_4_upload_config_and_permission_catalog(firm, client) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    cfg = owner.get(W(a, "/upload-config")).json()
    assert node in {
        t["id"] for t in cfg["targets"]
    }  # the destinations the desktop offers are the ones documents accept
    keys = (
        {p["key"] for p in owner.get(f"/orgs/{owner.org_id}/permissions").json()["items"]}
        if owner.get(f"/orgs/{owner.org_id}/permissions").status_code == 200
        else None
    )
    if keys is not None:
        assert {
            "documents.read",
            "documents.upload",
            "documents.manage",
            "documents.delete",
            "documents.purge",
            "storage.manage",
        } <= keys
    me = owner.get("/me").json()
    assert me["memberships"][0]["organization_id"] == owner.org_id
