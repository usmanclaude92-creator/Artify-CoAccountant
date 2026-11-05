"""Excel engine, part 2 (Phase 9): the declarative mapping language, its validation and its value resolution. Pure.

A mapping binds worksheet columns and fixed cells to governed dataset fields, static text or whitelisted calculations. It is
data validated against a closed schema: no scripts, no Excel formulas supplied by customers and no `eval` exist. The same
`resolve_*` functions drive the preview and the generated workbook, so the preview always shows what will be written."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from artify_api import xl_catalogue as cat
from artify_api import xl_engine as eng

FORMAT_RE = re.compile(r"^[0-9#,.\-+() %$\"A-Za-z/:;_*\[\]@ ]{1,60}$")
MAX_SHEETS = 10
MAX_COLUMNS = 120
MAX_FIXED = 300


def issue(code: str, message: str, path: str = "", severity: str = "error") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message, "path": path}


def mapped_columns(sheet_spec: Mapping[str, Any]) -> list[str]:
    return [str(c.get("column", "")).upper() for c in sheet_spec.get("columns", [])]


def validate_mapping(defn: Mapping[str, Any], analysis: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every problem with a mapping against the workbook analysis and the catalogue. Errors block activation and export;
    warnings are shown. Nothing is ignored silently."""
    out: list[dict[str, str]] = []
    sheets = defn.get("sheets")
    fixed = defn.get("fixed_cells", [])
    if not isinstance(sheets, list) or not sheets:
        return [issue("no_sheets", "Add at least one worksheet mapping.", "sheets")]
    if len(sheets) > MAX_SHEETS:
        out.append(issue("too_many_sheets", f"At most {MAX_SHEETS} worksheets can be mapped.", "sheets"))
    if not isinstance(fixed, list) or len(fixed) > MAX_FIXED:
        out.append(issue("too_many_fixed_cells", f"At most {MAX_FIXED} fixed cells can be mapped.", "fixed_cells"))
        fixed = []
    seen_sheets: set[str] = set()
    sheet_cols: dict[str, set[str]] = {}
    sheet_start: dict[str, int] = {}
    for si, sh in enumerate(sheets):
        p = f"sheets[{si}]"
        name = sh.get("sheet")
        info = eng.sheet_by_name(dict(analysis), name) if isinstance(name, str) else None
        if info is None:
            out.append(issue("sheet_not_found", f"Worksheet “{name}” does not exist in this template.", p))
            continue
        if name in seen_sheets:
            out.append(
                issue("duplicate_sheet", f"Worksheet “{name}” is mapped twice; use one mapping per worksheet.", p)
            )
        seen_sheets.add(name)
        if info["visibility"] != "visible":
            out.append(issue("hidden_sheet", f"Worksheet “{name}” is hidden.", p, "warning"))
        if info["protected"]:
            out.append(
                issue(
                    "protected_sheet",
                    f"Worksheet “{name}” is protected. Remove the protection in Excel, then upload the template again.",
                    p,
                )
            )
        ds = cat.DATASETS.get(str(sh.get("dataset")))
        if ds is None:
            out.append(issue("unknown_dataset", f"Unknown data source “{sh.get('dataset')}”.", p))
            continue
        start = sh.get("start_row")
        header = sh.get("header_row")
        if not isinstance(start, int) or isinstance(start, bool) or not 1 <= start <= 1048576:
            out.append(
                issue(
                    "invalid_start_row",
                    "The data start row must be a whole number between 1 and 1,048,576.",
                    f"{p}.start_row",
                )
            )
            start = None
        if header is not None and (
            not isinstance(header, int) or isinstance(header, bool) or not 1 <= header <= 1048576
        ):
            out.append(
                issue(
                    "invalid_header_row",
                    "The header row must be a whole number between 1 and 1,048,576.",
                    f"{p}.header_row",
                )
            )
            header = None
        if start is not None and isinstance(header, int) and start <= header:
            out.append(issue("start_before_header", "The data must start below the header row.", f"{p}.start_row"))
        if start is not None:
            sheet_start[name] = start
            if info["max_row"] and start > info["max_row"] + 5000:
                out.append(
                    issue(
                        "start_row_far",
                        "The data start row is far below the used part of the worksheet.",
                        f"{p}.start_row",
                        "warning",
                    )
                )
        tr = sh.get("template_rows", 1)
        if not isinstance(tr, int) or isinstance(tr, bool) or not 0 <= tr <= 1000:
            out.append(
                issue(
                    "invalid_template_rows",
                    "Template placeholder rows must be between 0 and 1000.",
                    f"{p}.template_rows",
                )
            )
        cols = sh.get("columns", [])
        if not isinstance(cols, list) or not cols:
            out.append(issue("no_columns", f"Worksheet “{name}” has no mapped columns.", f"{p}.columns"))
            cols = []
        if len(cols) > MAX_COLUMNS:
            out.append(
                issue("too_many_columns", f"At most {MAX_COLUMNS} columns can be mapped per worksheet.", f"{p}.columns")
            )
        used: dict[str, int] = {}
        fields = ds.field_map()
        mapped_fields: set[str] = set()
        formula_row = (info.get("row_formulas") or {}).get(str(start), []) if start else []
        for ci, c in enumerate(cols):
            cp = f"{p}.columns[{ci}]"
            letter = str(c.get("column", "")).upper()
            if not eng.valid_column(letter):
                out.append(issue("invalid_column", f"“{c.get('column')}” is not a valid Excel column.", cp))
                continue
            if letter in used:
                out.append(
                    issue(
                        "duplicate_target",
                        f"Column {letter} is mapped more than once (also entry {used[letter] + 1}).",
                        cp,
                    )
                )
            used[letter] = ci
            out.extend(_validate_value_spec(c, ds, cp, fields))
            for fld in _fields_of(c):
                if fld in fields:
                    mapped_fields.add(fld)
            if letter in formula_row and not c.get("allow_formula_overwrite"):
                out.append(
                    issue(
                        "formula_overwrite",
                        f"Column {letter} holds a formula in row {start}. Artify will not overwrite it unless you explicitly allow it for this column.",
                        cp,
                    )
                )
            if c.get("format") is not None and not FORMAT_RE.match(str(c["format"])):
                out.append(
                    issue(
                        "invalid_format",
                        "The number/date format contains characters that are not allowed.",
                        f"{cp}.format",
                    )
                )
        for letter in sh.get("formula_columns", []) or []:
            if not eng.valid_column(str(letter)):
                out.append(
                    issue("invalid_formula_column", f"“{letter}” is not a valid Excel column.", f"{p}.formula_columns")
                )
            elif str(letter).upper() in used:
                out.append(
                    issue(
                        "formula_column_mapped",
                        f"Column {str(letter).upper()} is both mapped and a formula column.",
                        f"{p}.formula_columns",
                    )
                )
            elif start and str(letter).upper() not in formula_row:
                out.append(
                    issue(
                        "formula_column_empty",
                        f"Column {str(letter).upper()} has no formula in row {start} to carry down.",
                        f"{p}.formula_columns",
                        "warning",
                    )
                )
        for req in ds.required:
            if req not in mapped_fields:
                out.append(
                    issue(
                        "required_field_unmapped",
                        f"The required field “{fields[req].label if req in fields else req}” is not mapped on worksheet “{name}”.",
                        p,
                    )
                )
        sheet_cols[name] = set(used)
    seen_cells: dict[tuple[str, str], int] = {}
    for fi, fc in enumerate(fixed):
        p = f"fixed_cells[{fi}]"
        sname = fc.get("sheet")
        info = eng.sheet_by_name(dict(analysis), sname) if isinstance(sname, str) else None
        if info is None:
            out.append(issue("sheet_not_found", f"Worksheet “{sname}” does not exist in this template.", p))
            continue
        ref = str(fc.get("cell", "")).upper()
        if not eng.valid_cell(ref):
            out.append(issue("invalid_cell", f"“{fc.get('cell')}” is not a valid cell reference.", p))
            continue
        key = (sname, ref)
        if key in seen_cells:
            out.append(issue("duplicate_target", f"Cell {ref} on “{sname}” is mapped more than once.", p))
        seen_cells[key] = fi
        if info["protected"]:
            out.append(issue("protected_sheet", f"Worksheet “{sname}” is protected.", p))
        m = re.match(r"^([A-Z]+)(\d+)$", ref)
        assert m
        col, row = m.group(1), int(m.group(2))
        if col in sheet_cols.get(sname, set()) and row >= sheet_start.get(sname, 10**9):
            out.append(
                issue(
                    "fixed_cell_in_data_region",
                    f"Cell {ref} lies inside the data columns of “{sname}” where rows are generated.",
                    p,
                )
            )
        if any(f["cell"] == ref for f in info["formula_cells"]) and not fc.get("allow_formula_overwrite"):
            out.append(
                issue(
                    "formula_overwrite",
                    f"Cell {ref} on “{sname}” holds a formula. Artify will not overwrite it unless you explicitly allow it.",
                    p,
                )
            )
        kind = fc.get("kind")
        if kind == "static":
            if not str(fc.get("value", "")).strip():
                out.append(issue("missing_static_value", f"Cell {ref} is a static value but has no text.", p))
        elif kind == "field":
            if fc.get("field") not in {f.key for f in cat.HEADER_FIELDS}:
                out.append(issue("unknown_field", f"“{fc.get('field')}” is not an available header field.", p))
        elif kind == "calc":
            if fc.get("op") not in cat.AGGREGATES:
                out.append(
                    issue(
                        "unknown_calc",
                        f"Calculation “{fc.get('op')}” is not allowed (use {', '.join(cat.AGGREGATES)}).",
                        p,
                    )
                )
            owner = next((s for s in sheets if s.get("sheet") == fc.get("source_sheet", sname)), None)
            ds = cat.DATASETS.get(str(owner.get("dataset"))) if owner else None
            if fc.get("op") != "count" and (ds is None or fc.get("of") not in ds.field_map()):
                out.append(
                    issue(
                        "unknown_field", f"“{fc.get('of')}” is not a field of the data source for this calculation.", p
                    )
                )
            if not owner:
                out.append(
                    issue("calc_without_source", "A calculated cell needs a mapped worksheet as its data source.", p)
                )
        else:
            out.append(issue("unknown_kind", "A fixed cell must be static, field or calc.", p))
        if fc.get("format") is not None and not FORMAT_RE.match(str(fc["format"])):
            out.append(
                issue(
                    "invalid_format", "The number/date format contains characters that are not allowed.", f"{p}.format"
                )
            )
    return out


