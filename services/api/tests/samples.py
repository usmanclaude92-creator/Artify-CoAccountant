"""Synthetic, harmless sample files for the document tests (no real documents anywhere)."""

from __future__ import annotations

import io
import uuid
import zipfile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _u() -> bytes:
    return uuid.uuid4().hex.encode()


def pdf(tag: str = "doc") -> bytes:
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% " + tag.encode() + b" " + _u() + b"\ntrailer<<>>\n%%EOF\n"


def pdf_with_javascript() -> bytes:
    return b"%PDF-1.4\n1 0 obj<</S/JavaScript/JS(app.alert(1))>>endobj\n% " + _u() + b"\n%%EOF\n"


def png() -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + _u() + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def jpg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF" + _u() + b"\xff\xd9"


def tiff() -> bytes:
    return b"II*\x00\x08\x00\x00\x00" + _u()


def bmp() -> bytes:
    body = (
        b"BM"
        + (80).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (54).to_bytes(4, "little")
        + (40).to_bytes(4, "little")
    )
    return body + _u().ljust(40, b"0")


def heic() -> bytes:
    return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + _u()


def ole(tag: str = "doc") -> bytes:
    return OLE_MAGIC + b"\x00" * 24 + _u() + tag.encode()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def docx(extra: dict[str, bytes] | None = None) -> bytes:
    return _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<w:document>" + _u() + b"</w:document>",
            **(extra or {}),
        }
    )


def xlsx(extra: dict[str, bytes] | None = None) -> bytes:
    return _zip(
        {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook>" + _u() + b"</workbook>", **(extra or {})}
    )


def csv() -> bytes:
    return b"date,amount\n2026-01-01,10.500\n# " + _u() + b"\n"


def txt() -> bytes:
    return b"Synthetic note " + _u() + b"\n"


ACCEPTED = {
    "a.pdf": pdf,
    "a.jpg": jpg,
    "a.jpeg": jpg,
    "a.png": png,
    "a.tif": tiff,
    "a.tiff": tiff,
    "a.bmp": bmp,
    "a.heic": heic,
    "a.doc": ole,
    "a.xls": lambda: ole("xls"),
    "a.docx": docx,
    "a.xlsx": xlsx,
    "a.csv": csv,
    "a.txt": txt,
}
