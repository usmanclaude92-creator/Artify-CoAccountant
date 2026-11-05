"""Synthetic Excel templates for Phase 9 tests. No real customer workbooks are used anywhere."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.table import Table

BOLD = Font(bold=True, name="Arial", size=11)
FILL = PatternFill("solid", fgColor="DDEBF7")
THIN = Side(style="thin", color="999999")


def purchase_template(
    path: Path, *, protected: bool = False, hidden_sheet: bool = True, with_table: bool = False, chart: bool = False
) -> Path:
    """Sheet "Purchases": title (merged A1:C1), period in B2, header row 4, one formatted placeholder row 5 with a formula in J,
    a totals footer in row 6 (SUM over F5:F5) with a merged label; sheet "Summary" referencing the footer; a hidden sheet."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Purchases"
    ws["A1"] = "Purchase register"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:C1")
    ws["A2"] = "Period from"
    ws["A3"] = "Company"
    headers = [
        "Date",
        "Voucher",
        "Supplier",
        "Invoice",
        "Narration",
        "Net",
        "VAT",
        "Gross",
        "Debit account",
        "Gross + 0 (formula)",
    ]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font, c.fill = BOLD, FILL
        c.border = Border(bottom=THIN)
    for col in "ABCDEFGHI":
        c = ws[f"{col}5"]
        c.border = Border(bottom=THIN)
        c.alignment = Alignment(horizontal="left")
    ws["A5"].number_format = "dd/mm/yyyy"
    for col in "FGH":
        ws[f"{col}5"].number_format = "#,##0.000"
    ws["J5"] = "=H5*1"
    ws["J5"].number_format = "#,##0.000"
    ws["A6"] = "Total"
    ws.merge_cells("A6:C6")
    ws["A6"].font = BOLD
    ws["F6"] = "=SUM(F5:F5)"
    ws["G6"] = "=SUM(G5:G5)"
    ws["H6"] = "=SUM(H5:H5)"
    ws["F6"].number_format = "#,##0.000"
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["E"].width = 40
    ws.row_dimensions[6].height = 24
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = "A4:J5"
    ws.print_area = "A1:J6"
    if with_table:
        ws.auto_filter.ref = None
        t = Table(displayName="Register", ref="A4:J5")
        ws.add_table(t)
    if protected:
        ws.protection.sheet = True
    summary = wb.create_sheet("Summary")
    summary["A1"] = "Total gross"
    summary["B1"] = "=Purchases!H6"
    summary["A2"] = "Company"
    wb.defined_names["TotalGross"] = DefinedName("TotalGross", attr_text="Purchases!$H$6")
    if hidden_sheet:
        hid = wb.create_sheet("Lookups")
        hid["A1"] = "private"
        hid.sheet_state = "hidden"
    if chart:
        from openpyxl.chart import BarChart, Reference

        bc = BarChart()
        bc.add_data(Reference(summary, min_col=2, min_row=1, max_row=1))
        summary.add_chart(bc, "D2")
    wb.save(path)
    return path


def simple_template(path: Path, headers: list[str], start_row: int = 2, *, formula_col: str | None = None) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Data"
    for i, h in enumerate(headers, 1):
        ws.cell(row=start_row - 1, column=i, value=h).font = BOLD
    if formula_col:
        ws[f"{formula_col}{start_row}"] = f"=A{start_row}"
    wb.save(path)
    return path


def with_part(path: Path, name: str, data: bytes, out: Path | None = None) -> Path:
    """Copy of an xlsx with an extra package part (for macro / external-link detection tests)."""
    out = out or path.with_name(path.stem + "_x" + path.suffix)
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(name, data)
    return out


def bytes_of(path: Path) -> bytes:
    return path.read_bytes()


def zip_with(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


OLE_HEAD = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 512
