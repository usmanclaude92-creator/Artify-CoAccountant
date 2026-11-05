"""Phase 12: resumable (chunked) uploads against PostgreSQL. Synthetic bytes only."""

# ruff: noqa: F811
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from artify_api import upload_staging
from artify_api.settings import get_settings
from test_p6_documents import D, backend, firm, init, pdf, sha  # noqa: F401  (backend = autouse storage fixture)


def chunk(actor, cid, op_id, data: bytes, offset: int, **headers: str):  # type: ignore[no-untyped-def]
    return actor.client.patch(
        f"/api/v1{D(cid, f'/uploads/{op_id}/chunk')}",
        params={"offset": offset},
        content=data,
        headers=actor.headers(**{"Content-Type": "application/octet-stream", **headers}),
    )


def start(firm, size: int = 9000, tag: str = "res") -> tuple[Any, str, str, bytes, str]:  # type: ignore[no-untyped-def]
    o, a, _b, node, _nb = firm
    head, rest = pdf(tag).split(b"\n", 1)
    content = head + b"\n%" + (b"x" * size) + b"\n" + rest  # a padded but otherwise valid PDF
    r = init(o, a, content, f"{tag}.pdf", node)
    assert r.status_code == 201, r.text
    return o, a, r.json()["id"], content, sha(content)


def test_a_file_sent_in_chunks_with_an_interruption_in_the_middle_completes_intact(firm) -> None:  # type: ignore[no-untyped-def]
    o, a, op, content, digest = start(firm)
    n = len(content)
    first = chunk(o, a, op, content[:3000], 0, **{"X-Chunk-SHA256": hashlib.sha256(content[:3000]).hexdigest()})
    assert first.status_code == 200 and first.json()["received_bytes"] == 3000 and first.json()["status"] == "uploading"
    # connection lost: the client asks where it is, and the server says so
    where = o.get(D(a, f"/uploads/{op}")).json()
    assert where["received_bytes"] == 3000 and where["declared_size"] == n and where["chunk_max_bytes"] > 0
    # the same chunk again, or a skipped chunk, is refused with the offset to use
    for bad in (0, 3500):
        m = chunk(o, a, op, content[bad : bad + 100], bad)
        assert m.status_code == 409 and m.json()["code"] == "offset_mismatch" and m.json()["received_bytes"] == 3000
    second = chunk(o, a, op, content[3000:6000], 3000)
    assert second.status_code == 200 and second.json()["received_bytes"] == 6000
    last = chunk(o, a, op, content[6000:], 6000)
    assert last.status_code == 200, last.text
    assert last.json()["status"] == "uploaded" and last.json()["received_bytes"] == n
    done = o.post(D(a, f"/uploads/{op}/complete"))
    assert done.status_code == 200, done.text
    doc = done.json()["document"]
    assert doc["content_sha256"] == digest and doc["size_bytes"] == n
    assert not upload_staging.path_for(uuid.UUID(op)).exists()  # staging is gone
    dl = o.get(f"/companies/{a}/documents/{doc['id']}/content")
    assert dl.status_code == 200 and dl.content == content


def test_a_chunk_with_the_wrong_checksum_or_size_changes_nothing(firm) -> None:  # type: ignore[no-untyped-def]
    o, a, op, content, _ = start(firm)
    assert chunk(o, a, op, content[:2000], 0).status_code == 200
    bad = chunk(o, a, op, content[2000:4000], 2000, **{"X-Chunk-SHA256": "0" * 64})
    assert (
        bad.status_code == 422
        and bad.json()["code"] == "chunk_checksum_mismatch"
        and bad.json()["received_bytes"] == 2000
    )
    assert o.get(D(a, f"/uploads/{op}")).json()["received_bytes"] == 2000
    assert upload_staging.size(uuid.UUID(op)) == 2000  # the rejected bytes were cut off again
    over = chunk(o, a, op, content[2000:] + b"extra-bytes", 2000)
    assert over.status_code == 422 and over.json()["code"] == "chunk_size_invalid"
    empty = chunk(o, a, op, b"", 2000)
    assert empty.status_code == 422
    assert o.get(D(a, f"/uploads/{op}")).json()["received_bytes"] == 2000


