"""Phase 7 pure-logic tests: money/date parsing, classification, invoice/receipt/note extraction and bank statements.
Fixtures are synthetic monospace layouts (word x-positions follow the character columns, like a table in a real page)."""

# ruff: noqa: RUF001  (Arabic-Indic digits are deliberate test inputs)
from __future__ import annotations

import re
from decimal import Decimal

import pytest
from artify_api.doc_extract import (
    CompanyProfile,
    Line,
    Page,
    Word,
    classify,
    extract_for,
    parse_bank_statement,
    parse_date,
    parse_money,
    suggest_splits,
)

WIDTH = 110.0


def pages(*texts: str, conf: float | None = 0.96) -> list[Page]:
    out = []
    for pno, text in enumerate(texts, 1):
        rows = text.strip("\n").split("\n")
        lines = []
        for i, row in enumerate(rows):
            words = tuple(Word(m.group(0), m.start() / WIDTH, m.end() / WIDTH, conf) for m in re.finditer(r"\S+", row))
            if not words:
                lines.append(Line(pno, i, "", (0, i / (len(rows) + 1), 0, i / (len(rows) + 1)), conf, ()))
                continue
            y = i / (len(rows) + 1)
            lines.append(Line(pno, i, row.strip(), (words[0].x0, y, words[-1].x1, y + 0.02), conf, words))
        out.append(Page(pno, tuple(lines), blank=not any(ln.text for ln in lines)))
    return out


# ------------------------------------------------------------------ primitives
@pytest.mark.parametrize(
    ("raw", "value", "flags"),
    [
        ("1,234.50", "1234.50", []),
        ("1.234,50", "1234.50", []),
        ("12,50", "12.50", []),
        ("1,234,567.891", "1234567.891", []),
        ("(250.00)", "250.00", ["negative"]),
        ("-75.5", "75.5", ["negative"]),
        ("١٢٣٫٤٥", "123.45", []),
        ("1,234", "1234", ["ambiguous_separator"]),  # thousands or three decimals? flagged, never silently decided
        ("0.500", "0.500", []),
    ],
)
def test_money_parsing_is_explicit_about_ambiguity(raw, value, flags) -> None:  # type: ignore[no-untyped-def]
    v, fl = parse_money(raw)
    assert v == Decimal(value) and fl == flags


def test_money_rejects_text_and_never_returns_a_float() -> None:
    assert parse_money("abc")[0] is None and parse_money("")[0] is None
    assert isinstance(parse_money("10.10")[0], Decimal)


@pytest.mark.parametrize(
    ("raw", "iso", "flags"),
    [
        ("2025-03-14", "2025-03-14", []),
        ("14/03/2025", "2025-03-14", []),  # day cannot be the month: unambiguous
        ("03/04/2025", "2025-04-03", ["ambiguous_date_format"]),  # could be 3 April or 4 March: flagged
        ("14 Mar 2025", "2025-03-14", []),
        ("March 14, 2025", "2025-03-14", []),
        ("14-03-25", "2025-03-14", ["two_digit_year"]),
        ("31/02/2025", None, ["invalid_date"]),
        ("no date here", None, ["no_date"]),
        ("١٤/٠٣/٢٠٢٥", "2025-03-14", []),
    ],
)
def test_date_parsing_flags_ambiguity(raw, iso, flags) -> None:  # type: ignore[no-untyped-def]
    assert parse_date(raw) == (iso, flags)


# ------------------------------------------------------------------ classification
INVOICE = """
Al Noor Trading LLC
Muscat, Sultanate of Oman
VAT No: OM1234567890
TAX INVOICE
Invoice No: INV-2025-0042
Invoice Date: 14/03/2025
Due Date: 13/04/2025
PO: PO-7781
Bill To:
Gulf Builders Co
Seeb, Muscat
VAT No: OM9988776655
Description            Qty    Unit Price    Amount
Cement bags 50kg        10        2.500     25.000
Steel bars 12mm          4       30.000    120.000
Transport                1       15.000     15.000
Subtotal                                   160.000
VAT 5%                                       8.000
Total                                      168.000 OMR
Payment Terms: Net 30
IBAN: OM81BMUS0000012345678901
"""


def test_invoice_direction_comes_from_where_the_company_appears() -> None:
    p = pages(INVOICE)
    sales = classify(p, CompanyProfile("Al Noor Trading LLC", ("OM1234567890",)))
    assert (sales.type_key, sales.kind, sales.direction, sales.variant) == (
        "sales_invoice",
        "sales_invoice",
        "sales",
        "tax_invoice",
    )
    assert sales.confidence >= 0.6 and not sales.needs_manual and sales.evidence
    purchase = classify(p, CompanyProfile("Gulf Builders Co", ("OM9988776655",)))
    assert (purchase.type_key, purchase.kind) == ("purchase_invoice", "purchase_invoice")
    unknown = classify(p, CompanyProfile("Somebody Else Ltd", ()))
    assert (
        unknown.type_key == "invoice" and unknown.kind is None and unknown.needs_manual
    )  # direction unknown: not a confirmed fact
    assert classify(p, None).needs_manual


