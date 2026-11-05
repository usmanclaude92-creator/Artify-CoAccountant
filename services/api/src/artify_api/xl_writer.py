"""Excel engine, part 3 (Phase 9): populate the accountant's OWN workbook from a validated mapping.

The uploaded template is loaded, filled and saved as a new file; the stored original is never touched. Rules:
* Only mapped cells are written. A cell that holds a formula is never overwritten unless the mapping explicitly allows it.
* Rows are generated at the configured start row. If rows below (a totals footer) must move, formulas anywhere in the
  workbook that point at the moved rows are rewritten, ranges that end at the last template row are extended, and merged
  cells, row heights, tables, filters, print areas, conditional formats and validations are moved or extended.
* Formulas are kept as formulas. Artify does NOT recalculate them: the workbook is flagged to recalculate when Excel opens it.
* Text that could be read as a formula (`=`, `+`, `-`, `@`, tab, CR) is stored as text, never as a formula.
* Values come from the governed datasets as Decimal/date/text; money is written as a real number, not as text.
Features openpyxl cannot preserve are reported by the analysis before a template is activated (see `xl_engine`)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import copy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter, range_boundaries

from artify_api import xl_catalogue as cat
from artify_api import xl_mapping as mp

INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
REF_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")


class WriteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


# ------------------------------------------------------------------ formula reference rewriting
def _shift_row(row: int, insert_at: int, last_tpl: int, delta: int, *, is_range_end: bool) -> int:
    if row >= insert_at or (is_range_end and row == last_tpl):
        return row + delta
    return row


def rewrite_formula(formula: str, own_sheet: str, target_sheet: str, insert_at: int, last_tpl: int, delta: int) -> str:
    """Rewrite same-workbook references to `target_sheet` rows after `delta` rows are inserted at `insert_at`."""
    if not formula.startswith("="):
        return formula
    tok = Tokenizer(formula)
    changed = False
    for t in tok.items:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        value = t.value
        sheet_part, _, ref = value.rpartition("!")
        sheet = sheet_part.strip("'").replace("''", "'") if sheet_part else None
        if (sheet or own_sheet) != target_sheet:
            continue
        parts = ref.split(":")
        if len(parts) > 2 or not all(REF_RE.match(p) for p in parts):
            continue
        new_parts = []
        for i, p in enumerate(parts):
            m = REF_RE.match(p)
            assert m
            new_row = _shift_row(int(m.group(4)), insert_at, last_tpl, delta, is_range_end=(len(parts) == 2 and i == 1))
            new_parts.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{new_row}")
        new_ref = ":".join(new_parts)
        if new_ref != ref:
            t.value = (sheet_part + "!" if sheet_part else "") + new_ref
            changed = True
    return "=" + "".join(t.value for t in tok.items) if changed else formula


def _rewrite_all(wb: Any, target_sheet: str, insert_at: int, last_tpl: int, delta: int) -> None:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.data_type == "f" and isinstance(c.value, str):
                    c.value = rewrite_formula(c.value, ws.title, target_sheet, insert_at, last_tpl, delta)
    for dn in list(wb.defined_names.values()):
        new = rewrite_formula("=" + str(dn.attr_text), "", target_sheet, insert_at, last_tpl, delta)[1:]
        if new != dn.attr_text:
            dn.attr_text = new
    for ws in wb.worksheets:
        for dn in list(ws.defined_names.values()):
            new = rewrite_formula("=" + str(dn.attr_text), ws.title, target_sheet, insert_at, last_tpl, delta)[1:]
            if new != dn.attr_text:
                dn.attr_text = new


def _range_after(rng: str, insert_at: int, last_tpl: int, delta: int) -> str:
    out = []
    for part in rng.split():
        min_col, min_row, max_col, max_row = range_boundaries(part)
        new_min = min_row + delta if min_row >= insert_at else min_row
        new_max = _shift_row(max_row, insert_at, last_tpl, delta, is_range_end=True)
        out.append(f"{get_column_letter(min_col)}{new_min}:{get_column_letter(max_col)}{new_max}")
    return " ".join(out)


# ------------------------------------------------------------------ writing values
def safe_text(cell: Any, text: str) -> None:
    cell.value = text
    if text.startswith(INJECTION_PREFIXES):
        cell.data_type = "s"
        cell.quotePrefix = True


def default_format(kind: str, scale: int) -> str | None:
    if kind == cat.MONEY:
        return "#,##0" + ("." + "0" * scale if scale else "")
    if kind == cat.DATE:
        return "yyyy-mm-dd"
    return None


def set_value(cell: Any, value: Any, *, fmt: str | None, kind: str, scale: int) -> None:
    if value is None or value == "":
        cell.value = None
        return
    if isinstance(value, bool):
        cell.value = "Yes" if value else "No"
    elif isinstance(value, (Decimal, int | float, date)):
        cell.value = value
    else:
        safe_text(cell, str(value))
        return
    chosen = fmt or (default_format(kind, scale) if cell.number_format == "General" else None)
    if chosen:
        cell.number_format = chosen


def _kind_of(spec: Mapping[str, Any], dataset: cat.Dataset) -> str:
    if spec.get("kind", "field") == "field":
        f = dataset.field_map().get(str(spec.get("field")))
        return f.kind if f else cat.TEXT
    if spec.get("kind") == "calc" and spec.get("op") in ("add", "subtract"):
        return cat.MONEY
    return cat.TEXT


def _check_writable(cell: Any, allow_formula: bool, where: str) -> None:
    if cell.data_type == "f" and not allow_formula:
        raise WriteError("formula_overwrite", f"{where} holds a formula and the mapping does not allow overwriting it.")


# ------------------------------------------------------------------ structure moving (when a totals footer must move down)
def _move_structures(
    wb: Any, ws: Any, start: int, insert_at: int, last_tpl: int, delta: int, report: dict[str, Any]
) -> None:
    _rewrite_all(wb, ws.title, insert_at, last_tpl, delta)
    merged = list(ws.merged_cells.ranges)
    below = [str(r) for r in merged if r.min_row >= insert_at]
    spanning = [str(r) for r in merged if r.min_row < insert_at <= r.max_row]
    if spanning:
        report["warnings"].append(
            {
                "code": "merged_range_spans_insert",
                "message": f"A merged range ({spanning[0]}) spans the insertion point and was not extended.",
            }
        )
    for r in below:
        ws.unmerge_cells(r)
    heights = {
        r: ws.row_dimensions[r].height
        for r in list(ws.row_dimensions.keys())
        if r >= insert_at and ws.row_dimensions[r].height is not None
    }
    for r in heights:
        ws.row_dimensions[r].height = None
    ws.insert_rows(insert_at, delta)
    for r, h in heights.items():
        ws.row_dimensions[r + delta].height = h
    for r in below:
        ws.merge_cells(_range_after(r, insert_at, last_tpl, delta))
    for t in ws.tables.values():
        t.ref = _range_after(t.ref, insert_at, last_tpl, delta)
        if t.autoFilter is not None and t.autoFilter.ref:
            t.autoFilter.ref = _range_after(t.autoFilter.ref, insert_at, last_tpl, delta)
    if ws.auto_filter and ws.auto_filter.ref:
        ws.auto_filter.ref = _range_after(ws.auto_filter.ref, insert_at, last_tpl, delta)
    if ws.print_area:
        pa = str(ws.print_area).split("!")[-1].replace("$", "")
        ws.print_area = _range_after(pa, insert_at, last_tpl, delta)
    if ws.data_validations and ws.data_validations.dataValidation:
        for dv in ws.data_validations.dataValidation:
            dv.sqref = type(dv.sqref)(_range_after(str(dv.sqref), insert_at, last_tpl, delta))
    if len(ws.conditional_formatting):
        new = ConditionalFormattingList()
        for cf in ws.conditional_formatting:
            rng = _range_after(str(cf.sqref), insert_at, last_tpl, delta)
            for rule in cf.rules:
                new.add(rng, rule)
        ws.conditional_formatting = new
    report["moved_footer_rows"] = report.get("moved_footer_rows", 0) + delta


def _has_content_below(ws: Any, first_row: int) -> bool:
    for r in range(first_row, (ws.max_row or 0) + 1):
        for c in range(1, (ws.max_column or 0) + 1):
            v = ws.cell(row=r, column=c).value
            if v is not None and str(v).strip() != "":
                return True
    return False


def _fill_sheet(
    wb: Any, ws: Any, spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], scale: int, report: dict[str, Any]
) -> dict[str, Any]:
    dataset = cat.DATASETS[spec["dataset"]]
    start = int(spec["start_row"])
    tpl = int(spec.get("template_rows", 1))
    n = len(rows)
    cols = spec["columns"]
    letters = [c["column"].upper() for c in cols]
    max_col = ws.max_column or 1
    last_tpl = start + max(tpl, 1) - 1
    insert_at = start + tpl
    delta = max(0, n - tpl)
    # per-column template state captured BEFORE anything moves
    formula_src: dict[str, str] = {}
    for letter in [str(x).upper() for x in spec.get("formula_columns", []) or []]:
        cell = ws[f"{letter}{start}"]
        if cell.data_type == "f" and isinstance(cell.value, str):
            formula_src[letter] = cell.value
    if delta and _has_content_below(ws, insert_at):
        _move_structures(wb, ws, start, insert_at, last_tpl if tpl else start, delta, report)
        report.setdefault("sheets", {}).setdefault(ws.title, {})["inserted_rows"] = delta
    elif delta:
        report.setdefault("sheets", {}).setdefault(ws.title, {})["inserted_rows"] = 0
    # data-region structures (tables, filters, print area) that include the data rows grow with them even without a footer
    template_height = ws.row_dimensions[start].height
    style_src = [ws.cell(row=last_tpl if tpl else start, column=c) for c in range(1, max_col + 1)]
    for i in range(n):
        r = start + i
        if r >= start + tpl:
            for c in range(1, max_col + 1):
                ws.cell(row=r, column=c)._style = copy(style_src[c - 1]._style)
            if template_height is not None:
                ws.row_dimensions[r].height = template_height
        for col, letter in zip(cols, letters, strict=True):
            cell = ws[f"{letter}{r}"]
            _check_writable(cell, bool(col.get("allow_formula_overwrite")), f"Cell {letter}{r}")
            value = mp.resolve_column(col, rows[i])
            set_value(cell, value, fmt=col.get("format"), kind=_kind_of(col, dataset), scale=scale)
        for letter, formula in formula_src.items():
            cell = ws[f"{letter}{r}"]
            if r == start:
                continue
            if cell.value is None or cell.data_type == "f":
                cell.value = Translator(formula, origin=f"{letter}{start}").translate_formula(f"{letter}{r}")
    # leftover placeholder rows: clear mapped cells so sample content does not look like data
    for i in range(n, tpl):
        r = start + i
        for letter in letters:
            cell = ws[f"{letter}{r}"]
            if cell.data_type != "f":
                cell.value = None
    if tpl > n:
        report["warnings"].append(
            {
                "code": "placeholder_rows_left",
                "message": f"Worksheet “{ws.title}” has {tpl - n} unused template row(s); their mapped cells were cleared and formulas were left as in the template.",
            }
        )
    if n and ws.tables:
        for t in ws.tables.values():
            c0, r0, c1, r1 = range_boundaries(t.ref)
            if r0 <= start <= r1 and r1 < start + n - 1 and not delta:
                t.ref = f"{get_column_letter(c0)}{r0}:{get_column_letter(c1)}{start + n - 1}"
    return {"rows_written": n, "first_row": start, "last_row": start + n - 1 if n else None}


def _write_fixed(ws: Any, fc: Mapping[str, Any], value: Any, scale: int, kind: str) -> None:
    cell = ws[str(fc["cell"]).upper()]
    _check_writable(cell, bool(fc.get("allow_formula_overwrite")), f"Cell {cell.coordinate}")
    set_value(cell, value, fmt=fc.get("format"), kind=kind, scale=scale)


def generate(
    template_path: Path,
    kind: str,
    mapping: Mapping[str, Any],
    data: Mapping[str, Sequence[Mapping[str, Any]]],
    meta: Mapping[str, Any],
    out_path: Path,
    *,
    scale: int = 2,
) -> dict[str, Any]:
    """Fill the template and save to `out_path`. `data[sheet]` are the dataset rows for each mapped worksheet."""
    wb = load_workbook(template_path, data_only=False, keep_vba=(kind == "xlsm"), keep_links=False)
    report: dict[str, Any] = {"sheets": {}, "warnings": [], "fixed_cells": 0}
    for sh in mapping["sheets"]:
        if sh["sheet"] not in wb.sheetnames:
            raise WriteError("sheet_not_found", f"Worksheet “{sh['sheet']}” is missing from the template.")
        if wb[sh["sheet"]].protection.sheet:
            raise WriteError("protected_sheet", f"Worksheet “{sh['sheet']}” is protected.")
    # fixed cells first: they sit at template coordinates and move together with the footer if rows are inserted
    for fc in mapping.get("fixed_cells", []):
        ws = wb[fc["sheet"]]
        k = fc["kind"]
        if k == "static":
            value: Any = fc.get("value")
            ck = cat.TEXT
        elif k == "field":
            value = meta.get(fc["field"])
            ck = next((f.kind for f in cat.HEADER_FIELDS if f.key == fc["field"]), cat.TEXT)
        else:
            src = fc.get("source_sheet", fc["sheet"])
            value = mp.aggregate(fc["op"], fc.get("of"), data.get(src, []))
            owner = next(s for s in mapping["sheets"] if s["sheet"] == src)
            f = cat.DATASETS[owner["dataset"]].field_map().get(str(fc.get("of")))
            ck = f.kind if f else cat.NUMBER
        _write_fixed(ws, fc, value, scale, ck)
        report["fixed_cells"] += 1
    for sh in mapping["sheets"]:
        ws = wb[sh["sheet"]]
        info = _fill_sheet(wb, ws, sh, data.get(sh["sheet"], []), scale, report)
        report["sheets"].setdefault(sh["sheet"], {}).update(info)
    wb.calculation.fullCalcOnLoad = True  # Excel recalculates formulas when the file is opened; Artify does not
    wb.save(out_path)
    return report


# ------------------------------------------------------------------ read-back verification
def verify_output(
    out_path: Path,
    template_path: Path,
    kind: str,
    mapping: Mapping[str, Any],
    data: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, str]]:
    """Reopen the generated file and compare it with what was meant to be written. Any problem makes the export fail."""
    problems: list[dict[str, str]] = []
    try:
        wb = load_workbook(out_path, data_only=False, keep_vba=(kind == "xlsm"), keep_links=False)
    except Exception as exc:  # the file must be readable by the same library that wrote it
        return [{"code": "unreadable_output", "message": f"The generated workbook cannot be reopened: {exc}"}]
    tpl = load_workbook(template_path, data_only=False, keep_vba=(kind == "xlsm"), keep_links=False)
    if wb.sheetnames != tpl.sheetnames:
        problems.append({"code": "sheets_changed", "message": "The worksheet list differs from the template."})
    for sh in mapping["sheets"]:
        ws = wb[sh["sheet"]]
        rows = data.get(sh["sheet"], [])
        start = int(sh["start_row"])
        for idx in sorted({0, len(rows) - 1} if rows else set()):
            for col in sh["columns"]:
                want = mp.resolve_column(col, rows[idx])
                got = ws[f"{col['column'].upper()}{start + idx}"].value
                if not _same(want, got):
                    problems.append(
                        {
                            "code": "value_mismatch",
                            "message": f"{sh['sheet']}!{col['column'].upper()}{start + idx} holds {got!r}, expected {want!r}.",
                        }
                    )
        # rows above the data region must be unchanged
        t_ws = tpl[sh["sheet"]]
        for r in range(1, start):
            for c in range(1, (t_ws.max_column or 0) + 1):
                a, b = t_ws.cell(row=r, column=c).value, ws.cell(row=r, column=c).value
                fixed_here = any(
                    fc["sheet"] == sh["sheet"] and fc["cell"].upper() == ws.cell(row=r, column=c).coordinate
                    for fc in mapping.get("fixed_cells", [])
                )
                if a != b and not fixed_here:
                    problems.append(
                        {
                            "code": "header_changed",
                            "message": f"{sh['sheet']}!{ws.cell(row=r, column=c).coordinate} above the data region changed.",
                        }
                    )
                    break
    return problems


def _same(want: Any, got: Any) -> bool:
    if want in (None, "") and got in (None, ""):
        return True
    if isinstance(want, Decimal):
        try:
            return Decimal(str(got)) == want
        except Exception:
            return False
    if isinstance(want, date):
        return bool(getattr(got, "date", lambda: got)() == want)
    return str(want) == str(got)
