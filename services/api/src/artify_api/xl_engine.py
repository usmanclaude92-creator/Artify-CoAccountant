"""Excel engine, part 1 (Phase 9): safe acceptance and analysis of uploaded workbooks.

Workbooks are UNTRUSTED input. Nothing in a workbook is ever executed: macros are never run, formulas are never evaluated,
external links are never followed. XML is parsed with defusedxml (no DTDs/entities), archives are bounded (entry count,
uncompressed size, compression ratio) before any parser sees them, and an old `.xls`, an encrypted package or a renamed
file is refused with an explanation rather than "converted" or silently accepted.

Analysis is ADVISORY: it suggests a header row, a data start row, columns and totals so the accountant can correct them.
It never modifies the uploaded file."""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import defusedxml
from defusedxml import ElementTree as SafeET
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_MAGIC = b"PK\x03\x04"
MAX_ZIP_ENTRIES = 3000
MAX_UNCOMPRESSED = 250 * 1024 * 1024
MAX_RATIO = 150
SCAN_ROWS = 5000
FORMULA_CAP = 3000
CATEGORIES = (
    "sales_register",
    "purchase_register",
    "expense_register",
    "bank_transactions",
    "customer_ledger",
    "supplier_ledger",
    "general_ledger",
    "journal_voucher",
    "trial_balance",
    "vat_summary",
    "daily_closing",
    "monthly_closing",
    "payroll_register",
    "fixed_asset_register",
    "inventory_register",
    "custom_report",
)


