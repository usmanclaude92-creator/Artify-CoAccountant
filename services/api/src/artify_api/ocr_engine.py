"""OCR pipeline: page rendering, preprocessing, text recognition behind a provider interface.

No database access. Input is the ORIGINAL bytes of a document version (never modified); output is a list of `PageOcr`
with recognised lines (normalised boxes, per-line and per-word confidence), the page image that was actually read (the
"processed representation") and a quality report. Searchable PDF pages use their embedded text; scanned pages and
images are preprocessed (orientation, deskew, denoise, contrast, sharpen, blank detection, resolution assessment) and
recognised by the configured provider. The default provider is the local Tesseract binary; others plug in through
`OcrProvider` without touching the rest of the application.
"""

# ruff: noqa: S603  (the OCR binary and its arguments are fixed by configuration; file paths come from our own temp dir)
from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps

from artify_api.doc_extract import Line, Word
from artify_api.settings import Settings, get_settings

with contextlib.suppress(Exception):  # HEIC support is optional at runtime
    from pillow_heif import register_heif_opener

    register_heif_opener()

Image.MAX_IMAGE_PIXELS = 120_000_000  # decompression-bomb guard for image pages


class OcrError(Exception):
    """Recognition failed. `retryable` says whether trying again later may help (timeouts, provider outages)."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class ProviderNotConfigured(OcrError):
    def __init__(self, message: str) -> None:
        super().__init__("provider_not_configured", message, False)


@dataclass
class PageOcr:
    page_no: int
    width: int
    height: int
    source: str  # pdf_text | ocr
    lines: list[Line]
    blank: bool
    avg_confidence: float | None
    rotation_applied: int = 0
    deskew_angle: float = 0.0
    quality: dict[str, Any] = field(default_factory=dict)
    image_png: bytes = b""

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


class OcrProvider(Protocol):
    key: str
    external: bool  # does document content leave Artify's infrastructure?

    def version(self) -> str: ...
    def recognize(self, img: Image.Image, languages: list[str], page_no: int) -> list[Line]: ...
    def detect_rotation(self, img: Image.Image) -> int: ...


# ---------------------------------------------------------------- Tesseract (local)
class TesseractProvider:
    key = "tesseract"
    external = False

    def __init__(self, cmd: str = "tesseract", timeout: int = 120) -> None:
        self.cmd, self.timeout = cmd, timeout
        self._version: str | None = None

    def available(self) -> bool:
        return shutil.which(self.cmd) is not None

    def version(self) -> str:
        if self._version is None:
            if not self.available():
                raise ProviderNotConfigured("The Tesseract OCR engine is not installed on this server.")
            out = subprocess.run([self.cmd, "--version"], capture_output=True, text=True, timeout=15, check=False)
            first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "tesseract"
            self._version = first.strip()
        return self._version

    def installed_languages(self) -> set[str]:
        out = subprocess.run([self.cmd, "--list-langs"], capture_output=True, text=True, timeout=15, check=False)
        return {ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()}

    def _run(self, args: list[str], img: Image.Image) -> str:
        if not self.available():
            raise ProviderNotConfigured("The Tesseract OCR engine is not installed on this server.")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "page.png"
            img.save(path, "PNG")
            try:
                res = subprocess.run(
                    [self.cmd, str(path), "stdout", *args],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OcrError("provider_timeout", "The OCR engine did not finish in time.", True) from exc
            if res.returncode != 0:
                raise OcrError("provider_error", "The OCR engine failed on this page.", True)
            return res.stdout

    def detect_rotation(self, img: Image.Image) -> int:
        try:
            out = self._run(["--psm", "0", "-l", "osd"], img)
        except OcrError:
            return 0
        m = re.search(r"Rotate:\s*(\d+)", out)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
        if m and conf and float(conf.group(1)) >= 2.0:
            return int(m.group(1)) % 360
        return 0

    def recognize(self, img: Image.Image, languages: list[str], page_no: int) -> list[Line]:
        langs = "+".join(languages)
        tsv = self._run(["-l", langs, "--psm", "4", "--oem", "1", "tsv"], img)
        w, h = img.size
        groups: dict[tuple[int, int, int], list[tuple[str, int, int, int, int, float]]] = {}
        for row in tsv.splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
                continue
            try:
                b, p, ln = int(cols[2]), int(cols[3]), int(cols[4])
                left, top, ww, hh, conf = int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9]), float(cols[10])
            except ValueError:
                continue
            groups.setdefault((b, p, ln), []).append((cols[11].strip(), left, top, ww, hh, conf))
        lines: list[Line] = []
        for idx, key in enumerate(sorted(groups, key=lambda k: (min(x[2] for x in groups[k]), k))):
            ws = sorted(groups[key], key=lambda x: x[1])
            words = tuple(Word(t, left_ / w, (left_ + ww) / w, max(0.0, c) / 100) for t, left_, _t, ww, _hh, c in ws)
            x0, y0 = min(x[1] for x in ws) / w, min(x[2] for x in ws) / h
            x1, y1 = max(x[1] + x[3] for x in ws) / w, max(x[2] + x[4] for x in ws) / h
            confs = [max(0.0, x[5]) for x in ws]
            lines.append(
                Line(
                    page_no,
                    idx,
                    " ".join(x[0] for x in ws),
                    (x0, y0, x1, y1),
                    round(sum(confs) / len(confs) / 100, 3),
                    words,
                )
            )
        return lines


_PROVIDERS: dict[str, type[Any]] = {"tesseract": TesseractProvider}


def get_provider(key: str | None = None, settings: Settings | None = None) -> OcrProvider:
    s = settings or get_settings()
    k = key or s.ocr_provider
    if k == "tesseract":
        return TesseractProvider(s.tesseract_cmd, s.ocr_timeout_seconds)
    raise ProviderNotConfigured(f"OCR provider '{k}' is not available.")


# ---------------------------------------------------------------- preprocessing
def _ink(img: Image.Image) -> tuple[np.ndarray, float]:
    g = np.asarray(img.convert("L"), dtype=np.uint8)
    thr = max(40, min(200, float(g.mean()) - 0.6 * float(g.std())))
    return g < thr, float((g < thr).mean())


def assess(img: Image.Image) -> dict[str, Any]:
    ink, ratio = _ink(img)
    g = np.asarray(img.convert("L"), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    dpi = img.info.get("dpi", (None,))[0] if isinstance(img.info.get("dpi"), tuple) else None
    est_dpi = int(dpi) if dpi else int(img.width / 8.27)  # an A4 page is 8.27 inches wide
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    content = (
        [int(cols[0]) / img.width, int(rows[0]) / img.height, int(cols[-1]) / img.width, int(rows[-1]) / img.height]
        if len(rows) and len(cols)
        else None
    )
    return {
        "estimated_dpi": est_dpi,
        "low_resolution": est_dpi < 150,
        "ink_ratio": round(ratio, 5),
        "sharpness": round(float(lap.var()), 1),
        "contrast": round(float(g.std()), 1),
        "content_box": content,
        "is_blank": ratio < 0.0015 or float(g.std()) < 3.0,
    }


def estimate_skew(img: Image.Image) -> float:
    """Skew angle in degrees (counter-clockwise positive) by maximising the variance of the horizontal ink profile."""
    small = img.convert("L")
    small.thumbnail((900, 900))
    ink, ratio = _ink(small)
    if ratio < 0.002:
        return 0.0
    arr = Image.fromarray((~ink * 255).astype(np.uint8))

    def score(angle: float) -> float:
        rot = arr.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        prof = (np.asarray(rot) < 128).sum(axis=1).astype(np.float64)
        return float(prof.var())

    coarse = max(np.arange(-8, 8.01, 0.5), key=score)
    fine = max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score)
    return round(float(fine), 1) if score(fine) > score(0.0) * 1.02 else 0.0


def preprocess(img: Image.Image, provider: OcrProvider | None) -> tuple[Image.Image, dict[str, Any]]:
    """OCR-friendly copy of a page. The original bytes are never touched; this is a separate representation."""
    info = assess(img)
    work = ImageOps.exif_transpose(img).convert("L")
    rotation = 0
    if provider is not None and not info["is_blank"]:
        rotation = provider.detect_rotation(work)
        if rotation:
            work = work.rotate(-rotation, expand=True, fillcolor=255)
    angle = 0.0 if info["is_blank"] else estimate_skew(work)
    if angle:
        work = work.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    steps = []
    if info["contrast"] < 60:
        work = ImageOps.autocontrast(work, cutoff=1)
        steps.append("autocontrast")
    arr = np.asarray(work)
    speck = float(((arr < 100) & (np.asarray(work.filter(ImageFilter.MedianFilter(3))) >= 100)).mean())
    if speck > 0.003:
        work = work.filter(ImageFilter.MedianFilter(3))
        steps.append("denoise")
    if info["sharpness"] < 400 and not info["is_blank"]:
        work = work.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=3))
        steps.append("sharpen")
    if work.width < 1200 and not info["is_blank"]:
        scale = 1600 / work.width
        work = work.resize((int(work.width * scale), int(work.height * scale)), Image.Resampling.LANCZOS)
        steps.append("upscale")
    info.update({"rotation_applied": rotation, "deskew_angle": angle, "steps": steps})
    return work, info


def _png(img: Image.Image, max_width: int = 1600) -> bytes:
    out = img.convert("L") if img.mode not in ("L", "RGB") else img
    if out.width > max_width:
        out = out.resize((max_width, int(out.height * max_width / out.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF native text
def _pdf_native_lines(page: Any, page_no: int) -> list[Line]:
    w, h = page.get_size()
    tp = page.get_textpage()
    n = tp.count_chars()
    if n == 0:
        return []
    text = tp.get_text_range(0, n)
    lines: list[Line] = []
    segs = [(m.start(), m.end()) for m in re.finditer(r"[^\r\n]+", text)]
    for a, b in segs:
        chunk = text[a:b]
        if not chunk.strip():
            continue
        words: list[Word] = []
        boxes = []
        for m in re.finditer(r"\S+", chunk):
            wb = []
            for i in range(a + m.start(), a + m.end()):
                with contextlib.suppress(Exception):
                    wb.append(tp.get_charbox(i))
            if not wb:
                continue
            x0, x1 = min(c[0] for c in wb) / w, max(c[2] for c in wb) / w
            y0, y1 = 1 - max(c[3] for c in wb) / h, 1 - min(c[1] for c in wb) / h
            words.append(Word(m.group(0), x0, x1, 1.0))
            boxes.append((x0, y0, x1, y1))
        if not words:
            continue
        x0, y0 = min(b_[0] for b_ in boxes), min(b_[1] for b_ in boxes)
        x1, y1 = max(b_[2] for b_ in boxes), max(b_[3] for b_ in boxes)
        lines.append(Line(page_no, len(lines), " ".join(wd.text for wd in words), (x0, y0, x1, y1), 1.0, tuple(words)))
    ordered = sorted(lines, key=lambda ln: (round(ln.bbox[1], 3), ln.bbox[0]))
    return [Line(ln.page, i, ln.text, ln.bbox, ln.conf, ln.words) for i, ln in enumerate(ordered)]


# ---------------------------------------------------------------- orchestration
@dataclass
class OcrRun:
    provider: str
    engine_version: str
    languages: list[str]
    pages: list[PageOcr]
    external: bool = False


def count_pages(data: bytes, ext: str) -> int:
    """Cheap page count (no recognition) so quotas can be checked before the expensive work."""
    if ext == "pdf":
        try:
            return len(pdfium.PdfDocument(data))
        except Exception as exc:
            raise OcrError("unreadable_document", "The PDF could not be opened.", False) from exc
    try:
        with Image.open(io.BytesIO(data)) as im:
            return int(getattr(im, "n_frames", 1))
    except Exception as exc:
        raise OcrError("unreadable_document", "The image could not be opened.", False) from exc


def run_ocr(
    data: bytes,
    ext: str,
    languages: list[str],
    provider: OcrProvider,
    *,
    max_pages: int = 200,
    dpi: int = 200,
    progress: Any = None,
) -> OcrRun:
    version = provider.version()
    pages: list[PageOcr] = []
    if ext == "pdf":
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:
            raise OcrError("unreadable_document", "The PDF could not be opened.", False) from exc
        total = len(pdf)
        if total > max_pages:
            raise OcrError("too_many_pages", f"The document has {total} pages; the limit is {max_pages}.", False)
        for i in range(total):
            pg = pdf[i]
            native = _pdf_native_lines(pg, i + 1)
            chars = sum(len(ln.text) for ln in native)
            scale = dpi / 72
            pil = pg.render(scale=scale).to_pil()
            if chars >= 25:
                w, h = pg.get_size()
                pages.append(
                    PageOcr(
                        i + 1,
                        int(w),
                        int(h),
                        "pdf_text",
                        native,
                        False,
                        100.0,
                        quality={"source": "embedded text"},
                        image_png=_png(pil, 1400),
                    )
                )
            else:
                pages.append(_ocr_image(pil, i + 1, languages, provider))
            if progress:
                progress(i + 1, total)
    else:
        try:
            im = Image.open(io.BytesIO(data))
            frames = int(getattr(im, "n_frames", 1))
        except Exception as exc:
            raise OcrError("unreadable_document", "The image could not be opened.", False) from exc
        if frames > max_pages:
            raise OcrError("too_many_pages", f"The image has {frames} pages; the limit is {max_pages}.", False)
        for i in range(frames):
            try:
                im.seek(i)
                frame = im.convert("RGB")
            except Exception as exc:
                raise OcrError("unreadable_document", "A page of the image could not be read.", False) from exc
            pages.append(_ocr_image(frame, i + 1, languages, provider))
            if progress:
                progress(i + 1, frames)
    return OcrRun(provider.key, version, languages, pages, provider.external)


def _ocr_image(img: Image.Image, page_no: int, languages: list[str], provider: OcrProvider) -> PageOcr:
    work, info = preprocess(img, provider)
    if info["is_blank"]:
        return PageOcr(
            page_no,
            work.width,
            work.height,
            "ocr",
            [],
            True,
            None,
            info["rotation_applied"],
            info["deskew_angle"],
            info,
            _png(work),
        )
    lines = provider.recognize(work, languages, page_no)
    confs = [ln.conf for ln in lines if ln.conf is not None]
    avg = round(sum(confs) / len(confs) * 100, 2) if confs else None
    if not lines:
        info["note"] = "no text recognised"
    return PageOcr(
        page_no,
        work.width,
        work.height,
        "ocr",
        lines,
        not lines and info["ink_ratio"] < 0.003,
        avg,
        info["rotation_applied"],
        info["deskew_angle"],
        info,
        _png(work),
    )