def _fields_of(c: Mapping[str, Any]) -> list[str]:
    if c.get("kind", "field") == "field":
        return [str(c.get("field"))]
    if c.get("kind") == "calc":
        return [str(x) for x in c.get("fields", [])]
    return []


def _validate_value_spec(
    c: Mapping[str, Any], ds: cat.Dataset, path: str, fields: Mapping[str, cat.Field]
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    kind = c.get("kind", "field")
    if kind == "field":
        if c.get("field") not in fields:
            out.append(issue("unknown_field", f"“{c.get('field')}” is not a field of “{ds.label}”.", path))
    elif kind == "static":
        if c.get("value") is None or not str(c.get("value")).strip():
            out.append(issue("missing_static_value", "A static column needs text.", path))
        elif len(str(c["value"])) > 500:
            out.append(issue("static_too_long", "Static text is limited to 500 characters.", path))
    elif kind == "calc":
        op, fs = c.get("op"), c.get("fields", [])
        if op not in cat.ROW_CALCS:
            out.append(
                issue("unknown_calc", f"Calculation “{op}” is not allowed (use {', '.join(cat.ROW_CALCS)}).", path)
            )
        if not isinstance(fs, list) or not 2 <= len(fs) <= 5 or any(f not in fields for f in fs):
            out.append(
                issue("invalid_calc_fields", "A calculation needs two to five fields from the data source.", path)
            )
        elif op in ("add", "subtract") and any(
            fields[f].kind != cat.MONEY and fields[f].kind != cat.NUMBER for f in fs
        ):
            out.append(
                issue("calc_type_mismatch", "Addition and subtraction only work on money or number fields.", path)
            )
    else:
        out.append(issue("unknown_kind", "A column must be field, static or calc.", path))
    when = c.get("when")
    if when is not None:
        if when.get("field") not in fields:
            out.append(
                issue(
                    "unknown_field",
                    f"The condition refers to “{when.get('field')}”, which is not a field of “{ds.label}”.",
                    f"{path}.when",
                )
            )
        if when.get("op") not in cat.CONDITION_OPS:
            out.append(issue("unknown_condition", f"Condition “{when.get('op')}” is not allowed.", f"{path}.when"))
        if when.get("op") in ("eq", "neq", "gt", "lt") and "value" not in when:
            out.append(issue("missing_condition_value", "This condition needs a value.", f"{path}.when"))
        if when.get("op") == "in" and not isinstance(when.get("value"), list):
            out.append(issue("missing_condition_value", "The “in” condition needs a list of values.", f"{path}.when"))
    # compatibility of the field's kind with the format
    if kind == "field" and c.get("field") in fields and c.get("format"):
        k = fields[c["field"]].kind
        f = str(c["format"]).lower()
        if k == cat.TEXT and re.search(r"[0#]|yy|dd", f):
            out.append(
                issue(
                    "format_type_mismatch",
                    "A number or date format was given for a text field.",
                    f"{path}.format",
                    "warning",
                )
            )
    return out


def is_ready(issues: Sequence[Mapping[str, str]]) -> bool:
    return not any(i["severity"] == "error" for i in issues)


# ------------------------------------------------------------------ value resolution (shared by preview and writer)
def _num(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v)) if v not in (None, "") else Decimal(0)


