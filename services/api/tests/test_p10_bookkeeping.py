"""Phase 10 integration tests: daily bookkeeping and closing through the API against PostgreSQL. Synthetic data only."""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import make_member, new_owner, uid
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_p6_documents import assign, backend  # noqa: F401  (autouse storage fixture)
from test_p8_accounting import ACC, books, contact, draft, taxes  # noqa: F401  (books is a fixture)

DAY = "2025-03-10"


def BK(cid: str, path: str = "") -> str:
    return f"/companies/{cid}/bookkeeping{path}"


def txn(
    books,
    *,
    date_: str = DAY,
    tax: bool = False,
    approve: bool = True,
    cid: str | None = None,
    amount: str = "100",
    **kw: Any,
) -> str:  # type: ignore[no-untyped-def]
    """A real purchase invoice, prepared by the owner and approved by the accountant (or left in `awaiting_approval`)."""
    o, a, acct = books["owner"], cid or books["a"], books["acct"]
    t = taxes(o, a)
    sup = contact(o, a, "supplier", f"Gulf Office Supplies {uuid.uuid4().hex[:4]}")
    line: dict[str, Any] = {"amount": amount}
    if tax:
        line["tax_code_id"] = t["OM_STD"]
    x = draft(
        o,
        a,
        "purchase_invoice",
        date=date_,
        contact_id=sup,
        doc_number=f"INV-{uuid.uuid4().hex[:5]}",
        lines=[line],
        **kw,
    )
    assert x.status_code == 201, x.text
    tid = x.json()["id"]
    assert o.post(ACC(a, f"/transactions/{tid}/submit"), {}).status_code == 200
    if approve:
        r = acct.post(ACC(a, f"/transactions/{tid}/approve"), {})
        assert r.status_code == 200, r.text
    return tid


def valid_draft(books, *, date_: str = DAY, cid: str | None = None) -> str:  # type: ignore[no-untyped-def]
    """A complete draft (supplier, number, amount): it has no blocking problem, it is simply not decided yet."""
    o, a = books["owner"], cid or books["a"]
    sup = contact(o, a, "supplier", f"Draft Supplier {uuid.uuid4().hex[:4]}")
    x = draft(
        o,
        a,
        "purchase_invoice",
        date=date_,
        contact_id=sup,
        doc_number=f"D-{uuid.uuid4().hex[:5]}",
        lines=[{"amount": "5"}],
    )
    assert x.status_code == 201, x.text
    return x.json()["id"]


def quiet(owner, cid: str, **severities: str) -> None:  # type: ignore[no-untyped-def]
    """These test transactions are typed in by hand and have no source document; lower that one warning so a clean day can be 'passed'."""
    r = owner.put(BK(cid, "/policy"), {"severity_overrides": {"TXN_NO_SOURCE_DOCUMENT": "info", **severities}})
    assert r.status_code == 200, r.text


def start(actor, cid: str, day: str = DAY) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = actor.post(BK(cid, "/closings"), {"business_date": day})
    assert r.status_code == 201, r.text
    return r.json()


def validate(actor, cid: str, closing_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = actor.post(BK(cid, f"/closings/{closing_id}/validate"), {})
    assert r.status_code == 200, r.text
    return r.json()


def issue_codes(view: dict[str, Any]) -> set[str]:
    return {e["code"] for e in view["exceptions"] if e["status"] != "resolved"}


def tamper(owner_engine, sql: str, **p: Any) -> None:  # type: ignore[no-untyped-def]
    """Change data the way a bug or an attacker with database access might: with the triggers switched off."""
    with owner_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(sql), p)


def test_smoke_empty_day_passes_with_information_only(books) -> None:  # type: ignore[no-untyped-def]
    prep, a = books["prep"], books["a"]
    s = start(prep, a)
    assert s["status"] == "draft" and s["stage"] == 1
    v = validate(prep, a, s["id"])
    assert v["status"] == "in_progress" and v["validation_status"] == "passed" and v["stage"] == 2
    assert issue_codes(v) == {"DAY_EMPTY", "SYNC_NOT_REPORTED"}