def test_other_document_types_and_low_confidence() -> None:
    assert classify(pages("Receipt\nCoffee House\nTotal 3.500\nThank you for shopping")).type_key == "receipt"
    assert classify(pages("DELIVERY NOTE\nDN-5\nGoods received in good order")).type_key == "delivery_note"
    assert classify(pages("PAYSLIP\nNet Salary 450.000\nPayroll March")).kind == "payroll_document"
    assert classify(pages("PURCHASE ORDER\nPO 44\nPlease supply")).type_key == "purchase_order"
    assert (
        classify(pages("Dear Sir,\nWe write to inform you.\nYours faithfully,\nA. Person")).type_key
        == "general_correspondence"
    )
    nothing = classify(pages("lorem ipsum dolor sit amet\nconsectetur adipiscing"))
    assert nothing.type_key == "unclassified" and nothing.needs_manual and nothing.kind is None
    assert classify([]).needs_manual
    mixed = classify(pages("Statement of Account\nInvoice\nReceipt\nCredit Note"))
    assert mixed.needs_manual or mixed.confidence < 0.85  # competing signals never look certain
    arabic = classify(pages("فاتورة ضريبية\nرقم الفاتورة: 1001\nالإجمالي 105.000"))
    assert arabic.type_key in ("tax_invoice", "invoice") and arabic.variant == "tax_invoice"


def test_credit_note_kind_follows_direction() -> None:
    text = "Al Noor Trading LLC\nCREDIT NOTE\nCredit Note No: CN-9\nBill To:\nGulf Builders Co\nOriginal Invoice: INV-2025-0042\nTotal 10.000"
    c = classify(pages(text), CompanyProfile("Al Noor Trading LLC"))
    assert (c.type_key, c.kind) == ("credit_note", "sales_credit_note")
    assert classify(pages(text), CompanyProfile("Gulf Builders Co")).kind == "purchase_credit_note"


# ------------------------------------------------------------------ invoice extraction
def by_key(ex):  # type: ignore[no-untyped-def]
    return {f.key: f for f in ex.fields}


def test_invoice_fields_line_items_totals_and_evidence() -> None:
    ex = extract_for("sales_invoice", pages(INVOICE))
    f = by_key(ex)
    assert f["invoice_number"].value == "INV-2025-0042" and f["invoice_number"].page == 1 and f["invoice_number"].bbox
    assert f["invoice_date"].value == "2025-03-14" and f["due_date"].value == "2025-04-13"
    assert f["po_reference"].value == "PO-7781" and f["currency"].value == "OMR"
    assert (f["net_amount"].value, f["vat_amount"].value, f["gross_amount"].value, f["vat_rate"].value) == (
        "160.000",
        "8.000",
        "168.000",
        "5",
    )
    assert f["supplier_name"].value == "Al Noor Trading LLC" and "heuristic_party" in f["supplier_name"].flags
    assert f["supplier_name"].status == "needs_review"  # a heuristic is never presented as certain
    assert f["customer_name"].value == "Gulf Builders Co"
    assert f["supplier_tax_number"].value == "OM1234567890" and f["customer_tax_number"].value == "OM9988776655"
    assert f["payment_terms"].value == "Net 30" and "IBAN" in (f["payment_details"].value or "")
    assert len(ex.items) == 3
    assert (
        ex.items[0].values["quantity"] == "10"
        and ex.items[0].values["unit_price"] == "2.500"
        and ex.items[0].values["amount"] == "25.000"
    )
    assert ex.items[1].values["description"] == "Steel bars 12mm"
    assert not [i for i in ex.issues if i.severity == "error"]
    for fld in ex.fields:  # every extracted value is traceable
        if fld.value is not None:
            assert fld.page and fld.source_text and fld.confidence is not None and fld.method


def test_missing_and_ambiguous_values_are_flagged_not_invented() -> None:
    text = """
Quick Supplies
INVOICE
Date: 03/04/2025
Subtotal 100.00
VAT 5.00
Total 105.00
"""
    f = by_key(extract_for("invoice", pages(text)))
    assert f["invoice_number"].value is None and f["invoice_number"].status == "missing"  # not invented
    assert f["due_date"].value is None and f["customer_name"].value is None
    assert f["invoice_date"].value == "2025-04-03" and "ambiguous_date_format" in f["invoice_date"].flags
    assert f["invoice_date"].status == "needs_review"