def condition_true(when: Mapping[str, Any] | None, row: Mapping[str, Any]) -> bool:
    if not when:
        return True
    v = row.get(when["field"])
    op = when["op"]
    if op == "present":
        return v not in (None, "", Decimal(0))
    if op == "absent":
        return v in (None, "", Decimal(0))
    want = when.get("value")
    if op == "eq":
        return str(v) == str(want)
    if op == "neq":
        return str(v) != str(want)
    if op == "in":
        return str(v) in {str(x) for x in want or []}
    try:
        a, b = _num(v), _num(want)
    except Exception:
        return False
    return a > b if op == "gt" else a < b


def resolve_column(spec: Mapping[str, Any], row: Mapping[str, Any]) -> Any:
    """The value a mapped column receives for one dataset row (Decimal, date, text or None). Pure."""
    if not condition_true(spec.get("when"), row):
        return spec.get("else_value")
    kind = spec.get("kind", "field")
    if kind == "static":
        value: Any = spec.get("value")
    elif kind == "calc":
        op, fs = spec["op"], spec.get("fields", [])
        vals = [row.get(f) for f in fs]
        if op == "add":
            value = sum((_num(v) for v in vals), Decimal(0))
        elif op == "subtract":
            value = _num(vals[0]) - sum((_num(v) for v in vals[1:]), Decimal(0))
        else:
            value = str(spec.get("separator", " ")).join(str(v) for v in vals if v not in (None, ""))
    else:
        value = row.get(spec["field"])
    if value in (None, "") and spec.get("default") not in (None, ""):
        value = spec["default"]
    return value


