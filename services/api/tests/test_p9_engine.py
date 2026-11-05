"""Phase 9: the Excel engine (upload safety, analysis, mapping validation, writer). Pure: files only, no database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as Dec
from pathlib import Path

import pytest
import xlfixtures as xf
from artify_api import xl_engine as eng
from artify_api import xl_mapping as mp
from artify_api import xl_writer as wr
from openpyxl import load_workbook


def accept(path: Path, name: str | None = None, *, macros: bool = False) -> eng.UploadInfo:
    return eng.inspect_upload(path, name or path.name, max_bytes=5 * 1024 * 1024, allow_macros=macros)


def refused(path: Path, name: str | None = None, **kw) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(eng.WorkbookError) as e:
        accept(path, name, **kw)
    return e.value.code


# ------------------------------------------------------------------ upload safety
def test_valid_workbook_is_accepted_with_its_checksum(tmp_path: Path) -> None:
    p = xf.purchase_template(tmp_path / "ok.xlsx")
    info = accept(p)
    assert info.kind == "xlsx" and info.size == p.stat().st_size and len(info.sha256) == 64 and not info.has_vba


def test_wrong_extensions_corrupt_and_legacy_files_are_refused_with_the_right_reason(tmp_path: Path) -> None:
    good = xf.purchase_template(tmp_path / "ok.xlsx")
    assert refused(good, "report.csv") == "unsupported_extension"
    assert refused(good, "report.xlsb") == "unsupported_extension"
    assert refused(good, "legacy.xls") == "legacy_or_encrypted_workbook"  # never renamed or converted
    old = tmp_path / "old.xlsx"
    old.write_bytes(xf.OLE_HEAD)
    assert refused(old) == "legacy_or_encrypted_workbook"  # an OLE file wearing an .xlsx name (old .xls or encrypted)
    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"this is not a workbook at all")
    assert refused(junk) == "corrupt_workbook"
    notxl = tmp_path / "plain.xlsx"
    notxl.write_bytes(xf.zip_with({"hello.txt": b"hi"}))
    assert refused(notxl) == "corrupt_workbook"
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    assert refused(empty) == "empty_file"
    cut = tmp_path / "cut.xlsx"
    cut.write_bytes(good.read_bytes()[:-60])
    assert refused(cut) in ("corrupt_workbook",)


def test_size_limit_and_compression_bombs_are_refused(tmp_path: Path) -> None:
    good = xf.purchase_template(tmp_path / "ok.xlsx")
    with pytest.raises(eng.WorkbookError) as e:
        eng.inspect_upload(good, "ok.xlsx", max_bytes=1000, allow_macros=False)
    assert e.value.code == "file_too_large"
    bomb = tmp_path / "bomb.xlsx"
    base = xf.zip_with(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"0" * (60 * 1024 * 1024),
        }
    )
    bomb.write_bytes(base)
    assert refused(bomb) == "unsafe_workbook"


def test_xml_entity_tricks_are_refused(tmp_path: Path) -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><workbook>&b;</workbook>'
    p = tmp_path / "evil.xlsx"
    p.write_bytes(xf.zip_with({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": evil}))
    assert refused(p) == "unsafe_workbook"


def test_macros_are_never_accepted_silently(tmp_path: Path) -> None:
    good = xf.purchase_template(tmp_path / "ok.xlsx")
    vba = xf.with_part(good, "xl/vbaProject.bin", b"FAKE-VBA")
    assert refused(vba, "disguised.xlsx") == "macro_in_xlsx"  # macros in a file named .xlsx
    macro = tmp_path / "macro.xlsm"
    macro.write_bytes(vba.read_bytes())
    assert refused(macro) == "macros_not_enabled"
    info = accept(macro, macros=True)
    assert info.kind == "xlsm" and info.has_vba and info.warnings[0]["code"] == "macros_kept_not_run"


def test_external_links_are_reported(tmp_path: Path) -> None:
    ext = xf.with_part(
        xf.purchase_template(tmp_path / "ok.xlsx"), "xl/externalLinks/externalLink1.xml", b"<externalLink/>"
    )
    info = accept(ext, "linked.xlsx")
    assert any(w["code"] == "external_links" for w in info.warnings)


# ------------------------------------------------------------------ analysis (advisory, read-only)
def test_analysis_describes_the_workbook_and_never_changes_it(tmp_path: Path) -> None:
    p = xf.purchase_template(tmp_path / "t.xlsx")
    before = p.read_bytes()
    a = eng.analyze(p, "xlsx")
    assert p.read_bytes() == before
    names = {s["name"]: s for s in a["sheets"]}
    assert set(names) == {"Purchases", "Summary", "Lookups"} and names["Lookups"]["visibility"] == "hidden"
    s = names["Purchases"]
    assert s["suggested_header_row"] == 4 and s["suggested_start_row"] == 5
    assert s["column_names"]["C"] == "Supplier" and s["column_names"]["F"] == "Net"
    assert "A1:C1" in s["merged_ranges"] and "A6:C6" in s["merged_ranges"]
    assert (
        s["freeze_panes"] == "A5"
        and s["auto_filter"] == "A4:J5"
        and "A1:J6" in (s["print_area"] or "").replace("$", "")
    )
    assert s["formula_count"] == 4 and {c["cell"] for c in s["formula_cells"]} == {"J5", "F6", "G6", "H6"}
    assert s["row_formulas"]["5"] == ["J"] and 6 in s["totals_rows"]
    assert 2 in s["empty_rows"] or 3 in s["empty_rows"] or s["empty_rows"] == []
    assert any(d["name"] == "TotalGross" for d in a["defined_names"])
    assert a["risk_level"] == "none" and {f["feature"] for f in a["fidelity"]} >= {"formulas", "styles", "merged_cells"}


def test_analysis_finds_tables_balances_blocks_and_protection(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"], ws["B1"] = "Opening balance", 100
    for i, h in enumerate(["Date", "Narration", "Debit", "Credit"], 1):
        ws.cell(row=3, column=i, value=h)
    ws["A4"], ws["C4"] = "2025-01-01", 5
    ws["A8"], ws["B8"] = "Closing balance", "=SUM(C4:C6)"
    ws["A12"], ws["B12"] = "Second table", "x"
    ws.protection.sheet = True
    p = tmp_path / "ledger.xlsx"
    wb.save(p)
    s = eng.analyze(p, "xlsx")["sheets"][0]
    assert {b["label"].lower() for b in s["balance_rows"]} == {"opening balance", "closing balance"}
    assert len(s["blocks"]) >= 3 and s["protected"] and s["suggested_header_row"] == 3
    assert 8 in s["totals_rows"]


def test_unsupported_features_are_flagged_before_anything_is_mapped(tmp_path: Path) -> None:
    charted = xf.purchase_template(tmp_path / "chart.xlsx", chart=True)
    a = eng.analyze(charted, "xlsx")
    assert a["risk_level"] == "unsupported" and any(
        f["feature"] == "charts" and f["status"] == "unsupported" for f in a["fidelity"]
    )
    pivot = xf.with_part(xf.purchase_template(tmp_path / "p.xlsx"), "xl/pivotTables/pivotTable1.xml", b"<pivot/>")
    assert eng.analyze(pivot, "xlsx")["risk_level"] == "unsupported"
    protected = eng.analyze(xf.purchase_template(tmp_path / "prot.xlsx", protected=True), "xlsx")
    assert (
        any(f["feature"] == "sheet_protection" for f in protected["fidelity"]) and protected["risk_level"] == "warned"
    )


# ------------------------------------------------------------------ mapping validation
def good_mapping() -> dict:  # type: ignore[type-arg]
    return {
        "sheets": [
            {
                "sheet": "Purchases",
                "dataset": "purchase_register",
                "header_row": 4,
                "start_row": 5,
                "template_rows": 1,
                "formula_columns": ["J"],
                "columns": [
                    {"column": "A", "field": "transaction_date", "format": "dd/mm/yyyy"},
                    {"column": "B", "field": "number"},
                    {"column": "C", "field": "contact_name"},
                    {"column": "D", "field": "doc_number"},
                    {"column": "E", "field": "narration"},
                    {"column": "F", "field": "net_amount"},
                    {"column": "G", "field": "tax_amount", "when": {"field": "tax_amount", "op": "gt", "value": "0"}},
                    {"column": "H", "field": "gross_amount", "required": True},
                    {"column": "I", "kind": "static", "value": "Purchases"},
                ],
            }
        ],
        "fixed_cells": [
            {"sheet": "Purchases", "cell": "B2", "kind": "field", "field": "period_start", "format": "dd/mm/yyyy"},
            {"sheet": "Purchases", "cell": "B3", "kind": "field", "field": "company_name"},
            {"sheet": "Summary", "cell": "B2", "kind": "static", "value": "Branch 1"},
        ],
    }


def codes(defn: dict, analysis: dict) -> set[str]:  # type: ignore[type-arg]
    return {i["code"] for i in mp.validate_mapping(defn, analysis) if i["severity"] == "error"}


@pytest.fixture
def analysis(tmp_path: Path) -> dict:  # type: ignore[type-arg]
    return eng.analyze(xf.purchase_template(tmp_path / "t.xlsx"), "xlsx")


def test_a_correct_mapping_validates(analysis) -> None:  # type: ignore[no-untyped-def]
    issues = mp.validate_mapping(good_mapping(), analysis)
    assert mp.is_ready(issues), issues


def test_mapping_problems_are_each_reported(analysis) -> None:  # type: ignore[no-untyped-def]
    def with_(**patch):  # type: ignore[no-untyped-def]
        m = good_mapping()
        m["sheets"][0].update(patch)
        return m

    assert "sheet_not_found" in codes(with_(sheet="Nope"), analysis)
    assert "invalid_start_row" in codes(with_(start_row=0), analysis)
    assert "invalid_start_row" in codes(with_(start_row=2_000_000), analysis)
    assert "invalid_header_row" in codes(with_(header_row=-3), analysis)
    assert "start_before_header" in codes(with_(start_row=4), analysis)
    assert "unknown_dataset" in codes(with_(dataset="payroll"), analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append({"column": "F", "field": "gross_amount"})
    assert "duplicate_target" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append({"column": "K", "field": "not_a_field"})
    assert "unknown_field" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append({"column": "1A", "field": "number"})
    assert "invalid_column" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"] = [c for c in m["sheets"][0]["columns"] if c["column"] != "H"]
    assert "required_field_unmapped" in codes(m, analysis)  # gross amount is required for a register
    m = good_mapping()
    m["sheets"][0]["columns"].append({"column": "K", "field": "contact_name", "format": "=1+1"})
    assert "invalid_format" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append({"column": "K", "kind": "static"})
    assert "missing_static_value" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append(
        {"column": "K", "kind": "calc", "op": "eval", "fields": ["net_amount", "tax_amount"]}
    )
    assert "unknown_calc" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append(
        {"column": "K", "kind": "calc", "op": "add", "fields": ["narration", "tax_amount"]}
    )
    assert "calc_type_mismatch" in codes(m, analysis)
    m = good_mapping()
    m["sheets"][0]["columns"].append(
        {"column": "K", "field": "number", "when": {"field": "number", "op": "regex", "value": ".*"}}
    )
    assert "unknown_condition" in codes(m, analysis)
    assert "no_sheets" in codes({"sheets": []}, analysis)


def test_formula_cells_protected_sheets_and_fixed_cell_conflicts(analysis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    m = good_mapping()
    m["sheets"][0]["formula_columns"] = []
    m["sheets"][0]["columns"].append({"column": "J", "field": "gross_amount"})  # J5 holds a formula
    assert "formula_overwrite" in codes(m, analysis)
    m["sheets"][0]["columns"][-1]["allow_formula_overwrite"] = True  # an explicit decision
    assert "formula_overwrite" not in codes(m, analysis)
    m = good_mapping()
    m["fixed_cells"].append({"sheet": "Purchases", "cell": "H6", "kind": "static", "value": "x"})  # footer SUM formula
    assert "formula_overwrite" in codes(m, analysis)
    m = good_mapping()
    m["fixed_cells"].append({"sheet": "Purchases", "cell": "B2", "kind": "static", "value": "again"})
    assert "duplicate_target" in codes(m, analysis)
    m = good_mapping()
    m["fixed_cells"].append(
        {"sheet": "Purchases", "cell": "C9", "kind": "static", "value": "x"}
    )  # inside the generated rows
    assert "fixed_cell_in_data_region" in codes(m, analysis)
    m = good_mapping()
    m["fixed_cells"].append({"sheet": "Purchases", "cell": "ZZZZ1", "kind": "static", "value": "x"})
    assert "invalid_cell" in codes(m, analysis)
    m = good_mapping()
    m["fixed_cells"].append({"sheet": "Purchases", "cell": "B4", "kind": "calc", "op": "sum", "of": "net_amount"})
    assert mp.is_ready(mp.validate_mapping(m, analysis))
    prot = eng.analyze(xf.purchase_template(tmp_path / "p.xlsx", protected=True), "xlsx")
    assert "protected_sheet" in codes(good_mapping(), prot)


# ------------------------------------------------------------------ generation
ROWS = [
    {
        "transaction_date": date(2025, 3, 1),
        "number": "TX-000001",
        "contact_name": "Gulf Supplies",
        "doc_number": "INV-1",
        "narration": "Paper",
        "net_amount": Dec("100.000"),
        "tax_amount": Dec("5.000"),
        "gross_amount": Dec("105.000"),
    },
    {
        "transaction_date": date(2025, 3, 2),
        "number": "TX-000002",
        "contact_name": "=cmd|' /C calc'!A0",
        "doc_number": "INV-2",
        "narration": "+SUM(1)",
        "net_amount": Dec("50.500"),
        "tax_amount": Dec(0),
        "gross_amount": Dec("50.500"),
    },
    {
        "transaction_date": date(2025, 3, 3),
        "number": "TX-000003",
        "contact_name": "Sohar Metals",
        "doc_number": "INV-3",
        "narration": "@x",
        "net_amount": Dec("10.000"),
        "tax_amount": Dec("0.500"),
        "gross_amount": Dec("10.500"),
    },
]
META = {"company_name": "Al Noor Trading LLC", "period_start": date(2025, 3, 1)}


def generate(tmp_path: Path, rows=ROWS, mapping=None, template=None):  # type: ignore[no-untyped-def]
    t = template or xf.purchase_template(tmp_path / "t.xlsx")
    out = tmp_path / "out.xlsx"
    m = mapping or good_mapping()
    rep = wr.generate(t, "xlsx", m, {"Purchases": rows}, META, out, scale=3)
    return t, out, rep, m


def test_rows_land_in_the_mapped_columns_with_formats_and_numeric_types(tmp_path: Path) -> None:
    t, out, rep, m = generate(tmp_path)
    ws = load_workbook(out)["Purchases"]
    assert [ws[f"B{r}"].value for r in (5, 6, 7)] == ["TX-000001", "TX-000002", "TX-000003"]
    assert ws["A5"].value.date() == date(2025, 3, 1) and ws["A5"].number_format == "dd/mm/yyyy"
    assert ws["F5"].value == 100 and isinstance(ws["F5"].value, int | float) and ws["F5"].number_format == "#,##0.000"
    assert Dec(str(ws["F6"].value)) == Dec("50.500") and Dec(str(ws["H7"].value)) == Dec("10.500")
    assert ws["I5"].value == "Purchases"
    assert ws["G6"].value is None  # conditional: no VAT, so the VAT cell stays empty
    assert Dec(str(ws["G5"].value)) == Dec("5.000")
    assert ws["B2"].value.date() == date(2025, 3, 1) and ws["B3"].value == "Al Noor Trading LLC"
    assert load_workbook(out)["Summary"]["B2"].value == "Branch 1"
    assert wr.verify_output(out, t, "xlsx", m, {"Purchases": ROWS}) == []
    assert rep["sheets"]["Purchases"]["rows_written"] == 3 and rep["sheets"]["Purchases"]["inserted_rows"] == 2


def test_formula_injection_is_neutralised(tmp_path: Path) -> None:
    _, out, _, _ = generate(tmp_path)
    ws = load_workbook(out)["Purchases"]
    for ref, text in (("C6", "=cmd|' /C calc'!A0"), ("E6", "+SUM(1)"), ("E7", "@x")):
        c = ws[ref]
        assert (
            c.data_type == "s" and c.value == text and c.quotePrefix
        )  # stored as text, flagged so Excel never evaluates it


def test_footer_formulas_cross_sheet_references_merges_and_layout_follow_the_new_rows(tmp_path: Path) -> None:
    t, out, _, _ = generate(tmp_path)
    wb = load_workbook(out)
    ws, orig = wb["Purchases"], load_workbook(t)["Purchases"]
    assert (
        ws["A8"].value == "Total" and ws["F8"].value == "=SUM(F5:F7)" and ws["H8"].value == "=SUM(H5:H7)"
    )  # extended over the new rows
    assert wb["Summary"]["B1"].value == "=Purchases!H8"  # another sheet pointed at the footer
    assert "TotalGross" in wb.defined_names and str(wb.defined_names["TotalGross"].attr_text) == "Purchases!$H$8"
    assert {str(r) for r in ws.merged_cells.ranges} == {"A1:C1", "A8:C8"}
    assert ws.row_dimensions[8].height == 24 and ws.freeze_panes == "A5"
    assert ws.auto_filter.ref == "A4:J7" and "J8" in str(ws.print_area).replace("$", "")
    assert ws.column_dimensions["C"].width == 32 and ws.column_dimensions["E"].width == 40
    # styles of the template row carried to the new rows
    for r in (5, 6, 7):
        assert ws[f"F{r}"].number_format == "#,##0.000" and ws[f"A{r}"].border.bottom.style == "thin"
    assert ws["A4"].font.b and ws["A4"].fill.fgColor.rgb == orig["A4"].fill.fgColor.rgb
    # the formula column was carried down with translated references; nothing in the template header changed
    assert [ws[f"J{r}"].value for r in (5, 6, 7)] == ["=H5*1", "=H6*1", "=H7*1"]
    assert [ws.cell(row=r, column=c).value for r in (1, 4) for c in (1, 6)] == [
        orig.cell(row=r, column=c).value for r in (1, 4) for c in (1, 6)
    ]
    assert wb.calculation.fullCalcOnLoad is True  # Excel recalculates; Artify does not claim cached results


def test_table_ranges_grow_with_the_data(tmp_path: Path) -> None:
    t = xf.purchase_template(tmp_path / "tab.xlsx", with_table=True)
    _, out, _, _ = generate(tmp_path, template=t)
    ws = load_workbook(out)["Purchases"]
    assert ws.tables["Register"].ref == "A4:J7"


def test_fewer_rows_than_placeholders_and_no_rows_at_all(tmp_path: Path) -> None:
    m = good_mapping()
    m["sheets"][0]["template_rows"] = 3  # the template reserved three rows
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Purchases"
    for i in range(1, 11):
        ws.cell(row=4, column=i, value=f"H{i}")
    for r in (5, 6, 7):
        for c in range(1, 10):
            ws.cell(row=r, column=c, value="sample")
    ws["A8"] = "Total"
    wb.create_sheet("Summary")
    p = tmp_path / "three.xlsx"
    wb.save(p)
    out = tmp_path / "o.xlsx"
    rep = wr.generate(p, "xlsx", m, {"Purchases": ROWS[:1]}, META, out, scale=3)
    w = load_workbook(out)["Purchases"]
    assert w["B5"].value == "TX-000001" and w["B6"].value is None and w["B7"].value is None and w["A8"].value == "Total"
    assert any(x["code"] == "placeholder_rows_left" for x in rep["warnings"])
    rep0 = wr.generate(p, "xlsx", m, {"Purchases": []}, META, out, scale=3)
    assert load_workbook(out)["Purchases"]["B5"].value is None and rep0["sheets"]["Purchases"]["rows_written"] == 0


def test_formulas_are_never_overwritten_by_accident(tmp_path: Path) -> None:
    m = good_mapping()
    m["sheets"][0]["formula_columns"] = []
    m["sheets"][0]["columns"].append({"column": "J", "field": "gross_amount", "allow_formula_overwrite": True})
    _, out, _, _ = generate(tmp_path, mapping=m)  # explicit permission: written
    assert Dec(str(load_workbook(out)["Purchases"]["J5"].value)) == Dec("105.000")
    m["sheets"][0]["columns"][-1].pop("allow_formula_overwrite")
    with pytest.raises(wr.WriteError) as e:
        generate(tmp_path, mapping=m)
    assert e.value.code == "formula_overwrite"
    m2 = good_mapping()
    m2["fixed_cells"].append({"sheet": "Purchases", "cell": "F6", "kind": "static", "value": "x"})
    with pytest.raises(wr.WriteError):
        generate(tmp_path, mapping=m2)


def test_protected_sheets_are_refused_at_write_time_too(tmp_path: Path) -> None:
    with pytest.raises(wr.WriteError) as e:
        generate(tmp_path, template=xf.purchase_template(tmp_path / "pr.xlsx", protected=True))
    assert e.value.code == "protected_sheet"


def test_calculated_cells_and_aggregates_use_only_whitelisted_operations(tmp_path: Path) -> None:
    m = good_mapping()
    m["sheets"][0]["columns"].append(
        {"column": "K", "kind": "calc", "op": "add", "fields": ["net_amount", "tax_amount"]}
    )
    m["sheets"][0]["columns"].append(
        {"column": "L", "kind": "calc", "op": "concat", "fields": ["number", "doc_number"], "separator": " / "}
    )
    m["fixed_cells"] += [
        {
            "sheet": "Summary",
            "cell": "B5",
            "kind": "calc",
            "op": "sum",
            "of": "gross_amount",
            "source_sheet": "Purchases",
        },
        {"sheet": "Summary", "cell": "B6", "kind": "calc", "op": "count", "source_sheet": "Purchases"},
    ]
    assert mp.is_ready(mp.validate_mapping(m, eng.analyze(xf.purchase_template(tmp_path / "t.xlsx"), "xlsx")))
    _, out, _, _ = generate(tmp_path, mapping=m)
    ws, summary = load_workbook(out)["Purchases"], load_workbook(out)["Summary"]
    assert Dec(str(ws["K5"].value)) == Dec("105.000") and ws["L5"].value == "TX-000001 / INV-1"
    assert (
        Dec(str(summary["B5"].value)) == Dec("166.000") and summary["B6"].value == 3
    )  # 105 + 50.5 + 10.5, exact decimals


def test_default_values_conditions_and_required_checks() -> None:
    col = {"column": "A", "field": "narration", "default": "n/a"}
    assert mp.resolve_column(col, {"narration": None}) == "n/a" and mp.resolve_column(col, {"narration": "x"}) == "x"
    when = {"column": "A", "field": "project", "when": {"field": "project", "op": "present"}}
    assert mp.resolve_column(when, {"project": "P1"}) == "P1" and mp.resolve_column(when, {"project": None}) is None
    by_type = {
        "column": "A",
        "kind": "static",
        "value": "Credit",
        "when": {"field": "txn_type", "op": "in", "value": ["sales_credit_note"]},
    }
    assert (
        mp.resolve_column(by_type, {"txn_type": "sales_credit_note"}) == "Credit"
        and mp.resolve_column(by_type, {"txn_type": "sales_invoice"}) is None
    )
    sheet = {"columns": [{"column": "A", "field": "number", "required": True}]}
    assert mp.missing_required(sheet, [{"number": "X"}, {"number": None}]) == [
        {"row": 2, "column": "A", "field": "number"}
    ]


def test_large_export_is_correct_and_fast_enough(tmp_path: Path) -> None:
    rows = [
        {
            "transaction_date": date(2025, 1, 1 + i % 28),
            "number": f"TX-{i:06d}",
            "contact_name": f"Supplier {i % 50}",
            "doc_number": f"INV-{i}",
            "narration": "bulk",
            "net_amount": Dec(i) / 10,
            "tax_amount": Dec(i) / 200,
            "gross_amount": Dec(i) / 10 + Dec(i) / 200,
        }
        for i in range(1, 5001)
    ]
    t, out, rep, m = generate(tmp_path, rows=rows)
    ws = load_workbook(out)["Purchases"]
    assert (
        ws["B5004"].value == "TX-005000"
        and ws["F5005"].value == "=SUM(F5:F5004)"
        and rep["sheets"]["Purchases"]["rows_written"] == 5000
    )
    assert wr.verify_output(out, t, "xlsx", m, {"Purchases": rows}) == []


def test_verification_catches_a_tampered_output(tmp_path: Path) -> None:
    t, out, _, m = generate(tmp_path)
    wb = load_workbook(out)
    wb["Purchases"]["H5"] = 999
    wb.save(out)
    problems = wr.verify_output(out, t, "xlsx", m, {"Purchases": ROWS})
    assert any(p["code"] == "value_mismatch" for p in problems)
    out.write_bytes(b"garbage")
    assert wr.verify_output(out, t, "xlsx", m, {"Purchases": ROWS})[0]["code"] == "unreadable_output"


def test_macro_enabled_template_keeps_its_vba_part_and_runs_nothing(tmp_path: Path) -> None:
    base = xf.purchase_template(tmp_path / "b.xlsx")
    xlsm = tmp_path / "m.xlsm"
    xlsm.write_bytes(xf.with_part(base, "xl/vbaProject.bin", b"FAKE-VBA-BYTES").read_bytes())
    out = tmp_path / "o.xlsm"
    wr.generate(xlsm, "xlsm", good_mapping(), {"Purchases": ROWS}, META, out, scale=3)
    import zipfile

    with zipfile.ZipFile(out) as z:
        assert z.read("xl/vbaProject.bin") == b"FAKE-VBA-BYTES"  # preserved, never executed