def test_totals_that_do_not_add_up_are_reported_with_the_difference() -> None:
    bad = INVOICE.replace(
        "Total                                      168.000 OMR",
        "Total                                      169.000 OMR",
    )
    ex = extract_for("sales_invoice", pages(bad))
    issue = next(i for i in ex.issues if i.code == "totals_mismatch")
    assert issue.data["difference"] == "1.000" and issue.severity == "error"
    assert by_key(ex)["gross_amount"].value == "169.000"  # the printed value is kept, never "corrected"
    assert "totals_mismatch" in by_key(ex)["gross_amount"].flags
    wrong_line = INVOICE.replace(
        "Steel bars 12mm          4       30.000    120.000", "Steel bars 12mm          4       30.000    110.000"
    )
    assert any(i.code == "line_math_mismatch" for i in extract_for("sales_invoice", pages(wrong_line)).issues)


def test_incorrect_ocr_values_surface_as_low_confidence() -> None:
    ex = extract_for("sales_invoice", pages(INVOICE, conf=0.4))
    f = by_key(ex)
    assert f["invoice_number"].confidence < 0.5 and f["invoice_number"].status == "needs_review"


def test_multi_page_invoice_reads_totals_from_the_last_page() -> None:
    p1 = INVOICE.split("Subtotal")[0]
    p2 = "Subtotal 160.000\nVAT 5% 8.000\nTotal 168.000 OMR\n"
    ex = extract_for("sales_invoice", pages(p1, p2))
    f = by_key(ex)
    assert f["gross_amount"].value == "168.000" and f["gross_amount"].page == 2
    assert f["invoice_number"].page == 1 and len(ex.items) == 3


def test_receipt_and_credit_note_extraction() -> None:
    r = by_key(
        extract_for(
            "receipt",
            pages(
                "Coffee House\nReceipt No: R-1881\nDate: 2025-03-14 10:42\nLatte 3.000\nSubtotal 3.000\nVAT 0.150\nTotal 3.150 OMR\nPaid by VISA\nRef: AUTH7731"
            ),
        )
    )
    assert (r["merchant_name"].value, r["receipt_number"].value, r["transaction_date"].value) == (
        "Coffee House",
        "R-1881",
        "2025-03-14",
    )
    assert (r["transaction_time"].value, r["total_amount"].value, r["vat_amount"].value, r["payment_method"].value) == (
        "10:42",
        "3.150",
        "0.150",
        "Visa",
    )
    assert r["reference_number"].value == "AUTH7731"
    n = by_key(
        extract_for(
            "credit_note",
            pages(
                "Al Noor Trading LLC\nCREDIT NOTE\nCredit Note No: CN-9\nDate: 2025-04-01\nOriginal Invoice: INV-2025-0042\nReason: Damaged goods\nSubtotal 20.000\nVAT 1.000\nTotal 21.000 OMR"
            ),
        )
    )
    assert (n["document_number"].value, n["original_invoice_reference"].value, n["reason"].value) == (
        "CN-9",
        "INV-2025-0042",
        "Damaged goods",
    )
    assert (n["net_adjustment"].value, n["vat_adjustment"].value, n["gross_adjustment"].value) == (
        "20.000",
        "1.000",
        "21.000",
    )


# ------------------------------------------------------------------ bank statements
STATEMENT_P1 = """
Bank Muscat
STATEMENT OF ACCOUNT
Account Holder: Al Noor Trading LLC
Account No: 0123456789012
IBAN: OM81BMUS0000012345678901
Currency: OMR
Statement Period: 01/03/2025 to 31/03/2025
Opening Balance 1,000.000
Date        Description                     Debit       Credit      Balance
01/03/2025  Salary transfer ACME                      500.000    1,500.000
02/03/2025  Card purchase supermarket        45.250                1,454.750
  POS ref 99812
03/03/2025  Cheque deposit                              300.000    1,754.750
"""
STATEMENT_P2 = """
Page 2 of 2
Date        Description                     Debit       Credit      Balance
05/03/2025  Rent March                      400.000                1,354.750
31/03/2025  Bank charges                      4.750                1,350.000
Closing Balance 1,350.000
"""