class WorkbookError(Exception):
    """A workbook that must not be accepted. `code` is stable; `message` is shown to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class UploadInfo:
    kind: str  # xlsx | xlsm
    size: int
    sha256: str
    warnings: list[dict[str, str]] = field(default_factory=list)
    has_vba: bool = False


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    if not name or len(name) > 200 or re.search(r"[\x00-\x1f]", name):
        raise WorkbookError("invalid_filename", "The file name is not valid.")
    return name


def inspect_upload(path: Path, filename: str, *, max_bytes: int, allow_macros: bool) -> UploadInfo:
    """Accept or refuse an uploaded workbook BEFORE any workbook library opens it."""
    name = _safe_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    size = path.stat().st_size
    if size == 0:
        raise WorkbookError("empty_file", "The file is empty.")
    if size > max_bytes:
        raise WorkbookError("file_too_large", f"The workbook is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    with path.open("rb") as f:
        head = f.read(8)
    if ext == "xls" or (ext in ("xlsx", "xlsm") and head.startswith(OLE_MAGIC)):
        raise WorkbookError(
            "legacy_or_encrypted_workbook",
            "This is an old Excel (.xls) workbook or a password-protected file. Open it in Excel and use "
            "Save As → Excel Workbook (.xlsx) (remove the password first), then upload that file. Artify does not "
            "rename or convert old formats automatically.",
        )
    if ext not in ("xlsx", "xlsm"):
        raise WorkbookError("unsupported_extension", "Only .xlsx and .xlsm workbooks can be used as templates.")
    if not head.startswith(ZIP_MAGIC):
        raise WorkbookError("corrupt_workbook", "The file is not a valid Excel workbook (it is not a zip package).")
    warnings: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise WorkbookError("unsafe_workbook", "The workbook contains too many internal parts.")
            total = sum(i.file_size for i in infos)
            if total > MAX_UNCOMPRESSED:
                raise WorkbookError("unsafe_workbook", "The workbook expands to an unreasonable size.")
            for i in infos:
                if ".." in i.filename.split("/") or i.filename.startswith("/"):
                    raise WorkbookError("unsafe_workbook", "The workbook contains an unsafe internal path.")
                if i.file_size > 5 * 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                    raise WorkbookError("unsafe_workbook", "The workbook looks like a compression bomb.")
            names = {i.filename for i in infos}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise WorkbookError("corrupt_workbook", "The file is a zip archive but not an Excel workbook.")
            bad = z.testzip()
            if bad:
                raise WorkbookError("corrupt_workbook", f"The workbook is damaged (part {bad} fails its checksum).")
            for part in ("[Content_Types].xml", "xl/workbook.xml"):
                try:
                    SafeET.fromstring(z.read(part))
                except defusedxml.DefusedXmlException:
                    raise WorkbookError(
                        "unsafe_workbook", "The workbook uses XML features that are not allowed."
                    ) from None
                except SafeET.ParseError:
                    raise WorkbookError("corrupt_workbook", "The workbook's structure cannot be read.") from None
            has_vba = "xl/vbaProject.bin" in names
            if has_vba and ext == "xlsx":
                raise WorkbookError(
                    "macro_in_xlsx",
                    "The file contains macros but is named .xlsx. It was not accepted; save it as .xlsm.",
                )
            if ext == "xlsm" and not allow_macros:
                raise WorkbookError(
                    "macros_not_enabled",
                    "Macro-enabled workbooks (.xlsm) are not enabled for this organization. Macros are never executed by Artify.",
                )
            if has_vba:
                warnings.append(
                    {
                        "code": "macros_kept_not_run",
                        "message": "The workbook contains macros. They are kept in the file but never run by Artify.",
                    }
                )
            if any(n.startswith("xl/externalLinks/") for n in names):
                warnings.append(
                    {
                        "code": "external_links",
                        "message": "The workbook links to other files. Links are not followed and are dropped from generated workbooks.",
                    }
                )
    except zipfile.BadZipFile:
        raise WorkbookError("corrupt_workbook", "The file is damaged or is not an Excel workbook.") from None
    return UploadInfo(ext, size, sha256_of(path), warnings, has_vba)


# ------------------------------------------------------------------ analysis
FEATURE_PARTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "xl/pivotTables/",
        "pivot_tables",
        "unsupported",
        "Pivot tables are not preserved when the workbook is rewritten.",
    ),
    ("xl/slicers/", "slicers", "unsupported", "Slicers are not preserved when the workbook is rewritten."),
    (
        "xl/ctrlProps/",
        "form_controls",
        "unsupported",
        "Form controls are not preserved when the workbook is rewritten.",
    ),
    ("xl/charts/", "charts", "unsupported", "Charts are not preserved when the workbook is rewritten."),
    ("xl/externalLinks/", "external_links", "unsupported", "External links are dropped; values come from the cells."),
    ("xl/embeddings/", "embedded_objects", "unsupported", "Embedded objects are not preserved."),
    ("xl/connections.xml", "data_connections", "unsupported", "Data connections are not preserved or refreshed."),
    ("xl/threadedComments/", "threaded_comments", "warned", "Threaded comments may be reduced to plain notes."),
    ("xl/media/", "images", "warned", "Images are kept when possible; check them in the generated file."),
)


def _formula_text(v: Any) -> str:
    return str(getattr(v, "text", v))


def _is_formula(cell: Any) -> bool:
    return bool(cell.data_type == "f")


def _row_cells(ws: Any, r: int, max_col: int) -> list[Any]:
    return [ws.cell(row=r, column=c) for c in range(1, max_col + 1)]


def _text_value(cell: Any) -> str | None:
    v = cell.value
    if v is None or _is_formula(cell):
        return None
    s = str(v).strip()
    return s or None


def analyze(path: Path, kind: str) -> dict[str, Any]:
    """Describe the workbook for the mapping builder. Read-only; the file is not changed."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        sheet_xml_x14 = False
        for n in names:
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml") and z.getinfo(n).file_size < 4 * 1024 * 1024:
                data = z.read(n)
                if (
                    b"x14:conditionalFormatting" in data
                    or b"x14:sparklineGroups" in data
                    or b"x14:dataValidation" in data
                ):
                    sheet_xml_x14 = True
                    break
    wb = load_workbook(path, data_only=False, keep_vba=False, keep_links=False)
    sheets: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        sheets.append(_analyze_sheet(ws))
    defined = []
    for name, dn in wb.defined_names.items():
        defined.append({"name": name, "refers_to": str(dn.attr_text)[:200], "scope": "workbook"})
    for ws in wb.worksheets:
        for name, dn in ws.defined_names.items():
            defined.append({"name": name, "refers_to": str(dn.attr_text)[:200], "scope": ws.title})
    fidelity: list[dict[str, str]] = [
        {"feature": "values_and_text", "status": "preserved", "detail": "Cell values, text, dates and numbers."},
        {
            "feature": "formulas",
            "status": "preserved",
            "detail": "Formulas are kept as written. Artify does not recalculate them; Excel recalculates when the file is opened.",
        },
        {
            "feature": "styles",
            "status": "preserved",
            "detail": "Fonts, fills, borders, number formats, alignment, column widths, row heights.",
        },
        {
            "feature": "merged_cells",
            "status": "preserved",
            "detail": "Merged ranges are kept (ranges below inserted rows are moved).",
        },
        {
            "feature": "layout",
            "status": "preserved",
            "detail": "Freeze panes, filters, print areas and page setup are kept; ranges that cover the data region are extended.",
        },
        {
            "feature": "tables_names",
            "status": "preserved",
            "detail": "Excel tables and defined names are kept; those that cover the data region are extended.",
        },
    ]
    present: set[str] = set()
    for prefix, key, status, detail in FEATURE_PARTS:
        if any(n.startswith(prefix) or n == prefix for n in names):
            if key == "images" and not any(n.startswith("xl/media/") for n in names):
                continue
            fidelity.append({"feature": key, "status": status, "detail": detail})
            present.add(key)
    if "xl/vbaProject.bin" in names:
        fidelity.append(
            {
                "feature": "macros",
                "status": "warned",
                "detail": "Macros are kept in .xlsm output but never run; re-test them in Excel.",
            }
        )
        present.add("macros")
    if sheet_xml_x14:
        fidelity.append(
            {
                "feature": "extended_conditional_formats",
                "status": "warned",
                "detail": "Extended conditional formats, sparklines or validations (Excel 2010+) may be lost.",
            }
        )
    if any(s["conditional_formats"] for s in sheets):
        fidelity.append(
            {
                "feature": "conditional_formats",
                "status": "preserved",
                "detail": "Basic conditional formats are kept; ranges are not extended over new rows.",
            }
        )
    if any(s["protected"] for s in sheets):
        fidelity.append(
            {
                "feature": "sheet_protection",
                "status": "warned",
                "detail": "Protected sheets cannot be filled; remove protection in Excel first.",
            }
        )
    risk = (
        "unsupported"
        if any(f["status"] == "unsupported" for f in fidelity)
        else "warned"
        if any(f["status"] == "warned" for f in fidelity)
        else "none"
    )
    return {
        "kind": kind,
        "sheet_count": len(sheets),
        "sheets": sheets,
        "defined_names": defined[:200],
        "fidelity": fidelity,
        "risk_level": risk,
        "advice": "Suggestions below are advisory. Check the header row, the first data row and the columns before mapping.",
    }


