"""Phase 8: the pure accounting core. Synthetic numbers only; no database."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal as Dec

import pytest
from artify_api import acct_core as A


def acct(
    i: str, typ: str = "expense", *, active: bool = True, postable: bool = True, currency: str | None = None
) -> A.AccountInfo:
    return A.AccountInfo(i, i, i, typ, A.default_normal_balance(typ) or "debit", active, postable, currency)


ACCTS = {k: acct(k) for k in ("EXP", "REV", "AP", "AR", "BANK", "VIN", "VOUT", "RND", "CASH")}
STD = A.TaxSpec("T5", "standard", Dec("5"), True, "VIN", "VOUT")


def tline(
    no=1, net="100", tax="5", gross="105", *, treatment="standard", recoverable=True, account="EXP", side=None
) -> A.TLine:
    return A.TLine(
        no, f"item {no}", account, Dec(net), Dec(tax), Dec(gross), "T5", treatment, "VIN", "VOUT", recoverable, side
    )


# ------------------------------------------------------------------ decimals
def test_floats_and_garbage_never_enter_a_calculation() -> None:
    for bad in (1.5, True, None, object()):
        with pytest.raises(TypeError):
            A.D(bad)
    for bad in ("abc", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            A.D(bad)
    assert A.D("1,234.50") == Dec("1234.50")
    assert A.D(7) == Dec(7)


def test_rounding_modes_and_three_decimal_currencies() -> None:
    assert A.rnd(Dec("0.0125"), 3, "half_up") == Dec("0.013")
    assert A.rnd(Dec("0.0125"), 3, "half_even") == Dec("0.012")
    assert A.rnd(Dec("0.0135"), 3, "half_even") == Dec("0.014")
    assert A.rnd(Dec("2.675"), 2) == Dec("2.68")  # no binary float artefact
    assert A.rnd(Dec("100"), 0) == Dec("100")


# ------------------------------------------------------------------ tax
def test_tax_exclusive_and_inclusive_are_exact_opposites() -> None:
    ex, tot = A.calc_lines([A.LineIn(None, None, Dec("100.000"), tax=STD)], inclusive=False, scale=3)
    assert (ex[0].net, ex[0].tax, ex[0].gross) == (Dec("100.000"), Dec("5.000"), Dec("105.000"))
    inc, _ = A.calc_lines([A.LineIn(None, None, Dec("105.000"), tax=STD)], inclusive=True, scale=3)
    assert (inc[0].net, inc[0].tax, inc[0].gross) == (Dec("100.000"), Dec("5.000"), Dec("105.000"))
    assert tot.by_rate["standard@5"] == {"taxable": "100.000", "tax": "5.000"}


def test_quantity_price_and_discount() -> None:
    calc, tot = A.calc_lines(
        [A.LineIn(Dec("3"), Dec("19.990"), None, discount=Dec("2.000"), tax=STD)], inclusive=False, scale=3
    )
    assert calc[0].net == Dec("57.970")  # 3 x 19.990 - 2.000
    assert calc[0].tax == Dec("2.899")  # 57.970 x 5% = 2.8985 -> half-up
    assert tot.gross == Dec("60.869")


def test_multiple_rates_and_untaxed_treatments() -> None:
    r15 = A.TaxSpec("T15", "standard", Dec("15"))
    zero = A.TaxSpec("Z", "zero_rated", Dec("0"))
    exempt = A.TaxSpec("E", "exempt", Dec("0"))
    out = A.TaxSpec("O", "out_of_scope", Dec("0"))
    lines = [
        A.LineIn(None, None, Dec("100"), tax=STD),
        A.LineIn(None, None, Dec("200"), tax=r15),
        A.LineIn(None, None, Dec("50"), tax=zero),
        A.LineIn(None, None, Dec("10"), tax=exempt),
        A.LineIn(None, None, Dec("7"), tax=out),
    ]
    calc, tot = A.calc_lines(lines, inclusive=False, scale=2)
    assert [c.tax for c in calc] == [Dec("5.00"), Dec("30.00"), Dec(0), Dec(0), Dec(0)]
    assert tot.net == Dec("367.00") and tot.tax == Dec("35.00") and tot.gross == Dec("402.00")
    assert set(tot.by_rate) == {"standard@5", "standard@15", "zero_rated@0", "exempt@0", "out_of_scope@0"}


def test_line_versus_document_rounding_is_deterministic_and_adds_up() -> None:
    lines = [A.LineIn(None, None, Dec("0.10"), tax=STD) for _ in range(3)]
    _by_line, t1 = A.calc_lines(lines, inclusive=False, scale=2, scope="line")
    by_doc, t2 = A.calc_lines(lines, inclusive=False, scale=2, scope="document")
    assert t1.tax == Dec("0.03") and t2.tax == Dec("0.02")  # 0.005 per line: 3 x 0.01 vs one rounding of 0.015 -> 0.02
    assert sum(c.tax for c in by_doc) == t2.tax
    assert sum(c.gross for c in by_doc) == t2.gross


def test_compare_totals_reports_but_never_changes_values() -> None:
    _, tot = A.calc_lines([A.LineIn(None, None, Dec("100"), tax=STD)], inclusive=False, scale=2)
    issues = A.compare_totals(
        {"net": Dec("100"), "tax": Dec("6.00"), "gross": Dec("106")}, tot, tolerance=Dec("0.01"), scale=2
    )
    assert {i.code for i in issues} == {"tax_mismatch", "gross_mismatch"}
    assert all(not i.blocking for i in issues)  # overridable with a written reason, not silently
    assert (
        A.compare_totals(
            {"net": Dec("100"), "tax": Dec("5.00"), "gross": Dec("105")}, tot, tolerance=Dec("0.01"), scale=2
        )
        == []
    )
    bad = A.compare_totals(
        {"net": Dec("100"), "tax": Dec("5"), "gross": Dec("120")}, tot, tolerance=Dec("0.01"), scale=2
    )
    assert "net_plus_tax_not_gross" in {i.code for i in bad}


def test_tax_spec_validation() -> None:
    assert A.validate_tax_spec(STD) == []
    assert A.validate_tax_spec(A.TaxSpec("x", "exempt", Dec("5")))[0].code == "invalid_tax_rate"
    assert A.validate_tax_spec(A.TaxSpec("x", "standard", Dec("150")))[0].code == "invalid_tax_rate"
    assert A.validate_tax_spec(A.TaxSpec("x", "made_up", Dec("0")))[0].code == "invalid_tax_treatment"


# ------------------------------------------------------------------ journals
def totals(lines):
    return sum(ln.debit for ln in lines), sum(ln.credit for ln in lines)


@pytest.mark.parametrize(
    ("typ", "lines"),
    [
        ("purchase_invoice", [tline()]),
        ("purchase_credit_note", [tline()]),
        ("sales_invoice", [tline(account="REV")]),
        ("sales_credit_note", [tline(account="REV")]),
        ("cash_expense", [tline()]),
        ("bank_expense", [tline()]),
        ("employee_expense", [tline()]),
        ("general_expense", [tline()]),
        ("bank_withdrawal", [tline()]),
        ("bank_charges", [tline("1", "10", "0", "10", treatment=None)]),
        ("bank_deposit", [tline(account="REV")]),
        ("customer_receipt", [tline("1", "105", "0", "105", treatment=None)]),
        ("customer_refund", [tline("1", "105", "0", "105", treatment=None)]),
        ("supplier_payment", [tline("1", "105", "0", "105", treatment=None)]),
        ("supplier_refund", [tline("1", "105", "0", "105", treatment=None)]),
    ],
)
def test_every_generated_journal_balances(typ: str, lines: list[A.TLine]) -> None:
    j, issues = A.build_journal(typ, lines, control_account="AP", settlement_account="BANK", contact_id="C1")
    assert not issues
    d, c = totals(j)
    assert d == c > 0
    assert A.validate_journal(j, ACCTS, base_currency="OMR", currency="OMR") == []


def test_purchase_and_sales_templates_use_the_right_sides() -> None:
    j, _ = A.build_journal("purchase_invoice", [tline()], control_account="AP", settlement_account=None)
    assert [(x.account_id, x.debit, x.credit) for x in j] == [
        ("EXP", Dec(100), Dec(0)),
        ("VIN", Dec(5), Dec(0)),
        ("AP", Dec(0), Dec(105)),
    ]
    j, _ = A.build_journal("sales_invoice", [tline(account="REV")], control_account="AR", settlement_account=None)
    assert [(x.account_id, x.debit, x.credit) for x in j] == [
        ("AR", Dec(105), Dec(0)),
        ("REV", Dec(0), Dec(100)),
        ("VOUT", Dec(0), Dec(5)),
    ]
    cn, _ = A.build_journal("sales_credit_note", [tline(account="REV")], control_account="AR", settlement_account=None)
    assert [(x.account_id, x.debit, x.credit) for x in cn] == [
        ("AR", Dec(0), Dec(105)),
        ("REV", Dec(100), Dec(0)),
        ("VOUT", Dec(5), Dec(0)),
    ]


def test_unrecoverable_tax_is_part_of_the_expense_and_exempt_lines_have_no_tax_line() -> None:
    j, _ = A.build_journal(
        "purchase_invoice", [tline(recoverable=False)], control_account="AP", settlement_account=None
    )
    assert [(x.account_id, x.debit, x.credit) for x in j] == [("EXP", Dec(105), Dec(0)), ("AP", Dec(0), Dec(105))]
    j, _ = A.build_journal(
        "purchase_invoice",
        [tline("1", "100", "0", "100", treatment="exempt")],
        control_account="AP",
        settlement_account=None,
    )
    assert [x.account_id for x in j] == ["EXP", "AP"]


def test_reverse_charge_posts_input_and_output_tax_and_owes_the_supplier_only_net() -> None:
    j, _ = A.build_journal(
        "purchase_invoice", [tline(treatment="reverse_charge")], control_account="AP", settlement_account=None
    )
    assert [(x.account_id, x.debit, x.credit) for x in j] == [
        ("EXP", Dec(100), Dec(0)),
        ("VIN", Dec(5), Dec(0)),
        ("VOUT", Dec(0), Dec(5)),
        ("AP", Dec(0), Dec(100)),
    ]
    d, c = totals(j)
    assert d == c


def test_manual_journals_are_validated_not_invented() -> None:
    manual = [
        tline(1, "50", "0", "50", account="EXP", side="debit", treatment=None),
        tline(2, "50", "0", "50", account="BANK", side="credit", treatment=None),
    ]
    j, issues = A.build_journal("journal_entry", manual, control_account=None, settlement_account=None)
    assert not issues and totals(j) == (Dec(50), Dec(50))
    _, issues = A.build_journal(
        "accrual", [tline(1, "5", "0", "5", side=None)], control_account=None, settlement_account=None
    )
    assert issues[0].code == "entry_side_missing"


def test_bank_transfer_moves_between_two_accounts() -> None:
    j, _ = A.build_journal(
        "bank_transfer",
        [tline("1", "40", "0", "40", treatment=None, account=None)],
        control_account=None,
        settlement_account="BANK",
        counter_account="CASH",
    )
    assert [(x.account_id, x.debit, x.credit) for x in j] == [("CASH", Dec(40), Dec(0)), ("BANK", Dec(0), Dec(40))]


def test_validation_blocks_every_kind_of_bad_journal() -> None:
    pool = {
        **ACCTS,
        "OFF": acct("OFF", active=False),
        "GRP": acct("GRP", postable=False),
        "USD": acct("USD", currency="USD"),
    }

    def codes(lines, **kw):  # type: ignore[no-untyped-def]
        return {i.code for i in A.validate_journal(lines, pool, base_currency="OMR", currency="OMR", **kw)}

    dr, cr = A.JLine("EXP", Dec(10), Dec(0)), A.JLine("AP", Dec(0), Dec(10))
    assert codes([dr, cr]) == set()
    assert "journal_unbalanced" in codes([dr, A.JLine("AP", Dec(0), Dec("9.999"))])
    assert "too_few_lines" in codes([dr])
    assert "both_sides" in codes([A.JLine("EXP", Dec(1), Dec(1)), cr])
    assert "negative_amount" in codes([A.JLine("EXP", Dec(-1), Dec(0)), cr])
    assert "line_without_amount" in codes([dr, cr, A.JLine("EXP", Dec(0), Dec(0))])
    assert "missing_account_mapping" in codes([A.JLine(None, Dec(10), Dec(0)), cr])
    assert "account_not_found" in codes([A.JLine("NOPE", Dec(10), Dec(0)), cr])
    assert "account_inactive" in codes([A.JLine("OFF", Dec(10), Dec(0)), cr])
    assert "account_not_postable" in codes([A.JLine("GRP", Dec(10), Dec(0)), cr])
    assert "account_currency_mismatch" in codes([A.JLine("USD", Dec(10), Dec(0)), cr])
    assert "dimension_missing" in codes([dr, cr], required_dimensions=["cost_center"])
    assert "journal_unbalanced_base" in codes(
        [A.JLine("EXP", Dec(10), Dec(0), base_debit=Dec(10)), A.JLine("AP", Dec(0), Dec(10), base_credit=Dec(9))]
    )


def test_currency_conversion_rounding_goes_to_the_rounding_account_or_blocks() -> None:
    usd = [
        A.JLine("EXP", Dec("10.00"), Dec(0)),
        A.JLine("VIN", Dec("0.50"), Dec(0)),
        A.JLine("AP", Dec(0), Dec("10.50")),
    ]
    conv, issues = A.to_base(
        usd, rate=Dec("0.3845"), base_scale=3, mode="half_up", rounding_account="RND", tolerance=Dec("0.01")
    )
    assert A.validate_journal(conv, ACCTS, base_currency="OMR", currency="USD") == []
    assert sum(c.base_debit for c in conv) == sum(c.base_credit for c in conv)
    assert [i.code for i in issues] in ([], ["rounding_difference_posted"])
    # without a rounding account an unbalanced conversion is reported, not hidden
    awkward = [
        A.JLine("EXP", Dec("1.00"), Dec(0)),
        A.JLine("VIN", Dec("1.00"), Dec(0)),
        A.JLine("AP", Dec(0), Dec("2.00")),
    ]
    conv2, _ = A.to_base(
        awkward, rate=Dec("0.3335"), base_scale=2, mode="half_up", rounding_account=None, tolerance=Dec("0.01")
    )
    if sum(c.base_debit for c in conv2) != sum(c.base_credit for c in conv2):
        assert "journal_unbalanced_base" in {
            i.code for i in A.validate_journal(conv2, ACCTS, base_currency="OMR", currency="USD")
        }


def test_random_documents_always_produce_balanced_journals() -> None:
    rnd = random.Random(8)
    for _ in range(300):
        scale = rnd.choice([2, 3])
        inclusive = rnd.random() < 0.5
        rate = Dec(rnd.choice(["0", "5", "15", "7.5"]))
        spec = A.TaxSpec("T", "standard" if rate else "zero_rated", rate, True, "VIN", "VOUT")
        lines = [
            A.LineIn(None, None, Dec(rnd.randint(1, 500000)) / Dec(10**scale), tax=spec)
            for _ in range(rnd.randint(1, 6))
        ]
        mode = rnd.choice(["half_up", "half_even"])
        calc, tot = A.calc_lines(
            lines, inclusive=inclusive, scale=scale, mode=mode, scope=rnd.choice(["line", "document"])
        )
        assert tot.net + tot.tax == tot.gross
        assert all(c.net + c.tax == c.gross for c in calc)
        tl = [
            A.TLine(i + 1, None, "EXP", c.net, c.tax, c.gross, "T", spec.treatment, "VIN", "VOUT")
            for i, c in enumerate(calc)
        ]
        for typ in ("purchase_invoice", "sales_invoice", "purchase_credit_note", "bank_withdrawal"):
            j, _ = A.build_journal(typ, tl, control_account="AP", settlement_account="BANK")
            d, c = totals(j)
            assert d == c, (typ, d, c)


# ------------------------------------------------------------------ periods and rates
def test_period_checks_never_move_the_date() -> None:
    periods = [
        {"name": "2026-01", "start_date": date(2026, 1, 1), "end_date": date(2026, 1, 31), "status": "open"},
        {"name": "2026-02", "start_date": date(2026, 2, 1), "end_date": date(2026, 2, 28), "status": "closed"},
    ]
    assert A.check_period(date(2026, 1, 15), periods, required=True) == []
    assert A.check_period(date(2026, 2, 3), periods, required=True)[0].code == "period_closed"
    assert A.check_period(date(2026, 3, 1), periods, required=True)[0].code == "no_period"
    assert A.check_period(date(2026, 3, 1), periods, required=False) == []


def test_exchange_rates_are_required_for_foreign_currency_and_never_invented() -> None:
    assert A.check_rate("OMR", "OMR", None, None, date(2026, 1, 1), 7) == []
    assert A.check_rate("USD", "OMR", None, None, date(2026, 1, 1), 7)[0].code == "missing_exchange_rate"
    assert A.check_rate("USD", "OMR", Dec("0"), None, date(2026, 1, 1), 7)[0].code == "missing_exchange_rate"
    assert (
        A.check_rate("USD", "OMR", Dec("0.385"), date(2025, 12, 1), date(2026, 1, 1), 7)[0].code
        == "stale_exchange_rate"
    )
    assert A.check_rate("USD", "OMR", Dec("0.385"), date(2025, 12, 31), date(2026, 1, 1), 7) == []


# ------------------------------------------------------------------ rules
def rule(i: str, kind: str, prio: int, conds: list, action: dict, **kw) -> A.Rule:
    return A.Rule(i, i, i, kind, prio, tuple(conds), action, kw.get("frm"), kw.get("to"), kw.get("active", True))


FACTS = {
    "txn_type": "purchase_invoice",
    "contact_name": "Gulf Office Supplies LLC",
    "description": "A4 paper and toner",
    "amount": "250.000",
    "currency": "OMR",
}


def test_rules_match_by_priority_and_effective_dates() -> None:
    on = date(2026, 3, 1)
    low = rule(
        "low", "account", 1, [{"field": "txn_type", "op": "eq", "value": "purchase_invoice"}], {"account_id": "EXP1"}
    )
    high = rule(
        "high", "account", 5, [{"field": "description", "op": "contains", "value": "toner"}], {"account_id": "EXP2"}
    )
    assert A.resolve_rules([low, high], FACTS, on, kind="account").value == "EXP2"
    expired = rule(
        "old",
        "account",
        9,
        [{"field": "txn_type", "op": "eq", "value": "purchase_invoice"}],
        {"account_id": "EXP9"},
        to=date(2026, 1, 31),
    )
    future = rule(
        "new",
        "account",
        9,
        [{"field": "txn_type", "op": "eq", "value": "purchase_invoice"}],
        {"account_id": "EXP8"},
        frm=date(2026, 6, 1),
    )
    inactive = rule(
        "off",
        "account",
        9,
        [{"field": "txn_type", "op": "eq", "value": "purchase_invoice"}],
        {"account_id": "EXP7"},
        active=False,
    )
    assert A.resolve_rules([low, expired, future, inactive], FACTS, on, kind="account").value == "EXP1"
    assert A.resolve_rules([], FACTS, on, kind="account").value is None


def test_conflicting_rules_of_equal_priority_are_flagged_not_chosen() -> None:
    a = rule(
        "a", "account", 5, [{"field": "contact_name", "op": "contains", "value": "office"}], {"account_id": "EXP1"}
    )
    b = rule("b", "account", 5, [{"field": "description", "op": "contains", "value": "paper"}], {"account_id": "EXP2"})
    r = A.resolve_rules([a, b], FACTS, date(2026, 3, 1), kind="account")
    assert r.conflict and r.value is None and set(r.rule_ids) == {"a", "b"}
    same = rule(
        "c", "account", 5, [{"field": "description", "op": "contains", "value": "paper"}], {"account_id": "EXP1"}
    )
    assert not A.resolve_rules([a, same], FACTS, date(2026, 3, 1), kind="account").conflict


def test_rule_operators() -> None:
    on = date(2026, 3, 1)
    for cond, want in [
        ({"field": "amount", "op": "gte", "value": "200"}, True),
        ({"field": "amount", "op": "lte", "value": "200"}, False),
        ({"field": "currency", "op": "in", "value": ["omr", "usd"]}, True),
        ({"field": "contact_name", "op": "starts_with", "value": "gulf"}, True),
        ({"field": "contact_name", "op": "neq", "value": "gulf"}, True),
        ({"field": "project", "op": "eq", "value": "x"}, False),
    ]:
        r = rule("r", "tax", 1, [cond], {"tax_code_id": "T"})
        assert A.rule_applies(r, FACTS, on) is want, cond


def test_rule_definitions_are_data_never_code() -> None:
    ok = A.validate_rule_definition(
        "account", [{"field": "description", "op": "contains", "value": "x"}], {"account_id": "u"}
    )
    assert ok == []
    assert A.validate_rule_definition("account", [], {"account_id": "u"})
    assert A.validate_rule_definition(
        "account", [{"field": "__class__", "op": "eq", "value": "x"}], {"account_id": "u"}
    )
    assert A.validate_rule_definition(
        "account", [{"field": "description", "op": "eval", "value": "x"}], {"account_id": "u"}
    )
    assert A.validate_rule_definition(
        "account", [{"field": "description", "op": "eq", "value": "x"}], {"account_id": "u", "code": "import os"}
    )
    assert A.validate_rule_definition(
        "narration", [{"field": "description", "op": "eq", "value": "x"}], {"template": "{contact.__class__}"}
    )
    assert A.validate_rule_definition(
        "narration", [{"field": "description", "op": "eq", "value": "x"}], {"template": "{secret}"}
    )
    assert A.validate_rule_definition(
        "dimension", [{"field": "description", "op": "eq", "value": "x"}], {"name": "colour", "value": "x"}
    )
    assert (
        A.render_narration("Inv {doc_number} from {contact}", {"doc_number": "INV-1", "contact": "Gulf"})
        == "Inv INV-1 from Gulf"
    )


# ------------------------------------------------------------------ contacts and duplicates
CONTACTS = [
    {
        "id": "1",
        "legal_name": "Gulf Office Supplies L.L.C.",
        "display_name": "Gulf Office",
        "tax_number": "OM1234567890",
    },
    {"id": "2", "legal_name": "Gulf Office Supply Co", "display_name": "Gulf Office Supply", "tax_number": None},
    {"id": "3", "legal_name": "Sohar Metals", "display_name": "Sohar Metals", "tax_number": "OM999"},
]


def test_contact_matching_is_labelled_and_never_automatic() -> None:
    m = A.match_contacts("Anything", "om 1234-567890", CONTACTS)
    assert (m[0].contact_id, m[0].strength) == ("1", "tax_number")
    m = A.match_contacts("GULF OFFICE SUPPLIES LLC", None, CONTACTS)
    assert (m[0].contact_id, m[0].strength) == ("1", "name_exact")
    m = A.match_contacts("Gulf Offices Supplies", None, CONTACTS)
    assert m and all(x.strength == "name_similar" for x in m)
    assert A.match_contacts("Unrelated Trading", None, CONTACTS) == []
    assert A.match_contacts(None, None, CONTACTS) == []


def test_duplicate_levels() -> None:
    base = A.DupFacts("C1", "INV-7", Dec("105.000"), "OMR", date(2026, 3, 1), "T1", "abc", "purchase_invoice")
    exact = A.DupFacts("C1", "inv-7", Dec("105.000"), "OMR", date(2026, 3, 9), "T1", None, "purchase_invoice")
    assert A.duplicate_level(base, exact)[0] == "exact"
    same_file = A.DupFacts("C9", "X", Dec(1), "OMR", date(2026, 5, 1), None, "abc", "purchase_invoice")
    assert A.duplicate_level(base, same_file)[0] == "exact"
    other_number = A.DupFacts("C1", "INV-8", Dec("105.000"), "OMR", date(2026, 3, 3), "T1", None, "purchase_invoice")
    assert A.duplicate_level(base, other_number)[0] == "possible"
    same_number_new_amount = A.DupFacts(
        "C1", "INV-7", Dec("99.000"), "OMR", date(2026, 3, 1), "T1", None, "purchase_invoice"
    )
    assert A.duplicate_level(base, same_number_new_amount)[0] == "possible"
    other_party = A.DupFacts("C2", "INV-7", Dec("105.000"), "OMR", date(2026, 3, 1), "T2", None, "purchase_invoice")
    assert A.duplicate_level(base, other_party)[0] is None
    only_amount = A.DupFacts(None, None, Dec("105.000"), "OMR", date(2026, 3, 1), None, None, "purchase_invoice")
    assert A.duplicate_level(base, only_amount)[0] is None
    assert A.next_number("TX-", 6, 42) == "TX-000042"
