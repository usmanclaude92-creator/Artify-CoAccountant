"""Pure file-safety checks for uploaded documents (no database, no web framework).

The client-declared extension and content type are never trusted: the extension must be on the allow-list, the
leading bytes must match what that extension claims, the structure must be plausible (not truncated or hollow), and
content that can execute (executables, scripts, Office macros, PDF launch/JavaScript actions) is refused.

This is a validation layer, NOT a malware scanner. Antivirus scanning is a separate interface (`scanner.py`) and is
only reported as active when a scanner is configured and reachable.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: extension -> (canonical media type, family)
ALLOWED: dict[str, tuple[str, str]] = {
    "pdf": ("application/pdf", "pdf"),
    "jpg": ("image/jpeg", "jpeg"),
    "jpeg": ("image/jpeg", "jpeg"),
    "png": ("image/png", "png"),
    "tif": ("image/tiff", "tiff"),
    "tiff": ("image/tiff", "tiff"),
    "bmp": ("image/bmp", "bmp"),
    "heic": ("image/heic", "heif"),
    "heif": ("image/heif", "heif"),
    "doc": ("application/msword", "ole"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "ooxml-word"),
    "xls": ("application/vnd.ms-excel", "ole"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "ooxml-excel"),
    "csv": ("text/csv", "text"),
    "txt": ("text/plain", "text"),
}
#: families a browser/desktop viewer can render safely without executing anything
PREVIEWABLE = frozenset({"pdf", "jpeg", "png", "bmp"})

_HEAD = 64 * 1024
_TAIL = 8 * 1024
_MAX_ZIP_ENTRIES = 5000
_MAX_ZIP_RATIO = 200  # uncompressed / compressed; above this is a decompression bomb
_MAX_ZIP_UNCOMPRESSED = 2 * 1024**3
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"heif"}

_RESERVED = {
    "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10)),
}  # fmt: skip
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')


class FileRejected(Exception):
    """The file must not be stored. `code` is stable and shown to clients; `message` is human readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Inspection:
    extension: str
    mime_type: str
    family: str
    previewable: bool
    findings: tuple[str, ...] = field(default_factory=tuple)  # non-blocking observations


# ------------------------------------------------------------------ filenames
def split_extension(filename: str) -> tuple[str, str]:
    """('report', 'pdf') for 'a/b\\report.PDF' — the extension is lower-cased, the stem keeps its case."""
    base = re.split(r"[\\/]", filename)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        return base, ""
    return stem, ext.lower()


def sanitize_filename(filename: str, *, max_len: int = 200) -> str:
    """A safe display/file name: no path parts, no reserved characters, no Windows device names, bounded length.

    Raises FileRejected when nothing usable is left. The extension is preserved."""
    base = re.split(r"[\\/]", unicodedata.normalize("NFC", filename))[-1]
    base = _BAD_CHARS.sub("_", base).strip(" .")
    stem, ext = base.rpartition(".")[0], base.rpartition(".")[2]
    if not stem:  # no dot, or only a leading dot
        stem, ext = base.lstrip("."), ""
    stem = stem.strip(" .")
    if not stem:
        raise FileRejected("invalid_filename", "The file name is empty after removing unsafe characters.")
    if stem.lower() in _RESERVED or stem.split(".")[0].lower() in _RESERVED:
        stem = f"{stem}_"
    suffix = f".{ext}" if ext else ""
    room = max_len - len(suffix)
    return stem[:room].rstrip(" .") + suffix


# ------------------------------------------------------------------ content inspection
def _read(path: Path) -> tuple[bytes, bytes, int]:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(_HEAD)
        tail = b""
        if size > _HEAD:
            f.seek(max(size - _TAIL, _HEAD))
            tail = f.read(_TAIL)
    return head, tail or head[-_TAIL:], size


def _reject_executable(head: bytes) -> None:
    if head[:2] == b"MZ":
        raise FileRejected("executable_content", "Windows executables are not accepted.")
    if head[:4] == b"\x7fELF":
        raise FileRejected("executable_content", "Executable files are not accepted.")
    if head[:2] == b"#!":
        raise FileRejected("executable_content", "Scripts are not accepted.")
    if head[:4] in (
        b"\xca\xfe\xba\xbe",
        b"\xcf\xfa\xed\xfe",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
    ):
        raise FileRejected("executable_content", "Executable files are not accepted.")
    if head[:4] == b"PK\x03\x04" and b"META-INF/MANIFEST.MF" in head[:2048]:
        raise FileRejected("executable_content", "Java archives are not accepted.")


