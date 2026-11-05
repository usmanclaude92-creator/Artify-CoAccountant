"""Synthetic document fixtures for Phase 7 tests: searchable PDFs, scanned pages and images generated in memory.
No real customer documents are ever used."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
PAGE = (1654, 2339)  # A4 at 200 dpi

INVOICE_LINES = [
    "Al Noor Trading LLC",
    "Muscat, Sultanate of Oman",
    "VAT No: OM1234567890",
    "TAX INVOICE",
    "Invoice No: INV-2025-0042",
    "Invoice Date: 2025-03-14",
    "Due Date: 2025-04-13",
    "Bill To:",
    "Gulf Builders Co",
    "Seeb, Muscat",
    "Description            Qty    Unit Price    Amount",
    "Cement bags 50kg        10        2.500     25.000",
    "Steel bars 12mm          4       30.000    120.000",
    "Transport                1       15.000     15.000",
    "Subtotal                                   160.000",
    "VAT 5%                                       8.000",
    "Total                                      168.000 OMR",
    "Payment Terms: Net 30",
]

STATEMENT_1 = [
    "Bank Muscat",
    "STATEMENT OF ACCOUNT",
    "Account Holder: Al Noor Trading LLC",
    "Account No: 0123456789012",
    "Currency: OMR",
    "Statement Period: 2025-03-01 to 2025-03-31",
    "Opening Balance 1,000.000",
    "Date         Description                Debit      Credit     Balance",
    "2025-03-01   Salary transfer ACME                  500.000   1,500.000",
    "2025-03-02   Card purchase supermarket   45.250              1,454.750",
    "2025-03-03   Cheque deposit                        300.000   1,754.750",
]
STATEMENT_2 = [
    "Page 2 of 2",
    "Date         Description                Debit      Credit     Balance",
    "2025-03-05   Rent March                 400.000              1,354.750",
    "2025-03-31   Bank charges                 4.750              1,350.000",
    "Closing Balance 1,350.000",
]


def draw_page(
    lines: list[str],
    *,
    mono: bool = False,
    size: int = 30,
    noise: float = 0.0,
    blur: float = 0.0,
    rotate: float = 0.0,
    arabic: list[str] | None = None,
) -> Image.Image:
    font = ImageFont.truetype(MONO if mono else SANS, size)
    img = Image.new("RGB", PAGE, "white")
    d = ImageDraw.Draw(img)
    y = 150
    for t in lines:
        d.text((120, y), t, font=font, fill="black")
        y += size + 28
    for t in arabic or []:
        d.text((900, y), t, font=ImageFont.truetype(SANS, size + 4), fill="black", direction="rtl", language="ar")
        y += size + 28
    if noise:
        px = img.load()
        rnd = random.Random(7)
        for _ in range(int(PAGE[0] * PAGE[1] * noise)):
            x, yy = rnd.randrange(PAGE[0]), rnd.randrange(PAGE[1])
            px[x, yy] = (0, 0, 0) if rnd.random() < 0.5 else (255, 255, 255)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    return img


def png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def jpeg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92, dpi=(200, 200))
    return b.getvalue()


def bmp(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "BMP")
    return b.getvalue()


def tiff(pages: list[Image.Image]) -> bytes:
    b = io.BytesIO()
    pages[0].save(b, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_lzw", dpi=(200, 200))
    return b.getvalue()


def scanned_pdf(pages: list[Image.Image]) -> bytes:
    """An image-only PDF (a scan): there is NO embedded text."""
    b = io.BytesIO()
    pages[0].save(b, "PDF", save_all=True, append_images=pages[1:], resolution=200.0)
    return b.getvalue()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(pages: list[list[str]], mono: bool = False, size: int = 10) -> bytes:
    """A searchable PDF with real text objects (Courier keeps table columns aligned)."""
    objs: list[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    font_obj = 3 + 2 * n_pages
    for i, lines in enumerate(pages):
        content = ["BT", f"/F1 {size} Tf", f"{size + 3} TL", "50 790 Td"]
        for ln in lines:
            content.append(f"({_esc(ln)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1", "replace")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font_obj} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /" + (b"Courier" if mono else b"Helvetica") + b" >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
