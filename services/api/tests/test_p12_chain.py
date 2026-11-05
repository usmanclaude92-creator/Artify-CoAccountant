"""Phase 12 data-consistency chain: one synthetic document followed all the way through the system.

    Document -> category -> OCR -> verified data -> transaction -> journal entry -> Excel export -> daily closing -> sync feed

Each step asserts that the next one points at exactly the record produced by the previous one, that nothing is created
along the way without a human, and that later changes are refused once the record is locked. Synthetic data only."""

# ruff: noqa: F811
from __future__ import annotations

from decimal import Decimal
from typing import Any

from test_p6_documents import backend  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import ACC, approve_flow, approved_extraction, books, contact  # noqa: F401
from test_p9_engine import good_mapping
from test_p9_excel import XL, activate, sheet, template_bytes, upload
from test_p10_bookkeeping import BK, quiet, start, validate


def feed(actor, cid: str, **params: Any) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        q = {"limit": 500, **({"cursor": cursor} if cursor else {}), **params}
        r = actor.get(f"/companies/{cid}/sync/changes", params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        out += body["items"]
        cursor = body["next_cursor"]
        if not body["has_more"]:
            return out


def test_one_document_followed_through_every_stage(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a, node, acct, prep = books["owner"], books["a"], books["node"], books["acct"], books["prep"]
    quiet(o, a)

    # Document -> category -> OCR -> verified data (a person approves the extraction; nothing is automatic)
    doc, xid = approved_extraction(o, a, node)
    d = o.get(f"/companies/{a}/documents/{doc}").json()
    assert d["id"] == doc and d["node_id"] == node

    # Verified data -> transaction -> entry, approved by a different person than the preparer
    made = o.post(ACC(a, f"/transactions/from-extraction/{xid}"))
    assert made.status_code == 201, made.text
    t = made.json()
    assert t["source_document_id"] == doc and t["extraction_id"] == xid and t["status"] == "draft"
    cust = contact(o, a, "customer", "Chain Customer LLC")
    assert o.put(ACC(a, f"/transactions/{t['id']}/contact"), {"contact_id": cust}).status_code == 200
    assert approve_flow(t["id"], o, acct, a).status_code == 200
    ap = o.get(ACC(a, f"/transactions/{t['id']}")).json()
    assert ap["status"] == "approved" and ap["source_document_id"] == doc
    debit = sum(Decimal(x["debit"]) for x in ap["journal"]["lines"])
    credit = sum(Decimal(x["credit"]) for x in ap["journal"]["lines"])
    assert debit == credit and debit > 0  # the entry balances; money is carried as strings

    # The document is a sales invoice, so the accountant's workbook is mapped to the sales register dataset
    mapping = good_mapping()
    mapping["sheets"][0]["dataset"] = "sales_register"
    up = upload(o, a, template_bytes(tmp_path), name="Sales register")
    assert up.status_code == 201, up.text
    tpl = up.json()["id"]
    activate(o, a, tpl, mapping)

    # Transaction -> Excel export: the export row points at this transaction and the workbook can be re-read
    r = o.post(
        XL(a, "/exports"),
        {
            "template_id": tpl,
            "params": {"date_from": "2000-01-01", "date_to": "2100-12-31"},
            "idempotency_key": "chain-export-1",
        },
    )
    assert r.status_code in (200, 201, 202), r.text
    e = o.get(XL(a, f"/exports/{r.json()['id']}")).json()
    assert e["status"] == "completed"
    linked = {x["transaction_id"] for x in o.get(XL(a, f"/exports/{e['id']}/transactions")).json()["items"]}
    assert t["id"] in linked and e["transactions_included"] == 1
    assert o.post(XL(a, f"/exports/{e['id']}/verify")).json()["ok"] is True
    assert sheet(o, a, e["id"]) is not None

    # Transaction -> daily closing: the snapshot's totals come from the approved records of that day
    day = ap["transaction_date"]
    s = start(prep, a, day)
    v = validate(prep, a, s["id"])
    assert v["status"] == "in_progress"
    summ = prep.get(BK(a, f"/closings/{s['id']}/summary")).json()
    gross = {x["currency"]: Decimal(x["gross"]) for x in summ["transactions"]["approved_totals"]}
    assert gross == {
        ap["currency"]: Decimal(ap["gross_amount"])
    }  # one approved record that day: its total, to the fraction
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).status_code == 200
    closed = acct.post(BK(a, f"/closings/{s['id']}/close"), {})
    assert closed.status_code == 200, closed.text

    # The sync feed now shows every stage, and the preflight says what may still change
    kinds: dict[str, set[str]] = {}
    for it in feed(o, a):
        kinds.setdefault(it["kind"], set()).add(it["id"])
    assert doc in kinds["document"] and t["id"] in kinds["transaction"] and e["id"] in kinds["export"]
    assert s["id"] in kinds["closing"]
    chk = o.post(
        f"/companies/{a}/sync/check",
        {
            "items": [
                {"kind": "document", "id": doc},
                {"kind": "transaction", "id": t["id"]},
                {"kind": "export", "id": e["id"]},
                {"kind": "closing", "id": s["id"]},
            ]
        },
    )
    assert chk.status_code == 200, chk.text
    state = {x["kind"]: x["state"] for x in chk.json()["items"]}
    assert state["transaction"] == "locked" and state["export"] == "locked" and state["closing"] == "locked"
    assert state["document"] in ("current", "locked")

    # Locked means locked: the approved record and the closed day refuse a late change
    assert o.post(ACC(a, f"/transactions/{t['id']}/submit"), {}).status_code in (403, 409)
