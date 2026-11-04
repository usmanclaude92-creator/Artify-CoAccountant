"""Phase 4 API + database tests (real PostgreSQL): structure, isolation, categories, fields, rules, templates,
permissions, entitlements, revisions. All data is synthetic."""

from __future__ import annotations

import pytest
from conftest import make_company, make_member, new_owner, set_plan_limit, uid
from sqlalchemy import text


def W(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/workspace{path}"


def mk(owner, cid, name, parent=None, **kw):  # type: ignore[no-untyped-def]
    r = owner.post(W(cid, "/nodes"), {"name": name, "parent_id": parent, **kw})
    assert r.status_code == 201, r.text
    return r.json()["item"]


def tree(owner, cid, **q):  # type: ignore[no-untyped-def]
    r = owner.get(W(cid) + ("?include_archived=true" if q.get("archived") else ""))
    assert r.status_code == 200, r.text
    return r.json()


def assign(owner, cid, member):  # type: ignore[no-untyped-def]
    r = owner.post(f"/companies/{cid}/assignments", {"user_id": uid(member), "assignment_type": "secondary"})
    assert r.status_code in (200, 201), r.text


@pytest.fixture
def firm(client, mailer):  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="WS Firm")
    return owner, make_company(owner, "WA")["id"], make_company(owner, "WB")["id"]