def _analyze_sheet(ws: Any) -> dict[str, Any]:
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    scan_to = min(max_row, SCAN_ROWS)
    formulas: list[dict[str, str]] = []
    formula_total = 0
    row_formulas: dict[int, list[str]] = {}
    row_text: dict[int, list[tuple[int, str, bool]]] = {}
    empty_rows: list[int] = []
    col_used = [False] * (max_col + 1)
    totals: list[dict[str, Any]] = []
    balances: list[dict[str, Any]] = []
    for r in range(1, scan_to + 1):
        cells = _row_cells(ws, r, max_col)
        non_empty = [c for c in cells if c.value is not None and str(c.value).strip() != ""]
        if not non_empty:
            empty_rows.append(r)
            continue
        texts: list[tuple[int, str, bool]] = []
        for c in non_empty:
            col_used[c.column] = True
            if _is_formula(c):
                formula_total += 1
                row_formulas.setdefault(r, []).append(get_column_letter(c.column))
                if len(formulas) < FORMULA_CAP:
                    formulas.append({"cell": c.coordinate, "formula": _formula_text(c.value)[:200]})
                f = _formula_text(c.value).upper()
                if re.search(r"\b(SUM|SUBTOTAL)\(", f):
                    totals.append({"row": r, "cell": c.coordinate, "reason": "sum_formula"})
            else:
                t = _text_value(c)
                if t:
                    texts.append((c.column, t, bool(c.font and c.font.b)))
                    low = t.lower()
                    if re.search(r"\b(total|sub-?total|grand total)\b", low):
                        totals.append({"row": r, "cell": c.coordinate, "reason": "label"})
                    if re.search(r"\b(opening|closing|brought forward|carried forward)\s+balance\b", low):
                        balances.append({"row": r, "cell": c.coordinate, "label": t[:80]})
        row_text[r] = texts
    # header candidates: rows (first 60) with the most text-only cells; bold rows get a bonus
    candidates: list[tuple[float, int]] = []
    for r in range(1, min(scan_to, 60) + 1):
        texts = row_text.get(r, [])
        strings = [t for t in texts if not re.fullmatch(r"[-+]?[\d.,]+", t[1])]
        if len(strings) >= 2:
            score = len(strings) + (1.5 if sum(1 for t in strings if t[2]) >= len(strings) / 2 else 0) - r * 0.01
            candidates.append((score, r))
    candidates.sort(reverse=True)
    header_candidates = []
    for _, r in candidates[:3]:
        header_candidates.append({"row": r, "columns": {get_column_letter(c): t for c, t, _b in row_text.get(r, [])}})
    suggested_header = header_candidates[0]["row"] if header_candidates else None
    suggested_start = None
    if isinstance(suggested_header, int):
        suggested_start = suggested_header + 1
    merged = [str(r) for r in ws.merged_cells.ranges]
    tables = [{"name": t.name, "ref": t.ref} for t in ws.tables.values()]
    blocks: list[dict[str, int]] = []
    start = None
    gap = 0
    for r in range(1, scan_to + 1):
        if r in row_text:
            if start is None:
                start = r
            gap = 0
            end = r
        elif start is not None:
            gap += 1
            if gap >= 2:
                blocks.append({"first_row": start, "last_row": end})
                start, gap = None, 0
    if start is not None:
        blocks.append({"first_row": start, "last_row": end})
    return {
        "name": ws.title,
        "visibility": ws.sheet_state,
        "protected": bool(ws.protection and ws.protection.sheet),
        "dimensions": ws.dimensions if max_row else None,
        "max_row": max_row,
        "max_column": max_col,
        "scanned_rows": scan_to,
        "truncated": max_row > SCAN_ROWS,
        "merged_ranges": merged[:200],
        "tables": tables,
        "freeze_panes": ws.freeze_panes,
        "auto_filter": ws.auto_filter.ref if ws.auto_filter and ws.auto_filter.ref else None,
        "print_area": str(ws.print_area) if ws.print_area else None,
        "conditional_formats": len(ws.conditional_formatting),
        "data_validations": len(ws.data_validations.dataValidation) if ws.data_validations else 0,
        "images": len(getattr(ws, "_images", [])),
        "formula_count": formula_total,
        "formula_cells": formulas[:FORMULA_CAP],
        "row_formulas": {str(k): v for k, v in list(row_formulas.items())[:400]},
        "header_candidates": header_candidates,
        "suggested_header_row": suggested_header,
        "suggested_start_row": suggested_start,
        "column_names": header_candidates[0]["columns"] if header_candidates else {},
        "totals_rows": sorted({t["row"] for t in totals})[:20],
        "totals_hints": totals[:20],
        "balance_rows": balances[:10],
        "empty_rows": empty_rows[:50],
        "empty_columns": [get_column_letter(c) for c in range(1, max_col + 1) if not col_used[c]][:50],
        "blocks": blocks[:20],
    }


