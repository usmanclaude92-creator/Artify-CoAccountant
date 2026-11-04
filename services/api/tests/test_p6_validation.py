"""Pure tests: content validation, filename handling and the filesystem storage driver (no database)."""

from __future__ import annotations

import io
import socket
import threading
import uuid

import pytest
import samples
from artify_api.documents import DocumentError, clean_filename
from artify_api.file_validation import canonical_extension, inspect_file
from artify_api.scanner import ClamdScanner, ScanUnavailable
from artify_api.storage import FilesystemStorage, StorageError

ALLOWED = ["pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "heic", "doc", "docx", "xls", "xlsx", "csv", "txt"]


def check(name: str, data: bytes):  # type: ignore[no-untyped-def]
    return inspect_file(io.BytesIO(data), len(data), name, ALLOWED)


@pytest.mark.parametrize("name", list(samples.ACCEPTED))
def test_every_supported_format_is_accepted_by_content(name: str) -> None:
    v = check(name, samples.ACCEPTED[name]())
    assert v.ok and not v.quarantine, (name, v.problems)
    assert v.extension == canonical_extension(name) and v.mime


@pytest.mark.parametrize(
    ("name", "data", "why"),
    [
        ("empty.pdf", b"", "empty"),
        ("x.exe", b"MZ" + b"\x00" * 100, "not accepted"),
        ("x.zip", b"PK\x03\x04" + b"\x00" * 30, "not accepted"),
        ("x.html", b"<html><script>1</script></html>", "not accepted"),
        ("noext", samples.pdf(), "not accepted"),
        ("cut.pdf", samples.pdf()[:-12], "corrupted"),
        ("cut.png", samples.png()[:-14], "corrupted"),
        ("cut.jpg", samples.jpg()[:-2], "corrupted"),
        ("fake.pdf", b"just some words", "not a supported"),
        ("mismatch.pdf", samples.png(), "does not match"),
        ("mismatch.png", samples.pdf(), "does not match"),
        ("mismatch.docx", samples.xlsx(), "does not match"),
        ("bad.docx", b"PK\x03\x04not really a zip", "corrupted"),
        ("script.txt", b"<script>alert(1)</script>", "markup"),
        ("shebang.txt", b"#!/bin/sh\nrm -rf /\n", "script"),
        ("binary.csv", b"a,b\n\x00\x01\x02\x03", "binary"),
        (
            "traversal.docx",
            samples._zip({"[Content_Types].xml": b"x", "word/a.xml": b"x", "../evil.txt": b"x"}),
            "unsafe",
        ),
    ],
)
def test_corrupt_empty_unsupported_and_dangerous_files_are_rejected(name: str, data: bytes, why: str) -> None:
    v = check(name, data)
    assert not v.ok and why in " ".join(v.problems), (name, v.problems)


def test_a_windows_executable_is_rejected_even_when_renamed_to_a_document() -> None:
    pe = bytearray(b"MZ" + b"\x00" * 126)
    pe[0x3C:0x40] = (64).to_bytes(4, "little")
    pe[64:68] = b"PE\x00\x00"
    for name in ("invoice.pdf", "notes.txt", "x.docx"):
        v = check(name, bytes(pe))
        assert not v.ok and "executable" in " ".join(v.problems), name
    assert check("customer.txt", b"MZ-1042 Muscat Zone\n").ok  # text that merely starts with "MZ" is fine


def test_active_content_is_quarantined_not_stored_as_a_document() -> None:
    assert check("js.pdf", samples.pdf_with_javascript()).quarantine
    assert check("macro.docx", samples.docx({"word/vbaProject.bin": b"x"})).quarantine
    assert check("macro.xlsx", samples.xlsx({"xl/vbaProject.bin": b"x"})).quarantine
    assert check("macro.doc", samples.ole() + "_VBA_PROJECT".encode("utf-16-le")).quarantine
    assert not check("clean.pdf", samples.pdf()).quarantine


def test_extension_comes_from_the_name_but_must_agree_with_the_content() -> None:
    assert check("SCAN.JPEG", samples.jpg()).extension == "jpg"
    assert check("Scan.TIFF", samples.tiff()).extension == "tif"
    assert check("a.csv", samples.txt()).ok and check("a.txt", samples.csv()).ok  # text types are interchangeable


def test_a_compression_bomb_is_rejected() -> None:
    bomb = samples._zip({"[Content_Types].xml": b"x", "word/document.xml": b"0" * (300 * 1024 * 1024)})
    v = check("bomb.docx", bomb)
    assert not v.ok and "compression bomb" in " ".join(v.problems)


@pytest.mark.parametrize("raw", ["", " ", "..", ".", "a\x00b.pdf", "x" * 300 + ".pdf", "bad\nname.pdf"])
def test_unusable_file_names_are_refused(raw: str) -> None:
    with pytest.raises(DocumentError):
        clean_filename(raw)


def test_file_names_lose_every_path_component() -> None:
    assert clean_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert clean_filename("C:\\Users\\me\\Invoice 1.pdf") == "Invoice 1.pdf"
    assert clean_filename("  Résumé.pdf ") == "Résumé.pdf"


# ------------------------------------------------------------------ storage driver
@pytest.fixture
def store(tmp_path):  # type: ignore[no-untyped-def]
    return FilesystemStorage(tmp_path / "root")


def test_keys_are_opaque_and_traversal_is_impossible(store) -> None:  # type: ignore[no-untyped-def]
    org, co = uuid.uuid4(), uuid.uuid4()
    key = store.new_key(org, co)
    assert key.startswith(f"o/{org.hex}/{co.hex}/") and "." not in key and key.count("/") == 3
    for bad in ("../../etc/passwd", "o/../x", "/etc/passwd", key + "/../..", key.replace("o/", "x/"), "o/a/b/c", ""):
        with pytest.raises(StorageError):
            store.exists(bad)
        with pytest.raises(StorageError):
            store.commit_stage("0" * 32, bad)
    for bad_stage in ("../x", "short", "0" * 31 + "/"):
        with pytest.raises(StorageError):
            store.stage_size(bad_stage)


def test_staging_is_resumable_exact_and_commit_never_overwrites(store) -> None:  # type: ignore[no-untyped-def]
    ref = uuid.uuid4().hex
    assert store.stage_append(ref, 0, b"hello ") == 6
    with pytest.raises(StorageError, match="offset mismatch"):
        store.stage_append(ref, 0, b"again")  # cannot rewrite received bytes
    with pytest.raises(StorageError, match="offset mismatch"):
        store.stage_append(ref, 99, b"gap")  # cannot leave a gap
    assert store.stage_append(ref, 6, b"world") == 11 and store.stage_size(ref) == 11
    key = store.new_key(uuid.uuid4(), uuid.uuid4())
    assert not store.exists(key)
    store.commit_stage(ref, key)
    assert store.exists(key) and store.size(key) == 11 and store.stage_size(ref) == 0
    assert b"".join(store.read(key)) == b"hello world" and b"".join(store.read(key, 6, 8)) == b"wor"
    store.stage_append(ref, 0, b"x")
    with pytest.raises(StorageError, match="already exists"):
        store.commit_stage(ref, key)
    store.delete(key)
    assert not store.exists(key) and key not in set(store.list_keys())


def test_clamd_protocol_clean_infected_and_unavailable() -> None:
    def serve(reply: bytes) -> int:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def run() -> None:
            conn, _ = srv.accept()
            buf = b""
            while not buf.endswith(b"\x00\x00\x00\x00"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            assert buf.startswith(b"zINSTREAM\x00")
            conn.sendall(reply)
            conn.close()
            srv.close()

        threading.Thread(target=run, daemon=True).start()
        return int(srv.getsockname()[1])

    assert ClamdScanner("127.0.0.1", serve(b"stream: OK\x00")).scan(io.BytesIO(b"data")) == (True, None)
    assert ClamdScanner("127.0.0.1", serve(b"stream: Eicar-Test-Signature FOUND\x00")).scan(io.BytesIO(b"data")) == (
        False,
        "Eicar-Test-Signature",
    )
    with pytest.raises(ScanUnavailable):
        ClamdScanner("127.0.0.1", serve(b"stream: INSTREAM size limit exceeded. ERROR\x00")).scan(io.BytesIO(b"x"))
    with pytest.raises(ScanUnavailable):
        ClamdScanner("127.0.0.1", 1, timeout=0.5).scan(io.BytesIO(b"x"))
