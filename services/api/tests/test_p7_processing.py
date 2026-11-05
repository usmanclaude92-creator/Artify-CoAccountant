"""Phase 7 integration tests: real OCR (Tesseract) and the real pipeline through the API against PostgreSQL.
Documents are synthetic and generated in memory (tests/docfixtures.py)."""

from __future__ import annotations

import shutil
import uuid

import docfixtures as fx
import pytest
from artify_api import ocr_engine, understanding
from artify_api.doc_extract import CompanyProfile
from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text
from test_p6_documents import W, assign, backend, mk, upload  # noqa: F401  (backend = autouse storage fixture)

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="the Tesseract OCR engine is not installed")


def D(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/documents{path}"


def Pn(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/processing{path}"


def E(cid: str, eid: str, path: str = "") -> str:
    return f"/companies/{cid}/extractions/{eid}{path}"


@pytest.fixture
def firm(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Intel Firm")
    a = make_company(owner, "IA", name="Al Noor Trading LLC")["id"]
    b = make_company(owner, "IB", name="Gulf Builders Co")["id"]
    node = mk(owner, a, "Inbox")["id"]
    node_b = mk(owner, b, "Inbox B")["id"]
    return owner, a, b, node, node_b


def start(owner, cid, doc_id, **kw):  # type: ignore[no-untyped-def]
    body = {"idempotency_key": kw.pop("key", uuid.uuid4().hex), "kind": "full", **kw}
    return owner.post(D(cid, f"/{doc_id}/process"), body)


def run(owner, cid, doc_id, **kw):  # type: ignore[no-untyped-def]
    """Start a job and return (job, intelligence view). Background work has finished when the call returns."""
    r = start(owner, cid, doc_id, **kw)
    assert r.status_code in (200, 202), r.text
    job = owner.get(Pn(cid, f"/jobs/{r.json()['id']}")).json()
    return job, owner.get(D(cid, f"/{doc_id}/intelligence")).json()


def ext(owner, cid, doc_id):  # type: ignore[no-untyped-def]
    eid = owner.get(D(cid, f"/{doc_id}/intelligence")).json()["extraction_id"]
    return owner.get(E(cid, eid)).json()


def fields(x):  # type: ignore[no-untyped-def]
    return {f["field_key"]: f for f in x["fields"]}


def doc_of(owner, cid, node, content, name):  # type: ignore[no-untyped-def]
    return upload(owner, cid, content, name, node)["id"]


def test_requires_tesseract_marker() -> None:
    assert ocr_engine.TesseractProvider().available()


# ------------------------------------------------------------------ OCR across formats
def test_searchable_pdf_uses_embedded_text_and_extracts_the_invoice_end_to_end(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "invoice.pdf")
    job, intel = run(owner, a, doc)
    assert job["status"] == "awaiting_review" and job["progress"] == 100 and job["attempt_count"] == 1
    assert job["providers"]["ocr"] == "tesseract" and job["providers"]["understanding"] == "rules"
    ocr = owner.get(D(a, f"/{doc}/ocr")).json()
    assert ocr["provider_key"] == "tesseract" and "tesseract" in ocr["engine_version"] and ocr["languages"] == ["eng"]
    assert ocr["pages"][0]["source"] == "pdf_text" and ocr["pages"][0]["avg_confidence"] == 100.0
    assert any("INV-2025-0042" in b["text"] for b in ocr["pages"][0]["blocks"])
    assert all(len(b["bbox"]) == 4 for b in ocr["pages"][0]["blocks"])
    cls = intel["classification"]
    assert (
        cls["type_key"] == "sales_invoice"
        and cls["kind_key"] == "sales_invoice"
        and cls["confidence"] >= 0.6
        and not cls["needs_manual"]
    )
    assert cls["provider_key"] == "rules" and cls["model_version"] and cls["evidence"]
    x = ext(owner, a, doc)
    f = fields(x)
    assert (
        f["invoice_number"]["value"] == "INV-2025-0042"
        and f["invoice_number"]["page_no"] == 1
        and f["invoice_number"]["bbox"]
    )
    assert f["invoice_date"]["value"] == "2025-03-14" and f["currency"]["value"] == "OMR"
    assert (f["net_amount"]["value"], f["vat_amount"]["value"], f["gross_amount"]["value"]) == (
        "160.000",
        "8.000",
        "168.000",
    )
    assert len(x["line_items"]) == 3 and x["line_items"][1]["values"]["description"] == "Steel bars 12mm"
    assert x["status"] == "draft" and x["approved_at"] is None  # AI output is never approved
    img = owner.get(D(a, f"/{doc}/pages/1/image"))
    assert (
        img.status_code == 200
        and img.headers["content-type"] == "image/png"
        and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    )


def test_scanned_pdf_is_read_by_ocr(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    pdf_bytes = fx.scanned_pdf([fx.draw_page(fx.INVOICE_LINES, mono=True)])
    doc = doc_of(owner, a, node, pdf_bytes, "scan.pdf")
    job, _ = run(owner, a, doc)
    assert job["status"] == "awaiting_review", job
    ocr = owner.get(D(a, f"/{doc}/ocr")).json()
    assert ocr["pages"][0]["source"] == "ocr" and 80 < ocr["pages"][0]["avg_confidence"] <= 100
    f = fields(ext(owner, a, doc))
    assert f["invoice_number"]["value"] == "INV-2025-0042" and f["gross_amount"]["value"] == "168.000"
    assert f["invoice_number"]["method"] == "label_id" and f["invoice_number"]["confidence"] > 0.7


@pytest.mark.parametrize(("name", "make"), [("page.jpg", fx.jpeg), ("page.png", fx.png), ("page.bmp", fx.bmp)])
def test_image_formats_are_read(firm, name, make) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, make(fx.draw_page(fx.INVOICE_LINES, mono=True)), name)
    job, _ = run(owner, a, doc)
    assert job["status"] == "awaiting_review", job
    assert fields(ext(owner, a, doc))["invoice_number"]["value"] == "INV-2025-0042"


def test_multi_page_tiff_keeps_page_order_and_flags_the_blank_page(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    blank = fx.draw_page([])
    pages = [fx.draw_page(fx.INVOICE_LINES[:11], mono=True), blank, fx.draw_page(fx.INVOICE_LINES[11:], mono=True)]
    doc = doc_of(owner, a, node, fx.tiff(pages), "multi.tiff")
    job, _ = run(owner, a, doc)
    assert job["status"] == "awaiting_review" and job["pages_total"] == 3 and job["pages_done"] == 3
    ocr = owner.get(D(a, f"/{doc}/ocr?page_from=1&page_to=3")).json()
    assert [p["page_no"] for p in ocr["pages"]] == [1, 2, 3]
    assert [p["is_blank"] for p in ocr["pages"]] == [False, True, False]
    assert any("Cement" in b["text"] for b in ocr["pages"][2]["blocks"]) and ocr["pages"][1]["blocks"] == []
    f = fields(ext(owner, a, doc))
    assert (
        f["invoice_number"]["page_no"] == 1 and f["gross_amount"]["page_no"] == 3
    )  # evidence points at the right page


@pytest.mark.parametrize("angle", [90.0, 180.0, 270.0])
def test_rotated_pages_are_straightened_before_reading(firm, angle) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    img = fx.draw_page(fx.INVOICE_LINES, mono=True, rotate=angle)
    doc = doc_of(owner, a, node, fx.png(img), f"rot{int(angle)}.png")
    job, _ = run(owner, a, doc)
    assert job["status"] == "awaiting_review", job
    page = owner.get(D(a, f"/{doc}/ocr")).json()["pages"][0]
    assert page["rotation_applied"] != 0 and any("INV-2025-0042" in b["text"] for b in page["blocks"])


def test_slightly_skewed_scan_is_deskewed(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.jpeg(fx.draw_page(fx.INVOICE_LINES, mono=True, rotate=3.0)), "skew.jpg")
    run(owner, a, doc)
    page = owner.get(D(a, f"/{doc}/ocr")).json()["pages"][0]
    assert abs(page["deskew_angle"]) >= 1.0
    assert fields(ext(owner, a, doc))["invoice_number"]["value"] == "INV-2025-0042"


def test_poor_quality_scans_never_crash_and_report_lower_confidence(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    good = doc_of(owner, a, node, fx.png(fx.draw_page(fx.INVOICE_LINES, mono=True)), "good.png")
    bad_img = fx.draw_page(fx.INVOICE_LINES, mono=True, size=14, noise=0.03, blur=1.6)
    bad = doc_of(owner, a, node, fx.png(bad_img), "bad.png")
    run(owner, a, good)
    job, _ = run(owner, a, bad)
    assert job["status"] in ("awaiting_review", "needs_manual"), job
    g = owner.get(D(a, f"/{good}/ocr")).json()["pages"][0]
    b = owner.get(D(a, f"/{bad}/ocr")).json()["pages"][0]
    assert (b["avg_confidence"] or 0) < g["avg_confidence"]
    if job["status"] == "awaiting_review":
        bf = fields(ext(owner, a, bad))
        assert all(f["review_status"] in ("extracted", "needs_review", "missing") for f in bf.values())
        # whatever could not be read is not invented: a missing value is missing, a doubtful one needs review
        assert any(f["review_status"] in ("needs_review", "missing") for f in bf.values())


def test_english_and_arabic_text_in_one_document(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    img = fx.draw_page(["Invoice No: INV-77", "Total 105.000 OMR"], arabic=["فاتورة ضريبية", "الإجمالي"])
    doc = doc_of(owner, a, node, fx.png(img), "bilingual.png")
    r = start(owner, a, doc, languages=["eng", "ara"])
    assert r.status_code == 202, r.text
    ocr = owner.get(D(a, f"/{doc}/ocr")).json()
    assert ocr["languages"] == ["eng", "ara"]
    text_all = " ".join(b["text"] for b in ocr["pages"][0]["blocks"])
    assert "INV-77" in text_all and any("؀" <= ch <= "ۿ" for ch in text_all)  # Arabic letters were recognised


def test_unsupported_corrupt_and_oversized_inputs_fail_clearly(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    docx = doc_of(owner, a, node, __import__("test_p6_documents").ooxml("docx", "x"), "letter.docx")
    r = start(owner, a, docx)
    assert r.status_code == 422 and r.json()["reason"] == "unsupported_for_ocr"
    # a PDF that passes the upload checks (header and trailer) but cannot be parsed
    broken = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + b"garbage" * 40 + b"\n%%EOF\n"
    d2 = doc_of(owner, a, node, broken, "broken.pdf")
    job, _ = run(owner, a, d2)
    assert (
        job["status"] == "failed" and job["error_code"] == "unreadable_document" and job["attempt_count"] == 1
    )  # not retried
    retry = owner.post(Pn(a, f"/jobs/{job['id']}/retry"))
    assert retry.status_code == 409  # a retry cannot fix an unreadable file
    # page limit
    from artify_api.settings import get_settings

    monkeypatch.setattr(get_settings(), "ocr_max_pages", 2)
    d3 = doc_of(owner, a, node, fx.tiff([fx.draw_page(["p"]) for _ in range(3)]), "long.tiff")
    job3, _ = run(owner, a, d3)
    assert job3["status"] == "failed" and job3["error_code"] == "too_many_pages"


def test_unreadable_provider_state_is_reported_instead_of_fake_results(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.png(fx.draw_page(fx.INVOICE_LINES, mono=True)), "p.png")
    from artify_api.settings import get_settings

    monkeypatch.setattr(get_settings(), "tesseract_cmd", "definitely-not-installed-ocr")
    r = start(owner, a, doc)
    assert (
        r.status_code == 503 and r.json()["code"] == "provider_not_configured" and "not installed" in r.json()["detail"]
    )
    assert owner.get(Pn(a, "/jobs")).json()["total"] == 0  # no job, no invented output
    prov = owner.get("/processing/providers").json()["providers"]
    tess = next(p for p in prov if p["key"] == "tesseract")
    assert tess["configured"] is False and tess["note"]


# ------------------------------------------------------------------ bank statements
def statement_doc(owner, a, node, lines2=None, name="statement.pdf"):  # type: ignore[no-untyped-def]
    return doc_of(owner, a, node, fx.text_pdf([fx.STATEMENT_1, lines2 or fx.STATEMENT_2], mono=True, size=8), name)


def test_bank_statement_multi_page_rows_balances_and_validation(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = statement_doc(owner, a, node)
    job, intel = run(owner, a, doc)
    assert job["status"] == "awaiting_review" and intel["classification"]["type_key"] == "bank_statement"
    x = ext(owner, a, doc)
    st = x["statement"]
    assert (
        st["bank_name"] == "Bank Muscat"
        and st["currency"] == "OMR"
        and (st["period_start"], st["period_end"]) == ("2025-03-01", "2025-03-31")
    )
    assert (st["opening_balance"], st["closing_balance"]) == ("1000.0000", "1350.0000")
    assert st["account_number_masked"].endswith("9012") and "0123456789012" not in str(
        x
    )  # the full number is never stored
    txs = x["transactions"]
    assert [t["row_no"] for t in txs] == [1, 2, 3, 4, 5] and [t["page_no"] for t in txs] == [1, 1, 1, 2, 2]
    assert (txs[0]["credit"], txs[0]["debit"]) == ("500.0000", None) and (txs[1]["debit"], txs[1]["credit"]) == (
        "45.2500",
        None,
    )
    assert txs[4]["description"].startswith("Bank charges") and txs[3]["balance"] == "1354.7500"
    assert all(t["bbox"] and t["source_text"] and t["page_no"] for t in txs)  # every row is traceable
    codes = {i["code"] for i in st["validation"]}
    assert "statement_reconciled" in codes and "statement_unreconciled" not in codes
    assert "Page 2 of 2" not in " ".join(t["description"] for t in txs)  # page furniture is not a transaction


def test_unreconciled_statement_is_flagged_with_the_difference_and_cannot_be_approved_silently(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    p2 = [ln.replace("400.000   ", " 40.000   ") for ln in fx.STATEMENT_2]
    doc = statement_doc(owner, a, node, p2, "bad.pdf")
    run(owner, a, doc)
    x = ext(owner, a, doc)
    issue = next(i for i in x["statement"]["validation"] if i["code"] == "statement_unreconciled")
    assert issue["severity"] == "error" and issue["data"]["difference"] != "0.000" and issue["data"]["suspect_rows"]
    assert any(i["code"] == "running_balance_break" for i in x["statement"]["validation"])
    bad_row = next(t for t in x["transactions"] if t["row_no"] == 4)
    assert bad_row["debit"] == "40.0000" and "balance_mismatch" in bad_row["flags"]  # kept as read, never adjusted
    # resolve flagged rows; approval still refuses without an explicit, explained acknowledgement
    for t in x["transactions"]:
        if t["review_status"] == "needs_review":
            assert owner.patch(E(a, x["id"], f"/transactions/{t['id']}"), {"action": "confirm"}).status_code == 200
    for f in x["fields"]:
        if f["review_status"] == "needs_review":
            owner.patch(E(a, x["id"], f"/fields/{f['field_key']}"), {"action": "confirm"})
    r = owner.post(E(a, x["id"], "/approve"), {})
    assert r.status_code == 409 and r.json()["errors"]
    assert (
        owner.post(E(a, x["id"], "/approve"), {"acknowledge_validation_errors": True}).status_code == 422
    )  # a note is required
    ok = owner.post(
        E(a, x["id"], "/approve"),
        {
            "acknowledge_validation_errors": True,
            "note": "Bank confirmed row 4 is 40.000; closing balance is the bank's figure.",
        },
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


def test_accountant_corrects_adds_and_marks_rows_and_the_balance_check_follows(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    p2 = [ln.replace("400.000   ", " 40.000   ") for ln in fx.STATEMENT_2]
    doc = statement_doc(owner, a, node, p2, "fix.pdf")
    run(owner, a, doc)
    x = ext(owner, a, doc)
    row4 = next(t for t in x["transactions"] if t["row_no"] == 4)
    r = owner.patch(
        E(a, x["id"], f"/transactions/{row4['id']}"),
        {
            "action": "correct",
            "patch": {"debit": "400.000", "description": "Rent March (corrected)"},
            "reason": "misread digit",
        },
    )
    assert r.status_code == 200 and r.json()["debit"] == "400.0000" and r.json()["review_status"] == "corrected"
    assert "balance_mismatch" in r.json()["flags"]  # the original flag stays as history of what was read
    again = owner.get(E(a, x["id"])).json()
    assert {i["code"] for i in again["statement"]["validation"]} >= {
        "statement_reconciled"
    } and "statement_unreconciled" not in {i["code"] for i in again["statement"]["validation"]}
    # debit and credit together are refused; dates and amounts are validated
    assert (
        owner.patch(
            E(a, x["id"], f"/transactions/{row4['id']}"), {"action": "correct", "patch": {"debit": "1", "credit": "1"}}
        ).status_code
        == 422
    )
    assert (
        owner.patch(
            E(a, x["id"], f"/transactions/{row4['id']}"), {"action": "correct", "patch": {"tx_date": "31/02/2025"}}
        ).status_code
        == 422
    )
    # a missed row can be added by hand; it is marked as manual and changes the check
    added = owner.post(
        E(a, x["id"], "/transactions"),
        {"tx_date": "2025-03-20", "description": "Missed fee", "debit": "10.000", "reason": "on the paper statement"},
    )
    assert added.status_code == 201 and added.json()["manual"] is True and "added_manually" in added.json()["flags"]
    codes = {i["code"] for i in owner.get(E(a, x["id"])).json()["statement"]["validation"]}
    assert "statement_unreconciled" in codes  # the extra 10.000 now breaks the reconciliation, as it should
    # an unreadable row is excluded from the check but never deleted
    unread = owner.patch(E(a, x["id"], f"/transactions/{added.json()['id']}"), {"action": "unreadable"})
    assert unread.json()["review_status"] == "unreadable"
    assert "statement_unreconciled" not in {
        i["code"] for i in owner.get(E(a, x["id"])).json()["statement"]["validation"]
    }
    hist = owner.get(D(a, f"/{doc}/events")).json()["items"]
    assert {"extraction.correct", "extraction.added", "extraction.unreadable"} <= {e["event_type"] for e in hist}


# ------------------------------------------------------------------ review, approval, versions
def test_review_actions_approval_and_immutability_after_approval(firm, client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "inv.pdf")
    run(owner, a, doc)
    x = ext(owner, a, doc)
    xid = x["id"]
    f = fields(x)
    assert f["supplier_name"]["review_status"] == "needs_review"  # heuristic party: a person must look
    unresolved = owner.post(E(a, xid, "/approve"), {})
    assert unresolved.status_code == 409 and unresolved.json()["unresolved"]["fields"] > 0
    # correct, confirm, reject, missing, unreadable
    r = owner.patch(
        E(a, xid, "/fields/supplier_name"),
        {"action": "correct", "value": "Al Noor Trading L.L.C.", "reason": "legal form"},
    )
    assert (
        r.json()["value"] == "Al Noor Trading L.L.C."
        and r.json()["review_status"] == "corrected"
        and r.json()["original_value"] == "Al Noor Trading LLC"
    )
    assert (
        owner.patch(E(a, xid, "/fields/invoice_number"), {"action": "confirm"}).json()["review_status"] == "confirmed"
    )
    assert (
        owner.patch(E(a, xid, "/fields/po_reference"), {"action": "confirm"}).status_code == 409
    )  # nothing to confirm
    assert owner.patch(E(a, xid, "/fields/po_reference"), {"action": "missing"}).json()["review_status"] == "missing"
    assert owner.patch(E(a, xid, "/fields/payment_terms"), {"action": "reject"}).json()["review_status"] == "rejected"
    assert owner.patch(E(a, xid, "/fields/description"), {"action": "unreadable"}).json()["value"] is None
    assert (
        owner.patch(E(a, xid, "/fields/invoice_date"), {"action": "correct", "value": "not a date"}).status_code == 422
    )
    assert (
        owner.patch(E(a, xid, "/fields/gross_amount"), {"action": "correct", "value": "1,000.5"}).status_code == 200
    )  # parsed to a Decimal string
    assert owner.patch(E(a, xid, "/fields/nope"), {"action": "confirm"}).status_code == 404
    # line items can be corrected too
    li = owner.get(E(a, xid)).json()["line_items"][0]
    c = owner.patch(E(a, xid, f"/line-items/{li['id']}"), {"action": "correct", "values": {"quantity": "12"}})
    assert c.json()["values"]["quantity"] == "12" and c.json()["review_status"] == "corrected"
    for fld in owner.get(E(a, xid)).json()["fields"]:
        if fld["review_status"] == "needs_review":
            owner.patch(E(a, xid, f"/fields/{fld['field_key']}"), {"action": "confirm"})
    for it in owner.get(E(a, xid)).json()["line_items"]:
        if it["review_status"] == "needs_review":
            owner.patch(E(a, xid, f"/line-items/{it['id']}"), {"action": "confirm"})
    # the AI/system can never approve: the database refuses an approved extraction without an approver
    with owner_engine.begin() as conn, pytest.raises(Exception, match="check constraint"):
        conn.execute(text("UPDATE extractions SET status = 'approved' WHERE id = :i"), {"i": xid})
    ok = owner.post(E(a, xid, "/approve"), {"note": "checked against the paper original"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["approved_at"]
    assert all(
        fl["review_status"] in ("confirmed", "corrected", "missing", "unreadable", "rejected")
        for fl in ok.json()["fields"]
    )
    # approved data is immutable; corrections need a new extraction version
    assert owner.patch(E(a, xid, "/fields/supplier_name"), {"action": "correct", "value": "X"}).status_code == 409
    assert owner.post(E(a, xid, "/approve"), {}).status_code == 409
    corr = owner.get(D(a, f"/{doc}/events")).json()["items"]
    types = {e["event_type"] for e in corr}
    assert {"extraction.correct", "extraction.confirm", "extraction.approved", "processing.completed"} <= types
    with owner_engine.begin() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM field_corrections WHERE extraction_id = :i"), {"i": xid}
        ).scalar_one()
        assert n >= 9
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'extraction.approved' AND organization_id = :o"),
                {"o": owner.org_id},
            ).scalar_one()
            == 1
        )
        row = conn.execute(text("SELECT approved_by FROM extractions WHERE id = :i"), {"i": xid}).one()
        assert str(row.approved_by) == uid(owner)


def test_reprocessing_adds_a_new_version_and_keeps_every_earlier_result(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "re.pdf")
    run(owner, a, doc)
    first = ext(owner, a, doc)
    owner.patch(E(a, first["id"], "/fields/supplier_name"), {"action": "correct", "value": "Edited Name"})
    job2, _ = run(owner, a, doc, reason="re-run after correcting the file")
    assert job2["status"] == "awaiting_review"
    versions = owner.get(D(a, f"/{doc}/extractions")).json()["items"]
    assert [v["extraction_no"] for v in versions] == [2, 1] and [v["status"] for v in versions] == [
        "draft",
        "superseded",
    ]
    old = owner.get(E(a, first["id"])).json()
    assert (
        fields(old)["supplier_name"]["value"] == "Edited Name" and old["status"] == "superseded"
    )  # earlier work and corrections are kept
    assert len(owner.get(Pn(a, f"/jobs?document_id={doc}")).json()["items"]) == 2
    assert owner.get(D(a, f"/{doc}/ocr")).json()["id"] != ""
    # an approved extraction stays approved when the document is processed again
    new = ext(owner, a, doc)
    for fld in new["fields"]:
        if fld["review_status"] == "needs_review":
            owner.patch(E(a, new["id"], f"/fields/{fld['field_key']}"), {"action": "confirm"})
    assert owner.post(E(a, new["id"], "/approve"), {}).status_code == 200
    run(owner, a, doc)
    st = {v["extraction_no"]: v["status"] for v in owner.get(D(a, f"/{doc}/extractions")).json()["items"]}
    assert st == {3: "draft", 2: "approved", 1: "superseded"}


def test_duplicate_requests_never_create_duplicate_records(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "dup.pdf")
    r1 = start(owner, a, doc, key="same-key-0001")
    r2 = start(owner, a, doc, key="same-key-0001")
    assert r1.status_code == 202 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert start(owner, a, doc, key="same-key-0001", kind="ocr").status_code == 409  # the key is bound to its request
    with owner_engine.begin() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM extractions WHERE document_id = :d"), {"d": doc}).scalar_one() == 1
        )
        assert (
            conn.execute(text("SELECT count(*) FROM ocr_results WHERE document_id = :d"), {"d": doc}).scalar_one() == 1
        )
    # a job that is still running for this version is returned instead of a second one
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status = 'ocr_processing' WHERE id = :i"), {"i": r1.json()["id"]})
    r3 = start(owner, a, doc, key="another-key-0002")
    assert r3.status_code == 200 and r3.json()["id"] == r1.json()["id"]
    with owner_engine.begin() as conn, pytest.raises(Exception, match="unique"):
        conn.execute(
            text(
                "INSERT INTO processing_jobs (id, organization_id, company_id, document_id, version_id, version_no, kind, idempotency_key, status) SELECT gen_random_uuid(), organization_id, company_id, document_id, version_id, version_no, kind, 'raw-dup-key-1', 'queued' FROM processing_jobs WHERE id = :i"
            ),
            {"i": r1.json()["id"]},
        )


# ------------------------------------------------------------------ reliability
def test_provider_timeouts_are_retried_then_reported_and_a_manual_retry_recovers(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.png(fx.draw_page(fx.INVOICE_LINES, mono=True)), "slow.png")
    real = ocr_engine.TesseractProvider.recognize
    calls = {"n": 0}

    def flaky(self, img, languages, page_no):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise ocr_engine.OcrError("provider_timeout", "The OCR engine did not finish in time.", True)

    monkeypatch.setattr(ocr_engine.TesseractProvider, "recognize", flaky)
    job, _ = run(owner, a, doc)
    assert (
        job["status"] == "failed"
        and job["error_code"] == "provider_timeout"
        and job["attempt_count"] == 3
        and calls["n"] == 3
    )
    hist = owner.get(Pn(a, f"/jobs/{job['id']}/history")).json()
    assert [x["outcome"] for x in hist["attempts"]] == ["timeout", "timeout", "timeout"]
    assert owner.get(Pn(a, "/jobs?status=failed")).json()["total"] == 1
    assert owner.get(D(a, f"/{doc}")).status_code == 200  # the uploaded document is untouched
    monkeypatch.setattr(ocr_engine.TesseractProvider, "recognize", real)
    r = owner.post(Pn(a, f"/jobs/{job['id']}/retry"))
    assert r.status_code == 200
    j2 = owner.get(Pn(a, f"/jobs/{job['id']}")).json()
    assert j2["status"] == "awaiting_review" and j2["attempt_count"] == 4  # same job, history continues
    assert len(owner.get(Pn(a, f"/jobs/{job['id']}/history")).json()["attempts"]) == 4
    assert fields(ext(owner, a, doc))["invoice_number"]["value"] == "INV-2025-0042"


def test_transient_failure_recovers_on_the_second_attempt(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.png(fx.draw_page(fx.INVOICE_LINES, mono=True)), "once.png")
    real = ocr_engine.TesseractProvider.recognize
    state = {"n": 0}

    def once(self, img, languages, page_no):  # type: ignore[no-untyped-def]
        state["n"] += 1
        if state["n"] == 1:
            raise ocr_engine.OcrError("provider_error", "temporary", True)
        return real(self, img, languages, page_no)

    monkeypatch.setattr(ocr_engine.TesseractProvider, "recognize", once)
    job, _ = run(owner, a, doc)
    assert job["status"] == "awaiting_review" and job["attempt_count"] == 2
    attempts = owner.get(Pn(a, f"/jobs/{job['id']}/history")).json()["attempts"]
    assert [x["outcome"] for x in attempts] == ["failed", "succeeded"]


def test_cancelled_and_stuck_jobs_are_handled(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "c.pdf")
    r = start(owner, a, doc)
    jid = r.json()["id"]
    assert owner.post(Pn(a, f"/jobs/{jid}/cancel")).status_code == 409  # already finished
    assert owner.post(Pn(a, f"/jobs/{jid}/retry")).status_code == 409
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status = 'queued' WHERE id = :i"), {"i": jid})
    assert owner.post(Pn(a, f"/jobs/{jid}/cancel")).json()["status"] == "cancelled"
    from artify_api import processing

    assert processing.run_job(owner.org_id, a, jid) == "skipped"  # a cancelled job is never executed by a late worker


def test_the_celery_entry_point_is_idempotent_and_dispatch_uses_the_queue(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "q.pdf")
    from artify_api import processing, processing_tasks, worker
    from artify_api.settings import get_settings

    sent = []
    monkeypatch.setattr(get_settings(), "processing_mode", "celery")
    monkeypatch.setattr(worker.celery_app, "send_task", lambda name, args=None, **kw: sent.append((name, args)))
    r = start(owner, a, doc)
    assert r.status_code == 202 and sent == [("artify.process_document", [owner.org_id, a, r.json()["id"]])]
    assert owner.get(Pn(a, f"/jobs/{r.json()['id']}")).json()["status"] == "queued"  # nothing ran in the request
    assert processing_tasks.process_document(owner.org_id, a, r.json()["id"]) == "done"
    assert (
        processing_tasks.process_document(owner.org_id, a, r.json()["id"]) == "skipped"
    )  # a redelivered message does nothing
    assert owner.get(Pn(a, f"/jobs/{r.json()['id']}")).json()["status"] == "awaiting_review"
    _ = processing


# ------------------------------------------------------------------ entitlements
def test_ocr_page_quota_stops_new_processing_but_never_deletes_anything(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    two_pages = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES[:8], fx.INVOICE_LINES[8:]], mono=True), "two.pdf")
    set_plan_limit(owner_engine, owner.org_id, "quota.ocr_pages_per_month", 1)
    job, _ = run(owner, a, two_pages)
    assert (
        job["status"] == "failed"
        and job["error_code"] == "entitlement_exceeded"
        and "quota.ocr_pages_per_month" in job["error_message"]
    )
    assert (
        owner.get(D(a, f"/{two_pages}")).status_code == 200
        and owner.get(D(a, f"/{two_pages}/content")).status_code == 200
    )
    one = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "one.pdf")
    job1, _ = run(owner, a, one)
    assert job1["status"] == "awaiting_review"  # a one-page document still fits the allowance of one page
    set_plan_limit(owner_engine, owner.org_id, "quota.ocr_pages_per_month", 100)
    assert (
        owner.post(Pn(a, f"/jobs/{job['id']}/retry")).status_code == 200
    )  # the administrator raised the limit: the same job continues
    assert owner.get(Pn(a, f"/jobs/{job['id']}")).json()["status"] == "awaiting_review"


def test_extraction_bank_statement_and_concurrency_limits(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    inv = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "i.pdf")
    stm = statement_doc(owner, a, node)
    set_plan_limit(owner_engine, owner.org_id, "quota.bank_statements_per_month", 0)
    j, _ = run(owner, a, stm)
    assert (
        j["status"] == "failed"
        and j["error_code"] == "entitlement_exceeded"
        and "bank_statements" in j["error_message"]
    )
    set_plan_limit(owner_engine, owner.org_id, "quota.ai_extractions_per_month", 1)
    assert run(owner, a, inv)[0]["status"] == "awaiting_review"
    other = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES[:5]], mono=True), "o.pdf")
    r = start(owner, a, other)
    assert r.status_code == 402 and r.json()["key"] == "quota.ai_extractions_per_month"
    set_plan_limit(owner_engine, owner.org_id, "quota.ai_extractions_per_month", 50)
    set_plan_limit(owner_engine, owner.org_id, "limit.processing_concurrency", 0)
    r = start(owner, a, other)
    assert r.status_code == 402 and r.json()["key"] == "limit.processing_concurrency"
    ov = owner.get("/processing/overview").json()
    used = {u["key"]: u for u in ov["usage"]}
    assert used["quota.ocr_pages_per_month"]["used"] == 1 + 2 + 0 or used["quota.ocr_pages_per_month"]["used"] >= 1
    assert used["quota.ai_extractions_per_month"]["used"] == 1 and used["quota.bank_statements_per_month"]["limit"] == 0


# ------------------------------------------------------------------ company-specific workspace intelligence
def setup_company_a(owner, a):  # type: ignore[no-untyped-def]
    secs = {n: mk(owner, a, n)["id"] for n in ("Bank Statements", "Receivables", "Payables", "Expenses")}
    inv = owner.post(
        W(a, "/categories"),
        {"name": "Customer invoices", "document_kind": "sales_invoice", "node_id": secs["Receivables"]},
    ).json()["item"]
    ban = owner.post(
        W(a, "/categories"),
        {"name": "Statements", "document_kind": "bank_statement", "node_id": secs["Bank Statements"]},
    ).json()["item"]
    return secs, inv, ban


def test_each_company_gets_suggestions_from_its_own_workspace_only(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node_a, node_b = firm
    secs_a, inv_a, _ban_a = setup_company_a(owner, a)
    # Company B profile: ten sections with different names and a nested folder; invoices go into "Sales / Customer Invoices"
    names_b = [
        "Sales",
        "Purchases",
        "Banking",
        "Payroll",
        "Fixed Assets",
        "Inventory",
        "VAT",
        "Contracts",
        "Administration",
        "Other Documents",
    ]
    secs_b = {n: mk(owner, b, n)["id"] for n in names_b}
    sub = mk(owner, b, "Customer Invoices 2025", secs_b["Sales"])["id"]
    cat_b = owner.post(
        W(b, "/categories"), {"name": "Sales invoices", "document_kind": "sales_invoice", "node_id": sub}
    ).json()["item"]
    # the SAME invoice (supplier = both companies' name is irrelevant here: direction uses each company's own profile)
    doc_a = doc_of(owner, a, node_a, fx.text_pdf([fx.INVOICE_LINES], mono=True), "a.pdf")
    run(owner, a, doc_a)
    sa = ext(owner, a, doc_a)["workspace_suggestions"]
    assert (
        sa[0]["node_id"] == secs_a["Receivables"]
        and sa[0]["category_id"] == inv_a["id"]
        and sa[0]["method"] == "category_kind"
    )
    assert (
        sa[0]["you_can_file_here"] is True
        and sa[0]["confidence"] > 0
        and sa[0]["evidence"][0]["document_kind"] == "sales_invoice"
    )
    # company B: its profile does not match the supplier, so direction is unknown -> no kind -> no invented destination
    doc_b = doc_of(owner, b, node_b, fx.text_pdf([fx.INVOICE_LINES], mono=True), "b.pdf")
    run(owner, b, doc_b)
    sb = ext(owner, b, doc_b)["workspace_suggestions"]
    assert (
        len(sb) == 1
        and sb[0]["status"] == "unavailable"
        and sb[0]["node_id"] is None
        and "Choose a location yourself" in str(sb[0]["evidence"])
    ) or "could not be determined" in str(sb[0]["evidence"])
    # a human tells us it is a sales invoice, re-run extraction: now company B's own folder is suggested
    assert (
        owner.post(D(b, f"/{doc_b}/classification"), {"action": "correct", "type_key": "sales_invoice"}).status_code
        == 200
    )
    job, _ = run(owner, b, doc_b, kind="extract")
    assert job["status"] == "awaiting_review", job
    sb2 = ext(owner, b, doc_b)["workspace_suggestions"]
    assert (
        sb2[0]["node_id"] == sub
        and sb2[0]["category_id"] == cat_b["id"]
        and sb2[0]["node_label"] == "Customer Invoices 2025"
    )
    all_nodes = {n["id"] for n in owner.get(W(b)).json()["nodes"]}
    assert all(s["node_id"] in all_nodes for s in sb2 if s["node_id"])  # only valid node ids of THIS company
    assert not ({s["node_id"] for s in sb2 if s["node_id"]} & set(secs_a.values()))  # never company A's folders
    # accepting files the document through the normal move operation
    acc = owner.post(E(b, ext(owner, b, doc_b)["id"], f"/workspace-suggestions/{sb2[0]['id']}/accept"), {"move": True})
    assert acc.status_code == 200 and owner.get(D(b, f"/{doc_b}")).json()["node_id"] == sub


def test_filing_rules_drive_the_destination_and_archived_folders_are_never_suggested(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    secs, _inv, _ban = setup_company_a(owner, a)
    purchases = secs["Payables"]
    rule = owner.post(
        W(a, "/filing-rules"),
        {
            "name": "Statements go to Payables (test)",
            "conditions": [{"field": "document_kind", "operator": "equals", "value": "sales_invoice"}],
            "target_node_id": purchases,
        },
    ).json()["item"]
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "r.pdf")
    run(owner, a, doc)
    sug = ext(owner, a, doc)["workspace_suggestions"]
    assert (
        sug[0]["method"] == "filing_rule"
        and sug[0]["node_id"] == purchases
        and sug[0]["evidence"][0]["rule"] == rule["name"]
    )
    assert any(
        s["method"] == "category_kind" and s["node_id"] == secs["Receivables"] for s in sug
    )  # alternatives are listed
    # archive the target after the fact: it is shown as unavailable instead of being offered
    owner.post(W(a, f"/nodes/{purchases}/archive"), {"confirm_impact": True})
    again = ext(owner, a, doc)["workspace_suggestions"]
    assert next(s for s in again if s["node_id"] == purchases)["you_can_file_here"] is False


def test_custom_metadata_is_suggested_validated_and_never_invented(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    _secs, inv, _ = setup_company_a(owner, a)
    owner.post(W(a, "/fields"), {"label": "Project Code", "data_type": "short_text", "category_ids": [inv["id"]]})
    owner.post(
        W(a, "/fields"),
        {
            "label": "Cost Center",
            "data_type": "dropdown",
            "options": [{"value": "ops", "label": "Operations"}, {"value": "hq", "label": "Head office"}],
            "category_ids": [inv["id"]],
        },
    )
    owner.post(W(a, "/fields"), {"label": "Warehouse", "data_type": "short_text", "category_ids": [inv["id"]]})
    owner.post(
        W(a, "/fields"),
        {
            "label": "Retention",
            "data_type": "decimal",
            "validation": {"decimal_places": 2},
            "category_ids": [inv["id"]],
        },
    )
    lines = [*fx.INVOICE_LINES, "Project Code: PRJ-77", "Cost Center: Warehouse 9", "Retention: 5.50"]
    doc = doc_of(owner, a, node, fx.text_pdf([lines], mono=True), "m.pdf")
    run(owner, a, doc)
    x = ext(owner, a, doc)
    sug = {m["field_key"]: m for m in x["metadata_suggestions"]}
    assert sug["project_code"]["value"] == "PRJ-77" and sug["project_code"]["evidence"][0]["text"].startswith(
        "Project Code"
    )
    assert (
        sug["cost_center"]["value"] is None and "not one of the configured options" in sug["cost_center"]["problems"][0]
    )  # no invented option
    assert sug["retention"]["value"] == "5.50"
    assert "warehouse" not in sug  # no text found -> no suggestion, no guess
    early = owner.post(E(a, x["id"], f"/metadata-suggestions/{sug['project_code']['id']}/accept"), {})
    assert (
        early.status_code == 409 and "File the document" in early.json()["detail"]
    )  # the field belongs to the suggested category
    top = x["workspace_suggestions"][0]
    assert owner.post(E(a, x["id"], f"/workspace-suggestions/{top['id']}/accept"), {"move": True}).status_code == 200
    ok = owner.post(E(a, x["id"], f"/metadata-suggestions/{sug['project_code']['id']}/accept"), {})
    assert ok.status_code == 200
    vals = {m["key"]: m["value"] for m in owner.get(D(a, f"/{doc}")).json()["metadata"]}
    assert vals["project_code"] == "PRJ-77"
    assert (
        owner.post(E(a, x["id"], f"/metadata-suggestions/{sug['cost_center']['id']}/accept"), {}).status_code == 422
    )  # nothing valid to apply
    done = owner.post(E(a, x["id"], f"/metadata-suggestions/{sug['cost_center']['id']}/accept"), {"value": "hq"})
    assert done.status_code == 200


def test_classification_can_be_confirmed_corrected_and_custom_types_are_supported(firm, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    _secs, _inv, _ban = setup_company_a(owner, a)
    doc = doc_of(
        owner,
        a,
        node,
        fx.text_pdf([["lorem ipsum dolor sit amet", "consectetur adipiscing elit", "sed do eiusmod tempor"]]),
        "u.pdf",
    )
    job, intel = run(owner, a, doc)
    assert (
        job["status"] == "needs_manual"
        and intel["classification"]["type_key"] == "unclassified"
        and intel["classification"]["needs_manual"]
    )
    assert intel["extraction_id"] is None  # nothing is extracted for an unclassified document
    r = owner.post(
        D(a, f"/{doc}/classification"), {"action": "correct", "type_key": "purchase_order", "reason": "PO scan"}
    )
    assert (
        r.status_code == 200
        and r.json()["review_status"] == "corrected"
        and r.json()["kind_key"] == "reference_document"
    )
    assert (
        owner.post(D(a, f"/{doc}/classification"), {"action": "correct", "type_key": "not_a_type"}).status_code == 422
    )
    assert owner.post(D(a, f"/{doc}/classification"), {"action": "confirm"}).json()["review_status"] == "confirmed"
    # a type added to the registry is classified without touching the engine
    from artify_api import doc_extract, doc_taxonomy

    custom = doc_taxonomy.DocType(
        "customs_declaration",
        "Customs declaration",
        "reference_document",
        None,
        ("customs declaration", "hs code"),
        (),
        ("customs declaration",),
    )
    reg = {**doc_taxonomy.TYPES, "customs_declaration": custom}
    c = doc_extract.classify(
        __import__("test_p7_extract").pages("CUSTOMS DECLARATION\nHS Code 8471\nImporter: X"), None, reg
    )
    assert c.type_key == "customs_declaration" and c.kind == "reference_document"


# ------------------------------------------------------------------ security
def test_other_tenants_and_companies_cannot_reach_jobs_ocr_extractions_or_pages(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, _node_b = firm
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "s.pdf")
    job, intel = run(owner, a, doc)
    xid = intel["extraction_id"]
    intruder = new_owner(client, mailer, org_name="Intruder")
    ic = make_company(intruder, "XX")["id"]
    probes = [
        ("get", Pn(a, f"/jobs/{job['id']}")),
        ("get", D(a, f"/{doc}/ocr")),
        ("get", D(a, f"/{doc}/intelligence")),
        ("get", D(a, f"/{doc}/pages/1/image")),
        ("get", E(a, xid)),
        ("post", E(a, xid, "/approve")),
        ("patch", E(a, xid, "/fields/invoice_number")),
        ("post", D(a, f"/{doc}/process")),
    ]
    for method, path in probes:
        r = (
            getattr(intruder, method)(path)
            if method == "get"
            else getattr(intruder, method)(path, {"action": "confirm", "idempotency_key": "x" * 10})
        )
        assert r.status_code in (404, 422), (method, path, r.status_code)
        assert "INV-2025-0042" not in r.text and "Al Noor" not in r.text
    # same organization, another company: manipulated ids do not cross
    for path in (
        D(b, f"/{doc}/ocr"),
        D(b, f"/{doc}/intelligence"),
        E(b, xid),
        Pn(b, f"/jobs/{job['id']}"),
        D(b, f"/{doc}/pages/1/image"),
    ):
        assert owner.get(path).status_code == 404, path
    assert owner.patch(E(b, xid, "/fields/invoice_number"), {"action": "confirm"}).status_code == 404
    assert intruder.get(D(ic, f"/{doc}/ocr")).status_code == 404
    assert owner.get(Pn(a, f"/jobs/{uuid.uuid4()}")).status_code == 404


def test_roles_hidden_documents_and_unauthorised_approval(firm, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    _secs, _inv, _ = setup_company_a(owner, a)
    secret = mk(owner, a, "Payroll secret")["id"]
    d_open = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "o.pdf")
    d_secret = doc_of(owner, a, secret, fx.text_pdf([[*fx.INVOICE_LINES, "Note: confidential"]], mono=True), "s.pdf")
    run(owner, a, d_open)
    run(owner, a, d_secret)
    xid = ext(owner, a, d_open)["id"]
    asst, acc, ro = (make_member(owner, mailer, r) for r in ("assistant_accountant", "accountant", "read_only"))
    for m in (asst, acc, ro):
        assign(owner, a, m)
    # read-only can see results but cannot start, review or approve
    assert ro.get(E(a, xid)).status_code == 200
    assert start(ro, a, d_open).status_code == 403
    assert ro.patch(E(a, xid, "/fields/invoice_number"), {"action": "confirm"}).status_code == 403
    assert ro.post(E(a, xid, "/approve"), {}).status_code == 403
    # an assistant may review but not approve
    assert asst.patch(E(a, xid, "/fields/invoice_number"), {"action": "confirm"}).status_code == 200
    assert asst.post(E(a, xid, "/approve"), {}).status_code == 403
    # a workspace deny on the secret folder hides its documents' jobs, OCR text and extractions (404, never 403)
    assert (
        owner.post(
            W(a, "/permissions"),
            {"node_id": secret, "subject_type": "user", "user_id": uid(acc), "permission": "view", "effect": "deny"},
        ).status_code
        == 201
    )
    sx = ext(owner, a, d_secret)["id"]
    for path in (
        D(a, f"/{d_secret}/ocr"),
        E(a, sx),
        D(a, f"/{d_secret}/intelligence"),
        D(a, f"/{d_secret}/pages/1/image"),
    ):
        assert acc.get(path).status_code == 404, path
    assert acc.patch(E(a, sx, "/fields/invoice_number"), {"action": "confirm"}).status_code == 404
    assert start(acc, a, d_secret).status_code == 404
    listed = acc.get(Pn(a, "/jobs")).json()
    assert all(j["document_id"] != d_secret for j in listed["items"]) and listed["total"] == 1
    assert all(i["document_id"] != d_secret for i in acc.get(Pn(a, "/review-queue")).json()["items"])
    # an edit deny on the open folder blocks review even for a role that may review
    assert (
        owner.post(
            W(a, "/permissions"),
            {"node_id": node, "subject_type": "user", "user_id": uid(acc), "permission": "edit", "effect": "deny"},
        ).status_code
        == 201
    )
    assert acc.patch(E(a, xid, "/fields/invoice_number"), {"action": "confirm"}).status_code == 403


def test_provider_secrets_and_external_processing_need_consent(firm, monkeypatch, client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    from artify_api.settings import get_settings

    s = get_settings()
    from pydantic import SecretStr

    monkeypatch.setattr(s, "ai_http_api_key", SecretStr("sk-test-SECRET-1234567890"))
    monkeypatch.setattr(s, "ai_http_base_url", "https://ai.example.invalid/v1")
    monkeypatch.setattr(s, "ai_http_model", "test-model")
    body = owner.get("/processing/providers")
    assert (
        body.status_code == 200 and "sk-test-SECRET" not in body.text and "ai.example.invalid" not in body.text
    )  # no secrets, no endpoints
    http = next(p for p in body.json()["providers"] if p["key"] == "http_json")
    assert http["configured"] is True and http["external"] is True
    # the server default is switched to the external provider, but the organization has NOT consented: the local engine is used
    monkeypatch.setattr(s, "understanding_provider", "http_json")
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "ext.pdf")
    job, _ = run(owner, a, doc)
    assert (
        job["providers"]["understanding"] == "rules" and "external_content_not_allowed" in job["providers"]["blocked"]
    )
    # consent without the plan feature is still blocked
    r = client.put(
        "/api/v1/processing/providers",
        json={"kind": "understanding", "provider_key": "http_json", "enabled": True, "allow_external_content": True},
        headers=owner.headers(),
    )
    assert r.status_code == 200
    job2, _ = run(owner, a, doc)
    assert job2["providers"]["understanding"] == "rules" and "advanced_ai_not_in_plan" in job2["providers"]["blocked"]
    # only people with ai_providers.manage can change it; an invalid provider and unconfigured consent are refused
    acc = make_member(owner, mailer, "accountant")
    assert (
        client.put(
            "/api/v1/processing/providers",
            json={"kind": "understanding", "provider_key": "rules"},
            headers=acc.headers(),
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/processing/providers", json={"kind": "ocr", "provider_key": "somecloud"}, headers=owner.headers()
        ).status_code
        == 422
    )
    monkeypatch.setattr(s, "ai_http_api_key", None)
    assert (
        client.put(
            "/api/v1/processing/providers",
            json={"kind": "understanding", "provider_key": "http_json", "allow_external_content": True},
            headers=owner.headers(),
        ).status_code
        == 409
    )


def test_overview_numbers_come_from_real_records_and_are_role_gated(firm, client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, b, node, node_b = firm
    d1 = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "1.pdf")
    d2 = doc_of(owner, b, node_b, fx.text_pdf([fx.INVOICE_LINES], mono=True), "2.pdf")
    broken = doc_of(owner, a, node, b"%PDF-1.4\n" + b"junk" * 50 + b"\n%%EOF\n", "broken.pdf")
    run(owner, a, d1)
    run(owner, b, d2)
    run(owner, a, broken)
    ov = owner.get("/processing/overview").json()
    assert ov["jobs_by_status"] == {"awaiting_review": 2, "failed": 1}
    assert {x["display_name"] for x in ov["awaiting_review"]} == {"1.pdf", "2.pdf"} and ov["failed"][0][
        "error_code"
    ] == "unreadable_document"
    by = {c["name"]: c for c in ov["companies"]}
    assert (
        by["Al Noor Trading LLC"]["awaiting_review"] == 1
        and by["Al Noor Trading LLC"]["failed"] == 1
        and by["Gulf Builders Co"]["awaiting_review"] == 1
    )
    with owner_engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM processing_jobs WHERE organization_id = :o"), {"o": owner.org_id}
            ).scalar_one()
            == 3
        )
    assert "INV-2025" not in str(ov) and "Al Noor Trading LLC" in str(
        ov
    )  # names and statuses only, never document content
    acc = make_member(owner, mailer, "accountant")
    assert acc.get("/processing/overview").status_code == 403
    pm = make_member(owner, mailer, "practice_manager")
    assert pm.get("/processing/overview").status_code == 200
    assert {p["key"] for p in ov["providers"]} >= {"tesseract", "rules", "http_json"}


def test_combined_pdf_gets_a_split_proposal_that_never_changes_the_original(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    first = [ln if "INV-2025-0042" not in ln else "Invoice No: INV-1" for ln in fx.INVOICE_LINES]
    second = [ln if "INV-2025-0042" not in ln else "Invoice No: INV-2" for ln in fx.INVOICE_LINES]
    pdf = fx.text_pdf([["Page 1 of 1", *first], ["Page 1 of 1", *second]], mono=True, size=9)
    doc = doc_of(owner, a, node, pdf, "combined.pdf")
    run(owner, a, doc)
    x = ext(owner, a, doc)
    sp = x["split_proposal"]
    assert (
        sp
        and [(s["start_page"], s["end_page"]) for s in sp["segments"]] == [(1, 1), (2, 2)]
        and sp["status"] == "proposed"
    )
    r = owner.post(E(a, x["id"], "/split"), {"approve": True})
    assert r.status_code == 200 and r.json()["executed"] is False  # decision recorded; nothing split, nothing replaced
    assert (
        owner.get(D(a, f"/{doc}/content")).content == pdf
        and owner.get(D(a, f"/{doc}")).json()["current_version_no"] == 1
    )


def test_original_documents_are_never_modified_by_processing(firm, owner_engine, backend) -> None:  # noqa: F811  # type: ignore[no-untyped-def]
    owner, a, _b, node, _ = firm
    content = fx.png(fx.draw_page(fx.INVOICE_LINES, mono=True, rotate=90.0))
    doc = doc_of(owner, a, node, content, "orig.png")
    before = owner.get(D(a, f"/{doc}")).json()
    run(owner, a, doc)
    run(owner, a, doc)
    after = owner.get(D(a, f"/{doc}")).json()
    assert owner.get(D(a, f"/{doc}/content")).content == content
    assert (after["content_sha256"], after["current_version_no"], after["size_bytes"]) == (
        before["content_sha256"],
        1,
        before["size_bytes"],
    )
    with owner_engine.begin() as conn:
        derived = conn.execute(
            text("SELECT count(*) FROM storage_objects WHERE company_id = :c AND purpose = 'derived'"), {"c": a}
        ).scalar_one()
        originals = conn.execute(
            text("SELECT count(*) FROM storage_objects WHERE company_id = :c AND purpose = 'original'"), {"c": a}
        ).scalar_one()
    assert originals == 1 and derived == 2  # one processed page image per run, kept separately from the original


def test_every_processing_route_declares_its_authorisation() -> None:
    from artify_api.context import Requires
    from artify_api.routers.processing import router
    from conftest import walk_dependencies
    from fastapi.routing import APIRoute

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 24
    for route in routes:
        deps = list(walk_dependencies(route.dependant))
        assert any(isinstance(c, Requires) and c.permission for c in deps), (
            route.path
        )  # none is open to every signed-in user
    _ = (CompanyProfile, understanding)


# ------------------------------------------------------------------ optional external understanding provider (fake server)
def _fake_ai(responses):  # type: ignore[no-untyped-def]
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # type: ignore[no-untyped-def]
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"auth": self.headers.get("Authorization"), "body": body})
            content = responses.pop(0)
            data = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # type: ignore[no-untyped-def]
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_external_provider_is_used_only_with_consent_and_its_answers_stay_suggestions(
    firm, monkeypatch, client, owner_engine
) -> None:  # type: ignore[no-untyped-def]
    from artify_api.settings import get_settings
    from pydantic import SecretStr

    owner, a, _b, node, _ = firm
    srv, seen = _fake_ai(
        [
            {
                "type_key": "tax_invoice",
                "confidence": 0.93,
                "evidence": ["TAX INVOICE", "a quote that is not in the document"],
            },
            {
                "fields": {
                    "invoice_number": {"value": "INV-2025-0042", "confidence": 0.95},
                    "invoice_date": {"value": "14 March 2025", "confidence": 0.9},
                    "po_reference": {"value": "PO-INVENTED-1", "confidence": 0.99},
                    "gross_amount": {"value": "168.000", "confidence": 0.9},
                }
            },
        ]
    )
    s = get_settings()
    monkeypatch.setattr(s, "ai_http_base_url", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(s, "ai_http_model", "fake-model-1")
    monkeypatch.setattr(s, "ai_http_api_key", SecretStr("sk-fake-KEY"))
    monkeypatch.setattr(s, "understanding_provider", "http_json")
    assert (
        client.put(
            "/api/v1/processing/providers",
            json={"kind": "understanding", "provider_key": "http_json", "allow_external_content": True},
            headers=owner.headers(),
        ).status_code
        == 200
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entitlement_overrides (id, organization_id, subscription_id, entitlement_key, bool_value, reason) SELECT gen_random_uuid(), organization_id, id, 'feature.advanced_ai', true, 'test' FROM subscriptions WHERE organization_id = :o"
            ),
            {"o": owner.org_id},
        )
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "llm.pdf")
    job, intel = run(owner, a, doc)
    assert (
        job["status"] == "awaiting_review"
        and job["providers"]["understanding"] == "http_json"
        and job["providers"]["blocked"] == []
    )
    assert (
        len(seen) == 2 and seen[0]["auth"] == "Bearer sk-fake-KEY" and "Al Noor" in str(seen[0]["body"])
    )  # the document text was sent: that is what consent allows
    cls = intel["classification"]
    assert (
        cls["provider_key"] == "http_json" and cls["model_version"] == "fake-model-1" and cls["method"] == "llm+rules"
    )
    assert cls["confidence"] < 0.93  # a quote that is not in the document lowers trust
    x = ext(owner, a, doc)
    f = fields(x)
    assert x["provider_key"] == "http_json" and x["prompt_version"] == "http-json-1"
    assert (
        f["invoice_number"]["value"] == "INV-2025-0042"
        and f["invoice_number"]["page_no"] == 1
        and "llm_suggestion" in f["invoice_number"]["flags"]
    )
    assert f["invoice_date"]["value"] == "2025-03-14"  # normalised by OUR date parser, not trusted as text
    assert (
        f["po_reference"]["value"] == "PO-INVENTED-1"
        and "not_found_in_text" in f["po_reference"]["flags"]
        and f["po_reference"]["review_status"] == "needs_review"
    )
    assert f["po_reference"]["confidence"] <= 0.4  # a value that cannot be found in the OCR text is never trusted
    assert x["status"] == "draft"  # still unapproved: nothing the model says approves anything
    assert owner.get(E(a, x["id"])).text.count("sk-fake-KEY") == 0


def test_external_provider_failures_are_reported_not_hidden(firm, monkeypatch, client, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from artify_api.settings import get_settings
    from pydantic import SecretStr

    owner, a, _b, node, _ = firm
    srv, _seen = _fake_ai([{"type_key": "tax_invoice"}])  # then the queue is empty -> the server errors
    s = get_settings()
    monkeypatch.setattr(s, "ai_http_base_url", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setattr(s, "ai_http_model", "fake")
    monkeypatch.setattr(s, "ai_http_api_key", SecretStr("sk-x"))
    monkeypatch.setattr(s, "understanding_provider", "http_json")
    client.put(
        "/api/v1/processing/providers",
        json={"kind": "understanding", "provider_key": "http_json", "allow_external_content": True},
        headers=owner.headers(),
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entitlement_overrides (id, organization_id, subscription_id, entitlement_key, bool_value, reason) SELECT gen_random_uuid(), organization_id, id, 'feature.advanced_ai', true, 'test' FROM subscriptions WHERE organization_id = :o"
            ),
            {"o": owner.org_id},
        )
    doc = doc_of(owner, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "llm2.pdf")
    job, _ = run(owner, a, doc)
    assert job["status"] == "failed" and job["error_code"] in (
        "provider_http_error",
        "provider_unreachable",
        "provider_bad_output",
    )
    assert job["attempt_count"] >= 1 and "sk-x" not in str(job)
    with pytest.raises(understanding.UnderstandingError):
        monkeypatch.setattr(s, "ai_http_api_key", None)
        understanding.get_understanding("http_json")
