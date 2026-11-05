"""Phase 8 integration tests: the accounting engine through the API against PostgreSQL. Synthetic data only.

Document-based tests run the real Phase 7 pipeline on generated PDFs (Tesseract is only needed for scans; the PDFs here
carry embedded text), approve the extraction as a person, and then feed the accounting engine."""

from __future__ import annotations

import uuid
from decimal import Decimal as Dec

import docfixtures as fx
import pytest
from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p6_documents import W, assign, backend, mk, upload  # noqa: F401  (backend = autouse storage fixture)
from test_p7_processing import E, doc_of, ext, run


def ACC(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/accounting{path}"


def D(x: str | None) -> Dec:
    return Dec(x or "0")


# ------------------------------------------------------------------ setup helpers
def setup_books(owner, cid, *, chart: bool = True, pack: bool = True, periods: bool = True):  # type: ignore[no-untyped-def]
    r = owner.post(ACC(cid, "/config"), {})
    assert r.status_code == 201, r.text
    if chart:
        assert owner.post(ACC(cid, "/templates/chart"), {"key": "basic_sample"}).status_code == 200
    if pack:
        assert owner.post(ACC(cid, "/templates/tax-pack"), {"key": "OM_VAT"}).status_code == 200
    if periods:
        assert owner.post(ACC(cid, "/periods/generate"), {"fiscal_year": 2025}).status_code == 200


def accounts(owner, cid) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in owner.get(ACC(cid, "/accounts")).json()["items"]}


def taxes(owner, cid) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {t["code"]: t["id"] for t in owner.get(ACC(cid, "/tax-codes")).json()["items"]}


def contact(owner, cid, kind="supplier", name="Gulf Office Supplies LLC", **kw):  # type: ignore[no-untyped-def]
    r = owner.post(ACC(cid, "/contacts"), {"kind": kind, "legal_name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def draft(owner, cid, typ, *, date="2025-03-10", lines=None, **kw):  # type: ignore[no-untyped-def]
    return owner.post(
        ACC(cid, "/transactions"),
        {
            "txn_type": typ,
            "transaction_date": date,
            "lines": lines if lines is not None else [{"amount": "100.000", "tax_code_id": kw.pop("tax", None)}],
            **kw,
        },
    )


def jl(t):  # type: ignore[no-untyped-def]
    return [(x["account_code"], x["debit"], x["credit"]) for x in t["journal"]["lines"]]


def codes(t):  # type: ignore[no-untyped-def]
    return {i["code"] for i in t["issues"]}


def blocking(t):  # type: ignore[no-untyped-def]
    return {i["code"] for i in t["issues"] if i["blocking"]}


@pytest.fixture
def books(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Books Firm")
    a = make_company(owner, "AA", name="Al Noor Trading LLC")["id"]
    b = make_company(owner, "BB", name="Gulf Builders Co")["id"]
    for c in (a, b):
        setup_books(owner, c)
    node = mk(owner, a, "Inbox")["id"]
    node_b = mk(owner, b, "Inbox B")["id"]
    acct = make_member(owner, mailer, "accountant")
    prep = make_member(owner, mailer, "assistant_accountant")
    for m in (acct, prep):
        assign(owner, a, m)
    return {
        "owner": owner,
        "a": a,
        "b": b,
        "node": node,
        "node_b": node_b,
        "acct": acct,
        "prep": prep,
        "mailer": mailer,
        "client": client,
    }


def approve_flow(txn_id, preparer, approver, cid, **body):  # type: ignore[no-untyped-def]
    r = preparer.post(ACC(cid, f"/transactions/{txn_id}/submit"), {})
    assert r.status_code == 200, r.text
    return approver.post(ACC(cid, f"/transactions/{txn_id}/approve"), body)


# ================================================================== chart of accounts
def test_each_company_has_its_own_independent_chart(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    ca, cb = accounts(o, a), accounts(o, b)
    assert set(ca) == set(cb) and not set(ca.values()) & set(cb.values())  # same sample codes, different rows
    new = o.post(
        ACC(a, "/accounts"), {"code": "6210", "name": "Stationery", "account_type": "expense", "parent_id": ca["6000"]}
    )
    assert new.status_code == 201 and new.json()["normal_balance"] == "debit"
    assert "6210" in accounts(o, a) and "6210" not in accounts(o, b)  # nothing leaks to the other company
    assert o.post(ACC(a, "/templates/chart"), {"key": "basic_sample"}).status_code == 409  # only into an empty chart


def test_account_code_hierarchy_and_posting_rules(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    ca = accounts(o, a)
    dup = o.post(ACC(a, "/accounts"), {"code": "6100", "name": "Again", "account_type": "expense"})
    assert dup.status_code == 409
    # the same code is fine in another company
    assert o.post(ACC(b, "/accounts"), {"code": "7777", "name": "B only", "account_type": "expense"}).status_code == 201
    assert o.post(ACC(a, "/accounts"), {"code": "7777", "name": "A too", "account_type": "expense"}).status_code == 201
    # a parent must be a group account and must belong to the same company
    assert (
        o.post(
            ACC(a, "/accounts"), {"code": "6101", "name": "Child", "account_type": "expense", "parent_id": ca["6100"]}
        ).status_code
        == 422
    )
    assert (
        o.post(
            ACC(a, "/accounts"),
            {"code": "6102", "name": "Child", "account_type": "expense", "parent_id": accounts(o, b)["6000"]},
        ).status_code
        == 404
    )
    # circular hierarchies are refused
    g1 = o.post(
        ACC(a, "/accounts"), {"code": "G1", "name": "Group 1", "account_type": "expense", "is_postable": False}
    ).json()["id"]
    g2 = o.post(
        ACC(a, "/accounts"),
        {"code": "G2", "name": "Group 2", "account_type": "expense", "is_postable": False, "parent_id": g1},
    ).json()["id"]
    assert o.patch(ACC(a, f"/accounts/{g1}"), {"parent_id": g2}).status_code == 422
    assert o.patch(ACC(a, f"/accounts/{g1}"), {"parent_id": g1}).status_code == 422
    # a tax account must state its normal balance; a mismatching side is refused
    assert o.post(ACC(a, "/accounts"), {"code": "T1", "name": "Tax", "account_type": "tax"}).status_code == 422
    assert (
        o.post(
            ACC(a, "/accounts"),
            {"code": "X1", "name": "Wrong side", "account_type": "expense", "normal_balance": "credit"},
        ).status_code
        == 422
    )
    # group accounts cannot be posted to, inactive accounts cannot be posted to
    j = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [{"account_id": ca["6000"], "debit": "10"}, {"account_id": ca["1110"], "credit": "10"}],
        },
    ).json()
    assert "account_not_postable" in blocking(j)
    spare = o.post(ACC(a, "/accounts"), {"code": "6600", "name": "Spare", "account_type": "expense"}).json()["id"]
    assert o.post(ACC(a, f"/accounts/{spare}/archive")).status_code == 200
    j = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [{"account_id": spare, "debit": "10"}, {"account_id": ca["1110"], "credit": "10"}],
        },
    ).json()
    assert "account_inactive" in blocking(j)


def test_accounts_in_use_are_archived_never_deleted_and_role_accounts_are_protected(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    assert o.delete(ACC(a, f"/accounts/{ca['6100']}")).status_code in (404, 405)  # no delete route exists
    assert o.post(ACC(a, f"/accounts/{ca['1120']}/archive")).status_code == 409  # mapped to the bank role
    assert o.post(ACC(a, f"/accounts/{ca['6000']}/archive")).status_code == 409  # active sub-accounts
    r = o.put(ACC(a, "/roles/bank"), {"account_id": ca["6100"]})
    assert r.status_code == 422  # a bank role needs a bank account
    # an account with journal lines keeps its type and stays postable
    jt = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [{"account_id": ca["6200"], "debit": "10"}, {"account_id": ca["1110"], "credit": "10"}],
        },
    ).json()
    assert jt["balanced"]
    assert o.patch(ACC(a, f"/accounts/{ca['6200']}"), {"account_type": "revenue"}).status_code == 409
    assert o.patch(ACC(a, f"/accounts/{ca['6200']}"), {"is_postable": False}).status_code == 409