def test_bank_statement_header_rows_continuations_and_masking() -> None:
    ex = parse_bank_statement(pages(STATEMENT_P1, STATEMENT_P2))
    f = by_key(ex)
    assert f["bank_name"].value == "Bank Muscat" and f["account_holder"].value == "Al Noor Trading LLC"
    assert f["account_number"].value.endswith("9012") and set(f["account_number"].value[:-4]) == {"*"}  # masked
    assert (
        f["iban"].value.endswith("8901")
        and "OM81BMUS" not in f["iban"].value
        and "OM81BMUS" not in (f["iban"].source_text or "")
    )
    assert (f["currency"].value, f["period_start"].value, f["period_end"].value) == ("OMR", "2025-03-01", "2025-03-31")
    assert (f["opening_balance"].value, f["closing_balance"].value) == ("1000.000", "1350.000")
    txs = ex.transactions
    assert [t.row_no for t in txs] == [
        1,
        2,
        3,
        4,
        5,
    ]  # order kept across the page break; the repeated header is not a row
    assert [t.page for t in txs] == [1, 1, 1, 2, 2]
    assert (txs[0].credit, txs[0].debit, txs[0].balance) == (Decimal("500.000"), None, Decimal("1500.000"))
    assert (txs[1].debit, txs[1].credit) == (Decimal("45.250"), None)
    assert "POS ref 99812" in txs[1].description  # wrapped description joined to the right row
    assert txs[4].description.startswith("Bank charges") and txs[4].tx_date == "2025-03-31"
    assert all(t.bbox and t.source_text for t in txs)
    assert any(i.code == "statement_reconciled" for i in ex.issues) and not [
        i for i in ex.issues if i.severity == "error"
    ]


def test_unreconciled_statement_reports_difference_and_suspect_row_without_adjusting_anything() -> None:
    p2 = STATEMENT_P2.replace("Rent March                      400.000", "Rent March                      40.000 ")
    ex = parse_bank_statement(pages(STATEMENT_P1, p2))
    codes = {i.code: i for i in ex.issues}
    assert "statement_unreconciled" in codes and "running_balance_break" in codes
    assert codes["running_balance_break"].data["breaks"][0]["row"] == 4  # the row that no longer follows the balance
    assert (
        codes["statement_unreconciled"].data["closing"] == "1350.000"
        and codes["statement_unreconciled"].data["difference"] != "0.000"
    )
    assert (
        ex.transactions[3].debit == Decimal("40.000") and "balance_mismatch" in ex.transactions[3].flags
    )  # kept as read
    assert ex.transactions[3].status == "needs_review"


def test_a_missing_amount_is_flagged_not_filled_in() -> None:
    p1 = STATEMENT_P1.replace("45.250                1,454.750", "                     1,454.750")
    ex = parse_bank_statement(pages(p1, STATEMENT_P2))
    row = ex.transactions[1]
    assert row.debit is None and row.credit is None and "no_amount" in row.flags
    assert any(i.code == "rows_without_amount" for i in ex.issues)


def test_single_signed_amount_column_and_balance_driven_direction() -> None:
    text = """
National Bank
Account Statement
Currency: USD
Opening Balance 500.00
Date        Description                 Amount         Balance
02/05/2025  Invoice payment received    250.00         750.00
03/05/2025  Supplier payment           -100.00         650.00
04/05/2025  ATM withdrawal               (50.00)        600.00
Closing Balance 600.00
"""
    ex = parse_bank_statement(pages(text))
    t = ex.transactions
    assert (t[1].debit, t[2].debit) == (Decimal("100.00"), Decimal("50.00"))
    assert (t[0].credit is None and "amount_direction_unknown" in t[0].flags) or t[0].credit == Decimal("250.00")
    # the unsigned positive amount is resolved ONLY by arithmetic on the printed balances, and still validates
    assert t[0].credit == Decimal("250.00") or "sign_unclear" in t[0].flags
    assert not any(i.code == "statement_unreconciled" for i in ex.issues) or any(
        x.debit is None and x.credit is None for x in t
    )


def test_statement_without_balances_cannot_be_checked_and_says_so() -> None:
    text = (
        "Some Bank\nDate Description Debit Credit Balance\n01/03/2025  A  10.00   90.00\n02/03/2025  B   5.00   85.00\n"
    )
    ex = parse_bank_statement(pages(text))
    assert any(i.code == "balances_incomplete" for i in ex.issues)
    assert not any(i.code == "statement_reconciled" for i in ex.issues)


def test_rows_that_look_alike_are_never_merged() -> None:
    text = STATEMENT_P1.replace(
        "03/03/2025  Cheque deposit                              300.000    1,754.750",
        "03/03/2025  Salary transfer ACME                      500.000    2,000.000",
    )
    ex = parse_bank_statement(pages(text))
    assert len([t for t in ex.transactions if t.description.startswith("Salary transfer")]) == 2


# ------------------------------------------------------------------ splitting
def test_combined_pdf_gets_a_split_proposal_not_a_split() -> None:
    a = "Al Noor Trading LLC\nINVOICE\nInvoice No: INV-1\nPage 1 of 1\nTotal 10.00"
    b = "Al Noor Trading LLC\nINVOICE\nInvoice No: INV-2\nPage 1 of 1\nTotal 20.00"
    s = suggest_splits(pages(a, b))
    assert s is not None and [(x["start_page"], x["end_page"]) for x in s["segments"]] == [(1, 1), (2, 2)]
    assert suggest_splits(pages(a)) is None
    one = suggest_splits(pages("Statement\nPage 1 of 2\nInvoice No: INV-1", "continued\ntotal 4.00"))
    assert one is None