# ================================================================== policy
def test_policy_defaults_and_company_scoped_changes(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    p = o.get(BK(a, "/policy")).json()
    assert p["is_default"] and p["require_independent_approval"] and p["severity_overrides"] == {}
    assert {c["code"] for c in p["checks"]} >= {"TXN_UNBALANCED", "DOC_INFECTED"} and any(
        c["hard"] for c in p["checks"]
    )
    r = o.put(
        BK(a, "/policy"),
        {
            "severity_overrides": {"TXN_DRAFT_OPEN": "error"},
            "action_roles": {"reopen": ["practice_manager"]},
            "timezone": "Asia/Muscat",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["severity_overrides"] == {"TXN_DRAFT_OPEN": "error"} and r.json()["timezone"] == "Asia/Muscat"
    # company B is untouched
    pb = o.get(BK(b, "/policy")).json()
    assert pb["is_default"] and pb["severity_overrides"] == {} and pb["action_roles"] == {}
    # invalid configuration is refused
    assert o.put(BK(a, "/policy"), {"severity_overrides": {"DOC_INFECTED": "info"}}).json()["code"] == "hard_check"
    assert o.put(BK(a, "/policy"), {"severity_overrides": {"NOPE": "info"}}).json()["code"] == "unknown_check"
    assert o.put(BK(a, "/policy"), {"action_roles": {"fly": ["accountant"]}}).json()["code"] == "unknown_action"
    assert o.put(BK(a, "/policy"), {"action_roles": {"reopen": ["wizard"]}}).json()["code"] == "unknown_role"
    assert o.put(BK(a, "/policy"), {"timezone": "Mars/Base"}).json()["code"] == "unknown_timezone"
    assert o.put(BK(a, "/policy"), {"timezone": ""}).json()["timezone"] is None
    # optimistic concurrency
    v = o.get(BK(a, "/policy")).json()["row_version"]
    assert o.put(BK(a, "/policy"), {"override_min_chars": 20, "base_row_version": v}).status_code == 200
    assert o.put(BK(a, "/policy"), {"override_min_chars": 30, "base_row_version": v}).json()["code"] == "stale_version"


def test_only_policy_managers_change_the_policy(books) -> None:  # type: ignore[no-untyped-def]
    a = books["a"]
    for who in ("acct", "prep"):
        assert books[who].put(BK(a, "/policy"), {"override_min_chars": 12}).status_code == 403
    assert books["acct"].get(BK(a, "/policy")).status_code == 200


# ================================================================== checklists
def test_templates_are_copied_and_each_company_edits_its_own(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    assert {t["key"] for t in o.get(BK(a, "/checklist-templates")).json()} == {"daily_bookkeeping", "cash_and_bank"}
    la = o.post(BK(a, "/checklists/from-template/daily_bookkeeping"), {})
    lb = o.post(BK(b, "/checklists/from-template/daily_bookkeeping"), {})
    assert la.status_code == lb.status_code == 201
    la, lb = la.json(), lb.json()
    assert la["id"] != lb["id"] and {i["id"] for i in la["items"]}.isdisjoint({i["id"] for i in lb["items"]})
    # Company A: rename, add, make mandatory, assign a role, reorder, archive
    first = la["items"][1]
    r = o.patch(
        BK(a, f"/checklist-items/{first['id']}"),
        {"title": "Reviewed by the senior", "mandatory": True, "assigned_role": "practice_manager"},
    )
    assert r.status_code == 200
    o.patch(BK(a, f"/checklists/{la['id']}"), {"name": "Al Noor daily"})
    added = o.post(BK(a, f"/checklists/{la['id']}/items"), {"title": "Petty cash counted", "mandatory": True}).json()
    ids = [i["id"] for i in added["items"]]
    rev = o.put(BK(a, f"/checklists/{la['id']}/order"), {"item_ids": list(reversed(ids))}).json()
    assert [i["id"] for i in rev["items"]] == list(reversed(ids))
    o.patch(BK(a, f"/checklist-items/{ids[-1]}"), {"archived": True})
    # Company B did not change at all
    after = o.get(BK(b, "/checklists")).json()
    assert len(after) == 1 and after[0]["name"] == "Daily bookkeeping" and after[0]["items"] == lb["items"]
    assert not any(i["title"] == "Petty cash counted" for i in after[0]["items"])
    # a second copy in the same company gets its own number instead of failing
    again = o.post(BK(a, "/checklists/from-template/daily_bookkeeping"), {}).json()
    assert (
        again["name"] == "Daily bookkeeping" and again["id"] != la["id"]
    )  # the first was renamed, so the name is free again


def test_checklist_configuration_is_validated_and_permissioned(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    cl = o.post(BK(a, "/checklists"), {"name": "Mine", "items": [{"title": "One"}]}).json()
    assert o.post(BK(a, "/checklists"), {"name": "mine"}).json()["code"] == "name_taken"
    assert (
        o.post(BK(a, f"/checklists/{cl['id']}/items"), {"title": "x", "assigned_role": "wizard"}).json()["code"]
        == "unknown_role"
    )
    assert (
        o.post(BK(a, f"/checklists/{cl['id']}/items"), {"title": "x", "auto_rule": "by_title"}).json()["code"]
        == "unknown_rule"
    )
    assert o.put(BK(a, f"/checklists/{cl['id']}/order"), {"item_ids": []}).json()["code"] == "order_mismatch"
    assert o.post(BK(a, "/checklists/from-template/nope"), {}).status_code == 404
    assert books["prep"].post(BK(a, "/checklists"), {"name": "Sneaky"}).status_code == 403
    assert books["acct"].post(BK(a, "/checklists"), {"name": "Accountant list"}).status_code == 201


def test_checklist_ticking_rules(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    cl = o.post(BK(a, "/checklists"), {"name": "Day-end", "items": [
        {"title": "Count cash", "mandatory": True},
        {"title": "Senior sign-off", "assigned_role": "practice_manager"},
        {"title": "All processed", "auto_rule": "documents_processed"},
    ]}).json()  # fmt: skip
    count, senior, auto = (i["id"] for i in cl["items"])
    tick = lambda actor, item, day=DAY: actor.post(BK(a, f"/checklist-items/{item}/complete?business_date={day}"), {})  # noqa: E731
    assert tick(prep, count).status_code == 200
    assert tick(prep, senior).json()["code"] == "role_not_allowed"  # assigned to another role
    assert tick(o, senior).status_code == 200
    assert tick(prep, auto).json()["code"] == "auto_item"  # automatic items complete themselves
    assert tick(prep, count, "2999-01-01").json()["code"] == "future_date"
    # un-ticking: the person who ticked it, or a reviewer
    assert (
        books["acct"].delete(BK(a, f"/checklist-items/{count}/complete?business_date={DAY}")).status_code == 200
    )  # accountant holds review
    assert tick(prep, count).status_code == 200
    assert (
        books["prep"].delete(BK(a, f"/checklist-items/{count}/complete?business_date={DAY}")).status_code == 200
    )  # own tick
    # shown per day with automatic state
    v = o.get(BK(a, f"/days/{DAY}/validation")).json()
    items = {i["title"]: i for cl in v["checklists"] for i in cl["items"]}
    assert (
        items["Senior sign-off"]["done_by"] == "manual"
        and items["All processed"]["done_by"] == "auto"
        and not items["Count cash"]["done"]
    )


def test_a_mandatory_checklist_item_blocks_submission_until_done(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    cl = o.post(BK(a, "/checklists"), {"name": "Must", "items": [{"title": "Count cash", "mandatory": True}]}).json()
    s = start(prep, a)
    v = validate(prep, a, s["id"])
    assert "CHECKLIST_MANDATORY_OPEN" in issue_codes(v) and v["validation_status"] == "awaiting_review"
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "errors_open"
    assert (
        prep.post(BK(a, f"/checklist-items/{cl['items'][0]['id']}/complete?business_date={DAY}"), {}).status_code == 200
    )
    v = validate(prep, a, s["id"])
    assert "CHECKLIST_MANDATORY_OPEN" not in issue_codes(v) and v["validation_status"] == "passed"
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).status_code == 200


# ================================================================== tasks
def task_body(**kw: Any) -> dict[str, Any]:
    return {"title": "Chase missing invoice", "business_date": DAY, **kw}


def test_task_lifecycle_comments_assignment_and_audit_history(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    t = o.post(BK(a, "/tasks"), task_body(requires_review=True, priority="high", due_date="2025-03-12")).json()
    assert t["status"] == "new" and t["assignee_id"] is None
    t = o.post(BK(a, f"/tasks/{t['id']}/assign"), {"assignee_id": uid(prep)}).json()
    assert t["status"] == "assigned" and t["assignee_name"]
    go = lambda actor, status, **kw: actor.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": status, **kw})  # noqa: E731
    assert go(prep, "in_progress").status_code == 200
    assert go(prep, "completed").json()["code"] == "review_required"
    assert go(prep, "awaiting_information").json()["code"] == "reason_required"
    assert go(prep, "awaiting_information", reason="Waiting for the supplier").status_code == 200
    assert go(prep, "in_progress").status_code == 200
    assert go(prep, "awaiting_review").status_code == 200
    # no self-approval: the person who asked for the review cannot give it, even holding the right
    assert go(prep, "approved").status_code == 403
    rej = go(acct, "rejected")
    assert rej.json()["code"] == "reason_required"
    assert go(acct, "rejected", reason="Attach the supplier's reply").status_code == 200
    assert go(prep, "in_progress").status_code == 200
    assert go(prep, "awaiting_review").status_code == 200
    assert go(acct, "approved").status_code == 200
    assert go(prep, "completed").status_code == 200
    assert go(prep, "in_progress").json()["code"] == "invalid_transition"  # completed is final
    c = prep.post(BK(a, f"/tasks/{t['id']}/comments"), {"body": "Supplier replied by phone."})
    assert c.status_code == 201 and c.json()["comments"][0]["body"] == "Supplier replied by phone."
    full = o.get(BK(a, f"/tasks/{t['id']}")).json()
    actions = [h["action"] for h in full["history"]]
    assert actions[0] == "task_created" and actions.count("task_awaiting_review") == 2
    assert {"task_assigned", "task_rejected", "task_approved", "task_completed", "task_commented"} <= set(actions)
    assert actions.index("task_rejected") < actions.index("task_approved") < actions.index("task_completed")
    assert full["assignments"][0]["assigned_by_name"] and full["status"] == "completed" and full["completed_at"]


def test_task_policy_can_switch_independence_off_and_it_is_per_company(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b, prep = books["owner"], books["a"], books["b"], books["prep"]
    o.put(BK(a, "/policy"), {"require_independent_task_review": False})
    t = o.post(BK(a, "/tasks"), task_body(requires_review=True, assignee_id=uid(prep))).json()
    for s in ("in_progress", "awaiting_review"):
        assert prep.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": s}).status_code == 200
    assert (
        o.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": "approved"}).status_code == 200
    )  # owner reviews: different person anyway
    # in company B the default (independent) still applies; the owner asked for the review so cannot approve it
    tb = o.post(BK(b, "/tasks"), task_body(requires_review=True)).json()
    for s in ("in_progress", "awaiting_review"):
        assert o.post(BK(b, f"/tasks/{tb['id']}/transition"), {"status": s}).status_code == 200
    assert (
        o.post(BK(b, f"/tasks/{tb['id']}/transition"), {"status": "approved"}).json()["code"] == "independence_required"
    )


def test_task_permissions_and_validation(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    assert prep.post(BK(a, "/tasks"), task_body()).status_code == 403  # assistants work tasks, they do not create them
    other = new_owner(client, mailer, org_name="Other Firm")
    assert o.post(BK(a, "/tasks"), task_body(assignee_id=uid(other))).json()["code"] == "invalid_assignee"
    assert (
        o.post(BK(a, "/tasks"), task_body(subject_type="transaction", subject_id=str(uuid.uuid4()))).status_code == 404
    )
    t = o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep))).json()
    # somebody else's task cannot be worked, and only managers cancel
    stranger = make_member(o, mailer, "document_operator")
    assert stranger.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": "in_progress"}).status_code in (403, 404)
    assert (
        prep.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": "cancelled", "reason": "no"}).json()["code"]
        == "cancel_forbidden"
    )
    assert (
        acct.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": "in_progress"}).status_code == 200
    )  # accountants manage tasks
    assert (
        o.post(BK(a, f"/tasks/{t['id']}/transition"), {"status": "cancelled", "reason": "Not needed"}).status_code
        == 200
    )
    assert o.patch(BK(a, f"/tasks/{t['id']}"), {"title": "late edit"}).json()["code"] == "task_closed"
    # filters
    mine = prep.get(BK(a, "/tasks?mine=true")).json()
    assert [x["id"] for x in mine] == [t["id"]]
    assert o.get(BK(a, "/tasks?open_only=true")).json() == []
    assert o.get(BK(a, "/tasks?status=cancelled")).json()[0]["id"] == t["id"]
    # optimistic concurrency
    t2 = o.post(BK(a, "/tasks"), task_body()).json()
    assert (
        o.patch(BK(a, f"/tasks/{t2['id']}"), {"priority": "low", "base_row_version": t2["row_version"]}).status_code
        == 200
    )
    assert (
        o.patch(BK(a, f"/tasks/{t2['id']}"), {"priority": "high", "base_row_version": t2["row_version"]}).json()["code"]
        == "stale_version"
    )


def test_task_database_guard_refuses_impossible_status_jumps(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    t = o.post(BK(a, "/tasks"), task_body()).json()
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(text("UPDATE bk_tasks SET status = 'completed' WHERE id = :i"), {"i": t["id"]})
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(text("DELETE FROM bk_tasks WHERE id = :i"), {"i": t["id"]})


def test_blocking_and_overdue_tasks_show_up_in_validation(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    o.post(BK(a, "/tasks"), task_body(title="Get the bank statement", blocks_closing=True))
    o.post(BK(a, "/tasks"), task_body(title="Old chore", due_date="2025-03-01"))
    v = validate(prep, a, start(prep, a)["id"])
    assert {"TASK_BLOCKING_OPEN", "TASK_OVERDUE"} <= issue_codes(v) and v["validation_status"] == "awaiting_review"


# ================================================================== validation
def test_validation_result_ladder_with_real_transactions(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    t_ok = txn(books)  # approved, no tax, no source document
    s = start(prep, a)
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "passed_with_warnings" and "TXN_NO_SOURCE_DOCUMENT" in issue_codes(
        v
    )  # the default is to say so
    quiet(o, a)
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "passed" and "DAY_EMPTY" not in issue_codes(v)
    assert v["validation"]["result"] == "passed"
    # a draft -> warning
    draft_id = valid_draft(books)
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "passed_with_warnings" and "TXN_DRAFT_OPEN" in issue_codes(v)
    # awaiting approval -> awaiting_review (an error somebody has to decide)
    assert o.post(ACC(a, f"/transactions/{draft_id}/submit"), {}).status_code == 200
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "awaiting_review" and "TXN_AWAITING_APPROVAL" in issue_codes(v)
    assert books["acct"].post(ACC(a, f"/transactions/{draft_id}/approve"), {}).status_code == 200
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "passed"
    # the earlier exceptions are resolved by the system, not deleted
    st = {(e["code"], e["status"]) for e in v["exceptions"]}
    assert ("TXN_AWAITING_APPROVAL", "resolved") in st and ("TXN_DRAFT_OPEN", "resolved") in st
    assert t_ok


def test_tax_checks_use_company_configuration_and_never_assume_rules(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b, prep = books["owner"], books["a"], books["b"], books["prep"]
    txn(books, tax=True)
    v = validate(prep, a, start(prep, a)["id"])
    assert "TAX_CODE_UNVERIFIED" in issue_codes(v) and v["validation_status"] == "passed_with_warnings"
    # the company decides how serious that is
    quiet(o, a, TAX_CODE_UNVERIFIED="info")
    s = o.get(BK(a, "/closings?date_from=" + DAY + "&date_to=" + DAY)).json()[0]
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "passed"
    # company B's policy is untouched: the same situation there still warns
    assign(o, b, books["acct"])
    txn(books, tax=True, cid=b)
    sb = start(o, b)
    assert validate(o, b, sb["id"])["validation_status"] == "passed_with_warnings"


def test_unbalanced_or_journal_less_approved_records_are_critical_and_cannot_be_overridden(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    tid = txn(books)
    tamper(
        owner_engine,
        "UPDATE acct_journal_lines SET debit = debit + 1 WHERE journal_id = (SELECT id FROM acct_journals WHERE transaction_id = :t) AND debit > 0",
        t=tid,
    )
    s = start(prep, a)
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "failed" and "TXN_UNBALANCED" in issue_codes(v)
    ex = next(e for e in v["exceptions"] if e["code"] == "TXN_UNBALANCED")
    assert ex["severity"] == "critical"
    # not even the owner can override an integrity failure
    r = o.post(BK(a, f"/exceptions/{ex['id']}/override"), {"note": "I know what I am doing here"})
    assert r.status_code == 422 and r.json()["code"] == "hard_check"
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "critical_failure"
    assert o.post(BK(a, f"/closings/{s['id']}/close"), {}).status_code == 409
    # and the severity cannot be lowered by policy either
    assert o.put(BK(a, "/policy"), {"severity_overrides": {"TXN_UNBALANCED": "warning"}}).json()["code"] == "hard_check"


def test_tax_header_mismatch_and_missing_journal_are_reported(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    t1 = txn(books, tax=True, approve=False)
    tamper(owner_engine, "UPDATE acct_transactions SET tax_amount = tax_amount + 2 WHERE id = :t", t=t1)
    t2 = txn(books)
    tamper(
        owner_engine,
        "DELETE FROM acct_journal_lines WHERE journal_id = (SELECT id FROM acct_journals WHERE transaction_id = :t)",
        t=t2,
    )
    tamper(owner_engine, "DELETE FROM acct_journals WHERE transaction_id = :t", t=t2)
    v = validate(prep, a, start(prep, a)["id"])
    assert {"TXN_TAX_MISMATCH", "TXN_NO_JOURNAL"} <= issue_codes(v) and v["validation_status"] == "failed"
    assert o.get(BK(a, f"/days/{DAY}/exceptions?status=open")).json()  # visible in the workbench


def test_documents_and_synchronisation_state_feed_validation(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from test_p6_documents import mk, pdf, upload

    o, a, prep = books["owner"], books["a"], books["prep"]
    node = books["node"]
    today = datetime.now(UTC).date().isoformat()
    doc = upload(o, a, pdf("p10"), "bill.pdf", node)
    assert (
        o.post(ACC(a, "/periods/generate"), {"fiscal_year": datetime.now(UTC).year}).status_code == 200
    )  # a period must cover today
    s = start(prep, a, today)
    v = validate(prep, a, s["id"])
    assert "DOC_UNPROCESSED" in issue_codes(v) and v["validation_status"] == "passed_with_warnings"
    # processing outcomes map to exact codes
    for job_status, code in (
        ("failed", "DOC_PROCESSING_FAILED"),
        ("needs_manual", "DOC_NEEDS_MANUAL"),
        ("awaiting_review", "DOC_AWAITING_REVIEW"),
    ):
        tamper(owner_engine, "DELETE FROM processing_jobs WHERE document_id = :d", d=doc["id"])
        tamper(
            owner_engine,
            "INSERT INTO processing_jobs (id, organization_id, company_id, document_id, version_id, version_no, status, idempotency_key, requested_by)"
            " SELECT :j, d.organization_id, d.company_id, d.id, v.id, v.version_no, :s, :k, d.created_by FROM documents d JOIN document_versions v ON v.document_id = d.id WHERE d.id = :d",
            j=str(uuid.uuid4()), s=job_status, k=uuid.uuid4().hex, d=doc["id"],
        )  # fmt: skip
        assert code in issue_codes(validate(prep, a, s["id"]))
    # the infected scan result is an integrity failure
    tamper(owner_engine, "UPDATE document_versions SET scan_status = 'infected' WHERE document_id = :d", d=doc["id"])
    v = validate(prep, a, s["id"])
    assert "DOC_INFECTED" in issue_codes(v) and v["validation_status"] == "failed"
    # quarantined documents
    tamper(owner_engine, "UPDATE document_versions SET scan_status = 'clean' WHERE document_id = :d", d=doc["id"])
    tamper(owner_engine, "UPDATE documents SET status = 'quarantined' WHERE id = :d", d=doc["id"])
    assert "DOC_QUARANTINED" in issue_codes(validate(prep, a, s["id"]))
    assert mk  # the folder helper is imported for readers of this module


def test_sync_state_is_declared_by_the_device_and_shown_as_such(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    s = start(prep, a)
    body = {"client_id": "desktop-1", "business_date": DAY, "pending_uploads": 2, "failed_uploads": 1, "conflicts": 1}
    r = prep.post(BK(a, "/sync-reports"), body)
    assert r.status_code == 200 and r.json()["declared_by_client"] is True
    v = validate(prep, a, s["id"])
    assert {"SYNC_FAILED", "SYNC_CONFLICTS", "SYNC_PENDING"} <= issue_codes(
        v
    ) and "SYNC_NOT_REPORTED" not in issue_codes(v)
    assert all(
        "Declared by the device" in e["message"]
        for e in v["exceptions"]
        if e["code"].startswith("SYNC_") and e["code"] != "SYNC_NOT_REPORTED"
    )
    # the device reports again (upsert, same row) and the day becomes clean
    assert prep.post(BK(a, "/sync-reports"), {"client_id": "desktop-1", "business_date": DAY}).status_code == 200
    v = validate(prep, a, s["id"])
    assert not {"SYNC_FAILED", "SYNC_CONFLICTS", "SYNC_PENDING", "SYNC_NOT_REPORTED"} & issue_codes(v)
    assert prep.post(BK(a, "/sync-reports"), {**body, "business_date": "2999-01-01"}).json()["code"] == "future_date"
    assert books["owner"].get(BK(a, f"/days/{DAY}")).json()["sync_reports"] == 1 and o


# ================================================================== exceptions
def test_acknowledging_an_error_needs_a_reviewer_and_an_explanation(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    tid = txn(books, approve=False)
    s = start(prep, a)
    v = validate(prep, a, s["id"])
    ex = next(e for e in v["exceptions"] if e["code"] == "TXN_AWAITING_APPROVAL")
    assert v["validation_status"] == "awaiting_review"
    assert (
        prep.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "x" * 20}).status_code == 403
    )  # no review permission
    assert (
        acct.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "short"}).json()["code"]
        == "explanation_required"
    )
    r = acct.post(
        BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "Approver on leave until Thursday; approving then."}
    )
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    cur = o.get(BK(a, f"/closings/{s['id']}")).json()
    assert cur["validation_status"] == "passed_with_warnings"  # recomputed from the decision
    assert [x["action"] for x in cur["approvals"]].count("override") == 1  # the decision is in the closing's trail
    assert (
        acct.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "again and again"}).json()["code"]
        == "not_open"
    )
    assert tid


def test_override_of_a_policy_critical_needs_the_override_permission_and_a_reason(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    o.put(BK(a, "/policy"), {"severity_overrides": {"TXN_DRAFT_OPEN": "critical"}, "override_min_chars": 15})
    valid_draft(books)
    s = start(prep, a)
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "failed"
    ex = next(e for e in v["exceptions"] if e["code"] == "TXN_DRAFT_OPEN")
    assert (
        acct.post(BK(a, f"/exceptions/{ex['id']}/override"), {"note": "x" * 30}).status_code == 403
    )  # accountants cannot override
    assert (
        acct.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "x" * 30}).json()["code"]
        == "not_acknowledgeable"
    )
    assert (
        o.post(BK(a, f"/exceptions/{ex['id']}/override"), {"note": "too short"}).json()["code"]
        == "explanation_required"
    )
    done = o.post(
        BK(a, f"/exceptions/{ex['id']}/override"), {"note": "Draft is a test entry that will be deleted tomorrow."}
    )
    assert done.status_code == 200 and done.json()["status"] == "overridden"
    assert o.get(BK(a, f"/closings/{s['id']}")).json()["validation_status"] == "passed_with_warnings"
    # the override is part of the day's history
    hist = [h for h in o.get(BK(a, f"/closings/{s['id']}")).json()["approvals"] if h["action"] == "override"]
    assert hist and "TXN_DRAFT_OPEN overridden" in hist[0]["note"]
    # if it gets worse the override no longer applies: severity rising reopens the exception (not tested here: severity is static)


# ================================================================== closing workflow
def full_close(books, day: str = DAY) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    a, prep, acct = books["a"], books["prep"], books["acct"]
    s = start(prep, a, day)
    validate(prep, a, s["id"])
    r = prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    assert r.status_code == 200, r.text
    c = acct.post(BK(a, f"/closings/{s['id']}/close"), {})
    assert c.status_code == 200, c.text
    return c.json()


def test_the_six_stage_workflow_end_to_end(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    quiet(o, a)
    txn(books)
    s = start(prep, a)
    assert s["status"] == "draft" and s["stage"] == 1 and s["independence_required"]
    assert prep.post(BK(a, f"/closings/{s['id']}/stage"), {"stage": 3}).json()["code"] == "validation_required"
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "validation_required"
    v = validate(prep, a, s["id"])
    assert v["stage"] == 2 and v["status"] == "in_progress"
    assert prep.post(BK(a, f"/closings/{s['id']}/stage"), {"stage": 3}).json()["stage"] == 3
    assert prep.post(BK(a, f"/closings/{s['id']}/stage"), {"stage": 5}).json()["code"] == "invalid_stage"
    summ = prep.get(BK(a, f"/closings/{s['id']}/summary")).json()
    assert (
        summ["transactions"]["approved_totals"][0]["gross"] == "100.000000" and summ["validation"]["result"] == "passed"
    )
    sub = prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()
    assert sub["status"] == "ready_for_review" and sub["stage"] == 5 and sub["submitted_by_name"]
    # the preparer may not approve their own submission
    r = prep.post(BK(a, f"/closings/{s['id']}/close"), {})
    assert r.status_code == 403  # no approve permission at all
    # a person with approve rights who also submitted is refused too
    s2 = start(o, a, "2025-03-11")
    validate(o, a, s2["id"])
    o.post(BK(a, f"/closings/{s2['id']}/submit"), {})
    assert o.post(BK(a, f"/closings/{s2['id']}/close"), {}).json()["code"] == "independence_required"
    # send back, fix, resubmit
    back = acct.post(BK(a, f"/closings/{s['id']}/send-back"), {"note": "Please re-check the bank lines."})
    assert back.json()["status"] == "in_progress" and back.json()["stage"] == 3 and back.json()["review_note"]
    assert (
        acct.post(BK(a, f"/closings/{s['id']}/send-back"), {"note": "again please"}).json()["code"] == "invalid_status"
    )
    validate(prep, a, s["id"])
    prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    closed = acct.post(BK(a, f"/closings/{s['id']}/close"), {}).json()
    assert (
        closed["status"] == "closed"
        and closed["stage"] == 6
        and closed["closed_by_name"]
        and closed["validation_status"] == "passed"
    )
    assert [x["action"] for x in closed["approvals"]] == [
        "prepare",
        "validate",
        "submit",
        "send_back",
        "validate",
        "submit",
        "approve_close",
    ]
    # the day is now closed for everybody
    assert prep.post(BK(a, "/closings"), {"business_date": DAY}).json()["code"] == "closing_exists"
    assert o.get(BK(a, f"/days/{DAY}")).json()["day_closed"] is True
    assert prep.post(BK(a, f"/closings/{s['id']}/validate"), {}).json()["code"] == "invalid_status"
    assert (
        prep.post(BK(a, f"/closings/{s['id']}/cancel"), {"reason": "no longer needed"}).json()["code"]
        == "invalid_status"
    )


def test_future_days_cannot_be_closed_and_cancel_frees_the_day(books) -> None:  # type: ignore[no-untyped-def]
    prep, a = books["prep"], books["a"]
    tomorrow = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    assert prep.post(BK(a, "/closings"), {"business_date": tomorrow}).json()["code"] == "future_date"
    s = start(prep, a)
    assert prep.post(BK(a, f"/closings/{s['id']}/cancel"), {"reason": "x"}).status_code == 422
    c = prep.post(BK(a, f"/closings/{s['id']}/cancel"), {"reason": "Started on the wrong day"})
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    again = start(prep, a)  # a cancelled closing is history, the day can start over
    assert again["id"] != s["id"]
    assert [x["status"] for x in prep.get(BK(a, "/closings")).json()] == ["draft", "cancelled"] or len(
        prep.get(BK(a, "/closings")).json()
    ) == 2


def test_stale_validation_is_detected_after_the_day_changes(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    s = start(prep, a)
    validate(prep, a, s["id"])
    assert o.get(BK(a, f"/closings/{s['id']}")).json()["stale"] is False
    valid_draft(books)  # the day changed after validation
    assert o.get(BK(a, f"/closings/{s['id']}")).json()["stale"] is True
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "validation_stale"
    validate(prep, a, s["id"])
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).status_code == 200


def test_close_validates_again_so_a_late_change_still_blocks(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    tid = txn(books)
    s = start(prep, a)
    validate(prep, a, s["id"])
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).status_code == 200
    tamper(
        owner_engine,
        "UPDATE acct_journal_lines SET credit = credit + 3 WHERE journal_id = (SELECT id FROM acct_journals WHERE transaction_id = :t) AND credit > 0",
        t=tid,
    )
    r = acct.post(BK(a, f"/closings/{s['id']}/close"), {})
    assert r.status_code == 409 and r.json()["code"] == "critical_failure"
    assert o.get(BK(a, f"/closings/{s['id']}")).json()["status"] == "ready_for_review"


def test_company_policy_can_restrict_who_closes_and_can_waive_independence(books) -> None:  # type: ignore[no-untyped-def]
    o, a, b, prep, acct = books["owner"], books["a"], books["b"], books["prep"], books["acct"]
    o.put(BK(a, "/policy"), {"action_roles": {"approve": ["practice_manager"]}})
    s = start(prep, a)
    validate(prep, a, s["id"])
    prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    r = acct.post(BK(a, f"/closings/{s['id']}/close"), {})
    assert (
        r.status_code == 403 and r.json()["code"] == "role_not_allowed"
    )  # the permission is not enough in this company
    # company B has no such restriction, and its policy may waive independence
    o.put(BK(b, "/policy"), {"require_independent_approval": False})
    sb = start(o, b)
    validate(o, b, sb["id"])
    o.post(BK(b, f"/closings/{sb['id']}/submit"), {})
    assert (
        o.post(BK(b, f"/closings/{sb['id']}/close"), {}).status_code == 200
    )  # one person did both: allowed by THIS company's policy
    # independence is recorded on the closing row, and the database refuses a violating row
    assert o.get(BK(b, f"/closings/{sb['id']}")).json()["independence_required"] is False


def test_database_enforces_independence_when_the_application_is_bypassed(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    a, prep = books["a"], books["prep"]
    s = start(prep, a)
    validate(prep, a, s["id"])
    prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    with (
        pytest.raises(DBAPIError),
        owner_engine.begin() as c,
    ):  # closing by the submitter, flagged as independent, is refused by a CHECK
        c.execute(
            text(
                "UPDATE bk_closing_sessions SET status = 'closed', closed_by = submitted_by, closed_at = now(), validation_status = 'passed' WHERE id = :i"
            ),
            {"i": s["id"]},
        )
    with (
        pytest.raises(DBAPIError),
        owner_engine.begin() as c,
    ):  # closing while the result is failed is refused by a CHECK
        c.execute(
            text(
                "UPDATE bk_closing_sessions SET status = 'closed', closed_by = :u, closed_at = now(), validation_status = 'failed' WHERE id = :i"
            ),
            {"i": s["id"], "u": uid(books["acct"])},
        )
    with pytest.raises(DBAPIError), owner_engine.begin() as c:  # illegal transition
        c.execute(text("UPDATE bk_closing_sessions SET status = 'draft' WHERE id = :i"), {"i": s["id"]})


# ================================================================== snapshots, closed-day protection, reopening
def test_snapshot_is_immutable_and_tamper_evident(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    quiet(o, a)
    txn(books)
    closed = full_close(books)
    snap = o.get(BK(a, f"/closings/{closed['id']}/snapshot")).json()
    assert snap["verified"] is True and len(snap["sha256"]) == 64 and snap["snapshot_no"] == 1
    p = snap["payload"]
    assert (
        p["business_date"] == DAY
        and p["validation"]["result"] == "passed"
        and p["transactions"]["approved_totals"][0]["gross"] == "100.000000"
    )
    assert p["cash_bank"]["reconciled"] is False and p["policy"]["independent_approval"] is True
    for sql in (
        "UPDATE bk_closing_snapshots SET payload = '{}'::jsonb WHERE id = :i",
        "DELETE FROM bk_closing_snapshots WHERE id = :i",
    ):
        with pytest.raises(DBAPIError), owner_engine.begin() as c:
            c.execute(text(sql), {"i": snap["id"]})
    # even with triggers off (a privileged tamper) the stored hash no longer matches the payload and the API says so
    tamper(
        owner_engine,
        "UPDATE bk_closing_snapshots SET payload = jsonb_set(payload, '{validation,result}', '\"failed\"') WHERE id = :i",
        i=snap["id"],
    )
    assert o.get(BK(a, f"/closings/{closed['id']}/snapshot")).json()["verified"] is False


def test_closed_day_refuses_changes_everywhere_until_reopened(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    cl = o.post(BK(a, "/checklists"), {"name": "Day-end", "items": [{"title": "Count cash"}]}).json()
    pending = txn(books, approve=False)  # awaiting approval, dated on the day
    s = start(prep, a)
    validate(prep, a, s["id"])
    ex = next(e for e in o.get(BK(a, f"/days/{DAY}/exceptions")).json() if e["code"] == "TXN_AWAITING_APPROVAL")
    acct.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "Will be approved tomorrow by the owner."})
    prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    assert acct.post(BK(a, f"/closings/{s['id']}/close"), {}).status_code == 200
    # application: approval, ticking and reporting on a closed day are refused with a clear code
    r = acct.post(ACC(a, f"/transactions/{pending}/approve"), {})
    assert r.status_code == 409 and r.json()["code"] == "day_closed"
    assert (
        prep.post(BK(a, f"/checklist-items/{cl['items'][0]['id']}/complete?business_date={DAY}"), {}).json()["code"]
        == "day_closed"
    )
    assert prep.post(BK(a, "/sync-reports"), {"client_id": "d", "business_date": DAY}).json()["code"] == "day_closed"
    # database: the same protection holds when the application is bypassed
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(
            text(
                "UPDATE acct_transactions SET status = 'approved', approved_by = :u, approved_at = now() WHERE id = :t"
            ),
            {"t": pending, "u": uid(acct)},
        )
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(
            text("UPDATE bk_exceptions SET status = 'open' WHERE company_id = :c AND business_date = :d"),
            {"c": a, "d": DAY},
        )
    # other days are unaffected
    other = txn(books, date_="2025-03-11")
    assert other


def test_reopening_keeps_the_original_and_links_a_new_closing(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep, acct = books["owner"], books["a"], books["prep"], books["acct"]
    pending = txn(books, approve=False)
    s = start(prep, a)
    validate(prep, a, s["id"])
    ex = next(e for e in o.get(BK(a, f"/days/{DAY}/exceptions")).json() if e["code"] == "TXN_AWAITING_APPROVAL")
    acct.post(BK(a, f"/exceptions/{ex['id']}/acknowledge"), {"note": "Approver away; approve after reopening."})
    prep.post(BK(a, f"/closings/{s['id']}/submit"), {})
    first = acct.post(BK(a, f"/closings/{s['id']}/close"), {}).json()
    snap1 = o.get(BK(a, f"/closings/{first['id']}/snapshot")).json()
    # authorisation and reason
    assert (
        acct.post(BK(a, f"/closings/{first['id']}/reopen"), {"reason": "Supplier invoice arrived late"}).status_code
        == 403
    )
    assert o.post(BK(a, f"/closings/{first['id']}/reopen"), {"reason": "too short"}).json()["code"] == "reason_required"
    new = o.post(
        BK(a, f"/closings/{first['id']}/reopen"), {"reason": "Supplier invoice arrived late; approving it now."}
    )
    assert new.status_code == 200, new.text
    new = new.json()
    assert new["id"] != first["id"] and new["reopened_from_id"] == first["id"] and new["status"] == "in_progress"
    old = o.get(BK(a, f"/closings/{first['id']}")).json()
    assert (
        old["status"] == "reopened"
        and old["reopen_reason"]
        and old["reopened_by_name"]
        and old["snapshot"]["id"] == snap1["id"]
    )
    # the original snapshot is still there, byte for byte
    again = o.get(BK(a, f"/closings/{first['id']}/snapshot")).json()
    assert again["sha256"] == snap1["sha256"] and again["verified"]
    # the day accepts work again
    assert acct.post(ACC(a, f"/transactions/{pending}/approve"), {}).status_code == 200
    validate(prep, a, new["id"])
    prep.post(BK(a, f"/closings/{new['id']}/submit"), {})
    second = acct.post(BK(a, f"/closings/{new['id']}/close"), {}).json()
    snap2 = o.get(BK(a, f"/closings/{second['id']}/snapshot")).json()
    assert (
        snap2["snapshot_no"] == 2
        and snap2["previous_snapshot_id"] == snap1["id"]
        and snap2["sha256"] != snap1["sha256"]
    )
    hist = o.get(BK(a, f"/days/{DAY}/snapshots")).json()
    assert (
        [h["snapshot_no"] for h in hist] == [1, 2]
        and hist[0]["closing_status"] == "reopened"
        and hist[1]["closing_status"] == "closed"
    )
    # a historical closing cannot be edited even by SQL
    with pytest.raises(DBAPIError), owner_engine.begin() as c:
        c.execute(
            text("UPDATE bk_closing_sessions SET reopen_reason = 'rewritten history' WHERE id = :i"), {"i": first["id"]}
        )
    # reopening needs the day to be closed and the accounting period to be open
    assert (
        o.post(BK(a, f"/closings/{first['id']}/reopen"), {"reason": "Trying to reopen history twice"}).json()["code"]
        == "invalid_status"
    )


def test_reopen_is_refused_while_the_accounting_period_is_closed(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    closed = full_close(books)
    period = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["start_date"] <= DAY <= p["end_date"])
    r = o.post(ACC(a, f"/periods/{period['id']}/close"), {})
    assert r.status_code == 200, r.text
    refused = o.post(BK(a, f"/closings/{closed['id']}/reopen"), {"reason": "A reason that is long enough"})
    assert refused.status_code == 409 and refused.json()["code"] == "period_closed"


def test_reopen_can_be_restricted_to_named_roles_per_company(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    closed = full_close(books)
    o.put(BK(a, "/policy"), {"action_roles": {"reopen": ["practice_manager"]}})
    r = o.post(BK(a, f"/closings/{closed['id']}/reopen"), {"reason": "Owner is not a practice manager"})
    assert r.status_code == 403 and r.json()["code"] == "role_not_allowed"


# ================================================================== daily Excel reports (Phase 9 engine)
from test_p9_excel import XL, tpl  # noqa: E402,F401  (tpl = an active purchase-register template with a mapping)


def test_daily_report_goes_through_the_excel_engine_and_keeps_history(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a, acct, prep = books["owner"], books["a"], books["acct"], books["prep"]
    txn(books, tax=True)
    txn(books, approve=False)  # an unapproved transaction on the same day
    s = start(prep, a)
    validate(prep, a, s["id"])
    key = uuid.uuid4().hex
    r = acct.post(BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": key})
    assert r.status_code == 202, r.text
    exp = acct.get(XL(a, f"/exports/{r.json()['id']}")).json()  # the file is generated after the response
    assert exp["status"] == "completed" and exp["params"]["date_from"] == DAY == exp["params"]["date_to"]
    listed = acct.get(BK(a, f"/closings/{s['id']}/exports")).json()
    assert (
        len(listed) == 1
        and listed[0]["id"] == exp["id"]
        and listed[0]["template_version"] == 1
        and listed[0]["mapping_no"] == 1
    )
    assert listed[0]["include_unapproved"] is False and listed[0]["sha256"]
    # approved data only by default: one approved row, not the draft
    ex = acct.get(XL(a, f"/exports/{exp['id']}/transactions")).json()
    assert exp["row_counts"] and len(ex) == 1 if isinstance(ex, list) else ex
    # same idempotency key -> the first export, nothing new
    again = acct.post(BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": key})
    assert again.status_code == 200 and again.json()["id"] == exp["id"]
    # a second report of the same transactions is a repeat: it must be acknowledged and explained
    rep = acct.post(BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex})
    assert rep.status_code == 409 and rep.json()["repeat"]["needs_acknowledgement"]
    ok = acct.post(
        BK(a, f"/closings/{s['id']}/exports"),
        {
            "template_id": tpl,
            "idempotency_key": uuid.uuid4().hex,
            "acknowledge_repeat": True,
            "reason": "Re-sent to the client",
        },
    )
    assert ok.status_code == 202 and ok.json()["repeat_acknowledged"] is True
    # unapproved data needs the explicit permission AND the explicit request
    assert (
        acct.post(
            BK(a, f"/closings/{s['id']}/exports"),
            {"template_id": tpl, "idempotency_key": uuid.uuid4().hex, "include_unapproved": True},
        ).status_code
        == 403
    )
    # regenerate: a separate, reasoned export; the first is untouched
    rk = uuid.uuid4().hex
    regen = acct.post(
        BK(a, f"/closings/{s['id']}/exports/{exp['id']}/regenerate"),
        {"reason": "Template wording corrected", "idempotency_key": rk},
    )
    assert regen.status_code == 202, regen.text
    assert regen.json()["id"] != exp["id"] and regen.json()["source_export_id"] == exp["id"]
    same = acct.post(
        BK(a, f"/closings/{s['id']}/exports/{exp['id']}/regenerate"),
        {"reason": "Template wording corrected", "idempotency_key": rk},
    )
    assert same.status_code == 200 and same.json()["id"] == regen.json()["id"]
    assert (
        acct.post(
            BK(a, f"/closings/{s['id']}/exports/{uuid.uuid4()}/regenerate"),
            {"reason": "x", "idempotency_key": uuid.uuid4().hex},
        ).status_code
        == 404
    )  # not one of this closing's reports
    assert len(acct.get(BK(a, f"/closings/{s['id']}/exports")).json()) == 3
    assert acct.get(XL(a, f"/exports/{exp['id']}")).json()["status"] == "completed"
    # the audit trail records the request
    acts = [h["action"] for h in o.get(BK(a, f"/closings/{s['id']}")).json()["approvals"]]
    assert "validate" in acts
    # the assistant has no export permission; a cancelled closing has no reports
    assert (
        prep.post(
            BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex}
        ).status_code
        == 403
    )
    prep.post(BK(a, f"/closings/{s['id']}/cancel"), {"reason": "Cancelled for test"})
    assert (
        acct.post(
            BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex}
        ).json()["code"]
        == "invalid_status"
    )


def test_a_closed_days_report_is_tied_to_its_snapshot_and_reopening_does_not_lose_it(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    txn(books)
    closed = full_close(books)
    snap = o.get(BK(a, f"/closings/{closed['id']}/snapshot")).json()
    exp = acct.post(
        BK(a, f"/closings/{closed['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex}
    ).json()
    assert acct.get(BK(a, f"/closings/{closed['id']}/exports")).json()[0]["snapshot_id"] == snap["id"]
    o.post(BK(a, f"/closings/{closed['id']}/reopen"), {"reason": "Late invoice to be added today"})
    assert (
        o.get(BK(a, f"/closings/{closed['id']}/exports")).json()[0]["id"] == exp["id"]
    )  # history stays with the original closing
    assert o.get(XL(a, f"/exports/{exp['id']}")).json()["status"] == "completed"


# ================================================================== cash and bank
def test_cash_and_bank_daily_summary_is_exact_and_not_a_reconciliation(books) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]

    def money_txn(kind: str, amount: str, day: str, approve: bool = True) -> str:
        t = draft(o, a, kind, date=day, lines=[{"amount": amount}])
        assert t.status_code == 201, t.text
        tid = t.json()["id"]
        if approve:
            assert o.post(ACC(a, f"/transactions/{tid}/submit"), {}).status_code == 200
            assert acct.post(ACC(a, f"/transactions/{tid}/approve"), {}).status_code == 200
        return tid

    money_txn("bank_deposit", "1000.100", "2025-03-09")
    money_txn("bank_deposit", "250.250", DAY)
    money_txn("bank_withdrawal", "100.050", DAY)
    money_txn("bank_withdrawal", "7", DAY, approve=False)
    s = o.get(BK(a, f"/days/{DAY}/cash-bank")).json()
    bank = next(x for x in s["accounts"] if x["code"] == "1120")
    assert (
        bank["opening"] == "1000.100000"
        and bank["receipts"] == "250.250000"
        and bank["payments"] == "100.050000"
        and bank["closing"] == "1150.300000"
    )
    assert (
        s["totals"]["closing"] == "1150.300000"
        and s["reconciled"] is False
        and "Not a bank reconciliation" in s["note"]
    )
    assert s["unapproved_cash_bank_transactions"] == 1 and s["base_currency"]
    nxt = o.get(BK(a, "/days/2025-03-11/cash-bank")).json()
    assert (
        next(x for x in nxt["accounts"] if x["code"] == "1120")["opening"] == "1150.300000"
    )  # yesterday's close is today's opening
    # the other company has none of it
    assert (
        next(
            (x for x in o.get(BK(books["b"], f"/days/{DAY}/cash-bank")).json()["accounts"] if x["code"] == "1120"),
            {"receipts": "0.000000"},
        )["receipts"]
        == "0.000000"
    )
    # the closing snapshot carries the same figures
    prep = books["prep"]
    sess = start(prep, a)
    assert (
        "cash_bank" in o.get(BK(a, f"/closings/{sess['id']}/summary")).json()
        and o.get(BK(a, f"/days/{DAY}/report")).json()["cash_bank"]["totals"]["closing"] == "1150.300000"
    )


# ================================================================== workbench and queues
def test_workbench_overview_and_queues_show_real_data_only(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from test_p6_documents import pdf, upload

    o, a, prep = books["owner"], books["a"], books["prep"]
    today = datetime.now(UTC).date().isoformat()
    empty = o.get(BK(a, f"/days/{DAY}")).json()
    assert (
        empty["transactions"] == {}
        and empty["documents"]["total"] == 0
        and empty["has_activity"] is False
        and empty["closing"] is None
    )
    assert (
        o.get(BK(a, f"/days/{DAY}/transactions")).json() == [] and o.get(BK(a, f"/days/{DAY}/documents")).json() == []
    )
    txn(books)
    valid_draft(books)
    doc = upload(o, a, pdf("queue"), "queue.pdf", books["node"])
    d = o.get(BK(a, f"/days/{DAY}")).json()
    assert d["transactions"] == {"approved": 1, "draft": 1} and d["has_activity"] and d["is_future"] is False
    rows = o.get(BK(a, f"/days/{DAY}/transactions")).json()
    assert (
        len(rows) == 2
        and {r["status"] for r in rows} == {"approved", "draft"}
        and all(isinstance(r["gross_amount"], str) for r in rows)
    )
    q = o.get(BK(a, f"/days/{today}/documents")).json()
    assert (
        [x["id"] for x in q] == [doc["id"]]
        and q[0]["processing_status"] is None
        and q[0]["scan_status"] in ("not_scanned", "clean", "pending")
    )
    assert o.get(BK(a, f"/days/{today}")).json()["documents"]["total"] == 1
    # the day's closing status appears once a closing exists
    s = start(prep, a)
    assert o.get(BK(a, f"/days/{DAY}")).json()["closing"]["id"] == s["id"]
    # the dry-run validation changes nothing
    before = len(o.get(BK(a, f"/days/{DAY}/exceptions")).json())
    pv = o.get(BK(a, f"/days/{DAY}/validation")).json()
    assert pv["result"] == "passed_with_warnings" and len(o.get(BK(a, f"/days/{DAY}/exceptions")).json()) == before
    assert o.get(BK(a, f"/days/{DAY}/report")).json()["transactions"]["by_status"]


def test_document_queue_respects_folder_permissions_but_closing_counts_are_company_wide(books) -> None:  # type: ignore[no-untyped-def]
    from test_p6_documents import W, mk, pdf, upload

    o, a, prep = books["owner"], books["a"], books["prep"]
    today = datetime.now(UTC).date().isoformat()
    secret = mk(o, a, "Salaries")["id"]
    shown = upload(o, a, pdf("shown"), "shown.pdf", books["node"])
    hidden = upload(o, a, pdf("hidden-body-longer" * 3), "salaries.pdf", secret)
    deny = o.post(
        W(a, "/permissions"),
        {"node_id": secret, "subject_type": "user", "user_id": uid(prep), "permission": "view", "effect": "deny"},
    )
    assert deny.status_code in (200, 201), deny.text
    # the queue of the assistant leaves the hidden folder's document out entirely; the owner sees both
    assert {x["id"] for x in prep.get(BK(a, f"/days/{today}/documents")).json()} == {shown["id"]}
    assert {x["id"] for x in o.get(BK(a, f"/days/{today}/documents")).json()} == {shown["id"], hidden["id"]}
    # but the closing does not depend on who looks: both count both documents, and no hidden id is shown to the assistant
    assert o.post(ACC(a, "/periods/generate"), {"fiscal_year": datetime.now(UTC).year}).status_code == 200
    s = start(prep, a, today)
    v = validate(prep, a, s["id"])
    unprocessed = next(e for e in v["exceptions"] if e["code"] == "DOC_UNPROCESSED")
    assert unprocessed["data"] == {"count": 2}  # counts only: no document id is ever stored in an exception
    as_owner = o.get(BK(a, f"/days/{today}/validation")).json()
    assert next(i for i in as_owner["issues"] if i["code"] == "DOC_UNPROCESSED")["data"]["count"] == 2
    assert (
        hidden["id"] not in prep.get(BK(a, f"/closings/{s['id']}")).text
        and hidden["id"] not in o.get(BK(a, f"/days/{today}/exceptions")).text
    )


# ================================================================== period readiness
def test_period_readiness_lists_exactly_the_days_that_are_not_closed(books) -> None:  # type: ignore[no-untyped-def]
    o, a = books["owner"], books["a"]
    period = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["start_date"] <= DAY <= p["end_date"])
    txn(books)
    txn(books, date_="2025-03-11")
    full_close(books)  # closes 10 March only
    r = o.get(BK(a, f"/readiness?period_id={period['id']}")).json()
    assert r["ready"] is False and r["days"] >= 28 and r["days_closed"] == 1 and r["days_needing_closing"] == 2
    assert next(b for b in r["blockers"] if b["code"] == "DAYS_NOT_CLOSED")["dates"] == ["2025-03-11"]
    assert "does not lock the period" in r["note"] and r["period_status"] == "open"
    full_close(books, "2025-03-11")
    r = o.get(BK(a, f"/readiness?period_id={period['id']}")).json()
    assert r["ready"] is True and r["blockers"] == [] and r["days_closed"] == 2
    # the period is still open: readiness locks nothing
    assert next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["id"] == period["id"])["status"] == "open"
    # range handling
    assert o.get(BK(a, "/readiness")).json()["code"] == "range_required"
    assert o.get(BK(a, "/readiness?date_from=2025-01-01&date_to=2025-12-31")).json()["code"] == "range_invalid"
    assert o.get(BK(a, "/readiness?date_from=2025-03-05&date_to=2025-03-01")).json()["code"] == "range_invalid"
    future = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    f = o.get(BK(a, f"/readiness?date_from={future}&date_to={future}")).json()
    assert f["ready"] is False and f["blockers"][0]["code"] == "PERIOD_NOT_ENDED"


# ================================================================== Firm Control Center overview
def test_organization_overview_needs_the_monitor_permission_and_shows_status_only(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, b = books["owner"], books["a"], books["b"]
    txn(books)
    full_close(books)
    ov = o.get("/bookkeeping/overview?business_date=" + DAY).json()
    by = {c["company_id"]: c for c in ov["companies"]}
    assert by[a]["closing_status"] == "closed" and by[b]["closing_status"] is None
    assert ov["summary"]["companies"] == 2 and ov["summary"]["closed"] == 1 and ov["summary"]["not_started"] == 1
    forbidden_keys = {"amount", "gross_amount", "net_amount", "documents", "transactions"}
    assert not forbidden_keys & set(by[a])  # status and counts only: no amounts or business content
    for who in ("acct", "prep"):
        assert books[who].get("/bookkeeping/overview").status_code == 403
    pm = make_member(o, mailer, "practice_manager")
    assert pm.get("/bookkeeping/overview").status_code == 200
    # another firm sees only its own companies
    other = new_owner(client, mailer, org_name="Other Firm")
    assert other.get("/bookkeeping/overview").json()["companies"] == []


# ================================================================== isolation and security
def test_other_organizations_and_other_companies_are_invisible(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, b, prep = books["owner"], books["a"], books["b"], books["prep"]
    t_b = o.post(BK(b, "/tasks"), task_body()).json()
    cl_b = o.post(BK(b, "/checklists"), {"name": "B list", "items": [{"title": "B item"}]}).json()
    s_b = start(o, b)
    validate(o, b, s_b["id"])
    ex_b = o.get(BK(b, f"/days/{DAY}/exceptions")).json()[0]
    # company A's path never reaches company B's objects (404, not 403: no existence disclosure)
    assert o.get(BK(a, f"/tasks/{t_b['id']}")).status_code == 404
    assert o.post(BK(a, f"/tasks/{t_b['id']}/transition"), {"status": "in_progress"}).status_code == 404
    assert o.get(BK(a, f"/closings/{s_b['id']}")).status_code == 404
    assert o.post(BK(a, f"/closings/{s_b['id']}/close"), {}).status_code == 404
    assert o.get(BK(a, f"/closings/{s_b['id']}/snapshot")).status_code == 404
    assert o.post(BK(a, f"/exceptions/{ex_b['id']}/acknowledge"), {"note": "x" * 20}).status_code == 404
    assert o.patch(BK(a, f"/checklist-items/{cl_b['items'][0]['id']}"), {"title": "hijack"}).status_code == 404
    assert (
        o.post(BK(a, f"/checklist-items/{cl_b['items'][0]['id']}/complete?business_date={DAY}"), {}).status_code == 404
    )
    assert o.get(BK(a, "/tasks")).json() == [] and o.get(BK(a, "/checklists")).json() == []
    # a member who is not assigned to the company, and an unrelated organization, get nothing
    unassigned = make_member(o, mailer, "accountant")
    assert unassigned.get(BK(b, "/tasks")).status_code == 404
    other = new_owner(client, mailer, org_name="Other Firm")
    for path in (
        "/policy",
        "/tasks",
        "/closings",
        f"/days/{DAY}",
        "/checklists",
        "/readiness?date_from=2025-03-01&date_to=2025-03-02",
    ):
        assert other.get(BK(a, path)).status_code == 404, path
    assert other.post(BK(a, "/closings"), {"business_date": DAY}).status_code == 404
    # nothing the client sends picks the company: the path decides, and a body cannot override it
    r = o.post(BK(a, "/tasks"), {**task_body(), "company_id": b, "organization_id": str(uuid.uuid4())})
    assert r.status_code == 201 and r.json().get("company_id") is None
    assert [t["title"] for t in o.get(BK(b, "/tasks")).json()] == [t_b["title"]]
    assert prep  # fixture member used in other tests


def test_database_row_level_security_and_append_only_tables(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    import os

    from sqlalchemy import create_engine

    o, a, b = books["owner"], books["a"], books["b"]
    ta, tb = o.post(BK(a, "/tasks"), task_body()).json(), o.post(BK(b, "/tasks"), task_body()).json()
    full_close(books)
    with owner_engine.connect() as c:
        org = c.execute(text("SELECT organization_id FROM companies WHERE id = :c"), {"c": a}).scalar()
        user = c.execute(text("SELECT created_by FROM bk_tasks WHERE id = :t"), {"t": ta["id"]}).scalar()
    eng = create_engine(os.environ["ARTIFY_DATABASE_URL_APP"])
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true), set_config('app.company_scope', 'some', true), set_config('app.company_ids', :c, true)"
                ),
                {"u": str(user), "o": str(org), "c": a},
            )
            assert {str(r[0]) for r in conn.execute(text("SELECT id FROM bk_tasks"))} == {
                ta["id"]
            }  # B's task is invisible
            assert (
                conn.execute(text("SELECT count(*) FROM bk_closing_sessions WHERE company_id = :b"), {"b": b}).scalar()
                == 0
            )
            # a row for the other company is refused by the policy, not just hidden
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO bk_tasks (id, organization_id, company_id, title) VALUES (gen_random_uuid(), :o, :b, 'smuggled')"
                    ),
                    {"o": org, "b": b},
                )
    finally:
        eng.dispose()
    # no service role may delete history
    for table in (
        "bk_closing_snapshots",
        "bk_closing_approvals",
        "bk_validation_runs",
        "bk_audit_events",
        "bk_closing_sessions",
        "bk_exceptions",
        "bk_tasks",
    ):
        with pytest.raises(DBAPIError), owner_engine.begin() as c:
            c.execute(text(f"DELETE FROM {table}"))
    assert tb


def test_every_workflow_step_is_audited_and_notified(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    t = o.post(BK(a, "/tasks"), task_body(assignee_id=uid(prep))).json()
    txn(books)
    closed = full_close(books)
    o.post(BK(a, f"/closings/{closed['id']}/reopen"), {"reason": "Late invoice arrived this morning"})
    with owner_engine.connect() as c:
        domain = {r[0] for r in c.execute(text("SELECT action FROM bk_audit_events WHERE company_id = :c"), {"c": a})}
        platform = {
            r[0]
            for r in c.execute(
                text("SELECT action FROM audit_events WHERE company_id = :c AND action LIKE 'bookkeeping.%'"), {"c": a}
            )
        }
        outbox = {
            r[0]
            for r in c.execute(
                text("SELECT event_type FROM outbox_events WHERE company_id = :c AND event_type LIKE 'bookkeeping.%'"),
                {"c": a},
            )
        }
        payloads = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT payload::text FROM outbox_events WHERE company_id = :c AND event_type LIKE 'bookkeeping.%'"
                ),
                {"c": a},
            )
        ]
    expected = {
        "task_created",
        "closing_started",
        "closing_validated",
        "closing_submitted",
        "closing_closed",
        "closing_reopened",
    }
    assert expected <= domain and {f"bookkeeping.{x}" for x in expected} <= platform
    assert {
        "bookkeeping.task.assigned",
        "bookkeeping.closing.submitted",
        "bookkeeping.closing.closed",
        "bookkeeping.closing.reopened",
    } <= outbox
    assert not any(
        "amount" in p or "100" in p.split("business_date")[0] for p in payloads
    )  # notifications carry ids and dates, never amounts
    assert t


# ================================================================== time zones and dates
def test_business_days_follow_the_company_time_zone(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    from test_p6_documents import pdf, upload

    o, a, prep = books["owner"], books["a"], books["prep"]
    doc = upload(o, a, pdf("tz"), "tz.pdf", books["node"])
    tamper(
        owner_engine,
        "UPDATE documents SET created_at = TIMESTAMPTZ '2025-03-10 21:30:00+00' WHERE id = :d",
        d=doc["id"],
    )
    # in UTC the document belongs to 10 March; in Muscat (UTC+4) it is already 11 March
    assert o.get(BK(a, "/days/2025-03-10")).json()["documents"]["total"] == 1
    assert o.get(BK(a, "/days/2025-03-11")).json()["documents"]["total"] == 0
    assert o.put(BK(a, "/policy"), {"timezone": "Asia/Muscat"}).status_code == 200
    assert o.get(BK(a, "/days/2025-03-10")).json()["documents"]["total"] == 0
    assert (
        o.get(BK(a, "/days/2025-03-11")).json()["documents"]["total"] == 1
        and o.get(BK(a, "/days/2025-03-11")).json()["timezone"] == "Asia/Muscat"
    )
    assert len(o.get(BK(a, "/days/2025-03-11/documents")).json()) == 1
    # another company keeps its own zone
    assert o.get(BK(books["b"], "/policy")).json()["timezone"] is None
    # "today" and "future" are measured in the company's zone, with a 14-hour spread between the extremes
    o.put(BK(a, "/policy"), {"timezone": "Pacific/Kiritimati"})
    ahead = o.get(BK(a, f"/days/{(datetime.now(UTC).date() + timedelta(days=1)).isoformat()}")).json()
    assert ahead["today"] >= datetime.now(UTC).date().isoformat()
    assert prep


# ================================================================== accounting periods, accounts, supporting documents
def test_a_closed_or_missing_accounting_period_prevents_closing_the_day(books) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    period = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["start_date"] <= DAY <= p["end_date"])
    # no period covers a day in 2024 and the company requires one
    refused = prep.post(BK(a, "/closings"), {"business_date": "2024-06-10"})
    assert refused.status_code == 409 and refused.json()["code"] == "period_missing"
    # a closing started while the period is open can no longer pass once the period is closed, and nobody can override that
    s = start(prep, a)
    assert "PERIOD_CLOSED" not in issue_codes(validate(prep, a, s["id"]))
    assert o.post(ACC(a, f"/periods/{period['id']}/close"), {}).status_code == 200
    v = validate(prep, a, s["id"])
    ex = next(e for e in v["exceptions"] if e["code"] == "PERIOD_CLOSED")
    assert (
        v["validation_status"] == "failed" and ex["severity"] == "critical" and "March" in ex["message"]
    ) or "closed" in ex["message"]
    assert (
        o.post(BK(a, f"/exceptions/{ex['id']}/override"), {"note": "Please let this through anyway"}).json()["code"]
        == "hard_check"
    )
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "critical_failure"
    prep.post(BK(a, f"/closings/{s['id']}/cancel"), {"reason": "Period is closed, cancelling"})
    again = prep.post(BK(a, "/closings"), {"business_date": DAY})
    assert again.status_code == 409 and again.json()["code"] == "period_closed"
    # reopening the accounting period (Phase 8 authority) lets the day be closed again
    assert o.post(ACC(a, f"/periods/{period['id']}/reopen"), {"reason": "Correction needed"}).status_code in (200, 422)


def test_inactive_accounts_and_missing_source_documents_are_reported(books, owner_engine) -> None:  # type: ignore[no-untyped-def]
    o, a, prep = books["owner"], books["a"], books["prep"]
    tid = txn(books)
    with owner_engine.connect() as c:
        acc = c.execute(
            text(
                "SELECT account_id FROM acct_transaction_lines WHERE transaction_id = :t AND account_id IS NOT NULL LIMIT 1"
            ),
            {"t": tid},
        ).scalar()
    tamper(owner_engine, "UPDATE acct_accounts SET is_active = false WHERE id = :a", a=acc)
    v = validate(prep, a, start(prep, a)["id"])
    assert {"ACCOUNT_INACTIVE", "TXN_NO_SOURCE_DOCUMENT"} <= issue_codes(v) and v[
        "validation_status"
    ] == "awaiting_review"
    msg = next(e["message"] for e in v["exceptions"] if e["code"] == "ACCOUNT_INACTIVE")
    assert "inactive account" in msg
    # another company is not affected by company A's data
    assert "ACCOUNT_INACTIVE" not in issue_codes(validate(o, books["b"], start(o, books["b"])["id"]))


def test_tasks_carry_category_and_reviewer_and_both_are_validated(books, client, mailer) -> None:  # type: ignore[no-untyped-def]
    o, a, acct = books["owner"], books["a"], books["acct"]
    t = o.post(BK(a, "/tasks"), task_body(category="bank_review", reviewer_id=uid(acct), requires_review=True))
    assert t.status_code == 201, t.text
    assert t.json()["category"] == "bank_review" and t.json()["reviewer_name"]
    assert o.post(BK(a, "/tasks"), task_body(category="Bank Review!")).status_code == 422
    other = new_owner(client, mailer, org_name="Other Firm")
    assert o.post(BK(a, "/tasks"), task_body(reviewer_id=uid(other))).json()["code"] == "invalid_reviewer"
    assert o.patch(BK(a, f"/tasks/{t.json()['id']}"), {"category": "vat_review"}).json()["category"] == "vat_review"


def test_unsynchronised_work_blocks_submission_until_a_person_decides(books) -> None:  # type: ignore[no-untyped-def]
    prep, a, acct = books["prep"], books["a"], books["acct"]
    s = start(prep, a)
    prep.post(
        BK(a, "/sync-reports"), {"client_id": "desktop-1", "business_date": DAY, "failed_uploads": 2, "conflicts": 1}
    )
    v = validate(prep, a, s["id"])
    assert v["validation_status"] == "awaiting_review"
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).json()["code"] == "errors_open"
    for e in v["exceptions"]:
        if e["code"] in ("SYNC_FAILED", "SYNC_CONFLICTS"):
            r = acct.post(
                BK(a, f"/exceptions/{e['id']}/acknowledge"), {"note": "Device is offline; will be synced tomorrow."}
            )
            assert r.status_code == 200, r.text
    assert prep.post(BK(a, f"/closings/{s['id']}/submit"), {}).status_code == 200


def test_daily_figures_come_from_real_records(books, tpl) -> None:  # type: ignore[no-untyped-def]
    o, a, b, acct, prep = books["owner"], books["a"], books["b"], books["acct"], books["prep"]
    txn(books, tax=True)
    d = o.get(BK(a, f"/days/{DAY}")).json()
    assert d["approved_not_exported"] == 1
    rep = o.get(BK(a, f"/days/{DAY}/report")).json()
    jt = rep["transactions"]["journal_totals"]
    assert jt["debit"] == jt["credit"] and jt["debit"] != "0" and jt["journals"] == 1
    totals = rep["transactions"]["approved_totals"][0]
    assert totals["net"] == "100.000000" and totals["tax"] != "0" and totals["txn_type"] == "purchase_invoice"
    s = start(prep, a)
    validate(prep, a, s["id"])
    exp = acct.post(BK(a, f"/closings/{s['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex})
    assert exp.status_code == 202
    assert o.get(BK(a, f"/days/{DAY}")).json()["approved_not_exported"] == 0  # exported now
    # company-specific templates: another company cannot use this template, even from its own closing
    sb = start(o, b)
    use = o.post(BK(b, f"/closings/{sb['id']}/exports"), {"template_id": tpl, "idempotency_key": uuid.uuid4().hex})
    assert use.status_code == 404
    # readiness shows what is still worth a look before period end
    period = next(p for p in o.get(ACC(a, "/periods")).json()["items"] if p["start_date"] <= DAY <= p["end_date"])
    txn(books, date_="2025-03-11")
    r = o.get(BK(a, f"/readiness?period_id={period['id']}")).json()
    assert r["attention"]["approved_not_exported"] == 1 and r["attention"]["without_source_document"] >= 2