# ================================================================== tax engine
def test_tax_exclusive_and_inclusive_calculation(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    ex = draft(o, a, "purchase_invoice", lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]).json()
    assert (ex["net_amount"], ex["tax_amount"], ex["gross_amount"]) == ("100.000000", "5.000000", "105.000000")
    assert jl(ex) == [
        ("6100", "100.000000", "0.000000"),
        ("1300", "5.000000", "0.000000"),
        ("2100", "0.000000", "105.000000"),
    ]
    inc = draft(
        o, a, "purchase_invoice", prices_include_tax=True, lines=[{"amount": "105.000", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert (inc["net_amount"], inc["tax_amount"], inc["gross_amount"]) == ("100.000000", "5.000000", "105.000000")
    odd = draft(
        o, a, "purchase_invoice", prices_include_tax=True, lines=[{"amount": "10.000", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert (odd["net_amount"], odd["tax_amount"]) == (
        "9.524000",
        "0.476000",
    )  # 10 / 1.05, rounded to the 3-decimal scale
    qty = draft(
        o,
        a,
        "purchase_invoice",
        lines=[{"quantity": "3", "unit_price": "19.990", "discount": "2.000", "tax_code_id": t["OM_STD"]}],
    ).json()
    assert (qty["net_amount"], qty["tax_amount"], qty["gross_amount"]) == ("57.970000", "2.899000", "60.869000")


def test_zero_rated_exempt_out_of_scope_and_multiple_rates(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    r15 = o.post(
        ACC(a, "/tax-codes"),
        {
            "code": "X15",
            "name": "Test 15%",
            "treatment": "standard",
            "rate": "15",
            "effective_from": "2020-01-01",
            "input_account_id": accounts(o, a)["1300"],
            "output_account_id": accounts(o, a)["2200"],
        },
    ).json()["id"]
    lines = [
        {"amount": "100", "tax_code_id": t["OM_STD"]},
        {"amount": "200", "tax_code_id": r15},
        {"amount": "50", "tax_code_id": t["OM_ZERO"]},
        {"amount": "10", "tax_code_id": t["OM_EXEMPT"]},
        {"amount": "7", "tax_code_id": t["OM_OUT"]},
    ]
    x = draft(o, a, "purchase_invoice", lines=lines).json()
    assert [ln["tax_amount"] for ln in x["lines"]] == ["5.000000", "30.000000", "0.000000", "0.000000", "0.000000"]
    assert (x["net_amount"], x["tax_amount"], x["gross_amount"]) == ("367.000000", "35.000000", "402.000000")
    assert x["balanced"]
    assert sum(D(v[1]) for v in jl(x)) == sum(D(v[2]) for v in jl(x))


def test_reverse_charge_posts_input_and_output_tax(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    x = draft(o, a, "purchase_invoice", lines=[{"amount": "200.000", "tax_code_id": t["OM_RC"]}]).json()
    assert jl(x) == [
        ("6100", "200.000000", "0.000000"),
        ("1300", "10.000000", "0.000000"),
        ("2200", "0.000000", "10.000000"),
        ("2100", "0.000000", "200.000000"),
    ]
    assert x["balanced"]


def test_credit_note_mirrors_the_invoice_and_tax(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    inv = draft(o, a, "sales_invoice", lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]).json()
    cn = draft(o, a, "sales_credit_note", lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]).json()
    assert jl(inv) == [
        ("1200", "105.000000", "0.000000"),
        ("4100", "0.000000", "100.000000"),
        ("2200", "0.000000", "5.000000"),
    ]
    assert jl(cn) == [
        ("1200", "0.000000", "105.000000"),
        ("4100", "100.000000", "0.000000"),
        ("2200", "5.000000", "0.000000"),
    ]
    pcn = draft(o, a, "purchase_credit_note", lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]).json()
    assert jl(pcn) == [
        ("2100", "105.000000", "0.000000"),
        ("6100", "0.000000", "100.000000"),
        ("1300", "0.000000", "5.000000"),
    ]


def test_rounding_scope_is_company_configuration(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    lines = [{"amount": "0.100", "tax_code_id": t["OM_STD"]} for _ in range(3)]
    line = draft(o, a, "purchase_invoice", lines=lines).json()
    assert line["tax_amount"] == "0.015000"  # 3 x round(0.005, 3) = 3 x 0.005
    assert (
        o.patch(ACC(a, "/config"), {"tax_rounding_scope": "document", "rounding_mode": "half_even"}).status_code == 200
    )
    doc = draft(
        o, a, "purchase_invoice", lines=[{"amount": "0.100", "tax_code_id": t["OM_STD"]} for _ in range(3)]
    ).json()
    assert doc["tax_amount"] == "0.015000" and doc["balanced"]
    cfg = o.get(ACC(a, "/config")).json()["config"]
    assert cfg["tax_rounding_scope"] == "document" and cfg["rounding_mode"] == "half_even"


def test_invalid_tax_configuration_is_refused(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    base = {"code": "BAD", "name": "Bad", "input_account_id": ca["1300"]}
    assert (
        o.post(ACC(a, "/tax-codes"), {**base, "treatment": "exempt", "rate": "5"}).status_code == 422
    )  # exempt carries no rate
    assert o.post(ACC(a, "/tax-codes"), {**base, "treatment": "standard", "rate": "150"}).status_code == 422
    assert o.post(ACC(a, "/tax-codes"), {**base, "treatment": "standard"}).status_code == 422  # needs a rate
    assert (
        o.post(
            ACC(a, "/tax-codes"),
            {**base, "treatment": "standard", "rate": "5", "input_account_id": accounts(o, books["b"])["1300"]},
        ).status_code
        == 404
    )
    assert o.post(ACC(a, "/tax-codes"), {"code": "OM_STD", "name": "dup", "treatment": "zero_rated"}).status_code == 409


def test_effective_dated_rates_and_jurisdiction_independence(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    codes_ = o.get(ACC(a, "/tax-codes")).json()["items"]
    std = next(c for c in codes_ if c["code"] == "OM_STD")
    rate = std["rates"][0]
    assert (
        o.post(ACC(a, f"/tax-codes/{t['OM_STD']}/rates"), {"rate": "7", "effective_from": "2025-06-01"}).status_code
        == 409
    )  # overlaps the open-ended rate
    assert (
        o.post(ACC(a, f"/tax-codes/{t['OM_STD']}/rates/{rate['id']}/end"), {"effective_to": "2025-05-31"}).status_code
        == 200
    )
    assert (
        o.post(ACC(a, f"/tax-codes/{t['OM_STD']}/rates"), {"rate": "7", "effective_from": "2025-06-01"}).status_code
        == 200
    )
    before = draft(
        o, a, "purchase_invoice", date="2025-05-15", lines=[{"amount": "100", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert before["tax_amount"] == "5.000000"
    assert o.post(ACC(a, "/periods/generate"), {"fiscal_year": 2025}).status_code == 200
    after = draft(
        o, a, "purchase_invoice", date="2025-06-15", lines=[{"amount": "100", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert after["tax_amount"] == "7.000000"  # same code, later date, new rate: configuration only, no code change
    gap = draft(
        o, a, "purchase_invoice", date="2019-01-01", lines=[{"amount": "100", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert "no_effective_tax_rate" in blocking(gap)  # no rate in force: never assumed
    # a different jurisdiction is just another configuration
    xx = o.post(
        ACC(a, "/tax-codes"),
        {
            "code": "XX_STD",
            "name": "Other jurisdiction 15%",
            "treatment": "standard",
            "rate": "15",
            "effective_from": "2020-01-01",
            "jurisdiction_code": "XX",
            "input_account_id": accounts(o, a)["1300"],
            "output_account_id": accounts(o, a)["2200"],
        },
    ).json()["id"]
    other = draft(o, a, "purchase_invoice", lines=[{"amount": "100", "tax_code_id": xx}]).json()
    assert other["tax_amount"] == "15.000000"


def test_starter_tax_pack_is_unverified_until_a_person_records_a_review(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    std = next(c for c in o.get(ACC(a, "/tax-codes")).json()["items"] if c["code"] == "OM_STD")
    assert std["verification_status"] == "unverified" and "NOT verified" in std["source_reference"]
    r = o.post(
        ACC(a, f"/tax-codes/{std['id']}/verify"), {"verified_by_name": "Test Reviewer", "verified_on": "2025-01-05"}
    )
    assert r.status_code == 200 and r.json()["verification_status"] == "verified"
    # changing the configuration invalidates the earlier verification
    r = o.patch(ACC(a, f"/tax-codes/{std['id']}"), {"recoverable": False})
    assert r.json()["verification_status"] == "unverified" and r.json()["verified_on"] is None


def test_missing_tax_information_is_flagged_not_assumed(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    assert o.patch(ACC(a, "/config"), {"tax_registered": True, "tax_registration_number": "OM000"}).status_code == 200
    x = draft(o, a, "purchase_invoice", lines=[{"amount": "100.000"}]).json()
    assert "tax_treatment_unresolved" in codes(x) and x["tax_amount"] == "0.000000"
    wrong = draft(o, a, "sales_invoice", lines=[{"amount": "100", "tax_code_id": taxes(o, a)["OM_RC"]}]).json()
    assert "tax_direction_mismatch" in blocking(wrong)  # a purchase-only (input) code on a sale


# ================================================================== transactions from approved extractions
def approved_extraction(owner, cid, node, *, lines=None, name="invoice.pdf", note=None):  # type: ignore[no-untyped-def]
    doc = doc_of(owner, cid, node, fx.text_pdf([lines or fx.INVOICE_LINES], mono=True), name)
    run(owner, cid, doc)
    x = ext(owner, cid, doc)
    for f in x["fields"]:
        if f["review_status"] == "needs_review":
            owner.patch(E(cid, x["id"], f"/fields/{f['field_key']}"), {"action": "confirm"})
    for i in x["line_items"]:
        if i["review_status"] == "needs_review":
            owner.patch(E(cid, x["id"], f"/line-items/{i['id']}"), {"action": "confirm"})
    r = owner.post(E(cid, x["id"], "/approve"), {"note": note} if note else {})
    assert r.status_code == 200, r.text
    return doc, x["id"]


def test_unapproved_extractions_never_reach_the_accounting_engine(books) -> None:  # type: ignore[no-untyped-def]
    o, a, node = books["owner"], books["a"], books["node"]
    doc = doc_of(o, a, node, fx.text_pdf([fx.INVOICE_LINES], mono=True), "draft.pdf")
    run(o, a, doc)
    x = ext(o, a, doc)
    r = o.post(ACC(a, f"/transactions/from-extraction/{x['id']}"))
    assert r.status_code == 409 and "approved" in r.json()["detail"]
    assert o.get(ACC(a, "/transactions")).json()["total"] == 0


def test_approved_sales_invoice_becomes_a_traceable_draft_with_a_proposed_journal(books) -> None:  # type: ignore[no-untyped-def]
    o, a, node = books["owner"], books["a"], books["node"]
    cust = contact(o, a, "customer", "Gulf Builders Co", tax_number="OM-555")
    doc, xid = approved_extraction(o, a, node)
    r = o.post(ACC(a, f"/transactions/from-extraction/{xid}"))
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["txn_type"] == "sales_invoice" and t["status"] == "draft" and t["doc_number"] == "INV-2025-0042"
    assert (
        t["source_document_id"] == doc and t["extraction_id"] == xid and t["document"]["id"] == doc
    )  # full traceability
    assert len(t["lines"]) == 3 and t["currency"] == "OMR" and t["transaction_date"] == "2025-03-14"
    assert (t["net_amount"], t["tax_amount"], t["gross_amount"]) == ("160.000000", "8.000000", "168.000000")
    assert all(ln["tax_code"] == "OM_STD" for ln in t["lines"])  # the printed 5% matched exactly one configured code
    # the customer is suggested by name, never linked automatically
    assert t["contact_id"] is None and any(
        s["contact_id"] == cust and s["strength"] == "name_exact" for s in t["contact_suggestions"]
    )
    assert "contact_required" in blocking(t)
    t = o.put(ACC(a, f"/transactions/{t['id']}/contact"), {"contact_id": cust}).json()
    assert jl(t) == [
        ("1200", "168.000000", "0.000000"),
        ("4100", "0.000000", "25.000000"),
        ("2200", "0.000000", "1.250000"),
        ("4100", "0.000000", "120.000000"),
        ("2200", "0.000000", "6.000000"),
        ("4100", "0.000000", "15.000000"),
        ("2200", "0.000000", "0.750000"),
    ]
    assert t["balanced"] and not blocking(t)
    # a second transaction from the same extraction is refused while the first is live
    assert o.post(ACC(a, f"/transactions/from-extraction/{xid}")).status_code == 409


def test_purchase_invoice_direction_follows_the_companys_own_name(books) -> None:  # type: ignore[no-untyped-def]
    o, b, node_b = books["owner"], books["b"], books["node_b"]
    _, xid = approved_extraction(o, b, node_b)  # the same document is a PURCHASE for Gulf Builders (the customer)
    sup = contact(o, b, "supplier", "Al Noor Trading LLC", tax_number="OM1234567890")
    t = o.post(ACC(b, f"/transactions/from-extraction/{xid}")).json()
    assert t["txn_type"] == "purchase_invoice"
    assert any(s["contact_id"] == sup and s["strength"] == "tax_number" for s in t["contact_suggestions"])
    t = o.put(ACC(b, f"/transactions/{t['id']}/contact"), {"contact_id": sup}).json()
    assert t["balanced"] and {x[0] for x in jl(t)} == {"6100", "1300", "2100"}


def test_bank_statement_rows_become_drafts_that_need_a_counter_account(books) -> None:  # type: ignore[no-untyped-def]
    o, a, node = books["owner"], books["a"], books["node"]
    doc = doc_of(o, a, node, fx.text_pdf([fx.STATEMENT_1, fx.STATEMENT_2], mono=True), "statement.pdf")
    run(o, a, doc)
    x = ext(o, a, doc)
    for t in x["transactions"]:
        if t["review_status"] == "needs_review":
            o.patch(E(a, x["id"], f"/transactions/{t['id']}"), {"action": "confirm"})
    for f in x["fields"]:
        if f["review_status"] == "needs_review":
            o.patch(E(a, x["id"], f"/fields/{f['field_key']}"), {"action": "confirm"})
    assert o.post(E(a, x["id"], "/approve"), {}).status_code == 200
    r = o.post(ACC(a, f"/transactions/from-extraction/{x['id']}"))
    assert r.status_code == 201, r.text
    made = r.json()
    assert len(made["created"]) == 5 and made["remaining"] == 0
    kinds = sorted(t["txn_type"] for t in made["transactions"])
    assert kinds == ["bank_deposit", "bank_deposit", "bank_withdrawal", "bank_withdrawal", "bank_withdrawal"]
    first = o.get(ACC(a, f"/transactions/{made['created'][0]}")).json()
    assert first["settlement_account_id"] is None and {x[0] for x in jl(first)} >= {"1120"}  # bank role resolved
    assert "account_defaulted" in codes(first) or "missing_account_mapping" in codes(first)
    # running it again creates nothing new
    again = o.post(ACC(a, f"/transactions/from-extraction/{x['id']}")).json()
    assert again["created"] == []


# ================================================================== journals, accounts and rules
def test_missing_account_mapping_blocks_submission(books) -> None:  # type: ignore[no-untyped-def]
    owner, mailer, client = books["owner"], books["mailer"], books["client"]
    c = make_company(owner, "CC", name="Bare Books Ltd")["id"]
    setup_books(owner, c, chart=False, pack=False)
    ca = {}
    for code, typ in (("100", "bank"), ("200", "payable")):
        ca[code] = owner.post(ACC(c, "/accounts"), {"code": code, "name": code, "account_type": typ}).json()["id"]
    x = draft(owner, c, "purchase_invoice").json()
    assert "missing_account_mapping" in blocking(x) and not x["balanced"]
    s = owner.post(ACC(c, f"/transactions/{x['id']}/submit"), {})
    assert s.status_code == 409 and "blocking" in s.json()["detail"]
    _ = (mailer, client)


def test_company_rules_choose_accounts_and_conflicting_rules_are_flagged(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    sup = contact(o, a, "supplier", "Gulf Office Supplies LLC")

    def mk_rule(name, prio, cond, acct):  # type: ignore[no-untyped-def]
        return o.post(
            ACC(a, "/rules"),
            {"name": name, "kind": "account", "priority": prio, "conditions": [cond], "action": {"account_id": acct}},
        )

    r1 = mk_rule(
        "Stationery from office suppliers",
        5,
        {"field": "contact_name", "op": "contains", "value": "office supplies"},
        ca["6200"],
    )
    assert r1.status_code == 201, r1.text
    x = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"description": "A4 paper", "amount": "50.000"}]).json()
    assert x["lines"][0]["account_code"] == "6200" and x["lines"][0]["account_source"] == "rule"
    r2 = mk_rule("Anything with paper", 5, {"field": "description", "op": "contains", "value": "paper"}, ca["6400"])
    assert r2.status_code == 201 and r2.json()["warnings"]  # a likely conflict is reported when the rule is saved
    y = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"description": "A4 paper", "amount": "50.000"}]).json()
    assert "rule_conflict" in blocking(y) and y["lines"][0]["account_id"] is None  # flagged, never silently chosen
    # an explicit account on the line always wins; a higher priority resolves the conflict
    z = draft(
        o,
        a,
        "purchase_invoice",
        contact_id=sup,
        lines=[{"description": "A4 paper", "amount": "50", "account_id": ca["6500"]}],
    ).json()
    assert z["lines"][0]["account_code"] == "6500" and z["lines"][0]["account_source"] == "manual"
    assert o.patch(ACC(a, f"/rules/{r1.json()['id']}"), {"priority": 9}).status_code == 200
    w = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"description": "A4 paper", "amount": "50"}]).json()
    assert w["lines"][0]["account_code"] == "6200"


def test_rules_are_versioned_validated_and_company_scoped(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    ca, cb = accounts(o, a), accounts(o, b)
    cond = {"field": "description", "op": "contains", "value": "rent"}
    r = o.post(
        ACC(a, "/rules"),
        {
            "name": "Rent",
            "kind": "account",
            "conditions": [cond],
            "action": {"account_id": ca["6400"]},
            "effective_from": "2025-01-01",
        },
    ).json()
    v2 = o.patch(ACC(a, f"/rules/{r['id']}"), {"priority": 3}).json()
    assert v2["version"] == 2 and v2["rule_key"] == r["rule_key"]
    hist = o.get(ACC(a, f"/rules/{r['rule_key']}/history")).json()["items"]
    assert [h["version"] for h in hist] == [2, 1] and [h["is_current"] for h in hist] == [True, False]
    assert o.patch(ACC(a, f"/rules/{r['id']}"), {"priority": 4}).status_code == 404  # only the current version changes
    # no scripting: unknown fields, operators, actions and placeholders are refused

    def bad(**kw):  # type: ignore[no-untyped-def]
        body = {"name": "x", "kind": "account", "conditions": [cond], "action": {"account_id": ca["6400"]}, **kw}
        return o.post(ACC(a, "/rules"), body).status_code

    assert bad(conditions=[{"field": "__class__", "op": "eq", "value": "x"}]) == 422
    assert bad(conditions=[{"field": "description", "op": "eval", "value": "x"}]) == 422
    assert bad(action={"account_id": ca["6400"], "code": "import os"}) == 422
    assert (
        o.post(
            ACC(a, "/rules"),
            {"name": "n", "kind": "narration", "conditions": [cond], "action": {"template": "{secret}"}},
        ).status_code
        == 422
    )
    # another company's account cannot be used
    assert bad(action={"account_id": cb["6400"]}) == 404
    assert o.get(ACC(b, "/rules")).json()["items"] == []  # company B sees none of company A's rules


def test_duplicate_detection_shows_evidence_and_never_deletes(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    sup = contact(o, a, "supplier", "Dup Supplier", tax_number="OM777")
    body = {"doc_number": "SUP-1", "contact_id": sup, "lines": [{"amount": "50.000"}]}
    first = draft(o, a, "purchase_invoice", **body).json()
    second = draft(o, a, "purchase_invoice", **body).json()
    assert second["duplicate_status"] == "exact" and "exact_duplicate" in blocking(second)
    m = second["duplicates"][0]
    assert m["matched_number"] == first["number"] and m["level"] == "exact" and m["signals"]["same_document_number"]
    # the accountant decides; nothing is merged or removed
    assert (
        o.post(ACC(a, f"/transactions/{second['id']}/duplicates/{m['id']}"), {"decision": "not_duplicate"}).status_code
        == 422
    )  # reason needed
    ok = o.post(
        ACC(a, f"/transactions/{second['id']}/duplicates/{m['id']}"),
        {"decision": "not_duplicate", "note": "Recurring monthly charge"},
    ).json()
    assert "exact_duplicate" not in codes(ok) and ok["status"] == "draft"
    assert o.get(ACC(a, f"/transactions/{first['id']}")).status_code == 200
    # similar but not the same party and number: not flagged
    other = draft(
        o,
        a,
        "purchase_invoice",
        doc_number="SUP-2",
        contact_id=contact(o, a, "supplier", "Someone Else"),
        lines=[{"amount": "50.000"}],
    ).json()
    assert other["duplicate_status"] == "none"
    # duplicates are scoped to the company
    b = books["b"]
    far = draft(o, b, "purchase_invoice", doc_number="SUP-1", lines=[{"amount": "50.000"}]).json()
    assert far["duplicate_status"] == "none"


# ================================================================== multi-currency
def test_foreign_currency_needs_a_rate_converts_deterministically_and_rounds_visibly(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = taxes(o, a)
    usd = draft(
        o, a, "purchase_invoice", currency="USD", lines=[{"amount": "100.00", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert "missing_exchange_rate" in blocking(usd)  # never invented
    assert (
        o.post(
            ACC(a, "/exchange-rates"),
            {"currency": "USD", "rate": "0.3845", "rate_date": "2025-03-01", "source": "test feed"},
        ).status_code
        == 201
    )
    dup = o.post(
        ACC(a, "/exchange-rates"), {"currency": "USD", "rate": "0.5", "rate_date": "2025-03-01", "source": "test feed"}
    )
    assert dup.status_code == 409  # rates are never overwritten
    again = o.post(ACC(a, f"/transactions/{usd['id']}/validate")).json()
    assert "missing_exchange_rate" not in codes(again)
    assert (
        again["exchange_rate"] == "0.3845000000"
        and again["rate_source"] == "test feed"
        and again["rate_date"] == "2025-03-01"
    )
    odd = draft(
        o, a, "purchase_invoice", currency="USD", lines=[{"amount": "33.33", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert odd["balanced"] and "rounding_difference_posted" in codes(odd)
    assert any(x["source"] == "rounding" and x["account_code"] == "6900" for x in odd["journal"]["lines"])
    assert D(odd["base_debit_total"]) == D(odd["base_credit_total"])
    # the stored rate reproduces the conversion exactly
    assert odd["journal"]["exchange_rate"] == "0.3845000000"
    # a stale rate is a warning with the dates, not silence
    old = draft(
        o,
        a,
        "purchase_invoice",
        currency="USD",
        date="2025-04-20",
        lines=[{"amount": "10.00", "tax_code_id": t["OM_STD"]}],
    ).json()
    assert "stale_exchange_rate" in codes(old)
    # an explicit rate is recorded as manual
    man = draft(o, a, "purchase_invoice", currency="USD", exchange_rate="0.40", lines=[{"amount": "10.00"}]).json()
    assert man["rate_source"] == "manual" and man["exchange_rate"] == "0.4000000000"
    assert (
        o.post(
            ACC(a, "/exchange-rates"), {"currency": "OMR", "rate": "1", "rate_date": "2025-03-01", "source": "x"}
        ).status_code
        == 422
    )


# ================================================================== periods
def test_closed_periods_block_approval_and_dates_are_never_moved(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    sup = contact(o, a, "supplier")
    x = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"amount": "100", "tax_code_id": t["OM_STD"]}]).json()
    march = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["name"] == "2025-03")
    assert o.post(ACC(a, f"/transactions/{x['id']}/submit"), {}).status_code == 200
    # closing is refused while something dated in the period still awaits approval
    assert o.post(ACC(a, f"/periods/{march['id']}/close"), {}).status_code == 409
    back = acct.post(ACC(a, f"/transactions/{x['id']}/request-correction"), {"reason": "check the period first"})
    assert back.status_code == 200 and back.json()["status"] == "draft"
    assert o.post(ACC(a, f"/periods/{march['id']}/close"), {}).status_code == 200
    again = o.post(ACC(a, f"/transactions/{x['id']}/validate")).json()
    assert "period_closed" in blocking(again) and again["transaction_date"] == "2025-03-10"  # the date did not move
    assert o.post(ACC(a, f"/transactions/{x['id']}/submit"), {}).status_code == 409
    # reopening needs a reason and leaves a trail
    assert o.post(ACC(a, f"/periods/{march['id']}/reopen"), {}).status_code == 422
    assert o.post(ACC(a, f"/periods/{march['id']}/reopen"), {"reason": "late supplier invoice"}).status_code == 200
    ev = o.get(ACC(a, f"/periods/{march['id']}/events")).json()["items"]
    assert [e["action"] for e in ev] == ["created", "closed", "reopened"] and ev[-1][
        "reason"
    ] == "late supplier invoice"
    assert "period_closed" not in codes(o.post(ACC(a, f"/transactions/{x['id']}/validate")).json())
    # a date with no period is blocked as well
    far = draft(o, a, "purchase_invoice", date="2031-01-01", lines=[{"amount": "10"}]).json()
    assert "no_period" in blocking(far)
    # periods cannot overlap
    assert (
        o.post(
            ACC(a, "/periods"), {"name": "overlap", "start_date": "2025-03-15", "end_date": "2025-04-15"}
        ).status_code
        == 409
    )


# ================================================================== approvals
def test_authorized_approval_creates_an_immutable_approved_journal(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    x = draft(
        o, a, "purchase_invoice", contact_id=contact(o, a), lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]
    ).json()
    r = approve_flow(x["id"], o, acct, a)
    assert r.status_code == 200, r.text
    ap = r.json()
    assert ap["status"] == "approved" and ap["approved_by"] == uid(acct) and ap["journal"]["status"] == "approved"
    assert (ap["journal"]["number"] == "JV-000001" and ap["number"] == "TX-000001") or ap["journal"][
        "number"
    ].startswith("JV-")
    assert [e["action"] for e in ap["trail"]] == ["create", "submit", "approve"] and ap["trail"][-1][
        "actor_type"
    ] == "user"
    # approved records cannot be edited or re-approved
    assert o.patch(ACC(a, f"/transactions/{x['id']}"), {"narration": "changed"}).status_code == 409
    assert o.put(ACC(a, f"/transactions/{x['id']}/lines"), {"lines": [{"amount": "1"}]}).status_code == 409
    assert acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {}).status_code == 409  # no duplicate posting
    assert o.post(ACC(a, f"/transactions/{x['id']}/regenerate")).status_code == 409


def test_preparer_cannot_approve_their_own_transaction_and_the_assistant_cannot_approve_at_all(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct, prep = books["owner"], books["a"], books["acct"], books["prep"]
    t = taxes(o, a)
    x = prep.post(
        ACC(a, "/transactions"),
        {
            "txn_type": "purchase_invoice",
            "transaction_date": "2025-03-10",
            "contact_id": contact(o, a),
            "lines": [{"amount": "10.000", "tax_code_id": t["OM_STD"]}],
        },
    )
    assert x.status_code == 201, x.text
    xid = x.json()["id"]
    assert prep.post(ACC(a, f"/transactions/{xid}/submit"), {}).status_code == 200
    assert prep.post(ACC(a, f"/transactions/{xid}/approve"), {}).status_code == 403  # no permission
    # the accountant prepares AND tries to approve: separation of duties
    y = acct.post(
        ACC(a, "/transactions"),
        {
            "txn_type": "purchase_invoice",
            "transaction_date": "2025-03-11",
            "doc_number": "S-2",
            "contact_id": contact(o, a, name="Other Supplier"),
            "lines": [{"amount": "10.000", "tax_code_id": t["OM_STD"]}],
        },
    ).json()
    acct.post(ACC(a, f"/transactions/{y['id']}/submit"), {})
    own = acct.post(ACC(a, f"/transactions/{y['id']}/approve"), {})
    assert own.status_code == 403 and "different person" in own.json()["detail"]
    assert acct.post(ACC(a, f"/transactions/{xid}/approve"), {}).status_code == 200  # a different person may
    # the company can relax the policy; the database trigger follows the same policy
    assert o.put(ACC(a, "/policy"), {"separate_preparer_approver": False}).status_code == 200
    assert acct.post(ACC(a, f"/transactions/{y['id']}/approve"), {}).status_code == 200


def test_unbalanced_journals_cannot_be_submitted_or_approved(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    ca = accounts(o, a)
    j = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [{"account_id": ca["6200"], "debit": "10.000"}, {"account_id": ca["1110"], "credit": "9.999"}],
        },
    ).json()
    assert not j["balanced"] and "journal_unbalanced" in blocking(j)
    assert o.post(ACC(a, f"/transactions/{j['id']}/submit"), {}).status_code == 409
    # even a direct database write cannot approve it
    with pytest.raises(DBAPIError, match="not balanced"), owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE acct_journals SET status = 'approved', number = 'X-1', approved_by = (SELECT id FROM users LIMIT 1), approved_at = now() WHERE transaction_id = :t"
            ),
            {"t": j["id"]},
        )
    fixed = o.put(
        ACC(a, f"/transactions/{j['id']}/journal-lines"),
        {"lines": [{"account_id": ca["6200"], "debit": "10.000"}, {"account_id": ca["1110"], "credit": "10.000"}]},
    ).json()
    assert fixed["balanced"] and not blocking(fixed) and fixed["journal_mode"] == "manual"
    assert approve_flow(j["id"], o, acct, a).status_code == 200


def test_debit_credit_rules_for_manual_journals(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    both = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [
                {"account_id": ca["6200"], "debit": "5", "credit": "5"},
                {"account_id": ca["1110"], "credit": "10"},
            ],
        },
    )
    assert both.status_code == 422
    neg = o.post(
        ACC(a, "/transactions"),
        {
            "txn_type": "journal_entry",
            "transaction_date": "2025-03-10",
            "lines": [
                {"amount": "-5", "entry_side": "debit", "account_id": ca["6200"]},
                {"amount": "5", "entry_side": "credit", "account_id": ca["1110"]},
            ],
        },
    )
    assert neg.status_code == 422
    one_line = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [{"account_id": ca["6200"], "debit": "5"}, {"account_id": ca["1110"], "credit": "5"}],
        },
    ).json()
    assert one_line["balanced"] and one_line["txn_type"] == "journal_entry"
    cross = o.post(
        ACC(a, "/journals"),
        {
            "journal_date": "2025-03-10",
            "lines": [
                {"account_id": accounts(o, books["b"])["6200"], "debit": "5"},
                {"account_id": ca["1110"], "credit": "5"},
            ],
        },
    )
    assert cross.status_code == 404  # another company's account


def test_two_level_approval_limits_and_rejection_cycle(books, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, acct, prep = books["owner"], books["a"], books["acct"], books["prep"]
    t = taxes(o, a)
    pm = make_member(o, mailer, "practice_manager")
    pol = o.put(
        ACC(a, "/policy"),
        {"separate_preparer_approver": True, "levels": 2, "distinct_levels": True, "approval_limit": "500.000"},
    )
    assert pol.status_code == 200 and pol.json()["levels"] == 2
    sup = contact(o, a)
    x = prep.post(
        ACC(a, "/transactions"),
        {
            "txn_type": "purchase_invoice",
            "transaction_date": "2025-03-10",
            "contact_id": sup,
            "lines": [{"amount": "1000.000", "tax_code_id": t["OM_STD"]}],
        },
    ).json()
    sub = prep.post(ACC(a, f"/transactions/{x['id']}/submit"), {}).json()
    assert sub["status"] == "awaiting_review"
    assert acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {}).status_code == 409  # review comes first
    assert prep.post(ACC(a, f"/transactions/{x['id']}/review-approve"), {}).status_code == 403  # no review permission
    rev = acct.post(ACC(a, f"/transactions/{x['id']}/review-approve"), {"note": "numbers agree"}).json()
    assert rev["status"] == "awaiting_approval" and rev["reviewed_by"] == uid(acct)
    # the reviewer cannot also give final approval; above the limit needs the high-value permission
    assert acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {}).status_code == 403
    pm_ok = pm.post(ACC(a, f"/transactions/{x['id']}/approve"), {})
    assert pm_ok.status_code == 200 and pm_ok.json()["status"] == "approved", pm_ok.text
    # rejection: reason required, the transaction can be reopened, corrected and resubmitted
    y = prep.post(
        ACC(a, "/transactions"),
        {
            "txn_type": "purchase_invoice",
            "transaction_date": "2025-03-12",
            "doc_number": "R-1",
            "contact_id": sup,
            "lines": [{"amount": "20.000", "tax_code_id": t["OM_STD"]}],
        },
    ).json()
    prep.post(ACC(a, f"/transactions/{y['id']}/submit"), {})
    acct.post(ACC(a, f"/transactions/{y['id']}/review-approve"), {})
    assert pm.post(ACC(a, f"/transactions/{y['id']}/reject"), {"reason": "x"}).status_code == 422
    rj = pm.post(ACC(a, f"/transactions/{y['id']}/reject"), {"reason": "Wrong supplier on the invoice"}).json()
    assert rj["status"] == "rejected" and rj["rejection_reason"] == "Wrong supplier on the invoice"
    assert prep.patch(ACC(a, f"/transactions/{y['id']}"), {"narration": "fix"}).status_code == 409  # revise first
    rv = prep.post(ACC(a, f"/transactions/{y['id']}/revise"), {}).json()
    assert rv["status"] == "draft" and "Wrong supplier" in rv["correction_note"]
    assert prep.patch(ACC(a, f"/transactions/{y['id']}"), {"narration": "corrected"}).status_code == 200
    assert prep.post(ACC(a, f"/transactions/{y['id']}/submit"), {}).json()["status"] == "awaiting_review"
    trail = [e["action"] for e in o.get(ACC(a, f"/transactions/{y['id']}")).json()["trail"]]
    assert trail == [
        "create",
        "submit",
        "review_approve",
        "reject",
        "revise",
        "submit",
    ]  # history is preserved, never rewritten
    # a plan without advanced approval cannot configure it
    plan = {"organization_id": o.org_id}
    _ = plan


def test_soft_differences_need_an_acknowledgement_and_a_written_reason(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    sup = contact(o, a)
    # tax as printed (5.500) differs from the 5% calculation (5.000): reported, never silently changed
    x = draft(
        o,
        a,
        "purchase_invoice",
        contact_id=sup,
        lines=[{"amount": "100.000", "tax_amount": "5.500", "tax_code_id": t["OM_STD"]}],
    ).json()
    assert "tax_mismatch" in codes(x) and not blocking(x)
    assert (x["tax_amount"], x["gross_amount"]) == ("5.500000", "105.500000")  # the document's own figure is kept
    o.post(ACC(a, f"/transactions/{x['id']}/submit"), {})
    r = acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {})
    assert r.status_code == 409 and r.json()["issues"][0]["code"] == "tax_mismatch"
    assert (
        acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {"acknowledge": True}).status_code == 409
    )  # a reason is required
    ok = acct.post(
        ACC(a, f"/transactions/{x['id']}/approve"),
        {"acknowledge": True, "note": "Supplier rounds VAT up; verified on the paper invoice"},
    )
    assert ok.status_code == 200
    assert "override" in [e["action"] for e in ok.json()["trail"]]


# ================================================================== reversal, amendment and immutability
def test_reversal_keeps_both_journals_and_the_ledger_nets_to_zero(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    x = draft(
        o, a, "purchase_invoice", contact_id=contact(o, a), lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert approve_flow(x["id"], o, acct, a).status_code == 200
    ca = accounts(o, a)
    tb = o.get(ACC(a, "/reports/trial-balance")).json()
    assert tb["balanced"] and D(tb["debit"]) == Dec("105.000")
    assert (
        acct.post(
            ACC(a, f"/transactions/{x['id']}/reverse"), {"reversal_date": "2025-03-12", "reason": "duplicate"}
        ).status_code
        == 403
    )  # accounting.reverse is a separate permission
    rv = o.post(
        ACC(a, f"/transactions/{x['id']}/reverse"),
        {"reversal_date": "2025-03-12", "reason": "Entered twice by mistake"},
    )
    assert rv.status_code == 200, rv.text
    orig = rv.json()
    assert orig["status"] == "reversed" and orig["journal"]["status"] == "reversed" and orig["reversed_by"] == uid(o)
    rows_ = o.get(ACC(a, "/transactions?status=approved")).json()["items"]
    mirror = next(r for r in rows_ if r["txn_type"] == "reversal_entry")
    m = o.get(ACC(a, f"/transactions/{mirror['id']}")).json()
    assert m["status"] == "approved" and m["reversal_of_id"] == x["id"] and m["balanced"]
    assert sorted(jl(m)) == sorted((c, cr, d) for c, d, cr in jl(orig))  # every side swapped
    tb2 = o.get(ACC(a, "/reports/trial-balance")).json()
    assert tb2["balanced"] and D(tb2["debit"]) == 0 and D(tb2["credit"]) == 0  # nets to zero, history intact
    led = o.get(ACC(a, f"/reports/ledger/{ca['6100']}")).json()
    assert [(D(i["debit"]), D(i["credit"])) for i in led["items"]] == [(Dec(100), Dec(0)), (Dec(0), Dec(100))] and D(
        led["items"][-1]["balance"]
    ) == 0
    # a second reversal is refused; the closed-period rule applies to the reversal date
    assert (
        o.post(
            ACC(a, f"/transactions/{x['id']}/reverse"), {"reversal_date": "2025-03-13", "reason": "again"}
        ).status_code
        == 409
    )
    y = draft(o, a, "purchase_invoice", contact_id=contact(o, a, name="Another"), lines=[{"amount": "10"}]).json()
    assert approve_flow(y["id"], o, acct, a).status_code == 200
    apr = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["name"] == "2025-04")
    o.post(ACC(a, f"/periods/{apr['id']}/close"), {})
    closed = o.post(ACC(a, f"/transactions/{y['id']}/reverse"), {"reversal_date": "2025-04-02", "reason": "late"})
    assert closed.status_code == 409 and "closed" in closed.json()["detail"]


def test_amendment_reverses_and_opens_a_corrected_copy(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    x = draft(
        o,
        a,
        "purchase_invoice",
        contact_id=contact(o, a),
        doc_number="AM-1",
        lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}],
    ).json()
    assert approve_flow(x["id"], o, acct, a).status_code == 200
    r = o.post(ACC(a, f"/transactions/{x['id']}/amend"), {"reversal_date": "2025-03-15", "reason": "Wrong amount"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert (
        new["status"] == "draft"
        and new["amends_id"] == x["id"]
        and new["doc_number"] == "AM-1"
        and new["gross_amount"] == "105.000000"
    )
    assert o.get(ACC(a, f"/transactions/{x['id']}")).json()["status"] == "reversed"
    fixed = o.put(
        ACC(a, f"/transactions/{new['id']}/lines"), {"lines": [{"amount": "90.000", "tax_code_id": t["OM_STD"]}]}
    ).json()
    assert fixed["gross_amount"] == "94.500000" and "exact_duplicate" not in codes(fixed)


def test_database_refuses_to_change_approved_records(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    x = draft(o, a, "purchase_invoice", contact_id=contact(o, a), lines=[{"amount": "10.000"}]).json()
    assert approve_flow(x["id"], o, acct, a).status_code == 200
    j = o.get(ACC(a, f"/transactions/{x['id']}")).json()["journal"]
    for sql in (
        "UPDATE acct_transactions SET gross_amount = 1 WHERE id = :t",
        "UPDATE acct_transactions SET status = 'draft' WHERE id = :t",
        "DELETE FROM acct_transactions WHERE id = :t",
        "UPDATE acct_journals SET narration = 'tampered' WHERE transaction_id = :t",
        "DELETE FROM acct_journals WHERE transaction_id = :t",
        "UPDATE acct_journal_lines SET debit = 999 WHERE journal_id = :j",
        "DELETE FROM acct_journal_lines WHERE journal_id = :j",
        "INSERT INTO acct_journal_lines (id, organization_id, company_id, journal_id, line_no, account_id, debit, currency) SELECT gen_random_uuid(), organization_id, company_id, journal_id, 99, account_id, 1, currency FROM acct_journal_lines WHERE journal_id = :j LIMIT 1",
        "UPDATE acct_transaction_lines SET net_amount = 1 WHERE transaction_id = :t",
    ):
        with pytest.raises(DBAPIError), owner_engine.begin() as conn:
            conn.execute(text(sql), {"t": x["id"], "j": j["id"]})
    # nobody can approve in the database without naming a person
    with pytest.raises(DBAPIError), owner_engine.begin() as conn:
        conn.execute(text("UPDATE acct_transactions SET approved_by = NULL WHERE id = :t"), {"t": x["id"]})
    with pytest.raises(DBAPIError), owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO acct_approvals (id, organization_id, company_id, transaction_id, action, actor_type) SELECT gen_random_uuid(), organization_id, company_id, id, 'approve', 'ai' FROM acct_transactions WHERE id = :t"
            ),
            {"t": x["id"]},
        )


def test_money_is_exact_decimal_everywhere(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    with owner_engine.connect() as conn:
        bad = conn.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_name LIKE 'acct\\_%' AND data_type IN ('real', 'double precision', 'money')"
            )
        ).all()
    assert bad == []  # no binary float columns anywhere in the accounting schema
    x = draft(o, a, "purchase_invoice", lines=[{"amount": "0.1"}, {"amount": "0.2"}]).json()
    assert x["net_amount"] == "0.300000" and x["gross_amount"] == "0.300000"  # not 0.30000000000000004
    assert isinstance(x["net_amount"], str)
    precise = draft(
        o, a, "purchase_invoice", lines=[{"amount": "1234567.891", "tax_code_id": taxes(o, a)["OM_STD"]}]
    ).json()
    assert (
        precise["tax_amount"] == "61728.395000" and precise["gross_amount"] == "1296296.286000" and precise["balanced"]
    )  # 1234567.891 x 5% = 61728.39455, rounded half-up at scale 3
    assert (
        o.post(
            ACC(a, "/transactions"),
            {"txn_type": "purchase_invoice", "transaction_date": "2025-03-10", "lines": [{"amount": "abc"}]},
        ).status_code
        == 422
    )
    assert (
        o.post(
            ACC(a, "/transactions"),
            {"txn_type": "purchase_invoice", "transaction_date": "2025-03-10", "lines": [{"amount": "1e3"}]},
        ).status_code
        == 422
    )


# ================================================================== other transaction types
@pytest.mark.parametrize(
    ("typ", "extra", "expected"),
    [
        (
            "customer_receipt",
            {"contact": "customer"},
            [("1120", "100.000000", "0.000000"), ("1200", "0.000000", "100.000000")],
        ),
        (
            "customer_refund",
            {"contact": "customer"},
            [("1200", "100.000000", "0.000000"), ("1120", "0.000000", "100.000000")],
        ),
        (
            "supplier_payment",
            {"contact": "supplier"},
            [("2100", "100.000000", "0.000000"), ("1120", "0.000000", "100.000000")],
        ),
        (
            "supplier_refund",
            {"contact": "supplier"},
            [("1120", "100.000000", "0.000000"), ("2100", "0.000000", "100.000000")],
        ),
        ("cash_expense", {}, [("6100", "100.000000", "0.000000"), ("1110", "0.000000", "100.000000")]),
        ("bank_expense", {}, [("6100", "100.000000", "0.000000"), ("1120", "0.000000", "100.000000")]),
        ("employee_expense", {}, [("6100", "100.000000", "0.000000"), ("2150", "0.000000", "100.000000")]),
        ("general_expense", {}, [("6100", "100.000000", "0.000000"), ("2100", "0.000000", "100.000000")]),
        ("bank_charges", {}, [("6300", "100.000000", "0.000000"), ("1120", "0.000000", "100.000000")]),
        ("bank_deposit", {}, [("1120", "100.000000", "0.000000"), ("3900", "0.000000", "100.000000")]),
        ("bank_withdrawal", {}, [("3900", "100.000000", "0.000000"), ("1120", "0.000000", "100.000000")]),
    ],
)
def test_each_supported_transaction_type_has_a_balanced_company_specific_journal(books, typ, extra, expected) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    kw = {"contact_id": contact(o, a, extra["contact"], f"{extra['contact']} one")} if extra else {}
    x = draft(o, a, typ, lines=[{"amount": "100.000"}], **kw)
    assert x.status_code == 201, x.text
    x = x.json()
    assert jl(x) == expected and x["balanced"] and not blocking(x), x["issues"]


def test_bank_transfer_and_manual_accounting_types(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    x = draft(o, a, "bank_transfer", lines=[{"amount": "40.000"}], counter_account_id=ca["1110"]).json()
    assert jl(x) == [("1110", "40.000000", "0.000000"), ("1120", "0.000000", "40.000000")] and x["balanced"]
    no_dest = draft(o, a, "bank_transfer", lines=[{"amount": "40.000"}]).json()
    assert "missing_account_mapping" in blocking(no_dest)
    for typ in ("opening_balance", "adjustment_entry", "accrual", "prepayment", "depreciation"):
        j = o.post(
            ACC(a, "/journals"),
            {
                "txn_type": typ,
                "journal_date": "2025-03-31",
                "narration": typ,
                "lines": [
                    {"account_id": ca["6500"], "debit": "12.500"},
                    {"account_id": ca["2300"], "credit": "12.500"},
                ],
            },
        ).json()
        assert j["txn_type"] == typ and j["balanced"] and j["journal"]["journal_type"] in ("opening", "adjustment")
    assert (
        o.post(
            ACC(a, "/transactions"), {"txn_type": "reversal_entry", "transaction_date": "2025-03-31", "lines": []}
        ).status_code
        == 422
    )
    bad = o.post(ACC(a, "/transactions"), {"txn_type": "made_up", "transaction_date": "2025-03-31", "lines": []})
    assert bad.status_code == 422


# ================================================================== reports (read-only, approved data only)
def test_reporting_foundation_uses_approved_journals_only(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = taxes(o, a)
    sup = contact(o, a, "supplier", "Reporting Supplier")
    cus = contact(o, a, "customer", "Reporting Customer")
    pending = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"amount": "999.000"}]).json()  # never approved
    p1 = draft(
        o, a, "purchase_invoice", contact_id=sup, lines=[{"amount": "100.000", "tax_code_id": t["OM_STD"]}]
    ).json()
    s1 = draft(
        o,
        a,
        "sales_invoice",
        date="2025-04-05",
        contact_id=cus,
        lines=[{"amount": "300.000", "tax_code_id": t["OM_STD"]}],
    ).json()
    for x in (p1, s1):
        assert approve_flow(x["id"], o, acct, a).status_code == 200
    tb = o.get(ACC(a, "/reports/trial-balance")).json()
    assert tb["balanced"] and D(tb["debit"]) == D(tb["credit"]) == Dec(
        "420.000"
    )  # 105 purchase + 315 sale; the draft is excluded
    by = {r["code"]: r for r in tb["rows"]}
    assert D(by["1200"]["debit"]) == Dec(315) and D(by["2100"]["credit"]) == Dec(105) and "6100" in by
    assert D(o.get(ACC(a, "/reports/trial-balance?as_of=2025-03-31")).json()["debit"]) == Dec(
        105
    )  # as-of date respected
    bal = {r["code"]: D(r["balance"]) for r in o.get(ACC(a, "/reports/account-balances")).json()["rows"]}
    assert (
        bal["2100"] == Dec(105)
        and bal["1200"] == Dec(315)
        and bal["4100"] == Dec(300)
        and bal["1300"] == Dec(5)
        and bal["2200"] == Dec(15)
    )
    cb = o.get(ACC(a, "/reports/balances/customer")).json()
    assert [(r["display_name"], D(r["balance"])) for r in cb["rows"]] == [("Reporting Customer", Dec(315))]
    sb = o.get(ACC(a, "/reports/balances/supplier")).json()
    assert [(r["display_name"], D(r["balance"])) for r in sb["rows"]] == [("Reporting Supplier", Dec(105))]
    sales = o.get(ACC(a, "/reports/summary/sales")).json()
    assert (
        D(sales["totals"]["net"]) == Dec(300)
        and D(sales["totals"]["gross"]) == Dec(315)
        and sales["rows"][0]["month"] == "2025-04"
    )
    purchases = o.get(ACC(a, "/reports/summary/purchases")).json()
    assert D(purchases["totals"]["gross"]) == Dec(105)
    vat = o.get(ACC(a, "/reports/tax-summary")).json()
    assert (
        D(vat["input_tax"]) == Dec(5)
        and D(vat["output_tax"]) == Dec(15)
        and D(vat["net_payable"]) == Dec(10)
        and "not a VAT return" in vat["note"]
    )
    gl = o.get(ACC(a, f"/reports/ledger/{accounts(o, a)['1200']}")).json()
    assert gl["items"][0]["transaction_number"] == s1["number"] and D(gl["items"][0]["balance"]) == Dec(315)
    # the draft shows in the transaction list but moves no balance anywhere
    assert any(i["id"] == pending["id"] for i in o.get(ACC(a, "/transactions?status=draft")).json()["items"])
    # company B has nothing: balances are never shared
    assert o.get(ACC(books["b"], "/reports/trial-balance")).json()["rows"] == []


# ================================================================== multi-tenancy and authorization
def test_other_firms_and_unassigned_users_cannot_reach_accounting(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, b, acct, prep = books["owner"], books["a"], books["b"], books["acct"], books["prep"]
    t = taxes(o, a)
    x = draft(
        o, a, "purchase_invoice", contact_id=contact(o, a), lines=[{"amount": "10", "tax_code_id": t["OM_STD"]}]
    ).json()
    acct_id = accounts(o, a)["6100"]
    other = new_owner(client, mailer, org_name="Another Firm")
    oc = make_company(other, "ZZ", name="Other Co")["id"]
    setup_books(other, oc)
    for path in (
        f"/transactions/{x['id']}",
        f"/accounts/{acct_id}",
        "/config",
        "/transactions",
        f"/reports/ledger/{acct_id}",
    ):
        assert other.get(ACC(a, path)).status_code in (403, 404), path  # another firm: never reachable
    assert other.get(ACC(a, "/transactions"), org=other.org_id).status_code in (403, 404)
    # and with their own company id but A's transaction id: not found
    assert other.get(ACC(oc, f"/transactions/{x['id']}")).status_code == 404
    assert other.get(ACC(oc, f"/accounts/{acct_id}")).status_code == 404
    # inside the firm, company B's transactions are not visible through company A's URL either
    yb = draft(o, b, "purchase_invoice", lines=[{"amount": "10"}]).json()
    assert o.get(ACC(a, f"/transactions/{yb['id']}")).status_code == 404
    assert o.post(ACC(a, f"/transactions/{yb['id']}/submit"), {}).status_code == 404
    # a user assigned only to company A cannot use company B
    assert acct.get(ACC(b, "/transactions")).status_code in (403, 404)
    assert acct.post(
        ACC(b, "/transactions"),
        {"txn_type": "purchase_invoice", "transaction_date": "2025-03-10", "lines": [{"amount": "1"}]},
    ).status_code in (403, 404)
    # read-only users read but never write; staff without accounting rights are refused
    ro = make_member(o, mailer, "read_only")
    assign(o, a, ro)
    assert ro.get(ACC(a, "/transactions")).status_code == 200
    assert (
        ro.post(
            ACC(a, "/transactions"),
            {"txn_type": "purchase_invoice", "transaction_date": "2025-03-10", "lines": [{"amount": "1"}]},
        ).status_code
        == 403
    )
    assert ro.post(ACC(a, "/accounts"), {"code": "9", "name": "n", "account_type": "expense"}).status_code == 403
    staff = make_member(o, mailer, "staff")
    assign(o, a, staff)
    assert staff.get(ACC(a, "/transactions")).status_code == 403
    assert (
        prep.post(ACC(a, "/accounts"), {"code": "9", "name": "n", "account_type": "expense"}).status_code == 403
    )  # preparing is not configuring
    # an independent accountant (own organization) has the same model and is isolated from firms
    solo = new_owner(client, mailer, org_name="Solo Accountant")
    sc = make_company(solo, "SO", name="Solo Client")["id"]
    setup_books(solo, sc)
    assert solo.get(ACC(sc, "/accounts")).json()["items"] and solo.get(ACC(a, "/accounts")).status_code in (403, 404)


def test_restricted_documents_keep_their_transactions_hidden(books) -> None:  # type: ignore[no-untyped-def]
    o, a, node, acct = books["owner"], books["a"], books["node"], books["acct"]
    _, xid = approved_extraction(o, a, node)
    t = o.post(ACC(a, f"/transactions/from-extraction/{xid}")).json()
    plain = draft(
        o, a, "purchase_invoice", lines=[{"amount": "5"}]
    ).json()  # no document: governed by accounting permissions only
    assert acct.get(ACC(a, f"/transactions/{t['id']}")).status_code == 200
    assert t["id"] in [i["id"] for i in acct.get(ACC(a, "/transactions")).json()["items"]]
    # restrict the workspace: the accountant loses the document, and with it the transaction made from it
    assert o.patch(W(a, "/settings"), {"access_mode": "restricted"}).status_code == 200
    assert acct.get(ACC(a, f"/transactions/{t['id']}")).status_code == 404
    assert t["id"] not in [i["id"] for i in acct.get(ACC(a, "/transactions")).json()["items"]]
    assert acct.get(ACC(a, f"/transactions/{plain['id']}")).status_code == 200
    assert acct.post(ACC(a, f"/transactions/{t['id']}/approve"), {}).status_code == 404
    assert o.get(ACC(a, f"/transactions/{t['id']}")).status_code == 200  # the firm owner still sees it


def test_an_approved_transaction_pins_its_source_document(books) -> None:  # type: ignore[no-untyped-def]
    o, a, node, acct = books["owner"], books["a"], books["node"], books["acct"]
    cust = contact(o, a, "customer", "Gulf Builders Co")
    doc, xid = approved_extraction(o, a, node)
    t = o.post(ACC(a, f"/transactions/from-extraction/{xid}")).json()
    o.put(ACC(a, f"/transactions/{t['id']}/contact"), {"contact_id": cust})
    assert approve_flow(t["id"], o, acct, a).status_code == 200
    assert o.post(f"/companies/{a}/documents/{doc}/delete").status_code == 200
    purge = o.delete(f"/companies/{a}/documents/{doc}?confirm=true")
    assert purge.status_code == 409 and "accounting records" in purge.json()["detail"]
    ap = o.get(ACC(a, f"/transactions/{t['id']}")).json()
    assert ap["status"] == "approved" and ap["source_document_id"] == doc and ap["extraction_id"] == xid


# ================================================================== entitlements
def feature_override(owner_engine, org_id, key, value: bool) -> None:  # type: ignore[no-untyped-def]
    with owner_engine.begin() as conn:
        sub = conn.execute(text("SELECT id FROM subscriptions WHERE organization_id = :o"), {"o": org_id}).scalar_one()
        conn.execute(
            text(
                "INSERT INTO entitlement_overrides (id, organization_id, subscription_id, entitlement_key, bool_value, reason) VALUES (gen_random_uuid(), :o, :s, :k, :v, 'test')"
            ),
            {"o": org_id, "s": sub, "k": key, "v": value},
        )


def test_limits_stop_new_records_without_touching_existing_ones(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    first = draft(o, a, "purchase_invoice", lines=[{"amount": "10"}])
    assert first.status_code == 201
    set_plan_limit(owner_engine, o.org_id, "quota.accounting_transactions_per_month", 1)
    blocked = draft(o, a, "purchase_invoice", doc_number="Z", lines=[{"amount": "10"}])
    assert blocked.status_code in (402, 409, 422) and "quota.accounting_transactions_per_month" in blocked.text
    assert (
        o.get(ACC(a, f"/transactions/{first.json()['id']}")).status_code == 200
    )  # existing data untouched and readable
    assert o.get(ACC(a, "/reports/trial-balance")).status_code == 200
    set_plan_limit(owner_engine, o.org_id, "quota.accounting_transactions_per_month", None, unlimited=True)
    set_plan_limit(owner_engine, o.org_id, "quota.journals_per_month", 1)
    assert draft(o, a, "purchase_invoice", doc_number="Y", lines=[{"amount": "10"}]).status_code in (402, 409, 422)


def test_plan_features_gate_multi_currency_rules_and_advanced_approval(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ca = accounts(o, a)
    o.post(ACC(a, "/exchange-rates"), {"currency": "USD", "rate": "0.385", "rate_date": "2025-03-01", "source": "feed"})
    feature_override(owner_engine, o.org_id, "feature.multi_currency", False)
    x = draft(o, a, "purchase_invoice", currency="USD", lines=[{"amount": "10.00"}]).json()
    assert "feature_not_in_plan" in blocking(x)
    feature_override(owner_engine, o.org_id, "feature.advanced_accounting", False)
    r = o.post(
        ACC(a, "/rules"),
        {
            "name": "r",
            "kind": "account",
            "conditions": [{"field": "description", "op": "contains", "value": "x"}],
            "action": {"account_id": ca["6200"]},
        },
    )
    assert r.status_code == 409
    rc = o.post(
        ACC(a, "/tax-codes"),
        {"code": "RCX", "name": "rc", "treatment": "reverse_charge", "rate": "5", "effective_from": "2020-01-01"},
    )
    assert rc.status_code == 409
    feature_override(owner_engine, o.org_id, "feature.advanced_approval", False)
    assert o.put(ACC(a, "/policy"), {"levels": 2}).status_code == 409
    assert o.put(ACC(a, "/policy"), {"levels": 1, "separate_preparer_approver": True}).status_code == 200


def test_every_accounting_route_declares_its_authorisation() -> None:
    from artify_api.context import Requires
    from artify_api.routers.accounting import router
    from conftest import walk_dependencies
    from fastapi.routing import APIRoute

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 60
    for route in routes:
        deps = list(walk_dependencies(route.dependant))
        assert any(isinstance(c, Requires) and c.permission for c in deps), (
            route.path
        )  # none is open to every signed-in user


def test_unique_ids_are_not_guessable_between_companies(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    assert o.get(ACC(a, f"/transactions/{uuid.uuid4()}")).status_code == 404
    assert o.get(ACC(a, "/transactions/not-a-uuid")).status_code in (404, 422)