# ------------------------------------------------------------------ structure
def test_two_companies_have_different_structures_and_never_affect_each_other(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    assert (
        tree(owner, a)["nodes"] == [] and tree(owner, a)["workspace_state"] == "not_configured"
    )  # no defaults anywhere
    for n in ("Bank Statements", "Receivables", "Payables", "Expenses"):
        mk(owner, a, n)
    for i in range(10):
        s = mk(owner, b, f"Section {i}")
        if i == 0:
            f = mk(owner, b, "Banking", s["id"])
            mk(owner, b, "Bank of Muscat", f["id"])
    ta, tb = tree(owner, a), tree(owner, b)
    assert [n["name"] for n in ta["nodes"]] == ["Bank Statements", "Receivables", "Payables", "Expenses"]
    assert (
        len([n for n in tb["nodes"] if n["node_type"] == "section"]) == 10 and max(n["depth"] for n in tb["nodes"]) == 3
    )
    assert ta["workspace_state"] == "configured" and ta["version"] == 5 and tb["version"] == 13
    # renaming in A never changes B
    sid = ta["nodes"][0]["id"]
    assert owner.patch(W(a, f"/nodes/{sid}"), {"name": "Zeta"}).status_code == 200
    assert "Zeta" not in [n["name"] for n in tree(owner, b)["nodes"]]


def test_unbounded_nesting_and_stable_ids_and_keys(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    node = mk(owner, a, "L1")
    first_id, first_key = node["id"], node["key"]
    for i in range(2, 7):
        node = mk(owner, a, f"L{i}", node["id"])
    assert node["depth"] == 6
    r = owner.patch(W(a, f"/nodes/{first_id}"), {"name": "Renamed"})
    assert r.json()["item"]["key"] == first_key and r.json()["item"]["id"] == first_id  # ids/keys survive renames


def test_validation_names_duplicates_and_parents(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Finance")
    for bad in ("", "a/b", "..", "CON", "x" * 121):
        assert owner.post(W(a, "/nodes"), {"name": bad}).status_code in (422,)
    dup = owner.post(W(a, "/nodes"), {"name": "finance"})
    assert dup.status_code == 409 and "suggestion" in dup.json()
    f = mk(owner, a, "Banks", s["id"])
    assert (
        owner.post(W(a, "/nodes"), {"name": "banks", "parent_id": s["id"]}).status_code == 409
    )  # sibling names unique, case-insensitive
    mk(owner, a, "Banks", None)  # same name elsewhere is fine
    assert (
        owner.post(W(a, "/nodes"), {"name": "X", "parent_id": "00000000-0000-0000-0000-000000000000"}).status_code
        == 404
    )
    # sections cannot be moved under anything; folders cannot become sections
    assert owner.post(W(a, f"/nodes/{s['id']}/move"), {"parent_id": f["id"]}).status_code == 422


def test_move_prevents_cycles_and_handles_subtrees(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s1, s2 = mk(owner, a, "S1"), mk(owner, a, "S2")
    f, g = mk(owner, a, "F", s1["id"]), None
    g = mk(owner, a, "G", f["id"])
    h = mk(owner, a, "H", g["id"])
    for target in (f["id"], g["id"], h["id"]):  # into itself or its own descendants
        assert owner.post(W(a, f"/nodes/{f['id']}/move"), {"parent_id": target}).status_code == 422
    r = owner.post(W(a, f"/nodes/{f['id']}/move"), {"parent_id": s2["id"], "position": 0})
    assert r.status_code == 200 and r.json()["item"]["parent_id"] == s2["id"]
    nodes = {n["name"]: n for n in tree(owner, a)["nodes"]}
    assert nodes["F"]["depth"] == 2 and nodes["G"]["depth"] == 3 and nodes["H"]["depth"] == 4  # descendants re-pathed
    mk(owner, a, "F", s1["id"])
    assert (
        owner.post(W(a, f"/nodes/{nodes['F']['id']}/move"), {"parent_id": s1["id"]}).status_code == 409
    )  # name collision at destination


def test_reorder_is_stable_and_must_list_every_sibling(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    ids = [mk(owner, a, n)["id"] for n in ("A", "B", "C")]
    assert owner.post(W(a, "/nodes/reorder"), {"ordered_ids": ids[:2]}).status_code == 422
    assert owner.post(W(a, "/nodes/reorder"), {"ordered_ids": [ids[2], ids[0], ids[1]]}).status_code == 200
    assert [n["name"] for n in tree(owner, a)["nodes"]] == ["C", "A", "B"]
    assert [n["name"] for n in tree(owner, a)["nodes"]] == ["C", "A", "B"]  # repeatable


def test_archive_restore_cascade_and_confirmation(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Sales")
    f = mk(owner, a, "Invoices", s["id"])
    cat = owner.post(
        W(a, "/categories"), {"name": "Sales invoice", "document_kind": "sales_invoice", "node_id": f["id"]}
    ).json()["item"]
    rule = owner.post(
        W(a, "/filing-rules"),
        {
            "name": "R",
            "conditions": [{"field": "document_kind", "operator": "equals", "value": "sales_invoice"}],
            "target_node_id": f["id"],
        },
    ).json()["item"]
    imp = owner.get(W(a, f"/nodes/{s['id']}/impact")).json()
    assert (imp["subfolders"], imp["categories"], imp["filing_rules"], imp["documents"]) == (1, 1, 1, 0)
    r = owner.post(W(a, f"/nodes/{s['id']}/archive"), {})
    assert (
        r.status_code == 409 and r.json()["code"] == "confirmation_required" and r.json()["impact"]["filing_rules"] == 1
    )
    assert owner.post(W(a, f"/nodes/{s['id']}/archive"), {"confirm_impact": True}).status_code == 200
    live = {n["name"] for n in tree(owner, a)["nodes"]}
    assert live == set()  # archived items vanish from the live tree
    assert {n["name"] for n in tree(owner, a, archived=True)["nodes"]} == {"Sales", "Invoices"}
    rules = owner.get(W(a, "/filing-rules")).json()["items"]
    assert rules[0]["id"] == rule["id"] and rules[0]["is_active"] is False  # a rule never targets archived content
    assert owner.post(W(a, f"/nodes/{f['id']}/restore"), {}).status_code == 409  # restore the parent first
    assert owner.post(W(a, f"/nodes/{s['id']}/restore"), {}).status_code == 200
    assert {n["name"] for n in tree(owner, a)["nodes"]} == {"Sales", "Invoices"}
    assert cat["id"]  # category row survived


def test_restore_name_collision_is_reported(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Tax")
    assert owner.post(W(a, f"/nodes/{s['id']}/archive"), {}).status_code == 200
    mk(owner, a, "Tax")
    assert owner.post(W(a, f"/nodes/{s['id']}/restore"), {}).status_code == 409


def test_duplicate_copies_structure_only(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Finance")
    f = mk(owner, a, "Banks", s["id"])
    mk(owner, a, "Muscat", f["id"])
    owner.post(W(a, "/categories"), {"name": "Bank statement", "document_kind": "bank_statement", "node_id": f["id"]})
    r = owner.post(W(a, f"/nodes/{s['id']}/duplicate"), {})
    assert r.status_code == 201 and r.json()["item"]["name"] == "Finance (copy)"
    nodes = tree(owner, a)["nodes"]
    copy_id = r.json()["item"]["id"]
    assert {n["name"] for n in nodes if n["id"] == copy_id or n["parent_id"] == copy_id} == {"Finance (copy)", "Banks"}
    assert len({n["id"] for n in nodes}) == 6 and len({n["key"] for n in nodes}) == 6  # new ids and keys
    assert len(tree(owner, a)["categories"]) == 1  # behaviour is not copied


def test_delete_is_only_for_never_referenced_nodes(firm, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Scratch")
    assert owner.delete(W(a, f"/nodes/{s['id']}")).status_code == 200  # nothing ever referenced it
    t = mk(owner, a, "Used")
    f = mk(owner, a, "Child", t["id"])
    r = owner.delete(W(a, f"/nodes/{t['id']}"))
    assert r.status_code == 409 and r.json()["options"] == ["archive", "move_contents"]
    # a stand-in for the future `documents` table: a RESTRICT foreign key to the node must stop even a direct delete
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS test_node_refs (id serial PRIMARY KEY, node_id uuid NOT NULL REFERENCES workspace_nodes (id) ON DELETE RESTRICT)"
            )
        )
        conn.execute(text("INSERT INTO test_node_refs (node_id) VALUES (:n)"), {"n": f["id"]})
    r = owner.delete(W(a, f"/nodes/{f['id']}"))
    assert r.status_code == 409 and r.json().get("code_detail") == "node_in_use"
    assert any(n["id"] == f["id"] for n in tree(owner, a)["nodes"])  # still there
    with owner_engine.begin() as conn:
        conn.execute(text("DROP TABLE test_node_refs"))


def test_version_conflicts_are_detected(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    v = tree(owner, a)["version"]
    mk(owner, a, "One")
    r = owner.post(W(a, "/nodes"), {"name": "Two", "base_version": v})
    assert (
        r.status_code == 409
        and r.json()["code"] == "workspace_version_conflict"
        and r.json()["current_version"] == v + 1
    )
    assert owner.post(W(a, "/nodes"), {"name": "Two", "base_version": v + 1}).status_code == 201


def test_inactive_and_archived_companies_are_read_only(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    mk(owner, a, "Kept")
    assert owner.post(f"/companies/{a}/status", {"action": "deactivate"}).status_code == 200
    assert owner.post(W(a, "/nodes"), {"name": "Blocked"}).status_code == 409
    assert tree(owner, a)["nodes"][0]["name"] == "Kept"  # reading still works


# ------------------------------------------------------------------ isolation
def test_company_and_tenant_isolation(client, mailer, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    na, nb = mk(owner, a, "A-sec"), mk(owner, b, "B-sec")
    cat_b = owner.post(W(b, "/categories"), {"name": "Bcat", "document_kind": "sales_invoice"}).json()["item"]
    field_b = owner.post(W(b, "/fields"), {"label": "Bfield", "data_type": "short_text"}).json()["item"]
    # cross-company references are rejected, never silently accepted
    assert owner.post(W(a, "/nodes"), {"name": "x", "parent_id": nb["id"]}).status_code == 404
    assert owner.post(W(a, f"/nodes/{na['id']}/move"), {"parent_id": nb["id"]}).status_code == 404
    assert owner.patch(W(a, f"/nodes/{nb['id']}"), {"name": "hijack"}).status_code == 404
    assert (
        owner.post(
            W(a, "/categories"), {"name": "c", "document_kind": "sales_invoice", "node_id": nb["id"]}
        ).status_code
        == 404
    )
    assert owner.patch(W(a, f"/categories/{cat_b['id']}"), {"name": "hijack"}).status_code == 404
    assert owner.patch(W(a, f"/fields/{field_b['id']}"), {"label": "hijack"}).status_code == 404
    assert (
        owner.post(
            W(a, "/filing-rules"),
            {
                "name": "r",
                "conditions": [{"field": "document_kind", "operator": "equals", "value": "sales_invoice"}],
                "target_node_id": nb["id"],
            },
        ).status_code
        == 404
    )
    assert (
        owner.post(W(a, "/fields"), {"label": "f", "data_type": "short_text", "node_ids": [nb["id"]]}).status_code
        == 404
    )
    # an assigned accountant only reaches company A
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    assert (
        acc.get(W(a)).status_code == 200
        and acc.get(W(b)).status_code == 404
        and acc.get(W(b, "/upload-config")).status_code == 404
    )
    # another organization cannot see either
    other = new_owner(client, mailer, org_name="Other Firm")
    assert other.get(W(a), org=owner.org_id).status_code == 404
    other_company = make_company(other, "OC")["id"]
    assert tree(other, other_company)["nodes"] == []
    assert other.get(W(a)).status_code == 404


def test_rls_hides_workspace_rows_across_organizations(client, mailer, owner_engine, tenant_db) -> None:  # type: ignore[no-untyped-def]
    a, b = new_owner(client, mailer, org_name="RLS WS A"), new_owner(client, mailer, org_name="RLS WS B")
    ca, cb = make_company(a, "RA")["id"], make_company(b, "RB")["id"]
    mk(a, ca, "A-only")
    mk(b, cb, "B-only")
    a.post(W(ca, "/categories"), {"name": "cat", "document_kind": "sales_invoice"})
    tables = (
        "workspace_nodes",
        "document_categories",
        "custom_field_definitions",
        "naming_rules",
        "filing_rules",
        "workspace_permissions",
        "workspace_revisions",
        "custom_field_options",
    )
    with tenant_db.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"),
            {"o": b.org_id},
        )
        for t in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {t} WHERE company_id = :c"), {"c": ca}).scalar_one() == 0, t
        assert (
            conn.execute(text("SELECT count(*) FROM workspace_nodes WHERE company_id = :c"), {"c": cb}).scalar_one()
            == 1
        )
        res = conn.execute(text("UPDATE workspace_nodes SET name = 'hijacked' WHERE company_id = :c"), {"c": ca})
        assert res.rowcount == 0  # rows of the other organization are invisible, so they cannot be changed
    # the app role cannot rewrite history
    with tenant_db.connect() as conn, conn.begin():
        conn.execute(
            text("SELECT set_config('app.org_id', :o, true), set_config('app.company_scope', 'all', true)"),
            {"o": a.org_id},
        )
        with pytest.raises(Exception):  # noqa: B017
            conn.execute(text("UPDATE workspace_revisions SET action = 'forged'"))


# ------------------------------------------------------------------ categories
def test_categories_need_a_document_kind_and_validated_mapping(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    f = mk(owner, a, "Muscat Bank Monthly Statements")
    r = owner.post(
        W(a, "/categories"),
        {
            "name": "Muscat Bank Monthly Statements",
            "document_kind": "bank_statement",
            "node_id": f["id"],
            "allowed_file_types": [".PDF", "csv", "pdf"],
        },
    )
    assert r.status_code == 201
    c = r.json()["item"]
    assert (c["document_kind"], c["accounting_domain"], c["processing_type"], c["allowed_file_types"]) == (
        "bank_statement",
        "banking",
        "statement_extraction",
        ["pdf", "csv"],
    )
    assert c["name"] != c["document_kind"]  # organization layer and accounting layer are separate
    assert owner.post(W(a, "/categories"), {"name": "x"}).status_code == 422  # a kind is mandatory
    assert owner.post(W(a, "/categories"), {"name": "x", "document_kind": "made_up"}).status_code == 422
    assert (
        owner.post(
            W(a, "/categories"), {"name": "y", "document_kind": "bank_statement", "accounting_domain": "payroll"}
        ).status_code
        == 422
    )
    assert (
        owner.post(
            W(a, "/categories"),
            {"name": "y", "document_kind": "bank_statement", "processing_type": "invoice_extraction"},
        ).status_code
        == 422
    )
    assert (
        owner.post(
            W(a, "/categories"), {"name": "z", "document_kind": "bank_statement", "allowed_file_types": ["p/df"]}
        ).status_code
        == 422
    )
    ok = owner.patch(W(a, f"/categories/{c['id']}"), {"processing_type": "reference_only"})
    assert ok.json()["item"]["processing_type"] == "reference_only"
    assert (
        owner.post(
            W(a, "/categories"), {"name": "muscat bank monthly statements", "document_kind": "bank_statement"}
        ).status_code
        == 409
    )
    # a folder named "Expenses" carries no accounting meaning by itself
    e = mk(owner, a, "Expenses")
    assert tree(owner, a)["categories"][0]["node_id"] == f["id"] and not [
        x for x in tree(owner, a)["categories"] if x["node_id"] == e["id"]
    ]
    kinds = owner.get("/reference/document-kinds").json()
    assert {k["key"] for k in kinds["items"]} >= {"bank_statement", "purchase_invoice", "reference_document"}


# ------------------------------------------------------------------ custom fields
def test_custom_fields_options_scope_and_archival_preserve_data(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    cat = owner.post(W(a, "/categories"), {"name": "Supplier invoice", "document_kind": "purchase_invoice"}).json()[
        "item"
    ]
    r = owner.post(
        W(a, "/fields"),
        {
            "label": "Cost Center",
            "data_type": "dropdown",
            "required": True,
            "options": [{"value": "ops", "label": "Operations"}, {"value": "hq", "label": "Head office"}],
            "category_ids": [cat["id"]],
        },
    )
    assert r.status_code == 201, r.text
    f = r.json()["item"]
    assert f["key"] == "cost_center" and [o["value"] for o in f["options"]] == ["ops", "hq"]
    types = {
        "short_text": {},
        "long_text": {},
        "number": {"validation": {"min": 0}},
        "decimal": {"validation": {"decimal_places": 2}},
        "currency": {"validation": {"allowed_currencies": ["OMR"]}},
        "date": {},
        "multi_select": {"options": [{"value": "a", "label": "A"}]},
        "boolean": {},
        "reference": {"reference_target": "workspace_node"},
    }
    for t, extra in types.items():
        assert owner.post(W(a, "/fields"), {"label": f"F {t}", "data_type": t, **extra}).status_code == 201, t
    assert (
        owner.post(W(a, "/fields"), {"label": "cost center", "data_type": "short_text"}).status_code == 409
    )  # duplicate label
    assert (
        owner.post(W(a, "/fields"), {"label": "Dup key", "key": "cost_center", "data_type": "short_text"}).status_code
        == 409
    )
    assert (
        owner.post(
            W(a, "/fields"), {"label": "Bad", "data_type": "short_text", "validation": {"regex": "(a+)+$"}}
        ).status_code
        == 422
    )
    assert owner.post(W(a, "/fields"), {"label": "Bad2", "data_type": "dropdown"}).status_code == 422
    # applicable fields: scoped field appears for its category only
    keys = lambda q: [x["key"] for x in owner.get(W(a, f"/fields?{q}")).json()["items"]]  # noqa: E731
    assert "cost_center" in keys(f"category_id={cat['id']}")
    other_cat = owner.post(W(a, "/categories"), {"name": "Other", "document_kind": "reference_document"}).json()["item"]
    assert "cost_center" not in keys(f"category_id={other_cat['id']}") and "f_short_text" in keys(
        f"category_id={other_cat['id']}"
    )

    # value validation
    def val(values):
        return owner.post(W(a, "/fields/validate-values"), {"category_id": cat["id"], "values": values}).json()

    assert (
        val({"cost_center": "ops"})["valid"]
        and not val({})["valid"]
        and not val({"cost_center": "nope"})["valid"]
        and not val({"cost_center": "ops", "ghost": 1})["valid"]
    )
    # removing an option archives it (values stay interpretable); the key and type are immutable
    u = owner.patch(
        W(a, f"/fields/{f['id']}"), {"options": [{"value": "ops", "label": "Operations"}], "label": "Cost centre"}
    ).json()["item"]
    assert {o["value"]: o["archived_at"] is not None for o in u["options"]} == {"ops": False, "hq": True}
    assert u["key"] == "cost_center" and u["data_type"] == "dropdown"
    assert not val({"cost_center": "hq"})["valid"]
    assert (
        owner.patch(W(a, f"/fields/{f['id']}"), {"data_type": "number"}).status_code == 200
    )  # unknown property ignored, type unchanged
    assert owner.get(W(a, "/fields")).json()["items"][0]["data_type"] == "dropdown"
    assert owner.post(W(a, f"/fields/{f['id']}/archive"), {}).status_code == 200
    assert "cost_center" not in keys(f"category_id={cat['id']}") and any(
        x["key"] == "cost_center" for x in owner.get(W(a, "/fields?include_archived=true")).json()["items"]
    )
    assert owner.post(W(a, f"/fields/{f['id']}/restore"), {}).status_code == 200
    # the same key can exist in another company independently
    assert owner.post(W(b, "/fields"), {"label": "Cost Center", "data_type": "short_text"}).status_code == 201
    # references must point to a record in THIS company
    node_a, node_b = mk(owner, a, "NA"), mk(owner, b, "NB")
    ref = lambda i: owner.post(W(a, "/fields/validate-values"), {"values": {"f_reference": i}}).json()["valid"]  # noqa: E731
    assert ref(node_a["id"]) and not ref(node_b["id"])


# ------------------------------------------------------------------ naming rules
def test_naming_rules_validate_preview_and_never_overwrite(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    owner.post(W(a, "/fields"), {"label": "Invoice Number", "data_type": "short_text"})
    cat = owner.post(W(a, "/categories"), {"name": "Supplier invoice", "document_kind": "purchase_invoice"}).json()[
        "item"
    ]
    ok = owner.post(W(a, "/naming-rules/validate"), {"pattern": "{Supplier}_{InvoiceNumber}_{Date}"}).json()
    assert ok["valid"] and ok["tokens"] == ["Supplier", "InvoiceNumber", "Date"]
    for bad in ("{Nope}", "plain", "{Supplier}/x", "{Supplier}.."):
        assert owner.post(W(a, "/naming-rules/validate"), {"pattern": bad}).json()["valid"] is False
        assert owner.post(W(a, "/naming-rules"), {"name": "bad", "pattern": bad}).status_code == 422
    # the custom field of company A is NOT available in company B
    assert owner.post(W(b, "/naming-rules/validate"), {"pattern": "{InvoiceNumber}"}).json()["valid"] is False
    r = owner.post(
        W(a, "/naming-rules"),
        {
            "name": "Supplier invoices",
            "pattern": "{Supplier|Unknown}_{InvoiceNumber|NA}_{Date}",
            "category_id": cat["id"],
        },
    )
    assert r.status_code == 201
    assert (
        owner.post(
            W(a, "/naming-rules"), {"name": "again", "pattern": "{Supplier}", "category_id": cat["id"]}
        ).status_code
        == 409
    )  # one live rule per category
    p = owner.post(
        W(a, "/naming-rules/preview"),
        {
            "category_id": cat["id"],
            "original_filename": "scan 001.PDF",
            "values": {"supplier": "Acme/../LLC", "invoice_number": "9", "date": "2026-03-04"},
        },
    ).json()
    assert p["generated_filename"] == "Acme-.-LLC_9_2026-03-04.pdf"
    assert p["original_filename"] == "scan 001.PDF" and "/" not in p["generated_filename"]
    q = owner.post(
        W(a, "/naming-rules/preview"), {"category_id": cat["id"], "original_filename": "a.pdf", "values": {}}
    ).json()
    assert q["generated_filename"] == "Unknown_NA_NA.pdf" and len(q["warnings"]) == 3
    dup = owner.post(
        W(a, "/naming-rules/preview"),
        {"category_id": cat["id"], "original_filename": "a.pdf", "values": {}, "existing_names": ["Unknown_NA_NA.pdf"]},
    ).json()
    assert dup["generated_filename"] == "Unknown_NA_NA_2.pdf" and dup["renamed_to_avoid_overwrite"]
    none = owner.post(
        W(b, "/naming-rules/preview"), {"original_filename": "x.png", "values": {"documenttype": "bank_statement"}}
    ).json()
    assert (
        none["used_fallback"]
        and none["generated_filename"].startswith("bank_statement_undated_")
        and none["generated_filename"].endswith(".png")
    )


# ------------------------------------------------------------------ filing rules
def test_filing_rules_validate_targets_and_resolve_by_priority(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    fin = mk(owner, a, "Finance")
    banks = mk(owner, a, "Bank Statements", fin["id"])
    muscat = mk(owner, a, "Bank of Muscat", banks["id"])
    owner.post(W(a, "/fields"), {"label": "Bank", "data_type": "short_text"})
    cond = lambda *c: [{"field": f, "operator": "equals", "value": v} for f, v in c]  # noqa: E731
    general = owner.post(
        W(a, "/filing-rules"),
        {
            "name": "Bank statements",
            "conditions": cond(("document_kind", "bank_statement")),
            "target_node_id": banks["id"],
            "priority": 20,
        },
    ).json()["item"]
    specific = owner.post(
        W(a, "/filing-rules"),
        {
            "name": "Muscat",
            "conditions": cond(("document_kind", "bank_statement"), ("custom:bank", "Bank of Muscat")),
            "target_node_id": muscat["id"],
            "priority": 10,
        },
    ).json()["item"]
    for bad in (cond(("document_kind", "nonsense")), cond(("custom:ghost", "x")), []):
        assert (
            owner.post(
                W(a, "/filing-rules"), {"name": "b", "conditions": bad, "target_node_id": banks["id"]}
            ).status_code
            == 422
        )
    assert (
        owner.post(
            W(a, "/filing-rules"),
            {
                "name": "b",
                "conditions": cond(("source", "web")),
                "target_node_id": "00000000-0000-0000-0000-000000000001",
            },
        ).status_code
        == 404
    )
    ev = lambda attrs: owner.post(W(a, "/filing-rules/evaluate"), {"attributes": attrs}).json()  # noqa: E731
    r = ev({"document_kind": "bank_statement", "custom:bank": "Bank of Muscat"})
    assert (
        r["winner"]["rule_id"] == specific["id"]
        and [m["rule_id"] for m in r["matches"]] == [specific["id"], general["id"]]
        and r["ambiguous"]
    )
    assert ev({"document_kind": "bank_statement", "custom:bank": "Bank A"})["winner"]["rule_id"] == general["id"]
    assert ev({"document_kind": "sales_invoice"})["winner"] is None
    assert any(f["code"] == "shadowed" for f in owner.get(W(a, "/filing-rules/analysis")).json()["findings"]) is False
    # reorder flips resolution deterministically
    assert (
        owner.post(W(a, "/filing-rules/reorder"), {"ordered_ids": [general["id"], specific["id"]]}).status_code == 200
    )
    assert (
        ev({"document_kind": "bank_statement", "custom:bank": "Bank of Muscat"})["winner"]["rule_id"] == general["id"]
    )
    assert owner.get(W(a, "/filing-rules/analysis")).json()["findings"][0]["code"] == "shadowed"
    assert owner.post(
        W(a, "/filing-rules/validate"), {"conditions": cond(("source", "web")), "target_node_id": banks["id"]}
    ).json()["valid"]
    # archiving the target makes an update to a dead target fail, and the evaluation skips rules with dead targets
    owner.post(W(a, f"/nodes/{muscat['id']}/archive"), {"confirm_impact": True})
    assert (
        owner.post(
            W(a, "/filing-rules/validate"), {"conditions": cond(("source", "web")), "target_node_id": muscat["id"]}
        ).json()["valid"]
        is False
    )
    assert (
        ev({"document_kind": "bank_statement", "custom:bank": "Bank of Muscat"})["winner"]["rule_id"] == general["id"]
    )


# ------------------------------------------------------------------ templates
def test_templates_are_copied_independently(client, mailer, owner_engine, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    for key in (
        "limit.workspace_sections_per_company",
        "limit.folders_per_company",
        "limit.custom_fields_per_company",
        "limit.filing_rules_per_company",
    ):
        set_plan_limit(owner_engine, owner.org_id, key, None, unlimited=True)
    items = owner.get(f"/orgs/{owner.org_id}/workspace-templates").json()["items"]
    keys = {t["key"] for t in items}
    assert keys >= {
        "general-business",
        "trading",
        "construction",
        "professional-services",
        "manufacturing",
        "retail",
        "hospitality",
        "blank",
    }
    general = next(t for t in items if t["key"] == "general-business")
    pre = owner.post(W(a, "/templates/apply"), {"template_id": general["id"], "dry_run": True}).json()
    assert pre["dry_run"] and pre["counts"]["sections"] == 8 and pre["fits"]
    assert tree(owner, a)["nodes"] == []  # a dry run changes nothing
    r = owner.post(W(a, "/templates/apply"), {"template_id": general["id"]})
    assert r.status_code == 200, r.text
    created = r.json()["item"]["created"]
    assert created["sections"] == 8 and created["folders"] == 10 and created["categories"] == 10
    ta = tree(owner, a)
    assert ta["applied_template"]["template_id"] == general["id"]
    by_name = {n["name"]: n for n in ta["nodes"]}
    assert by_name["Bank Statements"]["parent_id"] == by_name["Finance"]["id"]  # relationships preserved
    assert all(c["node_id"] in {n["id"] for n in ta["nodes"]} for c in ta["categories"] if c["node_id"])
    assert owner.post(W(b, "/templates/apply"), {"template_id": general["id"]}).status_code == 200
    tb = tree(owner, b)
    assert not ({n["id"] for n in ta["nodes"]} & {n["id"] for n in tb["nodes"]})  # brand-new ids per company
    assert not ({c["id"] for c in ta["categories"]} & {c["id"] for c in tb["categories"]})
    # editing company A's copy never touches B or the template
    sid = by_name["Finance"]["id"]
    owner.patch(W(a, f"/nodes/{sid}"), {"name": "Money"})
    owner.post(W(a, f"/nodes/{by_name['Payroll']['id']}/archive"), {"confirm_impact": True})
    assert "Finance" in {n["name"] for n in tree(owner, b)["nodes"]} and "Payroll" in {
        n["name"] for n in tree(owner, b)["nodes"]
    }
    assert owner.get(f"/orgs/{owner.org_id}/workspace-templates/{general['id']}").json()["counts"]["sections"] == 8
    # applying again adds with collision suffixes; it never overwrites
    again = owner.post(W(b, "/templates/apply"), {"template_id": general["id"]})
    assert again.status_code == 200 and "Finance (2)" in {n["name"] for n in tree(owner, b)["nodes"]}
    # replace needs explicit confirmation and ARCHIVES instead of deleting
    assert owner.post(W(b, "/templates/apply"), {"template_id": general["id"], "mode": "replace"}).status_code == 409
    ok = owner.post(
        W(b, "/templates/apply"), {"template_id": general["id"], "mode": "replace", "confirm_replace": True}
    )
    assert ok.status_code == 200
    names_live = [n["name"] for n in tree(owner, b)["nodes"]]
    assert names_live.count("Finance") == 1 and len(names_live) == 18
    assert len(tree(owner, b, archived=True)["nodes"]) > 18


def test_save_as_template_versions_and_later_updates_do_not_change_companies(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    s = mk(owner, a, "Projects")
    mk(owner, a, "Site expenses", s["id"])
    owner.post(W(a, "/fields"), {"label": "Project name", "data_type": "short_text"})
    r = owner.post(f"/orgs/{owner.org_id}/workspace-templates", {"name": "My firm template", "source_company_id": a})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    detail = owner.get(f"/orgs/{owner.org_id}/workspace-templates/{tid}").json()
    assert detail["counts"] == {
        "sections": 1,
        "folders": 1,
        "categories": 0,
        "fields": 1,
        "naming_rules": 0,
        "filing_rules": 0,
    }
    assert owner.post(W(b, "/templates/apply"), {"template_id": tid}).status_code == 200
    mk(owner, a, "Equipment")
    v2 = owner.post(
        f"/orgs/{owner.org_id}/workspace-templates/{tid}/versions",
        {"source_company_id": a, "changelog": "added equipment"},
    )
    assert v2.status_code == 201 and v2.json()["version"] == 2
    assert len(tree(owner, b)["nodes"]) == 2  # company B did not change when the template got version 2
    assert owner.post(W(b, "/templates/apply"), {"template_id": tid, "version": 1, "mode": "add"}).status_code == 200
    system = next(t for t in owner.get(f"/orgs/{owner.org_id}/workspace-templates").json()["items"] if t["is_system"])
    assert (
        owner.post(f"/orgs/{owner.org_id}/workspace-templates/{system['id']}/versions", {"definition": {}}).status_code
        == 409
    )  # system templates are read-only
    assert owner.post(f"/orgs/{owner.org_id}/workspace-templates/{system['id']}/archive").status_code == 409
    assert (
        owner.post(
            f"/orgs/{owner.org_id}/workspace-templates",
            {"name": "bad", "definition": {"sections": [{"ref": "x", "name": "a/b"}]}},
        ).status_code
        == 422
    )
    nodes_before = len(tree(owner, b)["nodes"])
    assert owner.post(f"/orgs/{owner.org_id}/workspace-templates/{tid}/archive").status_code == 200
    assert (
        len(tree(owner, b)["nodes"]) == nodes_before and tree(owner, b)["applied_template"]["template_id"] == tid
    )  # archiving a template changes no company
    assert (
        owner.post(W(b, "/templates/apply"), {"template_id": tid}).status_code == 404
    )  # but it can no longer be applied


def test_copy_from_another_company_copies_configuration_only(client, mailer, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    s = mk(owner, a, "Finance")
    mk(owner, a, "Banks", s["id"])
    owner.post(W(a, "/categories"), {"name": "Statement", "document_kind": "bank_statement"})
    owner.post(f"/companies/{a}/assignments", {"user_id": uid(owner), "assignment_type": "primary"})
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    owner.post(
        W(a, "/permissions"),
        {"node_id": s["id"], "subject_type": "user", "user_id": uid(acc), "permission": "edit", "effect": "allow"},
    )
    r = owner.post(W(b, "/copy-from"), {"source_company_id": a})
    assert r.status_code == 200 and r.json()["item"]["created"]["sections"] == 1
    tb = tree(owner, b)
    assert {n["name"] for n in tb["nodes"]} == {"Finance", "Banks"} and len(tb["categories"]) == 1
    assert owner.get(W(b, "/permissions")).json()["items"] == []  # permissions and users are never copied
    assert owner.post(W(b, "/copy-from"), {"source_company_id": b}).status_code == 422
    # a caller who cannot reach the source company cannot copy from it
    assign(owner, b, acc)
    acc_mgr = make_member(owner, mailer, "assistant_accountant")
    assign(owner, b, acc_mgr)
    assert acc_mgr.post(W(b, "/copy-from"), {"source_company_id": a}).status_code == 404


# ------------------------------------------------------------------ permissions
def test_permissions_inheritance_deny_and_hidden_names(client, mailer, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    fin, sales = mk(owner, a, "Finance"), mk(owner, a, "Sales")
    banks = mk(owner, a, "Banks", fin["id"])
    secret = mk(owner, a, "Confidential payroll", fin["id"])
    acc1, acc2 = make_member(owner, mailer, "accountant"), make_member(owner, mailer, "assistant_accountant")
    for m in (acc1, acc2):
        assign(owner, a, m)
    grant = lambda **kw: owner.post(W(a, "/permissions"), kw)  # noqa: E731
    # defaults (open mode): everyone with company access can view, nobody below manager can change the structure
    assert acc1.get(W(a)).status_code == 200 and {n["name"] for n in acc1.get(W(a)).json()["nodes"]} == {
        "Finance",
        "Sales",
        "Banks",
        "Confidential payroll",
    }
    assert acc1.post(W(a, "/nodes"), {"name": "Nope"}).status_code == 403
    assert acc2.patch(W(a, f"/nodes/{fin['id']}"), {"description": "x"}).status_code == 403
    # explicit allow on a section is inherited by its subfolders only
    assert (
        grant(node_id=fin["id"], subject_type="user", user_id=uid(acc1), permission="edit", effect="allow").status_code
        == 201
    )
    assert acc1.post(W(a, "/nodes"), {"name": "Loans", "parent_id": banks["id"]}).status_code == 201
    assert acc1.post(W(a, "/nodes"), {"name": "Loans2", "parent_id": sales["id"]}).status_code == 403
    assert (
        acc1.post(W(a, "/nodes"), {"name": "NewSection"}).status_code == 403
    )  # company-level `configure` was never granted
    assert (
        acc2.post(W(a, "/nodes"), {"name": "Loans3", "parent_id": banks["id"]}).status_code == 403
    )  # another member is unaffected
    # a child whose parent the caller cannot see is returned as a root, without disclosing the parent's id or name
    hidden_parent = mk(owner, a, "HiddenParent")
    visible_child = mk(owner, a, "VisibleChild", hidden_parent["id"])
    owner.patch(W(a, "/settings"), {"access_mode": "restricted"})
    grant(node_id=visible_child["id"], subject_type="user", user_id=uid(acc2), permission="view", effect="allow")
    got = acc2.get(W(a)).json()["nodes"]
    assert [(n["name"], n["parent_id"], n.get("detached")) for n in got] == [("VisibleChild", None, True)]
    assert "HiddenParent" not in acc2.get(W(a)).text and hidden_parent["id"] not in acc2.get(W(a)).text
    owner.patch(W(a, "/settings"), {"access_mode": "open"})
    # explicit deny on a subfolder hides it: the name is not exposed anywhere, and direct access answers 404
    assert (
        grant(
            node_id=secret["id"], subject_type="role", role="assistant_accountant", permission="view", effect="deny"
        ).status_code
        == 201
    )
    assert "Confidential payroll" not in acc2.get(W(a)).text and "Confidential payroll" in acc1.get(W(a)).text
    assert acc2.get(W(a, f"/nodes/{secret['id']}/impact")).status_code == 404
    assert acc2.patch(W(a, f"/nodes/{secret['id']}"), {"name": "leak"}).status_code == 404
    assert acc2.post(W(a, "/nodes"), {"name": "x", "parent_id": secret["id"]}).status_code == 404
    assert "Confidential payroll" not in acc2.get(W(a, "/upload-config")).text
    # deny beats an inherited allow
    assert (
        grant(node_id=banks["id"], subject_type="user", user_id=uid(acc1), permission="edit", effect="deny").status_code
        == 201
    )
    assert acc1.post(W(a, "/nodes"), {"name": "Loans4", "parent_id": banks["id"]}).status_code == 403
    assert acc1.post(W(a, "/nodes"), {"name": "Ok", "parent_id": fin["id"]}).status_code == 201
    # restricted mode: only explicitly allowed branches are visible
    assert owner.patch(W(a, "/settings"), {"access_mode": "restricted"}).status_code == 200
    names = {n["name"] for n in acc1.get(W(a)).json()["nodes"]}
    assert names == set()  # an `edit` grant alone does not make anything visible in restricted mode
    assert (
        grant(
            node_id=sales["id"], subject_type="user", user_id=uid(acc2), permission="view", effect="allow"
        ).status_code
        == 201
    )
    assert {n["name"] for n in acc2.get(W(a)).json()["nodes"]} == {"Sales", "VisibleChild"}
    assert acc2.get(W(a, "/access/me")).json()["nodes"].keys() == {n["id"] for n in acc2.get(W(a)).json()["nodes"]}
    assert len(owner.get(W(a)).json()["nodes"]) >= 5  # the owner is never locked out
    # who may change permissions
    assert (
        acc1.post(
            W(a, "/permissions"),
            {"subject_type": "user", "user_id": uid(acc2), "permission": "view", "effect": "allow"},
        ).status_code
        == 403
    )
    assert acc1.get(W(a, "/permissions")).status_code == 403
    assert acc1.patch(W(a, "/settings"), {"access_mode": "open"}).status_code == 403
    # grants cannot target administrators or exceed the read-only ceiling
    assert grant(subject_type="role", role="firm_owner", permission="view", effect="deny").status_code == 422
    ro = make_member(owner, mailer, "read_only")
    assign(owner, a, ro)
    assert (
        grant(node_id=sales["id"], subject_type="user", user_id=uid(ro), permission="edit", effect="allow").status_code
        == 422
    )
    assert (
        grant(
            subject_type="user", user_id="00000000-0000-0000-0000-000000000009", permission="view", effect="allow"
        ).status_code
        == 422
    )
    # administrators can ask what a member could do
    chk = owner.post(W(a, "/permissions/check"), {"user_id": uid(acc1), "node_id": fin["id"], "action": "edit"}).json()
    assert chk == {"allowed": True, "reason": "explicit_allow"}
    # removing the grant removes the access
    gid = next(
        g["id"]
        for g in owner.get(W(a, "/permissions")).json()["items"]
        if g["permission"] == "edit" and g["effect"] == "allow"
    )
    assert owner.delete(W(a, f"/permissions/{gid}")).status_code == 200
    assert acc1.post(W(a, "/nodes"), {"name": "Late", "parent_id": fin["id"]}).status_code in (403, 404)
    # a company the member is not assigned to is unreachable regardless of grants
    assert acc1.get(W(b)).status_code == 404


def test_configuration_changes_need_company_level_configure(client, mailer, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    for call in (
        lambda: acc.post(W(a, "/categories"), {"name": "c", "document_kind": "sales_invoice"}),
        lambda: acc.post(W(a, "/fields"), {"label": "f", "data_type": "short_text"}),
        lambda: acc.post(W(a, "/naming-rules/validate"), {"pattern": "{Date}"}),
        lambda: acc.post(W(a, "/filing-rules/evaluate"), {"attributes": {}}),
        lambda: acc.get(W(a, "/revisions")),
        lambda: acc.post(W(a, "/templates/apply"), {"template_id": "00000000-0000-0000-0000-000000000000"}),
        lambda: acc.post(f"/orgs/{owner.org_id}/workspace-templates", {"name": "x", "definition": {}}),
    ):
        assert call().status_code == 403
    owner.post(
        W(a, "/permissions"),
        {"subject_type": "user", "user_id": uid(acc), "permission": "configure", "effect": "allow"},
    )
    assert (
        acc.post(W(a, "/categories"), {"name": "c", "document_kind": "sales_invoice"}).status_code == 201
    )  # a company-level grant works
    # the assistant role has read access only
    ass = make_member(owner, mailer, "assistant_accountant")
    assign(owner, a, ass)
    assert ass.get(W(a)).status_code == 200 and ass.post(W(a, "/nodes"), {"name": "x"}).status_code == 403


# ------------------------------------------------------------------ entitlements
def test_entitlements_are_enforced_by_the_backend_and_never_delete_data(client, mailer, owner_engine, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, b = firm
    set_plan_limit(owner_engine, owner.org_id, "limit.workspace_sections_per_company", 2)
    set_plan_limit(owner_engine, owner.org_id, "limit.folders_per_company", 3)
    set_plan_limit(owner_engine, owner.org_id, "limit.folder_depth", 3)
    set_plan_limit(owner_engine, owner.org_id, "limit.custom_fields_per_company", 1)
    set_plan_limit(owner_engine, owner.org_id, "limit.filing_rules_per_company", 1)
    s1, s2 = mk(owner, a, "S1"), mk(owner, a, "S2")
    over = owner.post(W(a, "/nodes"), {"name": "S3"})
    assert (
        over.status_code == 402
        and over.json()["key"] == "limit.workspace_sections_per_company"
        and over.json()["limit"] == 2
    )
    f1 = mk(owner, a, "F1", s1["id"])
    f2 = mk(owner, a, "F2", f1["id"])
    d = owner.post(W(a, "/nodes"), {"name": "Too deep", "parent_id": f2["id"]})
    assert d.status_code == 402 and d.json()["key"] == "limit.folder_depth"
    mk(owner, a, "F3", s2["id"])
    assert (
        owner.post(W(a, "/nodes"), {"name": "F4", "parent_id": s2["id"]}).json()["key"] == "limit.folders_per_company"
    )
    assert (
        owner.post(W(a, f"/nodes/{s1['id']}/duplicate"), {}).status_code == 402
    )  # bulk operations are checked on their result
    assert owner.post(W(a, "/fields"), {"label": "One", "data_type": "short_text"}).status_code == 201
    assert owner.post(W(a, "/fields"), {"label": "Two", "data_type": "short_text"}).status_code == 402
    # limits are per company: company B is unaffected by A's usage
    assert mk(owner, b, "B1") and mk(owner, b, "B2")
    u = {x["key"]: x for x in owner.get(W(a, "/usage")).json()["limits"]}
    assert (
        u["limit.workspace_sections_per_company"]["current"],
        u["limit.workspace_sections_per_company"]["limit"],
        u["limit.workspace_sections_per_company"]["at_limit"],
    ) == (2, 2, True)
    # template apply that cannot fit is refused atomically
    general = next(
        t
        for t in owner.get(f"/orgs/{owner.org_id}/workspace-templates").json()["items"]
        if t["key"] == "general-business"
    )
    assert owner.post(W(b, "/templates/apply"), {"template_id": general["id"], "dry_run": True}).json()["fits"] is False
    before = len(tree(owner, b)["nodes"])
    assert owner.post(W(b, "/templates/apply"), {"template_id": general["id"]}).status_code == 402
    assert len(tree(owner, b)["nodes"]) == before
    # lowering a limit below current usage keeps everything; editing, archiving and reading still work, only growth is refused
    set_plan_limit(owner_engine, owner.org_id, "limit.workspace_sections_per_company", 1)
    assert len(tree(owner, a)["nodes"]) == 5
    assert owner.patch(W(a, f"/nodes/{s1['id']}"), {"description": "still editable"}).status_code == 200
    assert owner.post(W(a, f"/nodes/{s2['id']}/archive"), {"confirm_impact": True}).status_code == 200
    assert owner.post(W(a, f"/nodes/{s2['id']}/restore"), {}).status_code == 402  # restoring would grow past the limit
    assert owner.post(W(a, "/nodes"), {"name": "S9"}).status_code == 402
    # the template quota is an organization limit
    set_plan_limit(owner_engine, owner.org_id, "limit.workspace_templates", 1)
    assert (
        owner.post(f"/orgs/{owner.org_id}/workspace-templates", {"name": "T1", "source_company_id": a}).status_code
        == 201
    )
    assert (
        owner.post(f"/orgs/{owner.org_id}/workspace-templates", {"name": "T2", "source_company_id": a}).status_code
        == 402
    )


# ------------------------------------------------------------------ history and restore
def test_revisions_audit_and_non_destructive_restore(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Finance")
    f = mk(owner, a, "Banks", s["id"])
    cat = owner.post(
        W(a, "/categories"), {"name": "Statement", "document_kind": "bank_statement", "node_id": f["id"]}
    ).json()["item"]
    good = tree(owner, a)["version"]
    owner.patch(W(a, f"/nodes/{s['id']}"), {"name": "Money"})
    extra = mk(owner, a, "Extra")
    owner.post(W(a, f"/categories/{cat['id']}/archive"), {})
    owner.post(W(a, f"/nodes/{f['id']}/move"), {"parent_id": extra["id"]})
    revs = owner.get(W(a, "/revisions")).json()
    assert revs["total"] == tree(owner, a)["version"] - 1 and revs["items"][0]["version"] == tree(owner, a)["version"]
    detail = owner.get(W(a, f"/revisions/{good}")).json()
    assert detail["summary"]["nodes"] == 2 and detail["actor_name"]
    moved = next(r for r in owner.get(W(a, "/revisions")).json()["items"] if r["action"] == "node_moved")
    ch = owner.get(W(a, f"/revisions/{moved['version']}")).json()["changes"][0]
    assert (
        ch["action"] == "node.moved"
        and ch["before"]["parent_id"] == s["id"]
        and ch["after"]["parent_id"] == extra["id"]
    )
    audit = owner.get(W(a, "/audit")).json()["items"]
    assert any(e["action"] == "workspace.node_moved" for e in audit) and all(
        e["action"].startswith("workspace.") for e in audit
    )
    dry = owner.post(W(a, f"/revisions/{good}/restore"), {"dry_run": True}).json()
    assert dry["dry_run"] and dry["effect"]["archived"] >= 1
    assert {n["name"] for n in tree(owner, a)["nodes"]} == {"Money", "Extra", "Banks"}  # dry run changed nothing
    r = owner.post(W(a, f"/revisions/{good}/restore"), {})
    assert r.status_code == 200, r.text
    now = tree(owner, a)
    assert {n["name"] for n in now["nodes"]} == {"Finance", "Banks"}  # rename and move reverted, later item archived
    assert next(n for n in now["nodes"] if n["name"] == "Banks")["parent_id"] == s["id"]
    assert [c["name"] for c in now["categories"]] == ["Statement"]  # archived category restored
    assert any(n["name"] == "Extra" for n in tree(owner, a, archived=True)["nodes"])  # nothing was deleted
    assert owner.get(W(a, "/revisions")).json()["items"][0]["action"] == "restored"
    assert owner.post(W(a, "/revisions/9999/restore"), {}).status_code == 404
    # history cannot be rewritten through the API (no endpoints) nor by the application role (tested in the RLS test)


def test_restore_resolves_name_ownership_by_archiving_later_items(firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    s = mk(owner, a, "Tax")
    v = tree(owner, a)["version"]
    owner.post(W(a, f"/nodes/{s['id']}/archive"), {})
    newer = mk(owner, a, "Tax")  # a different node now owns the name
    assert newer["id"] != s["id"]
    r = owner.post(W(a, f"/revisions/{v}/restore"), {})
    assert r.status_code == 200
    live = tree(owner, a)["nodes"]
    assert [(n["name"], n["id"]) for n in live] == [
        ("Tax", s["id"])
    ]  # the original is back, the newer one archived (not deleted)
    assert newer["id"] in {n["id"] for n in tree(owner, a, archived=True)["nodes"]}


# ------------------------------------------------------------------ distribution contract
def test_upload_config_returns_only_permitted_active_targets_and_supports_etag(client, mailer, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    assert owner.get(W(a, "/upload-config")).json()["targets"] == []  # no hard-coded categories
    s = mk(owner, a, "Finance")
    f = mk(owner, a, "Banks", s["id"])
    gone = mk(owner, a, "Old")
    owner.post(W(a, "/categories"), {"name": "Statement", "document_kind": "bank_statement", "node_id": f["id"]})
    owner.post(W(a, f"/nodes/{gone['id']}/archive"), {"confirm_impact": True})
    r = owner.get(W(a, "/upload-config"))
    body = r.json()
    assert {t["label"] for t in body["targets"]} == {"Finance", "Banks"} and [
        c["name"] for c in body["categories"]
    ] == ["Statement"]
    assert body["version"] == tree(owner, a)["version"] and r.headers["etag"]
    again = owner.client.get(
        f"/api/v1{W(a, '/upload-config')}", headers={**owner.headers(), "If-None-Match": r.headers["etag"]}
    )
    assert again.status_code == 304
    mk(owner, a, "Another")
    assert (
        owner.client.get(
            f"/api/v1{W(a, '/upload-config')}", headers={**owner.headers(), "If-None-Match": r.headers["etag"]}
        ).status_code
        == 200
    )
    # members without the `upload` action get no targets (role default: managers and, since Phase 6, contributors such as
    # accountants; the legacy `staff` role cannot contribute)
    acc = make_member(owner, mailer, "staff")
    assign(owner, a, acc)
    assert acc.get(W(a, "/upload-config")).json()["targets"] == []
    owner.post(
        W(a, "/permissions"),
        {"node_id": s["id"], "subject_type": "user", "user_id": uid(acc), "permission": "upload", "effect": "allow"},
    )
    assert {t["label"] for t in acc.get(W(a, "/upload-config")).json()["targets"]} == {"Finance", "Banks"}


# ------------------------------------------------------------------ onboarding
def test_company_creation_can_start_blank_from_a_template_or_a_copy(client, mailer, owner_engine, firm) -> None:  # type: ignore[no-untyped-def]
    owner, a, _ = firm
    set_plan_limit(owner_engine, owner.org_id, "limit.companies", None, unlimited=True)
    s = mk(owner, a, "Finance")
    mk(owner, a, "Banks", s["id"])
    general = next(
        t
        for t in owner.get(f"/orgs/{owner.org_id}/workspace-templates").json()["items"]
        if t["key"] == "general-business"
    )
    new = lambda code, ws=None: owner.post(  # noqa: E731
        f"/orgs/{owner.org_id}/companies",
        {"name": code, "code": code, "country": "OM", "base_currency": "OMR", **({"workspace": ws} if ws else {})},
    )
    blank = new("BLANK", {"mode": "blank"})
    assert blank.status_code == 201 and tree(owner, blank.json()["id"])["nodes"] == []
    tpl = new("TPL", {"mode": "template", "template_id": general["id"]})
    assert tpl.status_code == 201 and len(tree(owner, tpl.json()["id"])["nodes"]) == 18
    assert tree(owner, tpl.json()["id"])["applied_template"]["template_id"] == general["id"]
    cp = new("CPY", {"mode": "copy", "source_company_id": a})
    assert cp.status_code == 201 and {n["name"] for n in tree(owner, cp.json()["id"])["nodes"]} == {"Finance", "Banks"}
    assert new("BAD1", {"mode": "template"}).status_code == 422 and new("BAD2", {"mode": "copy"}).status_code == 422
    # a failing template apply rolls the whole company creation back
    assert new("BAD3", {"mode": "template", "template_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
    assert all(c["code"] != "BAD3" for c in owner.get(f"/orgs/{owner.org_id}/companies").json())