def sheet_by_name(analysis: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((s for s in analysis.get("sheets", []) if s["name"] == name), None)


def column_index(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


COLUMN_RE = re.compile(r"^[A-Za-z]{1,3}$")
CELL_RE = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}$")


def valid_column(letter: str) -> bool:
    return bool(COLUMN_RE.match(letter)) and 1 <= column_index(letter) <= 16384


def valid_cell(ref: str) -> bool:
    if not CELL_RE.match(ref):
        return False
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref)
    assert m
    return 1 <= column_index(m.group(1)) <= 16384 and 1 <= int(m.group(2)) <= 1048576


def formula_inventory(path: Path) -> dict[str, dict[str, str]]:
    """{sheet: {coordinate: formula}} for comparing a generated workbook with its template."""
    wb = load_workbook(path, data_only=False, keep_vba=False, keep_links=False)
    out: dict[str, dict[str, str]] = {}
    for ws in wb.worksheets:
        d: dict[str, str] = {}
        for row in ws.iter_rows():
            for c in row:
                if _is_formula(c):
                    d[c.coordinate] = _formula_text(c.value)
        out[ws.title] = d
    return out


def iter_sheet_names(analysis: dict[str, Any]) -> Iterable[str]:
    return (s["name"] for s in analysis.get("sheets", []))