def aggregate(op: str, field: str | None, rows: Sequence[Mapping[str, Any]]) -> Any:
    if op == "count":
        return len(rows)
    vals: list[Any] = [r.get(field) for r in rows if field and r.get(field) not in (None, "")]
    if not vals:
        return Decimal(0) if op == "sum" else None
    if op == "sum":
        return sum((_num(v) for v in vals), Decimal(0))
    ordered = sorted(vals, key=str) if any(isinstance(v, str) for v in vals) else sorted(vals)
    return ordered[0] if op == "min" else ordered[-1]


def header_value(key: str, meta: Mapping[str, Any]) -> Any:
    return meta.get(key)


def missing_required(
    sheet_spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    """Rows whose value for a column marked `required` is empty. Reported before export, never filled in."""
    out: list[dict[str, Any]] = []
    for c in sheet_spec.get("columns", []):
        if not c.get("required"):
            continue
        for ri, r in enumerate(rows):
            if condition_true(c.get("when"), r) and resolve_column(c, r) in (None, ""):
                out.append(
                    {
                        "row": ri + 1,
                        "column": str(c.get("column")).upper(),
                        "field": c.get("field") or c.get("op") or "static",
                    }
                )
                if len(out) >= limit:
                    return out
    return out


def fmt_preview(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, date):
        return v.isoformat()
    return v


# ------------------------------------------------------------------ custom report definitions (safe query abstraction)
REPORT_MAX_FIELDS = 40
REPORT_DATE_FIELD = {
    "transactions": "transaction_date",
    "transaction_lines": "transaction_date",
    "journal_lines": "journal_date",
    "vat_transactions": "transaction_date",
    "bank_transactions": "journal_date",
    "general_ledger": "journal_date",
}


def validate_report_definition(defn: Mapping[str, Any]) -> list[dict[str, str]]:
    """A custom report names a governed dataset, its fields, filters, sort and one grouping. Nothing else is accepted."""
    out: list[dict[str, str]] = []
    ds = cat.DATASETS.get(str(defn.get("dataset")))
    if ds is None or not ds.custom:
        return [
            issue("unknown_dataset", f"“{defn.get('dataset')}” is not a data source custom reports can use.", "dataset")
        ]
    fields = ds.field_map()
    sel = defn.get("fields")
    if not isinstance(sel, list) or not sel or len(sel) > REPORT_MAX_FIELDS:
        out.append(issue("no_fields", f"Choose between 1 and {REPORT_MAX_FIELDS} fields.", "fields"))
        sel = []
    if len(set(sel)) != len(sel):
        out.append(issue("duplicate_field", "A field is selected twice.", "fields"))
    for f in sel:
        if f not in fields:
            out.append(issue("unknown_field", f"“{f}” is not a field of “{ds.label}”.", "fields"))
    for srt in defn.get("sort", []) or []:
        if srt.get("field") not in fields and srt.get("field") not in ("count",):
            out.append(issue("unknown_sort_field", f"Cannot sort by “{srt.get('field')}”.", "sort"))
        if srt.get("direction", "asc") not in ("asc", "desc"):
            out.append(issue("invalid_direction", "Sort direction is asc or desc.", "sort"))
    gb = defn.get("group_by")
    if gb is not None and gb not in ds.groupable:
        out.append(
            issue(
                "invalid_group",
                f"“{gb}” cannot be used for grouping here (use {', '.join(ds.groupable) or 'none'}).",
                "group_by",
            )
        )
    if gb == "month" and REPORT_DATE_FIELD.get(ds.key) is None:
        out.append(issue("invalid_group", "This data source has no date to group by month.", "group_by"))
    if gb is not None and not any(fields[f].kind == cat.MONEY for f in sel if f in fields):
        out.append(issue("no_amounts", "Grouped reports need at least one money field to total.", "fields"))
    return out


def _sort_key(k: str) -> Any:
    def key(r: Mapping[str, Any]) -> tuple[bool, Any]:
        v = r.get(k)
        return (v is None, v if isinstance(v, Decimal | int | date) else str(v))

    return key


def apply_report(
    defn: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Project, group and sort rows for a custom report. Returns (columns, rows). Money is summed with Decimal."""
    ds = cat.DATASETS[str(defn["dataset"])]
    fields = ds.field_map()
    sel = list(defn["fields"])
    gb = defn.get("group_by")
    if gb is None:
        cols = [{"key": f, "label": fields[f].label, "kind": fields[f].kind} for f in sel]
        data = [{f: r.get(f) for f in sel} for r in rows]
    else:
        date_field = REPORT_DATE_FIELD.get(ds.key, "")
        money = [f for f in sel if fields[f].kind == cat.MONEY]
        groups: dict[Any, dict[str, Any]] = {}
        for r in rows:
            key = (
                (str(r.get(date_field))[:7] if r.get(date_field) else "(none)")
                if gb == "month"
                else (r.get(gb) if r.get(gb) not in (None, "") else "(none)")
            )
            g = groups.setdefault(key, {"__key": key, "count": 0, **dict.fromkeys(money, Decimal(0))})
            g["count"] += 1
            for m in money:
                g[m] += _num(r.get(m))
        label = "Month" if gb == "month" else fields[gb].label
        cols = [
            {"key": "group", "label": label, "kind": cat.TEXT},
            *({"key": m, "label": f"{fields[m].label} (total)", "kind": cat.MONEY} for m in money),
            {"key": "count", "label": "Rows", "kind": cat.NUMBER},
        ]
        data = [{"group": g["__key"], **{m: g[m] for m in money}, "count": g["count"]} for g in groups.values()]
    for srt in reversed(defn.get("sort") or []):
        data.sort(key=_sort_key(srt["field"]), reverse=srt.get("direction") == "desc")
    elif_limit = int(defn.get("limit") or 100000)
    return cols, data[:elif_limit]