def test_the_chunk_size_guard_is_a_setting(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    o, a, op, content, _ = start(firm)
    monkeypatch.setattr(get_settings(), "upload_chunk_max_bytes", 1000)
    r = chunk(o, a, op, content[:1500], 0)
    assert r.status_code == 422 and r.json()["code"] == "chunk_size_invalid"
    assert chunk(o, a, op, content[:1000], 0).status_code == 200


def test_the_final_checksum_and_content_rules_still_apply(firm) -> None:  # type: ignore[no-untyped-def]
    o, a, _b, node, _nb = firm
    head, rest = pdf("will-differ").split(b"\n", 1)
    content = head + b"\n%" + b"z" * 3000 + b"\n" + rest
    r = init(o, a, content, "d.pdf", node)  # announces the checksum of `content`
    op = r.json()["id"]
    tampered = content[:200] + b"Y" + content[201:]  # same size, different bytes
    assert chunk(o, a, op, tampered[:1500], 0).status_code == 200
    end = chunk(o, a, op, tampered[1500:], 1500)
    assert end.status_code == 422, end.text
    op_state = o.get(D(a, f"/uploads/{op}")).json()
    assert (
        op_state["status"] == "failed"
        and op_state["error_code"] == "checksum_mismatch"
        and op_state["received_bytes"] == 0
    )
    assert not upload_staging.path_for(uuid.UUID(op)).exists()
    # a file that is not what it claims is refused at the end, like a one-shot upload
    junk = b"MZ" + b"\x00" * 3000
    j = init(o, a, junk, "setup.pdf", node)
    jop = j.json()["id"]
    assert chunk(o, a, jop, junk[:1000], 0).status_code == 200
    jend = chunk(o, a, jop, junk[1000:], 1000)
    assert jend.status_code == 422 and o.get(D(a, f"/uploads/{jop}")).json()["status"] in ("failed", "quarantined")


def test_the_database_is_the_authority_when_staging_disagrees(firm) -> None:  # type: ignore[no-untyped-def]
    o, a, op, content, digest = start(firm)
    uid = uuid.UUID(op)
    assert chunk(o, a, op, content[:3000], 0).status_code == 200
    # bytes written but never committed (crash between write and commit): they are cut back, not trusted
    with upload_staging.path_for(uid).open("ab") as f:
        f.write(b"GARBAGE-NEVER-COMMITTED")
    ok = chunk(o, a, op, content[3000:6000], 3000)
    assert ok.status_code == 200 and ok.json()["received_bytes"] == 6000
    # staging lost (another replica, wiped volume): the server says where the real data ends; the client restarts there
    upload_staging.discard(uid)
    lost = chunk(o, a, op, content[6000:], 6000)
    assert lost.status_code == 409 and lost.json()["received_bytes"] == 0
    for off in range(0, len(content), 4000):
        r = chunk(o, a, op, content[off : off + 4000], off)
        assert r.status_code == 200, r.text
    done = o.post(D(a, f"/uploads/{op}/complete"))
    assert done.status_code == 200 and done.json()["document"]["content_sha256"] == digest


def test_other_people_and_companies_cannot_touch_an_upload_and_cancel_cleans_up(firm) -> None:  # type: ignore[no-untyped-def]
    o, a, op, content, _ = start(firm)
    b = firm[2]
    assert chunk(o, a, op, content[:1000], 0).status_code == 200
    assert chunk(o, b, op, content[1000:2000], 1000).status_code == 404  # another company: not found
    assert o.get(D(b, f"/uploads/{op}")).status_code == 404
    uid = uuid.UUID(op)
    assert upload_staging.path_for(uid).exists()
    assert o.post(D(a, f"/uploads/{op}/cancel")).json()["status"] == "cancelled"
    assert not upload_staging.path_for(uid).exists()
    after = chunk(o, a, op, content[1000:2000], 1000)
    assert after.status_code == 409  # cancelled sessions accept nothing
