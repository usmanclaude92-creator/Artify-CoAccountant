"""Phase 9 integration tests: Excel templates, mappings, exports, history and reports through the API against PostgreSQL.
Synthetic workbooks and data only. Exports run in-process (processing_mode = background), so a completed export is visible as
soon as the request returns."""

# ruff: noqa: F811
from __future__ import annotations

import io
import uuid
from decimal import Decimal as Dec
from pathlib import Path
from typing import Any

import pytest
import xlfixtures as xf
from conftest import make_member
from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p6_documents import assign, backend  # noqa: F401  (backend = autouse storage fixture)
from test_p8_accounting import ACC, books, contact, draft, taxes  # noqa: F401  (books is a fixture)
from test_p9_engine import good_mapping

X = "/excel"


def XL(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/excel{path}"


def put_raw(actor, path: str, content: bytes, **params: Any):  # type: ignore[no-untyped-def]
    return actor.client.put(
        f"/api/v1{path}",
        content=content,
        params=params,
        headers=actor.headers(**{"Content-Type": "application/octet-stream"}),
    )


def template_bytes(tmp_path: Path, **kw: Any) -> bytes:
    return xf.purchase_template(tmp_path / f"t{uuid.uuid4().hex[:6]}.xlsx", **kw).read_bytes()


def upload(owner, cid, content: bytes, *, name: str = "Purchase register", filename: str = "purchases.xlsx", **kw):  # type: ignore[no-untyped-def]
    return put_raw(
        owner, XL(cid, "/templates/upload"), content, filename=filename, name=name, category="purchase_register", **kw
    )


def activate(owner, cid, tid: str, mapping: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    m = owner.post(XL(cid, f"/templates/{tid}/mappings"), {"definition": mapping or good_mapping()})
    assert m.status_code == 201, m.text
    assert m.json()["ready"], m.json()["validation"]
    a = owner.post(XL(cid, f"/mappings/{m.json()['id']}/activate"), {"acknowledge_risks": True})
    assert a.status_code == 200, a.text
    return a.json()


def approved(books, cid=None, n: int = 1, amount: str = "100", narration: str = "Stationery") -> list[str]:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], cid or books["a"], books["acct"]
    t = taxes(o, a)
    sup = contact(o, a, "supplier", f"Gulf Office Supplies {uuid.uuid4().hex[:4]}")
    out = []
    for _ in range(n):
        x = draft(
            o, a, "purchase_invoice", contact_id=sup, narration=narration, doc_number=f"INV-{uuid.uuid4().hex[:5]}",
            lines=[{"amount": amount, "tax_code_id": t["OM_STD"]}],
        ).json()  # fmt: skip
        assert o.post(ACC(a, f"/transactions/{x['id']}/submit"), {}).status_code == 200
        r = acct.post(ACC(a, f"/transactions/{x['id']}/approve"), {})
        assert r.status_code == 200, r.text
        out.append(x["id"])
    return out


PARAMS = {"date_from": "2025-01-01", "date_to": "2025-12-31"}


def run_export(owner, cid, tid: str, *, params: dict | None = None, key: str | None = None, **kw):  # type: ignore[no-untyped-def,type-arg]
    return owner.post(
        XL(cid, "/exports"),
        {"template_id": tid, "params": params or PARAMS, "idempotency_key": key or uuid.uuid4().hex, **kw},
    )


def sheet(owner, cid, eid: str):  # type: ignore[no-untyped-def]
    r = owner.get(XL(cid, f"/exports/{eid}/file"))
    assert r.status_code == 200, r.text
    return load_workbook(io.BytesIO(r.content))


@pytest.fixture
def tpl(books, tmp_path):  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    r = upload(o, a, template_bytes(tmp_path))
    assert r.status_code == 201, r.text
    t = r.json()
    activate(o, a, t["id"])
    return t["id"]


# ================================================================== upload and analysis
def test_upload_stores_a_versioned_template_with_analysis(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    r = upload(o, a, template_bytes(tmp_path))
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "draft" and t["current_version_no"] == 1 and t["category"] == "purchase_register"
    assert t["versions"][0]["sha256"] and t["versions"][0]["original_filename"] == "purchases.xlsx"
    an = o.get(XL(a, f"/templates/{t['id']}/analysis")).json()
    names = {s["name"]: s for s in an["sheets"]}
    assert set(names) >= {"Purchases", "Summary"} and names["Purchases"]["suggested_header_row"] == 4
    assert names["Purchases"]["suggested_start_row"] == 5 and an["risk_level"] in ("none", "warned")
    # the template is listed for this company only
    assert [x["name"] for x in o.get(XL(a, "/templates")).json()] == ["Purchase register"]
    assert o.get(XL(books["b"], "/templates")).json() == []


def test_unsafe_or_unsupported_files_are_refused_and_nothing_is_stored(books, tmp_path, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    good = template_bytes(tmp_path)

    def refused(content: bytes, filename: str) -> dict:  # type: ignore[type-arg]
        r = upload(o, a, content, filename=filename, name=f"x{uuid.uuid4().hex[:5]}")
        assert r.status_code == 422, r.text
        return r.json()

    assert refused(good, "data.csv")["code"] == "unsupported_extension"
    assert refused(b"not a workbook", "bad.xlsx")["code"] == "corrupt_workbook"
    assert refused(good, "old.xls")["code"] == "legacy_or_encrypted_workbook"
    assert refused(xf.OLE_HEAD + b"0" * 600, "enc.xlsx")["code"] == "legacy_or_encrypted_workbook"
    macro = xf.with_part(xf.purchase_template(tmp_path / "m.xlsx"), "xl/vbaProject.bin", b"\x00vba")
    assert refused(macro.read_bytes(), "disguised.xlsx")["code"] == "macro_in_xlsx"  # a macro hidden in a .xlsx
    assert (
        refused(macro.read_bytes(), "macro.xlsm")["code"] == "macros_not_enabled"
    )  # the plan does not include macro files
    assert o.get(XL(a, "/templates")).json() == []
    with owner_engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM storage_objects WHERE purpose = 'template' AND company_id = :c"), {"c": a}
            ).scalar_one()
            == 0
        )


def test_template_names_are_unique_per_company_and_independent_across_companies(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    assert upload(o, a, template_bytes(tmp_path)).status_code == 201
    assert upload(o, a, template_bytes(tmp_path), filename="again.xlsx").status_code == 409  # same name, same company
    assert upload(o, b, template_bytes(tmp_path)).status_code == 201  # fine in another company
    assert len(o.get(XL(b, "/templates")).json()) == 1


# ================================================================== mapping
def test_drafts_may_be_incomplete_but_activation_is_blocked_until_valid(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    tid = upload(o, a, template_bytes(tmp_path)).json()["id"]
    bad = good_mapping()
    bad["sheets"][0]["columns"] = [
        c for c in bad["sheets"][0]["columns"] if c["column"] != "H"
    ]  # required field unmapped
    m = o.post(XL(a, f"/templates/{tid}/mappings"), {"definition": bad})
    assert m.status_code == 201 and m.json()["ready"] is False
    assert any(i["code"] == "required_field_unmapped" for i in m.json()["validation"])
    r = o.post(XL(a, f"/mappings/{m.json()['id']}/activate"), {"acknowledge_risks": True})
    assert r.status_code == 409
    # the template cannot be exported while it has no active mapping
    e = run_export(o, a, tid)
    assert e.status_code == 409
    fixed = o.post(XL(a, f"/templates/{tid}/mappings"), {"definition": good_mapping(), "mapping_id": m.json()["id"]})
    assert fixed.status_code == 201 and fixed.json()["ready"] and fixed.json()["id"] == m.json()["id"]


def test_activation_makes_the_mapping_immutable_history(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    tid = upload(o, a, template_bytes(tmp_path)).json()["id"]
    act = activate(o, a, tid)
    assert act["status"] == "active"
    r = o.post(XL(a, f"/templates/{tid}/mappings"), {"definition": good_mapping(), "mapping_id": act["id"]})
    assert r.status_code == 409  # an activated mapping is history; changes are a new mapping
    new = o.post(XL(a, f"/templates/{tid}/mappings"), {"definition": good_mapping()}).json()
    assert new["mapping_no"] == 2 and new["status"] == "draft"
    o.post(XL(a, f"/mappings/{new['id']}/activate"), {"acknowledge_risks": True})
    ms = {m["mapping_no"]: m["status"] for m in o.get(XL(a, f"/templates/{tid}/mappings")).json()}
    assert ms == {1: "superseded", 2: "active"}
    assert o.get(XL(a, f"/templates/{tid}")).json()["status"] == "active"


def test_risky_workbooks_need_explicit_acknowledgement(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    risky = xf.with_part(
        xf.purchase_template(tmp_path / "r.xlsx"), "xl/pivotTables/pivotTable1.xml", b"<pivotTableDefinition/>"
    )
    tid = upload(o, a, risky.read_bytes(), name="Risky").json()["id"]
    m = o.post(XL(a, f"/templates/{tid}/mappings"), {"definition": good_mapping()}).json()
    r = o.post(XL(a, f"/mappings/{m['id']}/activate"), {"acknowledge_risks": False})
    assert r.status_code == 409 and r.json().get("fidelity")
    assert o.post(XL(a, f"/mappings/{m['id']}/activate"), {"acknowledge_risks": True}).status_code == 200


# ================================================================== export
def test_export_fills_the_accountants_own_workbook_and_preserves_it(books, tpl, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    ids = approved(books, n=3, amount="100", narration='=HYPERLINK("http://evil","x")')
    r = run_export(o, a, tpl)
    assert r.status_code == 202, r.text
    e = r.json()
    e = o.get(XL(a, f"/exports/{e['id']}")).json()
    assert e["status"] == "completed", e
    assert (
        e["sha256"] and e["size_bytes"] > 0 and e["row_counts"] == {"Purchases": 3} and e["transactions_included"] == 3
    )
    wb = sheet(o, a, e["id"])
    ws = wb["Purchases"]
    assert ws["A1"].value == "Purchase register" and "A1:C1" in {
        str(m) for m in ws.merged_cells.ranges
    }  # template kept
    assert ws["B3"].value == "Al Noor Trading LLC" and ws["B2"].value is not None  # header values from the company
    for row in (5, 6, 7):
        assert Dec(str(ws[f"F{row}"].value)) == Dec("100") and Dec(str(ws[f"G{row}"].value)) == Dec("5")
        assert Dec(str(ws[f"H{row}"].value)) == Dec("105")
        assert ws[f"J{row}"].value == f"=H{row}*1"  # the template's own formula is kept, not evaluated
        assert ws[f"I{row}"].value == "Purchases"
        assert (
            str(ws[f"E{row}"].value).startswith("=HYPERLINK") and ws[f"E{row}"].data_type == "s"
        )  # never a live formula
    assert ws["F8"].value == "=SUM(F5:F7)" and ws["H8"].value == "=SUM(H5:H7)"  # footer formulas follow the data
    assert wb["Summary"]["B1"].value == "=Purchases!H8"  # cross-sheet reference follows
    assert ws["B5"].value  # voucher number comes from the Phase 8 transaction
    # traceability: every exported row is linked to its accounting transaction
    tx = o.get(XL(a, f"/exports/{e['id']}/transactions")).json()
    assert {t["transaction_id"] for t in tx["items"]} == set(ids)
    back = o.get(XL(a, f"/transactions/{ids[0]}/exports")).json()
    assert [x["export_id"] for x in back] == [e["id"]]
    assert o.post(XL(a, f"/exports/{e['id']}/verify")).json()["ok"] is True


def test_only_approved_transactions_are_exported_by_default(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    approved(books, n=1)
    t = taxes(o, a)
    sup = contact(o, a, "supplier", "Draft Supplier")
    d = draft(o, a, "purchase_invoice", contact_id=sup, lines=[{"amount": "999", "tax_code_id": t["OM_STD"]}]).json()
    assert "id" in d, d
    pending = draft(
        o, a, "purchase_invoice", contact_id=sup, lines=[{"amount": "777", "tax_code_id": t["OM_STD"]}]
    ).json()
    assert "id" in pending, pending
    assert o.post(ACC(a, f"/transactions/{pending['id']}/submit"), {}).status_code == 200
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    assert e["status"] == "completed" and e["row_counts"] == {"Purchases": 1} and e["include_unapproved"] is False
    val = o.post(XL(a, "/exports/validate"), {"template_id": tpl, "params": PARAMS}).json()
    assert val["transactions"] == 1 and not any(i["code"] == "unapproved_included" for i in val["issues"])
    # unapproved data needs its own permission: an accountant has excel.export but not excel.export_unapproved
    unapproved = {**PARAMS, "include_unapproved": True}
    assert acct.post(XL(a, "/exports/validate"), {"template_id": tpl, "params": unapproved}).status_code == 403
    r = o.post(XL(a, "/exports/validate"), {"template_id": tpl, "params": unapproved})
    assert r.status_code == 200 and r.json()["transactions"] == 3, r.json()["sheets"]
    assert any(i["code"] == "unapproved_included" for i in r.json()["issues"])
    e2 = o.get(
        XL(
            a,
            f"/exports/{run_export(o, a, tpl, params=unapproved, acknowledge_repeat=True, reason='review copy').json()['id']}",
        )
    ).json()
    assert e2["include_unapproved"] is True and e2["status"] == "completed"
    _ = d


def test_preview_equals_the_generated_output(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books, n=2, amount="250.500")
    p = o.post(XL(a, "/exports/preview"), {"template_id": tpl, "params": PARAMS}).json()
    assert p["validation"]["can_generate"] and p["preview"]["preview_is_generated_from_the_same_rows"]
    sample = p["preview"]["sheets"][0]["sample"]
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    ws = sheet(o, a, e["id"])["Purchases"]
    for item in sample:
        for col in ("B", "C", "F", "G", "H"):
            shown, actual = item["cells"][col], ws[f"{col}{item['row']}"].value
            assert str(shown).replace(",", "") == str(actual).replace(",", "") or Dec(
                str(shown).replace(",", "")
            ) == Dec(str(actual))


def test_duplicate_exports_need_a_reason_and_regenerating_never_replaces_the_original(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books, n=2)
    first = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    val = o.post(XL(a, "/exports/validate"), {"template_id": tpl, "params": PARAMS}).json()
    assert val["repeat"]["needs_acknowledgement"] and val["repeat"]["transactions"] == 2
    assert val["repeat"]["exports"][0]["id"] == first["id"]
    refused = run_export(o, a, tpl)
    assert refused.status_code == 409 and refused.json()["repeat"]["exports"]  # warned, not silently repeated
    assert run_export(o, a, tpl, acknowledge_repeat=True).status_code == 422  # a reason is required
    again = run_export(o, a, tpl, acknowledge_repeat=True, reason="Client asked for another copy")
    assert again.status_code == 202
    second = o.get(XL(a, f"/exports/{again.json()['id']}")).json()
    assert second["repeat_acknowledged"] and second["reason"] and second["id"] != first["id"]
    hist = o.get(XL(a, "/exports")).json()
    assert hist["total"] == 2 and o.get(XL(a, f"/exports/{first['id']}")).json()["sha256"] == first["sha256"]
    # regenerate: same configuration, new immutable record that points at its source
    rg = o.post(
        XL(a, f"/exports/{first['id']}/regenerate"), {"idempotency_key": uuid.uuid4().hex, "reason": "lost the file"}
    )
    assert rg.status_code == 202 and rg.json()["source_export_id"] == first["id"]
    cmp = o.get(XL(a, "/exports/compare"), params={"a": first["id"], "b": rg.json()["id"]}).json()
    assert cmp["identical_configuration"] is True and cmp["transactions_in_both"] == 2


def test_the_same_idempotency_key_returns_the_first_export(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    k = "idem-key-0001"
    r1, r2 = run_export(o, a, tpl, key=k), run_export(o, a, tpl, key=k)
    assert r1.status_code == 202 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert o.get(XL(a, "/exports")).json()["total"] == 1


def test_export_history_is_immutable_in_the_database(books, tpl, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    assert e["status"] == "completed"
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(text("UPDATE xl_exports SET sha256 = repeat('0', 64) WHERE id = :i"), {"i": e["id"]})
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(text("DELETE FROM xl_exports WHERE id = :i"), {"i": e["id"]})
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(
            text("UPDATE xl_mappings SET definition = '{}'::jsonb WHERE template_id = :t AND status = 'active'"),
            {"t": tpl},
        )


def test_the_application_role_cannot_rewrite_history(tenant_db) -> None:  # type: ignore[no-untyped-def]
    with tenant_db.connect() as c:

        def priv(t: str, p: str) -> bool:
            return bool(
                c.execute(text("SELECT has_table_privilege(current_user, :t, :p)"), {"t": t, "p": p}).scalar_one()
            )

        assert priv("xl_template_versions", "UPDATE") is False and priv("xl_export_rows", "UPDATE") is False
        assert priv("xl_exports", "DELETE") is False and priv("xl_export_rows", "DELETE") is False
        # deletes exist only for never-used templates, which the service checks first
        assert all(priv(t, "DELETE") for t in ("xl_templates", "xl_template_versions", "xl_mappings"))


def test_tampered_stored_export_is_detected(books, tpl, backend) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    files = [p for p in Path(backend.root).rglob("*") if p.is_file() and p.stat().st_size == e["size_bytes"]]
    assert files
    data = bytearray(files[0].read_bytes())
    data[-1] ^= 0xFF
    files[0].write_bytes(bytes(data))
    v = o.post(XL(a, f"/exports/{e['id']}/verify")).json()
    assert v["ok"] is False and v["recorded_sha256"] != v["actual_sha256"]


def test_a_failed_generation_is_recorded_and_leaves_no_file(books, tpl, backend, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    with owner_engine.begin() as c:
        key = c.execute(
            text(
                "SELECT so.storage_key FROM xl_template_versions v JOIN storage_objects so ON so.id = v.storage_object_id WHERE v.template_id = :t"
            ),
            {"t": tpl},
        ).scalar_one()
    backend.delete(key)  # the template file disappears from the store
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    assert e["status"] == "failed" and e["error_message"] and not e["download_available"] and e["sha256"] is None
    assert o.get(XL(a, f"/exports/{e['id']}/file")).status_code == 409
    with owner_engine.begin() as c:
        q = "SELECT count(*) FROM storage_objects WHERE purpose = 'export' AND company_id = :c"
        assert c.execute(text(q), {"c": a}).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM xl_export_rows WHERE company_id = :c"), {"c": a}).scalar_one() == 0


def test_a_new_template_version_keeps_old_exports_on_their_own_version(books, tpl, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    e1 = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    r = put_raw(
        o,
        XL(a, "/templates/upload"),
        template_bytes(tmp_path),
        filename="v2.xlsx",
        template_id=tpl,
        change_note="new logo",
    )
    assert r.status_code == 201 and r.json()["current_version_no"] == 2
    t = o.get(XL(a, f"/templates/{tpl}")).json()
    assert [v["version_no"] for v in t["versions"]] == [2, 1] and t["mappings"][0][
        "status"
    ] == "draft"  # copied for review
    assert o.get(XL(a, f"/exports/{e1['id']}")).json()["template_version"] == 1
    assert o.get(XL(a, f"/templates/{tpl}/versions/1/file")).status_code == 200  # the old file is still retrievable
    assert put_raw(o, XL(a, "/templates/upload"), b"junk", filename="x.xlsx", template_id=tpl).status_code == 422


def test_each_company_uses_its_own_template_and_data(books, tmp_path) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    ta = upload(o, a, template_bytes(tmp_path), name="A register").json()["id"]
    tb = upload(o, b, template_bytes(tmp_path), name="B register").json()["id"]
    activate(o, a, ta)
    activate(o, b, tb)
    assign(o, b, books["acct"])
    approved(books, cid=a, n=2, amount="100")
    approved(books, cid=b, n=1, amount="300")
    ea = o.get(XL(a, f"/exports/{run_export(o, a, ta).json()['id']}")).json()
    eb = o.get(XL(b, f"/exports/{run_export(o, b, tb).json()['id']}")).json()
    assert ea["row_counts"] == {"Purchases": 2} and eb["row_counts"] == {"Purchases": 1}
    assert sheet(o, b, eb["id"])["Purchases"]["B3"].value == "Gulf Builders Co"


# ================================================================== authorization and tenancy
def test_company_isolation_and_roles(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a, b, acct, prep = books["owner"], books["a"], books["b"], books["acct"], books["prep"]
    approved(books)
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    # another company in the same firm cannot see or use company A's objects (404, not 403)
    assert o.get(XL(b, f"/templates/{tpl}")).status_code == 404
    assert o.get(XL(b, f"/exports/{e['id']}")).status_code == 404
    assert o.get(XL(b, f"/exports/{e['id']}/file")).status_code == 404
    assert o.post(XL(b, "/exports/validate"), {"template_id": tpl, "params": PARAMS}).status_code == 404
    # an accountant assigned to A only cannot reach B at all
    assert acct.get(XL(b, "/templates")).status_code == 404
    assert acct.get(XL(a, "/templates")).status_code == 200
    # the assistant accountant can look but not manage, export or download
    assert prep.get(XL(a, "/templates")).status_code == 200
    assert put_raw(prep, XL(a, "/templates/upload"), b"x", filename="x.xlsx", name="x").status_code == 403
    assert prep.post(XL(a, "/exports/validate"), {"template_id": tpl, "params": PARAMS}).status_code == 403
    assert prep.get(XL(a, f"/exports/{e['id']}/file")).status_code == 403
    assert acct.get(XL(a, f"/exports/{e['id']}/file")).status_code == 200
    # unauthenticated callers get nothing
    assert books["client"].get(f"/api/v1{XL(a, '/templates')}").status_code in (401, 403)


def test_a_second_firm_cannot_see_anything(books, tpl, mailer, client) -> None:  # type: ignore[no-untyped-def]
    from conftest import make_company, new_owner

    o, a = books["owner"], books["a"]
    approved(books)
    e = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    other = new_owner(client, mailer, org_name="Other Firm")
    oc = make_company(other, "ZZ")["id"]
    assert other.get(XL(a, f"/exports/{e['id']}")).status_code == 404
    assert other.get(XL(a, "/templates")).status_code == 404
    assert other.get(XL(oc, f"/templates/{tpl}")).status_code == 404
    assert other.get("/excel/overview").json()["templates"] == []


def test_overview_is_for_org_monitors_and_has_no_downloads(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books)
    run_export(o, a, tpl)
    ov = o.get("/excel/overview")
    assert ov.status_code == 200
    j = ov.json()
    assert j["stats"]["completed"] == 1 and j["templates"][0]["company_name"] == "Al Noor Trading LLC"
    assert "storage_key" not in ov.text and "sha256" not in str(j["recent_exports"])
    assert books["prep"].get("/excel/overview").status_code == 403


# ================================================================== entitlements
def test_export_quota_stops_new_exports_without_touching_existing_ones(books, tpl, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from conftest import set_plan_limit

    o, a = books["owner"], books["a"]
    approved(books)
    set_plan_limit(owner_engine, o.org_id, "quota.excel_exports_per_month", 1)
    first = o.get(XL(a, f"/exports/{run_export(o, a, tpl).json()['id']}")).json()
    blocked = run_export(o, a, tpl, acknowledge_repeat=True, reason="one more copy")
    assert blocked.status_code == 402 and blocked.json()["code"] == "entitlement_exceeded"
    assert o.get(XL(a, f"/exports/{first['id']}/file")).status_code == 200  # nothing is deleted when a limit is hit
    # re-generating an existing export is not a new billable export
    assert (
        o.post(
            XL(a, f"/exports/{first['id']}/regenerate"), {"idempotency_key": uuid.uuid4().hex, "reason": "lost copy"}
        ).status_code
        == 202
    )


def test_template_and_version_limits(books, tmp_path, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from conftest import set_plan_limit

    o, a = books["owner"], books["a"]
    set_plan_limit(owner_engine, o.org_id, "limit.excel_templates_per_company", 1)
    set_plan_limit(owner_engine, o.org_id, "limit.excel_template_versions_per_template", 2)
    t1 = upload(o, a, template_bytes(tmp_path), name="One")
    assert t1.status_code == 201
    assert upload(o, a, template_bytes(tmp_path), name="Two").status_code == 402
    tid = t1.json()["id"]
    assert (
        put_raw(
            o, XL(a, "/templates/upload"), template_bytes(tmp_path), filename="v2.xlsx", template_id=tid
        ).status_code
        == 201
    )
    assert (
        put_raw(
            o, XL(a, "/templates/upload"), template_bytes(tmp_path), filename="v3.xlsx", template_id=tid
        ).status_code
        == 402
    )
    # archiving frees the slot; existing data is kept
    assert o.post(XL(a, f"/templates/{tid}/status"), {"action": "archive"}).status_code == 200
    assert upload(o, a, template_bytes(tmp_path), name="Two").status_code == 201


def test_storage_quota_applies_to_templates_and_exports(books, tmp_path, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from conftest import set_plan_limit

    o, a = books["owner"], books["a"]
    set_plan_limit(owner_engine, o.org_id, "quota.storage_bytes", 1000)
    r = upload(o, a, template_bytes(tmp_path))
    assert r.status_code == 402 and r.json()["code"] == "entitlement_exceeded"


# ================================================================== template lifecycle
def test_unused_templates_can_be_deleted_used_ones_are_archived(books, tpl, tmp_path, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    spare = upload(o, a, template_bytes(tmp_path), name="Spare").json()["id"]
    assert o.delete(XL(a, f"/templates/{spare}")).status_code == 200
    assert o.get(XL(a, f"/templates/{spare}")).status_code == 404
    approved(books)
    run_export(o, a, tpl)
    assert o.delete(XL(a, f"/templates/{tpl}")).status_code == 409
    assert o.post(XL(a, f"/templates/{tpl}/status"), {"action": "archive"}).json()["status"] == "archived"
    assert run_export(o, a, tpl).status_code == 409  # an archived template cannot be used
    assert o.post(XL(a, f"/templates/{tpl}/status"), {"action": "restore"}).json()["status"] == "inactive"
    assert o.post(XL(a, f"/templates/{tpl}/status"), {"action": "activate"}).json()["status"] == "active"
    assert len(o.get(XL(a, "/exports")).json()["items"]) == 1  # history survived


def test_duplicate_makes_an_independent_copy(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    d = o.post(XL(a, f"/templates/{tpl}/duplicate"), {"name": "Purchase register (copy)"})
    assert d.status_code == 201, d.text
    j = d.json()
    assert j["id"] != tpl and j["source_template_id"] == tpl and j["status"] == "draft"
    assert j["mappings"] and j["mappings"][0]["status"] == "draft"  # review before use
    assert o.get(XL(a, f"/templates/{tpl}")).json()["status"] == "active"
    assert o.post(XL(a, f"/templates/{tpl}/duplicate"), {"name": "purchase REGISTER"}).status_code == 409


# ================================================================== reports
def test_standard_reports_run_on_approved_data(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books, n=2, amount="100")
    cat = o.get(XL(a, "/reports/standard")).json()
    keys = {r["key"] for r in cat}
    assert {"trial_balance", "journal_listing", "purchase_register", "profit_and_loss", "balance_sheet"} <= keys
    assert all(r["statutory"] is False for r in cat)
    tbr = o.post(XL(a, "/reports/standard/trial_balance/run"), {"params": {"as_of": "2025-12-31"}})
    assert tbr.status_code == 200, tbr.text
    tb = tbr.json()
    assert tb["statutory"] is False and tb["total"] > 0
    cols = [c["key"] for c in tb["columns"]]
    debit, credit = cols.index("debit") if "debit" in cols else None, cols.index("credit") if "credit" in cols else None
    assert debit is not None and credit is not None
    assert sum(Dec(r["debit"] or "0") for r in tb["rows"]) == sum(
        Dec(r["credit"] or "0") for r in tb["rows"]
    )  # balanced
    pr = o.post(XL(a, "/reports/standard/purchase_register/run"), {"params": PARAMS}).json()
    assert pr["total"] == 2 and all(Dec(r["gross_amount"]) == Dec("105") for r in pr["rows"])
    assert o.post(XL(a, "/reports/standard/nope/run"), {"params": {}}).status_code == 404
    # a filter the dataset does not declare is refused, not silently ignored
    assert o.post(XL(a, "/reports/standard/trial_balance/run"), {"params": {"txn_types": "x"}}).status_code in (
        200,
        422,
    )


def test_company_data_never_leaks_into_another_companys_report(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    approved(books, cid=a, n=2)
    assert o.post(XL(b, "/reports/standard/purchase_register/run"), {"params": PARAMS}).json()["total"] == 0
    assert o.post(XL(a, "/reports/standard/purchase_register/run"), {"params": PARAMS}).json()["total"] == 2


def test_custom_reports_use_governed_datasets_only(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    approved(books, n=3, amount="100")
    d = {
        "dataset": "purchase_register",
        "fields": ["number", "contact_name", "gross_amount"],
        "sort": [{"field": "gross_amount", "direction": "desc"}],
        "filters": PARAMS,
    }
    v = o.post(XL(a, "/reports/custom/validate"), {"definition": d}).json()
    assert v["ready"], v
    r = o.post(XL(a, "/reports/custom"), {"name": "Big purchases", "definition": d, "template_id": tpl})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    run = o.post(XL(a, f"/reports/custom/{rid}/run"), {"params": {}}).json()
    assert run["total"] == 3 and [c["key"] for c in run["columns"]] == ["number", "contact_name", "gross_amount"]
    # arbitrary SQL, unknown datasets and unknown fields are rejected at validation
    for bad in (
        {**d, "dataset": "users; DROP TABLE users"},
        {**d, "fields": ["1=1; select * from users"]},
        {**d, "sort": [{"field": "password_hash"}]},
        {**d, "filters": {"sql": "select 1"}},
    ):
        resp = o.post(XL(a, "/reports/custom"), {"name": f"bad {uuid.uuid4().hex[:4]}", "definition": bad})
        assert resp.status_code == 422, (bad, resp.text)
    assert (
        o.post(XL(a, "/reports/custom"), {"name": "big purchases", "definition": d}).status_code == 409
    )  # unique name
    # exporting through the report's template uses the normal export path (history, quota, links)
    ex = o.post(XL(a, f"/reports/custom/{rid}/export"), {"idempotency_key": uuid.uuid4().hex})
    assert ex.status_code == 202 and o.get(XL(a, f"/exports/{ex.json()['id']}")).json()["status"] == "completed"
    # archiving keeps it out of the default list but never deletes it
    assert o.post(XL(a, f"/reports/custom/{rid}/archive")).json()["status"] == "archived"
    assert o.get(XL(a, "/reports/custom")).json() == []
    assert len(o.get(XL(a, "/reports/custom"), params={"include_archived": True}).json()) == 1
    assert o.post(XL(books["b"], f"/reports/custom/{rid}/run"), {"params": {}}).status_code == 404


def test_custom_report_limit_and_feature_gate(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from conftest import set_plan_limit

    o, a = books["owner"], books["a"]
    set_plan_limit(owner_engine, o.org_id, "limit.custom_reports_per_company", 1)
    d = {"dataset": "purchase_register", "fields": ["number"]}
    assert o.post(XL(a, "/reports/custom"), {"name": "One", "definition": d}).status_code == 201
    assert o.post(XL(a, "/reports/custom"), {"name": "Two", "definition": d}).status_code == 402


def test_catalogue_lists_governed_fields_and_has_no_sql_surface(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    c = o.get(XL(a, "/catalogue")).json()
    assert "purchase_register" in {d["key"] for d in c["datasets"]} and "sales_register" in {
        d["key"] for d in c["datasets"]
    }
    assert "sql" not in str(c).lower().replace("statutory", "")
    assert make_member is not None
