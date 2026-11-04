"""Content-based validation of uploaded files. Pure: it reads a seekable binary file and returns a verdict.

Nothing the client says is trusted: the file name only proposes an extension, and the CONTENT must agree with it.
Two outcomes besides "ok":
- `problems`: the file is rejected and not stored (empty, corrupt, unsupported, executable, extension/content mismatch,
  zip bomb, unsafe archive entries);
- `quarantine`: the file is a valid document but carries active content (PDF JavaScript/launch actions, Office macros).
  The upload ends as `quarantined` and nothing is stored as a document (there is no review queue yet).
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import BinaryIO

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|RichMedia|EmbeddedFile)(?![A-Za-z])")
_VBA_MARKERS = (b"_VBA_PROJECT", "_VBA_PROJECT".encode("utf-16-le"), b"VBA/", "VBA".encode("utf-16-le"))
_EXECUTABLE_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "a Linux executable"),
    (b"#!", "a script"),
    (b"\xca\xfe\xba\xbe", "a Java/Mach-O binary"),
    (b"\xcf\xfa\xed\xfe", "a macOS executable"),
    (b"\xfe\xed\xfa", "a macOS executable"),
)
_SCRIPT_MARKERS = (b"<script", b"<?php", b"<%@", b"<!doctype html", b"<html")
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 2 * 1024**3
MAX_ZIP_RATIO = 200

# canonical extension -> (mime, family)
TYPES: dict[str, tuple[str, str]] = {
    "pdf": ("application/pdf", "pdf"),
    "jpg": ("image/jpeg", "image"),
    "png": ("image/png", "image"),
    "tif": ("image/tiff", "image"),
    "bmp": ("image/bmp", "image"),
    "heic": ("image/heic", "image"),
    "doc": ("application/msword", "word"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word"),
    "xls": ("application/vnd.ms-excel", "excel"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "excel"),
    "csv": ("text/csv", "text"),
    "txt": ("text/plain", "text"),
}
_ALIASES = {"jpeg": "jpg", "tiff": "tif"}


def canonical_extension(filename: str) -> str:
    ext = filename.rpartition(".")[2].lower() if "." in filename else ""
    return _ALIASES.get(ext, ext)


@dataclass
class Inspection:
    extension: str | None = None  # canonical, validated against the content
    mime: str | None = None
    family: str | None = None
    problems: list[str] = field(default_factory=list)
    quarantine: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _read(fh: BinaryIO, pos: int, n: int) -> bytes:
    fh.seek(pos)
    return fh.read(n)


def _scan_chunks(fh: BinaryIO, size: int, patterns: Iterable[re.Pattern[bytes]], limit: int = 1) -> list[bytes]:
    """Find regex hits across the whole file in bounded memory (chunks overlap so a token cannot hide on a boundary)."""
    hits: list[bytes] = []
    overlap, step = 64, 1024 * 1024
    pos, carry = 0, b""
    fh.seek(0)
    while pos < size:
        chunk = carry + fh.read(step)
        for rx in patterns:
            for m in rx.finditer(chunk):
                hits.append(m.group(0))
                if len(hits) >= limit:
                    return hits
        carry = chunk[-overlap:]
        pos += step
    return hits


def _contains(fh: BinaryIO, size: int, needles: Iterable[bytes]) -> bool:
    return bool(_scan_chunks(fh, size, [re.compile(re.escape(n)) for n in needles]))


def _zip_office(fh: BinaryIO, size: int, out: Inspection) -> str | None:
    try:
        with zipfile.ZipFile(fh) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                out.problems.append("The file contains too many internal parts and was rejected.")
                return None
            total = 0
            for i in infos:
                n = i.filename
                if n.startswith("/") or ".." in n.replace("\\", "/").split("/") or "\\" in n.split("/")[0][:3]:
                    out.problems.append("The file contains unsafe internal paths and was rejected.")
                    return None
                total += i.file_size
            if total > MAX_ZIP_UNCOMPRESSED or (size and total / size > MAX_ZIP_RATIO and total > 50 * 1024 * 1024):
                out.problems.append(
                    "The file expands to an unreasonable size (possible compression bomb) and was rejected."
                )
                return None
            bad = z.testzip()
            if bad is not None:
                out.problems.append("The document is corrupted (damaged internal part).")
                return None
            names = {i.filename for i in infos}
    except zipfile.BadZipFile:
        out.problems.append("The document is corrupted (not a valid Office file).")
        return None
    if "[Content_Types].xml" not in names:
        out.problems.append("This archive is not a supported Office document.")
        return None
    if any(n.startswith("word/") for n in names):
        kind = "docx"
    elif any(n.startswith("xl/") for n in names):
        kind = "xlsx"
    else:
        out.problems.append("This archive is not a supported Office document.")
        return None
    if any(n.lower().endswith(("vbaproject.bin", ".bin")) and "vba" in n.lower() for n in names):
        out.quarantine.append("The Office document contains macros.")
    return kind


def _text_ok(fh: BinaryIO, size: int, out: Inspection) -> None:
    head = _read(fh, 0, 4096)
    enc = "utf-16" if head.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    lowered = head.lower().lstrip()
    if any(lowered.startswith(m) for m in _SCRIPT_MARKERS):
        out.problems.append("Text files containing web or script markup are not accepted.")
        return
    decoder_ctl = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    fh.seek(0)
    if enc == "utf-8-sig":
        import codecs

        dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
        legacy = False
        while chunk := fh.read(1024 * 1024):
            if decoder_ctl.search(chunk):
                out.problems.append("The text file contains binary data.")
                return
            if not legacy:
                try:
                    dec.decode(chunk)
                except UnicodeDecodeError:
                    legacy = True  # accept single-byte legacy text (cp1252) as long as it has no control bytes
    else:
        try:
            fh.seek(0)
            fh.read(min(size, 8 * 1024 * 1024)).decode("utf-16")
        except UnicodeDecodeError:
            out.problems.append("The text file is not valid text.")


def _executable_kind(fh: BinaryIO, head: bytes, size: int) -> str | None:
    if head.startswith(b"MZ") and size >= 64:  # a real PE file: e_lfanew points at "PE\0\0" ("MZ-1234" text is fine)
        off = int.from_bytes(head[0x3C:0x40], "little")
        if 0 < off < size and _read(fh, off, 4) == b"PE\x00\x00":
            return "a Windows executable"
    for prefix, label in _EXECUTABLE_PREFIXES:
        if prefix != b"MZ" and head.startswith(prefix):
            return label
    return None


def inspect_file(fh: BinaryIO, size: int, filename: str, allowed_extensions: Iterable[str]) -> Inspection:
    out = Inspection()
    allowed = {_ALIASES.get(e.lower(), e.lower()) for e in allowed_extensions}
    ext = canonical_extension(filename)
    if size <= 0:
        out.problems.append("The file is empty.")
        return out
    if not ext or ext not in TYPES or ext not in allowed:
        out.problems.append(f"Files of this type are not accepted (.{ext or 'none'}).")
        return out
    head = _read(fh, 0, 4096)
    exe = _executable_kind(fh, head, size)
    if exe:
        out.problems.append(f"The file content is {exe}, not a document.")
        return out
    tail = _read(fh, max(0, size - 2048), 2048)
    detected: str | None = None
    if head.startswith(b"%PDF-"):
        detected = "pdf"
        if b"%%EOF" not in tail:
            out.problems.append("The PDF is corrupted or incomplete (no end-of-file marker).")
        elif _scan_chunks(fh, size, [_PDF_ACTIVE]):
            out.quarantine.append("The PDF contains JavaScript or launch/embedded-file actions.")
    elif head.startswith(b"\xff\xd8\xff"):
        detected = "jpg"
        if b"\xff\xd9" not in tail[-64:]:
            out.problems.append("The JPEG image is corrupted or truncated.")
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "png"
        if b"IEND" not in tail[-16:]:
            out.problems.append("The PNG image is corrupted or truncated.")
    elif head[:4] in (b"II*\x00", b"MM\x00*"):
        detected = "tif"
    elif head.startswith(b"BM") and size > 26:
        detected = "bmp"
        if int.from_bytes(head[14:18], "little") not in (12, 40, 52, 56, 64, 108, 124):
            out.problems.append("The BMP image is corrupted.")
    elif head[4:8] == b"ftyp" and head[8:12] in HEIC_BRANDS:
        detected = "heic"
    elif head.startswith(OLE_MAGIC):
        detected = ext if ext in ("doc", "xls") else "doc"
        if ext not in ("doc", "xls"):
            out.problems.append(f"The file content is an older Office document, not a .{ext} file.")
        elif _contains(fh, size, _VBA_MARKERS):
            out.quarantine.append("The Office document contains macros.")
    elif head.startswith(b"PK\x03\x04"):
        kind = _zip_office(fh, size, out)
        detected = kind
    elif ext in ("csv", "txt"):
        detected = ext
        _text_ok(fh, size, out)
    else:
        out.problems.append("The file content is not a supported document type.")
        return out
    if out.problems:
        return out
    if detected != ext and not (detected in ("csv", "txt") and ext in ("csv", "txt")):
        out.problems.append(f"The file content looks like .{detected}, which does not match its .{ext} extension.")
        return out
    out.extension = ext
    out.mime, out.family = TYPES[ext]
    return out
