"""Guards found by reviewing the Phase 4/5 branch against Phase 3.

1. Personal workspace grants must not outlive the assignment that made them meaningful.
2. Copying a company's configuration needs `configure` on the SOURCE company too (it exports hidden nodes and rules).
3. Every mutating workspace handler must perform an authorization check inside the function, because the route-level
   dependency only declares `workspace.read` (the real action is decided per node by the access engine)."""

from __future__ import annotations

import ast
from pathlib import Path

from conftest import make_company, make_member, new_owner, uid
from sqlalchemy import text
from test_p4_workspace import W, assign, mk

HANDLERS = Path(__file__).resolve().parent.parent / "src" / "artify_api" / "routers" / "workspace.py"
# Mutating handlers that carry no direct `.require(` call, with the reason each one is still gated.
DELEGATED = {
    "copy_from_company": "delegates to run_apply (require configure on the target) and _source_definition (on the source)",
}
DEPENDENCY_GATED = {"TemplateAdmin"}  # Requires("workspace_templates.manage") on the route itself


def _grant_configure(owner, company_id, member):  # type: ignore[no-untyped-def]
    r = owner.post(
        W(company_id, "/permissions"),
        {"subject_type": "user", "user_id": uid(member), "permission": "configure", "effect": "allow"},
    )
    assert r.status_code == 201, r.text


def test_personal_grants_do_not_revive_when_an_accountant_is_reassigned(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Grant Cleanup Firm")
    a, b = make_company(owner, "GA")["id"], make_company(owner, "GB")["id"]
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    assign(owner, b, acc)
    _grant_configure(owner, a, acc)
    _grant_configure(owner, b, acc)
    # a role-wide grant for accountants must survive: it is not about this person
    role_grant = owner.post(
        W(a, "/permissions"), {"subject_type": "role", "role": "accountant", "permission": "view", "effect": "allow"}
    )
    assert role_grant.status_code == 201
    category = {"name": "c", "document_kind": "sales_invoice"}
    assert acc.post(W(a, "/categories"), category).status_code == 201

    # removing the assignment on A removes the personal grants on A only
    assert owner.delete(f"/companies/{a}/assignments/{uid(acc)}").status_code == 204
    assign(owner, a, acc)
    assert acc.post(W(a, "/categories"), {"name": "c2", "document_kind": "sales_invoice"}).status_code == 403
    assert acc.post(W(b, "/categories"), {"name": "c3", "document_kind": "sales_invoice"}).status_code == 201
    kinds = {(g["subject_type"], g["permission"]) for g in owner.get(W(a, "/permissions")).json()["items"]}
    assert ("role", "view") in kinds and ("user", "configure") not in kinds

    # the revocation is audited on that company
    rows = owner.get(f"/orgs/{owner.org_id}/activity?company_id={a}&action=workspace.grants_revoked").json()["items"]
    assert rows and rows[0]["summary"]["reason"] == "assignment removed"

    # a role change that clears every assignment clears the personal grants everywhere
    _grant_configure(owner, a, acc)
    changed = owner.patch(f"/orgs/{owner.org_id}/members/{uid(acc)}", {"role": "document_operator"})
    assert changed.status_code == 200, changed.text
    for company in (a, b):
        left = owner.get(W(company, "/permissions")).json()["items"]
        assert not [g for g in left if g["subject_type"] == "user"], company


def test_copying_configuration_needs_configure_on_the_source_company(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Copy Leak Firm")
    a, b = make_company(owner, "CA")["id"], make_company(owner, "CB")["id"]
    finance = mk(owner, a, "Finance")
    secret = mk(owner, a, "Confidential payroll", finance["id"])
    acc = make_member(owner, mailer, "accountant")
    assign(owner, a, acc)
    assign(owner, b, acc)
    deny = owner.post(
        W(a, "/permissions"),
        {"node_id": secret["id"], "subject_type": "role", "role": "accountant", "permission": "view", "effect": "deny"},
    )
    assert deny.status_code == 201
    assert "Confidential payroll" not in acc.get(W(a)).text  # hidden from this accountant on the source
    _grant_configure(owner, b, acc)  # may configure the TARGET only

    for body in ({"source_company_id": a}, {"source_company_id": a, "dry_run": True}):
        refused = acc.post(W(b, "/copy-from"), body)
        assert refused.status_code == 403, refused.text
    assert "Confidential payroll" not in acc.get(W(b)).text and not acc.get(W(b)).json()["nodes"]

    # with configure on the source as well the copy is allowed, and so is the owner's own copy
    _grant_configure(owner, a, acc)
    assert acc.post(W(b, "/copy-from"), {"source_company_id": a}).status_code == 200
    c = make_company(owner, "CC")["id"]
    assert owner.post(W(c, "/copy-from"), {"source_company_id": a}).status_code == 200


def test_every_mutating_workspace_handler_checks_authorization_in_its_body() -> None:
    tree = ast.parse(HANDLERS.read_text())
    source = HANDLERS.read_text()
    checked = 0
    unguarded: list[str] = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        verbs = [
            d.func.attr
            for d in fn.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and getattr(d.func.value, "id", "") == "router"
            and d.func.attr in {"post", "patch", "put", "delete"}
        ]
        if not verbs:
            continue
        checked += 1
        body = ast.get_source_segment(source, fn) or ""
        annotations = {ast.unparse(a.annotation) for a in fn.args.args if a.annotation}
        if ".require(" in body or annotations & DEPENDENCY_GATED or fn.name in DELEGATED:
            continue
        unguarded.append(fn.name)
    assert checked >= 30, "the scan found suspiciously few mutating handlers"
    assert not unguarded, f"mutating workspace handlers without an authorization check: {unguarded}"
    # the two delegation targets really do the checking
    run_apply = (
        ast.get_source_segment(source, next(f for f in tree.body if getattr(f, "name", "") == "run_apply")) or ""
    )
    source_def = (
        ast.get_source_segment(source, next(f for f in tree.body if getattr(f, "name", "") == "_source_definition"))
        or ""
    )
    assert '.require("configure")' in run_apply and '.require("configure")' in source_def


def test_grant_cleanup_helper_only_touches_the_named_person(client, mailer, owner_engine) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Grant Scope Firm")
    a = make_company(owner, "SA")["id"]
    one, two = make_member(owner, mailer, "accountant"), make_member(owner, mailer, "accountant")
    for m in (one, two):
        assign(owner, a, m)
        _grant_configure(owner, a, m)
    assert owner.delete(f"/companies/{a}/assignments/{uid(one)}").status_code == 204
    with owner_engine.connect() as conn:
        left = [
            str(r[0])
            for r in conn.execute(
                text("SELECT subject_user_id FROM workspace_permissions WHERE company_id = :c"), {"c": a}
            )
        ]
    assert left == [uid(two)]