def _check_pdf(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if b"%PDF-" not in head[:1024]:
        raise FileRejected("content_mismatch", "The file is not a PDF document.")
    if b"%%EOF" not in tail:
        raise FileRejected("corrupt_file", "The PDF looks truncated (no end-of-file marker).")
    window = head + tail
    for token in (b"/Launch", b"/JavaScript", b"/JS ", b"/JS(", b"/JS<", b"/RichMedia", b"/OpenAction"):
        if token in window:
            if token == b"/OpenAction" and b"/JavaScript" not in window and b"/Launch" not in window:
                continue
            raise FileRejected("active_content", "PDFs that contain scripts or launch actions are not accepted.")
    return ()


def _check_jpeg(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if head[:3] != b"\xff\xd8\xff":
        raise FileRejected("content_mismatch", "The file is not a JPEG image.")
    if size < 128 or b"\xff\xd9" not in tail[-64:]:
        raise FileRejected("corrupt_file", "The JPEG image looks truncated.")
    return ()


def _check_png(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise FileRejected("content_mismatch", "The file is not a PNG image.")
    if head[12:16] != b"IHDR" or b"IEND" not in tail[-16:]:
        raise FileRejected("corrupt_file", "The PNG image is truncated or damaged.")
    return ()


def _check_tiff(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if head[:4] not in (b"II*\x00", b"MM\x00*"):
        raise FileRejected("content_mismatch", "The file is not a TIFF image.")
    if size < 16:
        raise FileRejected("corrupt_file", "The TIFF image is too small to be valid.")
    return ()


def _check_bmp(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if head[:2] != b"BM" or size < 26:
        raise FileRejected("content_mismatch", "The file is not a BMP image.")
    declared = int.from_bytes(head[2:6], "little")
    if declared and declared > size + 64:
        raise FileRejected("corrupt_file", "The BMP image is truncated.")
    return ()


def _check_heif(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if head[4:8] != b"ftyp" or head[8:12] not in _HEIF_BRANDS:
        raise FileRejected("content_mismatch", "The file is not a HEIC/HEIF image.")
    return ("HEIC/HEIF files are stored as-is; they cannot be previewed in the app.",)


def _check_ole(head: bytes, tail: bytes, size: int, path: Path) -> tuple[str, ...]:
    if head[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise FileRejected("content_mismatch", "The file is not a legacy Office document.")
    blob = path.read_bytes() if size <= 64 * 1024**2 else head
    if "_VBA_PROJECT".encode("utf-16-le") in blob or "Macros".encode("utf-16-le") in blob:
        raise FileRejected("macro_content", "Office documents that contain macros are not accepted.")
    return ()


def _check_ooxml(path: Path, family: str, size: int) -> tuple[str, ...]:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileRejected("corrupt_file", "The Office document is damaged (not a valid package).") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ZIP_ENTRIES:
            raise FileRejected("archive_bomb", "The document package has too many parts.")
        total = sum(i.file_size for i in infos)
        compressed = sum(i.compress_size for i in infos) or 1
        if total > _MAX_ZIP_UNCOMPRESSED or total / compressed > _MAX_ZIP_RATIO:
            raise FileRejected("archive_bomb", "The document expands to an unreasonable size.")
        names = [i.filename for i in infos]
        for n in names:
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") or ":" in n:
                raise FileRejected("unsafe_archive_entry", "The document package contains an unsafe path.")
        lowered = {n.lower() for n in names}
        if "[content_types].xml" not in lowered:
            raise FileRejected("content_mismatch", "The file is not an Office document package.")
        marker = "word/" if family == "ooxml-word" else "xl/"
        if not any(n.lower().startswith(marker) for n in names):
            raise FileRejected("content_mismatch", "The package is not the kind of Office document its extension says.")
        if any(n.lower().endswith(("vbaproject.bin", ".exe", ".dll", ".js", ".vbs", ".bat", ".ps1")) for n in names):
            raise FileRejected("macro_content", "Office documents that contain macros or executables are not accepted.")
        if zf.testzip() is not None:
            raise FileRejected("corrupt_file", "The Office document package is damaged.")
    return ()


def _check_text(head: bytes, tail: bytes, size: int) -> tuple[str, ...]:
    if b"\x00" in head:
        if head[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 with BOM is legitimate for CSV exported by Excel
            return ()
        raise FileRejected("content_mismatch", "The file is binary, not text.")
    sample = head
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(sample) - 4:  # tolerate a multi-byte character cut by the sample boundary
            try:
                sample.decode("cp1252")
            except UnicodeDecodeError:
                raise FileRejected("content_mismatch", "The file is not readable text.") from exc
    ctrl = sum(1 for b in sample if b < 9 or 13 < b < 32)
    if sample and ctrl / len(sample) > 0.02:
        raise FileRejected("content_mismatch", "The file contains too many control characters to be text.")
    return ()


def inspect_file(path: Path, filename: str, *, declared_mime: str | None = None) -> Inspection:
    """Validate the stored bytes against the file name. Raises FileRejected; otherwise returns the verified facts."""
    _stem, ext = split_extension(filename)
    if ext not in ALLOWED:
        raise FileRejected(
            "unsupported_type",
            f"Files of type '.{ext or '(none)'}' are not accepted. Allowed: " + ", ".join(sorted(ALLOWED)) + ".",
        )
    mime, family = ALLOWED[ext]
    head, tail, size = _read(path)
    if size == 0:
        raise FileRejected("empty_file", "The file is empty.")
    _reject_executable(head)
    findings: tuple[str, ...]
    if family == "pdf":
        findings = _check_pdf(head, tail, size)
    elif family == "jpeg":
        findings = _check_jpeg(head, tail, size)
    elif family == "png":
        findings = _check_png(head, tail, size)
    elif family == "tiff":
        findings = _check_tiff(head, tail, size)
    elif family == "bmp":
        findings = _check_bmp(head, tail, size)
    elif family == "heif":
        findings = _check_heif(head, tail, size)
    elif family == "ole":
        findings = _check_ole(head, tail, size, path)
    elif family in ("ooxml-word", "ooxml-excel"):
        findings = _check_ooxml(path, family, size)
    else:
        findings = _check_text(head, tail, size)
    # the declared type is advisory: a mismatch is recorded, the verified type wins
    if declared_mime and declared_mime.split(";")[0].strip().lower() not in (mime, "application/octet-stream", ""):
        findings = (*findings, f"Declared type '{declared_mime}' differs from the verified type '{mime}'.")
    return Inspection(ext, mime, family, family in PREVIEWABLE, findings)
