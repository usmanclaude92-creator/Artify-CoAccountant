# ruff: noqa: C408
"""Phase 10 pure-core tests: severity policy, result evaluation, task and closing rules, time zones, snapshot hashing, readiness.
No database. Synthetic facts only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from artify_api import bk_core as c

D0 = date(2025, 3, 10)


def facts(**kw) -> c.DayFacts:  # type: ignore[no-untyped-def]
    return c.DayFacts(business_date=kw.pop("business_date", D0), today=kw.pop("today", date(2025, 3, 12)), **kw)


def codes(issues) -> list[str]:  # type: ignore[no-untyped-def]
    return [i.code for i in issues]


# ------------------------------------------------------------------ validation
def test_an_empty_day_reports_only_information_and_passes() -> None:
    iss = c.validate_day(
        facts(
            sync_reports=[
                {"client_id": "d1", "pending_uploads": 0, "failed_uploads": 0, "pending_changes": 0, "conflicts": 0}
            ]
        )
    )
    assert codes(iss) == ["DAY_EMPTY"]
    assert c.evaluate(iss)[0] == "passed"


def test_future_day_is_a_hard_critical_that_policy_cannot_lower() -> None:
    iss = c.validate_day(facts(business_date=date(2025, 3, 13)), {"FUTURE_DATE": "info"})
    assert iss[0].code == "FUTURE_DATE" and iss[0].severity == "critical"
    assert c.evaluate(iss)[0] == "failed"
    with pytest.raises(c.RuleError) as e:
        c.validate_severity_overrides({"FUTURE_DATE": "info"})
    assert e.value.code == "hard_check"


def test_results_follow_the_documented_ladder() -> None:
    mk = lambda sev: c.Issue("X", sev, "m")  # noqa: E731
    assert c.evaluate([mk("info")])[0] == "passed"
    assert c.evaluate([mk("warning")])[0] == "passed_with_warnings"
    assert c.evaluate([mk("warning"), mk("error")])[0] == "awaiting_review"
    assert c.evaluate([mk("error"), mk("critical")])[0] == "failed"
    assert c.evaluate([])[0] == "passed"


def test_acknowledged_and_overridden_issues_stop_blocking_but_stay_visible() -> None:
    err, crit = c.Issue("A", "error", "m"), c.Issue("B", "critical", "m")
    res, counts = c.evaluate([err, crit], {err.fingerprint: "acknowledged", crit.fingerprint: "overridden"})
    assert res == "passed_with_warnings" and counts["acknowledged"] == 1 and counts["overridden"] == 1
    # an acknowledgement cannot hide a critical issue
    res, _ = c.evaluate([crit], {crit.fingerprint: "acknowledged"})
    assert res == "failed"


def test_severity_policy_changes_the_result_but_not_the_issue() -> None:
    f = facts(drafts=[{"id": "t1", "number": "TX-1", "status": "draft"}])
    assert c.evaluate(c.validate_day(f))[0] == "passed_with_warnings"
    assert c.evaluate(c.validate_day(f, {"TXN_DRAFT_OPEN": "critical"}))[0] == "failed"
    assert c.evaluate(c.validate_day(f, {"TXN_DRAFT_OPEN": "info"}))[0] == "passed"
    with pytest.raises(c.RuleError):
        c.validate_severity_overrides({"NOT_A_CHECK": "error"})
    with pytest.raises(c.RuleError):
        c.validate_severity_overrides({"TXN_DRAFT_OPEN": "fatal"})


def test_each_data_problem_has_its_own_exact_code() -> None:
    f = facts(
        unbalanced=[{"id": "1", "number": "A", "debit": "10", "credit": "9"}],
        no_journal=[{"id": "2", "number": "B"}],
        tax_mismatch=[{"id": "3", "number": "C", "header": "5", "lines": "4"}],
        blocking_validation=[{"id": "4", "number": "D", "codes": ["NO_ACCOUNT"]}],
        awaiting=[{"id": "5", "number": "E", "status": "awaiting_approval"}],
        duplicates=[{"id": "6", "number": "F", "level": "exact"}, {"id": "7", "number": "G", "level": "possible"}],
        inactive_tax=[{"id": "8", "number": "H", "tax_code": "OLD"}],
        unverified_tax=[{"id": "tc", "tax_code": "VAT5", "count": 3}],
        inactive_account=[{"id": "9", "number": "I", "account": "6100"}],
        no_source_document=[{"id": "10", "number": "J"}],
        period_closed=True,
        period_name="March 2025",
        period_required=True,
        has_period=False,
        tax_recorded=True,
        docs={
            "total": 9,
            "infected": 1,
            "quarantined": 1,
            "unprocessed": 2,
            "failed": 1,
            "needs_manual": 1,
            "awaiting_review": 1,
        },
        sync_reports=[
            {"client_id": "d", "pending_uploads": 1, "failed_uploads": 2, "pending_changes": 0, "conflicts": 3}
        ],
        checklist_open=[{"id": "i", "title": "Count cash", "list": "Day-end"}],
        blocking_tasks=[{"id": "t", "title": "Chase invoice", "status": "in_progress"}],
        overdue_tasks=[{"id": "o", "title": "Old", "status": "new", "due_date": "2025-03-01"}],
    )
    got = set(codes(c.validate_day(f)))
    assert got == {
        "TXN_UNBALANCED", "TXN_NO_JOURNAL", "TXN_TAX_MISMATCH", "TXN_BLOCKING_VALIDATION", "TXN_AWAITING_APPROVAL",
        "TXN_DUPLICATE_EXACT", "TXN_DUPLICATE_POSSIBLE", "TAX_CODE_INACTIVE", "TAX_CODE_UNVERIFIED", "TAX_CONFIG_MISSING",
        "ACCOUNT_INACTIVE", "TXN_NO_SOURCE_DOCUMENT", "PERIOD_CLOSED", "PERIOD_MISSING",
        "DOC_INFECTED", "DOC_QUARANTINED", "DOC_UNPROCESSED", "DOC_PROCESSING_FAILED", "DOC_NEEDS_MANUAL", "DOC_AWAITING_REVIEW",
        "SYNC_CONFLICTS", "SYNC_FAILED", "SYNC_PENDING", "CHECKLIST_MANDATORY_OPEN", "TASK_BLOCKING_OPEN", "TASK_OVERDUE",
    }  # fmt: skip
    assert all(code in c.CODES for code in got)
    assert {k for k, v in c.CODES.items() if v.hard} == {
        "FUTURE_DATE",
        "TXN_UNBALANCED",
        "TXN_NO_JOURNAL",
        "DOC_INFECTED",
        "PERIOD_CLOSED",
        "PERIOD_MISSING",
    }


def test_no_tax_rule_is_assumed_when_nothing_is_configured() -> None:
    """Tax recorded without any company tax configuration is reported, never 'fixed' with a built-in rate or jurisdiction."""
    iss = c.validate_day(facts(tax_recorded=True, tax_configured=False, txn_counts={"approved": 1}))
    assert "TAX_CONFIG_MISSING" in codes(iss)
    ok = c.validate_day(facts(tax_recorded=True, tax_configured=True, txn_counts={"approved": 1}))
    assert "TAX_CONFIG_MISSING" not in codes(ok)


def test_validation_is_deterministic_and_stable_ordered() -> None:
    f = facts(
        drafts=[{"id": "b", "number": "2", "status": "draft"}, {"id": "a", "number": "1", "status": "draft"}],
        awaiting=[{"id": "z", "number": "9", "status": "awaiting_review"}],
    )
    a, b = c.validate_day(f), c.validate_day(f)
    assert [i.fingerprint for i in a] == [i.fingerprint for i in b]
    assert a[0].severity == "error"  # most severe first


def test_per_object_issues_are_capped_but_not_silently_hidden_in_counts() -> None:
    f = facts(drafts=[{"id": str(n), "number": f"T{n}", "status": "draft"} for n in range(120)])
    assert len([i for i in c.validate_day(f) if i.code == "TXN_DRAFT_OPEN"]) == c.CAP


# ------------------------------------------------------------------ resolving
def test_acknowledge_and_override_rules() -> None:
    c.can_resolve("warning", "TXN_DRAFT_OPEN", "acknowledge", note=None, min_chars=10)
    c.can_resolve(
        "error",
        "TXN_AWAITING_APPROVAL",
        "acknowledge",
        note="Approver is on leave; will approve tomorrow.",
        min_chars=10,
    )
    with pytest.raises(c.RuleError) as e:
        c.can_resolve("error", "TXN_AWAITING_APPROVAL", "acknowledge", note="ok", min_chars=10)
    assert e.value.code == "explanation_required"
    with pytest.raises(c.RuleError) as e:
        c.can_resolve("critical", "TXN_DRAFT_OPEN", "acknowledge", note="a long enough note", min_chars=10)
    assert e.value.code == "not_acknowledgeable"
    with pytest.raises(c.RuleError) as e:
        c.can_resolve("warning", "TXN_DRAFT_OPEN", "override", note="a long enough note", min_chars=10)
    assert e.value.code == "not_overridable"
    with pytest.raises(c.RuleError) as e:
        c.can_resolve("critical", "TXN_UNBALANCED", "override", note="a long enough note", min_chars=10)
    assert e.value.code == "hard_check"


# ------------------------------------------------------------------ tasks
def move(cur: str, tgt: str, **kw):  # type: ignore[no-untyped-def]
    base = dict(
        requires_review=False,
        actor="u1",
        review_requested_by=None,
        independence_required=False,
        has_review_perm=False,
        is_assignee=True,
        can_manage=False,
    )
    base.update(kw)
    c.check_task_transition(cur, tgt, **base)


def test_every_status_has_a_defined_set_of_moves() -> None:
    assert set(c.TASK_TRANSITIONS) == set(c.TASK_STATUSES)
    assert c.TASK_TRANSITIONS["completed"] == frozenset() and c.TASK_TRANSITIONS["cancelled"] == frozenset()


def test_invalid_moves_are_refused() -> None:
    for cur, tgt in [
        ("new", "completed"),
        ("completed", "in_progress"),
        ("cancelled", "new"),
        ("approved", "rejected"),
        ("awaiting_review", "completed"),
    ]:
        with pytest.raises(c.RuleError) as e:
            move(cur, tgt, can_manage=True, has_review_perm=True)
        assert e.value.code == "invalid_transition"


def test_review_flow_and_no_self_approval() -> None:
    with pytest.raises(c.RuleError) as e:
        move("in_progress", "completed", requires_review=True)
    assert e.value.code == "review_required"
    move("in_progress", "awaiting_review", requires_review=True)
    with pytest.raises(c.RuleError) as e:
        move("in_progress", "awaiting_review", requires_review=False)
    assert e.value.code == "review_not_required"
    with pytest.raises(c.RuleError) as e:  # the person who asked for the review cannot approve it
        move(
            "awaiting_review",
            "approved",
            has_review_perm=True,
            review_requested_by="u1",
            independence_required=True,
            is_assignee=False,
        )
    assert e.value.code == "independence_required"
    move(
        "awaiting_review",
        "approved",
        has_review_perm=True,
        review_requested_by="u2",
        independence_required=True,
        is_assignee=False,
    )
    move(
        "awaiting_review",
        "approved",
        has_review_perm=True,
        review_requested_by="u1",
        independence_required=False,
        is_assignee=False,
    )  # policy off
    with pytest.raises(c.RuleError) as e:
        move("awaiting_review", "rejected", has_review_perm=False, is_assignee=False)
    assert e.value.code == "review_forbidden"


def test_only_the_assignee_or_a_manager_works_a_task() -> None:
    with pytest.raises(c.RuleError) as e:
        move("assigned", "in_progress", is_assignee=False)
    assert e.value.code == "not_your_task"
    move("assigned", "in_progress", is_assignee=False, can_manage=True)
    with pytest.raises(c.RuleError) as e:
        move("assigned", "cancelled")
    assert e.value.code == "cancel_forbidden"


# ------------------------------------------------------------------ closing rules
def test_submit_rules() -> None:
    c.check_submit(status="in_progress", result="passed_with_warnings", stale=False)
    for kw, code in [
        (dict(status="closed", result="passed", stale=False), "invalid_status"),
        (dict(status="in_progress", result="passed", stale=True), "validation_stale"),
        (dict(status="in_progress", result="failed", stale=False), "critical_failure"),
        (dict(status="in_progress", result="awaiting_review", stale=False), "errors_open"),
    ]:
        with pytest.raises(c.RuleError) as e:
            c.check_submit(**kw)  # type: ignore[arg-type]
        assert e.value.code == code


def test_close_rules_block_critical_and_enforce_independence() -> None:
    ok = dict(status="ready_for_review", result="passed", submitted_by="u1", closer="u2", independence_required=True)
    c.check_close(**ok)  # type: ignore[arg-type]
    for patch, code in [
        (dict(result="failed"), "critical_failure"),
        (dict(result="awaiting_review"), "errors_open"),
        (dict(closer="u1"), "independence_required"),
        (dict(status="in_progress"), "invalid_status"),
    ]:
        with pytest.raises(c.RuleError) as e:
            c.check_close(**{**ok, **patch})  # type: ignore[arg-type]
        assert e.value.code == code
    c.check_close(**{**ok, "closer": "u1", "independence_required": False})  # type: ignore[arg-type]


def test_reopen_rules() -> None:
    c.check_reopen(status="closed", reason="Late supplier invoice arrived", period_closed=False)
    for kw, code in [
        (dict(status="in_progress", reason="long enough reason", period_closed=False), "invalid_status"),
        (dict(status="closed", reason="short", period_closed=False), "reason_required"),
        (dict(status="closed", reason="long enough reason", period_closed=True), "period_closed"),
    ]:
        with pytest.raises(c.RuleError) as e:
            c.check_reopen(**kw)  # type: ignore[arg-type]
        assert e.value.code == code


def test_company_can_restrict_who_acts_but_an_empty_list_leaves_it_to_the_permission() -> None:
    assert c.role_allows({}, "reopen", ["accountant"])
    assert c.role_allows({"reopen": []}, "reopen", ["accountant"])
    assert c.role_allows({"reopen": ["practice_manager"]}, "reopen", ["practice_manager", "x"])
    assert not c.role_allows({"reopen": ["practice_manager"]}, "reopen", ["accountant"])
    assert c.role_allows({"reopen": ["practice_manager"]}, "approve", ["accountant"])  # other actions unaffected


# ------------------------------------------------------------------ checklist rules
def test_automatic_rules_read_data_not_titles() -> None:
    f = facts(docs={"unprocessed": 1}, awaiting=[], drafts=[])
    assert not c.auto_rule_met("documents_processed", f, [])
    assert c.auto_rule_met("transactions_decided", f, [])
    assert not c.auto_rule_met("sync_clean", f, [])  # no report is not "clean"
    f2 = facts(sync_reports=[{"failed_uploads": 0, "conflicts": 0}])
    assert c.auto_rule_met("sync_clean", f2, [])
    assert not c.auto_rule_met("no_open_critical", f, [c.Issue("X", "critical", "m")])
    with pytest.raises(c.RuleError):
        c.auto_rule_met("by_title_please", f, [])


def test_checklist_state_is_manual_or_automatic() -> None:
    items = [{"id": "a", "auto_rule": None}, {"id": "b", "auto_rule": "r"}, {"id": "c", "auto_rule": "r2"}]
    st = c.checklist_state(items, {"a"}, lambda r: r == "r")
    assert [(i["id"], i["done"], i["done_by"]) for i in st] == [
        ("a", True, "manual"),
        ("b", True, "auto"),
        ("c", False, None),
    ]


# ------------------------------------------------------------------ time zones
def test_business_date_uses_the_company_time_zone() -> None:
    moment = datetime(2025, 3, 10, 21, 30, tzinfo=UTC)  # 21:30 UTC
    assert c.business_date_of(moment, "UTC") == date(2025, 3, 10)
    assert c.business_date_of(moment, "Asia/Muscat") == date(2025, 3, 11)  # UTC+4 -> already tomorrow
    assert c.business_date_of(moment, "America/Los_Angeles") == date(2025, 3, 10)
    assert c.business_date_of(moment, "Pacific/Kiritimati") == date(2025, 3, 11)  # UTC+14
    assert c.business_date_of(moment, "Not/AZone") == date(2025, 3, 10)  # unknown zone falls back to UTC, never crashes
    assert c.business_date_of(datetime(2025, 3, 10, 21, 30), "Asia/Muscat") == date(2025, 3, 11)  # naive treated as UTC


def test_dst_boundaries_do_not_move_the_day() -> None:
    assert c.business_date_of(datetime(2025, 3, 9, 6, 59, tzinfo=UTC), "America/New_York") == date(2025, 3, 9)
    assert c.business_date_of(datetime(2025, 3, 9, 7, 0, tzinfo=UTC), "America/New_York") == date(2025, 3, 9)
    assert c.business_date_of(datetime(2025, 11, 2, 4, 59, tzinfo=UTC), "America/New_York") == date(2025, 11, 2)


# ------------------------------------------------------------------ cash and bank
def test_cash_bank_summary_is_decimal_exact_and_not_a_reconciliation() -> None:
    rows = [
        c.CashRow("1", "1120", "Main bank", "bank", Decimal("1000.100"), Decimal("250.250"), Decimal("100.050"), 3),
        c.CashRow("2", "1110", "Cash", "cash", Decimal("0"), Decimal("0.100"), Decimal("0.200"), 1),
    ]
    out = c.cash_bank_summary(rows)
    assert out["accounts"][0]["closing"] == Decimal("1150.300")
    assert out["accounts"][1]["closing"] == Decimal("-0.100")
    assert out["totals"]["closing"] == Decimal("1150.200") and out["reconciled"] is False
    assert all(isinstance(v, Decimal) for v in out["totals"].values())


# ------------------------------------------------------------------ snapshots
def test_snapshot_digest_is_canonical_and_changes_with_content() -> None:
    a = {"b": Decimal("1.50"), "a": [date(2025, 3, 10), {"z": 1, "y": 2}]}
    b = {"a": [date(2025, 3, 10), {"y": 2, "z": 1}], "b": Decimal("1.50")}
    assert c.payload_digest(a) == c.payload_digest(b)
    assert c.payload_digest(a) != c.payload_digest({**a, "b": Decimal("1.51")})
    assert '"b":"1.50"' in c.canonical_json(a)  # money stays an exact string


# ------------------------------------------------------------------ readiness
def _day(d: int, *, act: bool, status: str = "none", exc: int = 0, open_txn: int = 0) -> dict:  # type: ignore[type-arg]
    return {
        "date": date(2025, 3, d),
        "has_activity": act,
        "closing_status": status,
        "open_exceptions": exc,
        "open_transactions": open_txn,
    }


def test_readiness_needs_every_active_day_closed_and_is_only_an_overview() -> None:
    days = [_day(1, act=True, status="closed"), _day(2, act=False), _day(3, act=True, status="in_progress")]
    r = c.period_readiness(days, today=date(2025, 3, 31), period_status="open")
    assert (
        not r["ready"] and r["blockers"][0]["code"] == "DAYS_NOT_CLOSED" and r["blockers"][0]["dates"] == ["2025-03-03"]
    )
    assert r["days_needing_closing"] == 2 and r["days_closed"] == 1
    done = c.period_readiness(
        [_day(1, act=True, status="closed"), _day(2, act=False)], today=date(2025, 3, 31), period_status="open"
    )
    assert done["ready"] and "does not lock the period" in done["note"]
    future = c.period_readiness(
        [_day(1, act=True, status="closed"), _day(2, act=False)], today=date(2025, 3, 1), period_status="open"
    )
    assert not future["ready"] and future["blockers"][0]["code"] == "PERIOD_NOT_ENDED"
    open_items = c.period_readiness(
        [_day(1, act=True, status="closed", exc=2, open_txn=1)], today=date(2025, 3, 31), period_status=None
    )
    assert {b["code"] for b in open_items["blockers"]} == {"OPEN_EXCEPTIONS", "OPEN_TRANSACTIONS"}


def test_every_role_that_can_read_bookkeeping_can_read_accounting() -> None:
    """Exceptions, the transaction list and the daily report show accounting content, so bookkeeping access never exceeds accounting access."""
    from artify_api.permissions import SYSTEM_ROLES

    for key, (_, _, perms) in SYSTEM_ROLES.items():
        if "bookkeeping.read" in perms:
            assert "accounting.read" in perms, key
        assert (
            not ({"bookkeeping.close.approve", "bookkeeping.close.reopen", "bookkeeping.close.override"} & perms)
            or "bookkeeping.read" in perms
        ), key
    assert (
        "bookkeeping.close.reopen" not in SYSTEM_ROLES["accountant"][2]
        and "bookkeeping.close.override" not in SYSTEM_ROLES["accountant"][2]
    )
