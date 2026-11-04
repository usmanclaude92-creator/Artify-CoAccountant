"""Phase 4 pure engine tests: custom field validation, naming rules, filing rules, workspace access evaluation."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from artify_api.workspace_engine import (
    AccessSubject,
    ConfigError,
    FieldDef,
    Grant,
    RuleDef,
    analyze_rules,
    decide,
    ensure_unique_name,
    evaluate_rules,
    fallback_name,
    parse_pattern,
    render_name,
    validate_conditions,
    validate_field_definition,
    validate_node_name,
    validate_value,
    validate_values,
)

# ------------------------------------------------------------------ node names


@pytest.mark.parametrize(
    "bad", ["", "  ", "a/b", "a\\b", "..", ".", "x..y", "CON", "nul.txt", "a:b", "a*b", "a\x00b", "x" * 121, "trail."]
)
def test_unsafe_node_names_are_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        validate_node_name(bad)


def test_unicode_and_spaces_are_allowed_in_node_names() -> None:
    assert validate_node_name("  كشوف الحساب البنكي ") == "كشوف الحساب البنكي"
    assert validate_node_name("Bank of Muscat (OMR)") == "Bank of Muscat (OMR)"


# ------------------------------------------------------------------ custom field definitions and values
def fd(t: str, **kw) -> FieldDef:  # type: ignore[no-untyped-def]
    return FieldDef(key="f", label="F", data_type=t, **kw)


def test_every_field_type_validates_and_normalizes() -> None:
    assert validate_value(fd("short_text", validation={"max_length": 5}), " abc ") == "abc"
    assert validate_value(fd("long_text"), "long text") == "long text"
    assert validate_value(fd("number", validation={"min": 1, "max": 10}), 7) == 7
    assert validate_value(fd("decimal", validation={"decimal_places": 2}), "12.50") == "12.50"
    assert validate_value(
        fd("currency", validation={"allowed_currencies": ["OMR", "USD"]}), {"amount": "5.250", "currency": "OMR"}
    ) == {"amount": "5.250", "currency": "OMR"}
    assert validate_value(fd("date", validation={"min_date": "2026-01-01"}), "2026-03-04") == "2026-03-04"
    assert validate_value(fd("boolean"), True) is True
    assert validate_value(fd("dropdown", option_values=frozenset({"a", "b"})), "a") == "a"
    assert validate_value(
        fd("multi_select", option_values=frozenset({"a", "b"}), validation={"max_items": 2}), ["a", "b"]
    ) == ["a", "b"]
    ref = uuid.uuid4()
    assert validate_value(fd("reference", reference_target="workspace_node"), str(ref), lambda t, i: i == ref) == str(
        ref
    )


@pytest.mark.parametrize(
    ("defn", "value"),
    [
        (fd("short_text", validation={"max_length": 3}), "toolong"),
        (fd("short_text", validation={"mask": "AA-999"}), "AB-12x"),
        (fd("number"), 1.5),
        (fd("number"), True),
        (fd("number", validation={"max": 5}), 6),
        (fd("decimal"), 12.5),  # floats are rejected: money/decimals travel as strings
        (fd("decimal", validation={"decimal_places": 2}), "1.234"),
        (fd("currency"), "5.00"),
        (fd("currency", validation={"allowed_currencies": ["OMR"]}), {"amount": "1", "currency": "USD"}),
        (fd("date"), "04/03/2026"),
        (fd("date", validation={"max_date": "2026-01-01"}), "2026-02-01"),
        (fd("boolean"), "yes"),
        (fd("dropdown", option_values=frozenset({"a"})), "zzz"),
        (fd("multi_select", option_values=frozenset({"a", "b"})), ["a", "a"]),
        (fd("multi_select", option_values=frozenset({"a"})), ["b"]),
        (fd("reference", reference_target="document_category"), str(uuid.uuid4())),
        (fd("short_text"), None),
    ],
)
def test_invalid_values_are_rejected(defn: FieldDef, value) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError):
        validate_value(defn, value)


@pytest.mark.parametrize(
    ("t", "validation", "default", "options", "target"),
    [
        ("short_text", {"regex": ".*"}, None, [], None),  # no regex / executable content
        ("number", {"min": 5, "max": 1}, None, [], None),
        ("dropdown", {}, None, [], None),  # needs options
        ("short_text", {}, None, ["a"], None),  # options only for dropdowns
        ("dropdown", {}, "z", ["a"], None),  # default must be a valid option
        ("reference", {}, None, [], None),  # needs a target
        ("short_text", {}, None, [], "workspace_node"),
        ("dropdown", {}, None, ["a", "A"], None),  # duplicate options
        ("decimal", {"decimal_places": 99}, None, [], None),
        ("nonsense", {}, None, [], None),
    ],
)
def test_invalid_field_definitions_are_rejected(t, validation, default, options, target) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError):
        validate_field_definition(t, validation, default, options, target)


def test_value_sets_report_missing_required_unknown_and_never_drop_silently() -> None:
    defs = [FieldDef("project", "Project", "short_text", required=True), FieldDef("amount", "Amount", "decimal")]
    values, problems = validate_values(defs, {"amount": "10", "stray": 1})
    assert values == {"amount": "10"}
    assert any("Project" in p for p in problems) and any("stray" in p for p in problems)


# ------------------------------------------------------------------ naming rules
def render(pattern: str, values: dict, ext: str | None = "pdf", fields=()) -> tuple[str, list[str]]:  # type: ignore[no-untyped-def]
    return render_name(parse_pattern(pattern, fields), values, extension=ext)


def test_naming_renders_tokens_dates_and_extension() -> None:
    name, warnings = render(
        "{Supplier}_{InvoiceNumber}_{InvoiceDate}",
        {"supplier": "Acme LLC", "invoicenumber": "INV-9", "invoicedate": "2026-03-04"},
        fields=["invoice_number", "invoice_date", "supplier"],
    )
    assert name == "Acme_LLC_INV-9_2026-03-04.pdf"
    name, warnings = render(
        "{Company}_{Period}_{DocumentType}",
        {"company": "Gamma", "documentdate": "2026-03-04", "documenttype": "bank_statement"},
    )
    assert name == "Gamma_2026-03_bank_statement.pdf" and not warnings


def test_missing_values_use_fallbacks_and_warn() -> None:
    name, warnings = render("{Supplier|Unknown}_{Project}", {})
    assert name == "Unknown_NA.pdf" and len(warnings) == 2


@pytest.mark.parametrize(
    "pattern",
    [
        "{Nonsense}",
        "no tokens",
        "{Supplier}/{Project}",
        "..{Supplier}",
        "{Supplier}\\x",
        "{Supplier",
        "{}",
        "{Supplier|a/b}",
    ],
)
def test_invalid_patterns_are_rejected(pattern: str) -> None:
    with pytest.raises(ConfigError):
        parse_pattern(pattern, [])


def test_hostile_values_cannot_escape_the_file_name() -> None:
    name, _ = render("{Supplier}", {"supplier": "../../etc/passwd"}, ext="../exe")
    assert "/" not in name and "\\" not in name and ".." not in name and not name.startswith(".")
    name, _ = render("{Supplier}", {"supplier": 'a<b>:"c"|d?*'}, ext="pdf")
    assert not any(ch in name for ch in '<>:"|?*')
    name, _ = render("{Supplier}", {"supplier": "CON"}, ext=None)
    assert name.upper().split(".")[0] != "CON"
    assert len(render("{Supplier}", {"supplier": "x" * 500})[0]) <= 160


def test_custom_field_tokens_and_ambiguity() -> None:
    assert render("{cost_center}", {"cost_center": "CC-1"}, fields=["cost_center"])[0] == "CC-1.pdf"
    with pytest.raises(ConfigError):
        parse_pattern("{costcenter}", ["cost_center", "cost-center"])


def test_names_never_overwrite_and_fallback_is_deterministic() -> None:
    assert ensure_unique_name("a.pdf", ["a.pdf", "a_2.pdf"]) == "a_3.pdf"
    assert ensure_unique_name("A.PDF", ["a.pdf"]) == "A_2.PDF"
    assert (
        fallback_name("bank_statement", None, "ab12", "pdf")
        == fallback_name("bank_statement", None, "ab12", "pdf")
        == "bank_statement_undated_ab12.pdf"
    )


def test_financial_year_token_follows_the_company_start_month() -> None:
    parts = parse_pattern("{FinancialYear}_{Supplier}", [])
    assert (
        render_name(parts, {"documentdate": "2026-02-10", "supplier": "A"}, financial_year_start_month=4)[0]
        == "2025-2026_A"
    )
    assert (
        render_name(parts, {"documentdate": "2026-05-10", "supplier": "A"}, financial_year_start_month=4)[0]
        == "2026-2027_A"
    )


# ------------------------------------------------------------------ filing rules
NODE_A, NODE_B = uuid.uuid4(), uuid.uuid4()


def rule(name: str, prio: int, conds: list[dict], node=NODE_A, minute: int = 0) -> RuleDef:  # type: ignore[no-untyped-def]
    c = validate_conditions(conds, ["bank", "department"], [])
    return RuleDef(uuid.uuid4(), name, prio, datetime(2026, 1, 1, 0, minute), tuple(c), node)


def test_condition_validation() -> None:
    for bad in (
        [],
        [{"field": "nope", "operator": "equals", "value": 1}],
        [{"field": "document_kind", "operator": "equals", "value": "not_a_kind"}],
        [{"field": "document_kind", "operator": "contains", "value": "bank"}],
        [{"field": "custom:missing", "operator": "equals", "value": "x"}],
        [{"field": "source", "operator": "equals", "value": "fax"}],
        [{"field": "filename", "operator": "equals"}],
        [{"field": "filename", "operator": "is_present", "value": 1}],
        [{"field": "filename", "operator": "in", "value": []}],
        [{"field": "filename", "operator": "equals", "value": "x", "extra": 1}],
        [{"field": "category_id", "operator": "equals", "value": str(uuid.uuid4())}],
    ):
        with pytest.raises(ConfigError):
            validate_conditions(bad, ["bank"], [])


def test_priority_resolution_and_tie_break_are_deterministic() -> None:
    specific = rule(
        "Muscat",
        5,
        [
            {"field": "document_kind", "operator": "equals", "value": "bank_statement"},
            {"field": "custom:bank", "operator": "equals", "value": "Bank of Muscat"},
        ],
        NODE_B,
    )
    general = rule(
        "Any bank statement", 10, [{"field": "document_kind", "operator": "equals", "value": "bank_statement"}], NODE_A
    )
    attrs = {"document_kind": "bank_statement", "custom:bank": "bank of muscat"}  # case-insensitive
    winner, hits = evaluate_rules([general, specific], attrs)
    assert winner is specific and hits == [specific, general]
    winner, _ = evaluate_rules([general, specific], {"document_kind": "bank_statement", "custom:bank": "Bank A"})
    assert winner is general
    assert evaluate_rules([general, specific], {"document_kind": "sales_invoice"})[0] is None
    older, newer = (
        rule("older", 7, [{"field": "source", "operator": "equals", "value": "mobile"}], minute=1),
        rule("newer", 7, [{"field": "source", "operator": "equals", "value": "mobile"}], minute=2),
    )
    assert evaluate_rules([newer, older], {"source": "mobile"})[0] is older


def test_operators() -> None:
    attrs = {"filename": "Statement_March.PDF", "custom:department": "Construction"}

    def hit(field: str, op: str, value=None) -> bool:  # type: ignore[no-untyped-def]
        return (
            evaluate_rules(
                [rule("r", 1, [{"field": field, "operator": op, **({} if value is None else {"value": value})}])], attrs
            )[0]
            is not None
        )

    assert (
        hit("filename", "contains", "march")
        and hit("filename", "starts_with", "statement")
        and hit("filename", "ends_with", ".pdf")
    )
    assert hit("custom:department", "in", ["Retail", "Construction"]) and not hit(
        "custom:department", "not_equals", "construction"
    )
    assert (
        hit("custom:bank", "is_empty")
        and hit("custom:department", "is_present")
        and hit("custom:bank", "not_equals", "x")
    )


def test_static_analysis_flags_duplicates_and_shadowed_rules() -> None:
    broad = rule("broad", 10, [{"field": "document_kind", "operator": "equals", "value": "bank_statement"}], NODE_A)
    narrow = rule(
        "narrow",
        20,
        [
            {"field": "document_kind", "operator": "equals", "value": "bank_statement"},
            {"field": "custom:bank", "operator": "equals", "value": "X"},
        ],
        NODE_B,
    )
    dup = rule("dup", 10, [{"field": "source", "operator": "equals", "value": "web"}], NODE_A, minute=5)
    codes = {f["code"] for f in analyze_rules([broad, narrow, dup])}
    assert codes == {"shadowed", "duplicate_priority"}


# ------------------------------------------------------------------ workspace access evaluation
ME, SECTION, FOLDER, SUB = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CHAIN = [SUB, FOLDER, SECTION]  # nearest first


def subj(role: str = "accountant", mode: str = "open", base=frozenset({"workspace.read"})) -> AccessSubject:  # type: ignore[no-untyped-def]
    return AccessSubject(ME, role, base, mode)


def g(node, perm: str, effect: str, *, user: bool = True, role: str = "accountant") -> Grant:  # type: ignore[no-untyped-def]
    return Grant(node, "user" if user else "role", ME if user else None, None if user else role, perm, effect)


def test_role_defaults_in_open_mode() -> None:
    assert decide(subj(), CHAIN, [], "view")[0] and not decide(subj(), CHAIN, [], "edit")[0]
    manager = subj("practice_manager", base=frozenset({"workspace.read", "workspace.manage"}))
    assert decide(manager, CHAIN, [], "move")[0] and not decide(manager, CHAIN, [], "manage_permissions")[0]
    admin = subj("firm_owner", base=frozenset({"workspace.read", "workspace.manage", "workspace.permissions.manage"}))
    assert decide(admin, CHAIN, [g(SECTION, "view", "deny")], "view")[0]  # administrators can never be locked out


def test_grants_inherit_down_and_nearest_level_wins() -> None:
    allow_section = [g(SECTION, "edit", "allow")]
    assert decide(subj(), CHAIN, allow_section, "edit") == (True, "explicit_allow")
    assert not decide(subj(), [uuid.uuid4()], allow_section, "edit")[0]  # an unrelated tree is not affected
    grants = [g(SECTION, "edit", "allow"), g(FOLDER, "edit", "deny")]
    assert decide(subj(), CHAIN, grants, "edit") == (False, "explicit_deny")
    assert decide(subj(), [SECTION], grants, "edit")[0]  # the deny lives lower in the tree
    reopened = [*grants, g(SUB, "edit", "allow")]
    assert decide(subj(), CHAIN, reopened, "edit")[0]  # nearest wins


def test_deny_beats_allow_on_the_same_level_and_parents_never_inherit_from_children() -> None:
    both = [g(FOLDER, "view", "allow", user=False), g(FOLDER, "view", "deny")]
    assert decide(subj(), CHAIN, both, "view") == (False, "explicit_deny")
    assert not decide(subj(), [SECTION], [g(SUB, "edit", "allow")], "edit")[0]  # child grants do not climb


def test_grants_only_apply_to_their_subject() -> None:
    other = Grant(SECTION, "user", uuid.uuid4(), None, "edit", "allow")
    assert not decide(subj(), CHAIN, [other], "edit")[0]
    assert decide(subj(), CHAIN, [Grant(SECTION, "role", None, "accountant", "edit", "allow")], "edit")[0]
    assert not decide(
        subj("assistant_accountant"), CHAIN, [Grant(SECTION, "role", None, "accountant", "edit", "allow")], "edit"
    )[0]


def test_restricted_mode_requires_an_explicit_allow() -> None:
    restricted = subj(mode="restricted")
    assert not decide(restricted, CHAIN, [], "view")[0]
    assert decide(restricted, CHAIN, [g(SECTION, "view", "allow")], "view")[0]
    assert not decide(restricted, [uuid.uuid4()], [g(SECTION, "view", "allow")], "view")[0]
    manager = subj("practice_manager", "restricted", frozenset({"workspace.read", "workspace.manage"}))
    assert decide(manager, CHAIN, [], "view")[0]  # managers keep working in restricted mode


def test_read_only_members_are_capped_at_view() -> None:
    ro = subj("read_only")
    assert decide(ro, CHAIN, [], "view")[0]
    assert decide(ro, CHAIN, [g(SECTION, "edit", "allow")], "edit") == (False, "read_only_ceiling")


def test_unknown_actions_are_denied() -> None:
    assert decide(subj(), CHAIN, [], "teleport") == (False, "unknown_action")
