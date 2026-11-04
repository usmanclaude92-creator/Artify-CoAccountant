"""Workspace configuration engine: pure rules with no database and no web framework.

Contains the Artify-controlled catalogs (document kinds), node-name safety, custom-field definition/value
validation, the document naming-rule engine, filing-rule validation/evaluation and the workspace access
(allow/deny/inheritance) evaluation. The database service in `workspace.py` and the routes only orchestrate these.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------- catalogs (Artify-controlled)


@dataclass(frozen=True)
class DocumentKind:
    key: str
    name: str
    accounting_domain: str
    processing_type: str
    creates_financial_records: bool
    description: str


# docs/architecture/ACCOUNTING_DATA_MAPPING.md §3. Closed catalog: customers map INTO it, never edit it.
DOCUMENT_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind("bank_statement", "Bank statement", "banking", "statement_extraction", True, "Bank activity"),
    DocumentKind("sales_invoice", "Sales invoice", "receivables", "invoice_extraction", True, "Receivable"),
    DocumentKind(
        "sales_credit_note", "Sales credit note", "receivables", "invoice_extraction", True, "Receivable reduction"
    ),
    DocumentKind("purchase_invoice", "Purchase invoice", "payables", "invoice_extraction", True, "Payable"),
    DocumentKind(
        "purchase_credit_note", "Purchase credit note", "payables", "invoice_extraction", True, "Payable reduction"
    ),
    DocumentKind("expense_receipt", "Expense receipt", "expenses", "receipt_extraction", True, "Direct expense"),
    DocumentKind(
        "payment_receipt", "Payment receipt", "settlements", "receipt_extraction", False, "Settlement evidence"
    ),
    DocumentKind("payroll_document", "Payroll document", "payroll", "reference_only", False, "Payroll evidence"),
    DocumentKind("inventory_document", "Inventory document", "inventory", "reference_only", False, "Stock evidence"),
    DocumentKind("tax_document", "Tax document", "taxation", "reference_only", False, "Tax correspondence/returns"),
    DocumentKind("reference_document", "Reference document", "general", "reference_only", False, "Non-posting"),
)
KIND_BY_KEY = {k.key: k for k in DOCUMENT_KINDS}
ACCOUNTING_DOMAINS = frozenset(k.accounting_domain for k in DOCUMENT_KINDS)
PROCESSING_TYPES = frozenset(k.processing_type for k in DOCUMENT_KINDS)
FIELD_TYPES = (
    "short_text",
    "long_text",
    "number",
    "decimal",
    "currency",
    "date",
    "dropdown",
    "multi_select",
    "boolean",
    "reference",
)
REFERENCE_TARGETS = ("workspace_node", "document_category")
NODE_ACTIONS = ("view", "upload", "edit", "rename", "move", "archive", "delete", "configure", "manage_permissions")
FILE_TYPE_RE = re.compile(r"^[a-z0-9]{1,10}$")


class ConfigError(ValueError):
    """A configuration the engine rejects. `args[0]` is a plain-language message."""


# ---------------------------------------------------------------- node names and keys
_BAD_NAME = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_node_name(name: str) -> str:
    """Names become local folder names on Windows: reject path separators, control and reserved characters."""
    clean = unicodedata.normalize("NFC", name).strip()
    if not clean or len(clean) > 120:
        raise ConfigError("A name must be between 1 and 120 characters.")
    if _BAD_NAME.search(clean):
        raise ConfigError('A name cannot contain / \\ : * ? " < > | or control characters.')
    if clean in (".", "..") or clean.endswith(".") or ".." in clean:
        raise ConfigError("A name cannot be '.', '..', contain '..' or end with a dot.")
    if clean.split(".")[0].upper() in _RESERVED:
        raise ConfigError("That name is reserved by Windows and cannot be used for a folder.")
    return clean


def slugify_key(text_value: str, fallback: str = "item") -> str:
    ascii_text = unicodedata.normalize("NFKD", text_value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:48]
    return slug or fallback


def unique_key(base: str, taken: Iterable[str], sep: str = "-") -> str:
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}{sep}{n}" in used:
        n += 1
    return f"{base}{sep}{n}"


def unique_name(base: str, taken_lower: Iterable[str]) -> str:
    used = set(taken_lower)
    if base.lower() not in used:
        return base
    n = 2
    while f"{base} ({n})".lower() in used:
        n += 1
    return f"{base} ({n})"


def normalize_file_types(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        ext = v.strip().lower().lstrip(".")
        if not FILE_TYPE_RE.match(ext):
            raise ConfigError(f"'{v}' is not a valid file extension.")
        if ext not in out:
            out.append(ext)
    return out


# ---------------------------------------------------------------- custom field definitions and values
_VALIDATION_KEYS: dict[str, frozenset[str]] = {
    "short_text": frozenset({"min_length", "max_length", "mask"}),
    "long_text": frozenset({"min_length", "max_length"}),
    "number": frozenset({"min", "max"}),
    "decimal": frozenset({"min", "max", "decimal_places"}),
    "currency": frozenset({"min", "max", "decimal_places", "allowed_currencies"}),
    "date": frozenset({"min_date", "max_date"}),
    "dropdown": frozenset(),
    "multi_select": frozenset({"min_items", "max_items"}),
    "boolean": frozenset(),
    "reference": frozenset(),
}
_MASK_CHARS = set("A9*")


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    data_type: str
    required: bool = False
    validation: Mapping[str, Any] = field(default_factory=dict)
    default_value: Any = None
    reference_target: str | None = None
    option_values: frozenset[str] = frozenset()


def _int(v: Any, what: str, lo: int = 0, hi: int = 10_000) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ConfigError(f"{what} must be a whole number between {lo} and {hi}.")
    return v


def _dec(v: Any, what: str) -> Decimal:
    if isinstance(v, (bool, float)) or not isinstance(v, (str, int)):
        raise ConfigError(f'{what} must be a number written as text, for example "12.50".')
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation as exc:
        raise ConfigError(f"{what} is not a valid number.") from exc
    if not d.is_finite():
        raise ConfigError(f"{what} is not a valid number.")
    return d


def _iso_date(v: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(v))
    except ValueError as exc:
        raise ConfigError(f"{what} must be a date written as YYYY-MM-DD.") from exc


def validate_field_definition(
    data_type: str,
    validation: Mapping[str, Any],
    default_value: Any,
    option_values: Sequence[str],
    reference_target: str | None,
) -> None:
    """Reject anything that is not plain declarative configuration (no regex, no code) or is inconsistent."""
    if data_type not in FIELD_TYPES:
        raise ConfigError(f"Unknown field type '{data_type}'.")
    unknown = set(validation) - _VALIDATION_KEYS[data_type]
    if unknown:
        raise ConfigError(f"Validation setting(s) {sorted(unknown)} do not apply to a {data_type} field.")
    if (data_type == "reference") != (reference_target is not None):
        raise ConfigError("Only reference fields have a reference target, and they must have one.")
    if reference_target is not None and reference_target not in REFERENCE_TARGETS:
        raise ConfigError(f"Reference target must be one of {', '.join(REFERENCE_TARGETS)}.")
    has_options = data_type in ("dropdown", "multi_select")
    if has_options and not option_values:
        raise ConfigError("Add at least one option for a dropdown or multi-select field.")
    if not has_options and option_values:
        raise ConfigError("Only dropdown and multi-select fields have options.")
    if len({o.lower() for o in option_values}) != len(option_values):
        raise ConfigError("Option values must be unique.")
    v = validation
    if "min_length" in v:
        _int(v["min_length"], "min_length")
    if "max_length" in v:
        _int(v["max_length"], "max_length", 1)
    if "min_length" in v and "max_length" in v and v["min_length"] > v["max_length"]:
        raise ConfigError("min_length cannot exceed max_length.")
    if "mask" in v:
        mask = v["mask"]
        if not isinstance(mask, str) or not 1 <= len(mask) <= 64:
            raise ConfigError("mask must be 1-64 characters (A = letter, 9 = digit, * = any character).")
    if data_type == "number":
        lo = _int(v["min"], "min", -(10**15), 10**15) if "min" in v else None
        hi = _int(v["max"], "max", -(10**15), 10**15) if "max" in v else None
        if lo is not None and hi is not None and lo > hi:
            raise ConfigError("min cannot exceed max.")
    if data_type in ("decimal", "currency"):
        lo_d = _dec(v["min"], "min") if "min" in v else None
        hi_d = _dec(v["max"], "max") if "max" in v else None
        if lo_d is not None and hi_d is not None and lo_d > hi_d:
            raise ConfigError("min cannot exceed max.")
        if "decimal_places" in v:
            _int(v["decimal_places"], "decimal_places", 0, 6)
    if "allowed_currencies" in v:
        cur = v["allowed_currencies"]
        if (
            not isinstance(cur, list)
            or not cur
            or not all(isinstance(c, str) and re.fullmatch(r"[A-Z]{3}", c) for c in cur)
        ):
            raise ConfigError("allowed_currencies must be a list of 3-letter currency codes.")
    if data_type == "date":
        a = _iso_date(v["min_date"], "min_date") if "min_date" in v else None
        b = _iso_date(v["max_date"], "max_date") if "max_date" in v else None
        if a and b and a > b:
            raise ConfigError("min_date cannot exceed max_date.")
    if "min_items" in v:
        _int(v["min_items"], "min_items")
    if "max_items" in v:
        _int(v["max_items"], "max_items", 1)
    if "min_items" in v and "max_items" in v and v["min_items"] > v["max_items"]:
        raise ConfigError("min_items cannot exceed max_items.")
    if default_value is not None:
        # a default must itself be a valid value (reference defaults are not allowed: ids differ per company)
        if data_type == "reference":
            raise ConfigError("A reference field cannot have a default value.")
        probe = FieldDef(
            "default", "default", data_type, False, validation, None, reference_target, frozenset(option_values)
        )
        validate_value(probe, default_value)


def _check_mask(mask: str, value: str) -> bool:
    if len(mask) != len(value):
        return False
    for m, ch in zip(mask, value, strict=True):
        if m == "A" and not ch.isalpha():
            return False
        if m == "9" and not ch.isdigit():
            return False
        if m not in _MASK_CHARS and m != ch:
            return False
    return True


def validate_value(defn: FieldDef, value: Any, ref_exists: Callable[[str, uuid.UUID], bool] | None = None) -> Any:
    """Validate one value against a definition and return its normalized JSON form. Raises ConfigError."""
    label = defn.label
    v = defn.validation
    t = defn.data_type
    if value is None or value == "":
        raise ConfigError(f"{label}: a value is required here (leave the field out to leave it empty).")
    if t in ("short_text", "long_text"):
        if not isinstance(value, str):
            raise ConfigError(f"{label}: must be text.")
        text_value = value.strip()
        if t == "short_text" and len(text_value) > 255:
            raise ConfigError(f"{label}: at most 255 characters.")
        if t == "long_text" and len(text_value) > 10_000:
            raise ConfigError(f"{label}: at most 10,000 characters.")
        if len(text_value) < v.get("min_length", 0):
            raise ConfigError(f"{label}: at least {v['min_length']} characters.")
        if "max_length" in v and len(text_value) > v["max_length"]:
            raise ConfigError(f"{label}: at most {v['max_length']} characters.")
        if "mask" in v and not _check_mask(v["mask"], text_value):
            raise ConfigError(f"{label}: must match the format {v['mask']} (A = letter, 9 = digit).")
        return text_value
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{label}: must be a whole number.")
        if "min" in v and value < v["min"]:
            raise ConfigError(f"{label}: must be at least {v['min']}.")
        if "max" in v and value > v["max"]:
            raise ConfigError(f"{label}: must be at most {v['max']}.")
        return value
    if t in ("decimal", "currency"):
        raw = value.get("amount") if t == "currency" and isinstance(value, dict) else value
        if t == "currency" and not isinstance(value, dict):
            raise ConfigError(f'{label}: must be {{"amount": "12.50", "currency": "OMR"}}.')
        amount = _dec(raw, label)
        places = v.get("decimal_places")
        if places is not None and -amount.as_tuple().exponent > places:  # type: ignore[operator]
            raise ConfigError(f"{label}: at most {places} decimal places.")
        if "min" in v and amount < Decimal(str(v["min"])):
            raise ConfigError(f"{label}: must be at least {v['min']}.")
        if "max" in v and amount > Decimal(str(v["max"])):
            raise ConfigError(f"{label}: must be at most {v['max']}.")
        if t == "decimal":
            return format(amount, "f")
        currency = value.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ConfigError(f"{label}: currency must be a 3-letter code.")
        if "allowed_currencies" in v and currency not in v["allowed_currencies"]:
            raise ConfigError(f"{label}: currency must be one of {', '.join(v['allowed_currencies'])}.")
        return {"amount": format(amount, "f"), "currency": currency}
    if t == "date":
        if not isinstance(value, str):
            raise ConfigError(f"{label}: must be a date written as YYYY-MM-DD.")
        d = _iso_date(value, label)
        if "min_date" in v and d < date.fromisoformat(v["min_date"]):
            raise ConfigError(f"{label}: must be on or after {v['min_date']}.")
        if "max_date" in v and d > date.fromisoformat(v["max_date"]):
            raise ConfigError(f"{label}: must be on or before {v['max_date']}.")
        return d.isoformat()
    if t == "boolean":
        if not isinstance(value, bool):
            raise ConfigError(f"{label}: must be yes or no.")
        return value
    if t == "dropdown":
        if not isinstance(value, str) or value not in defn.option_values:
            raise ConfigError(f"{label}: choose one of the configured options.")
        return value
    if t == "multi_select":
        if not isinstance(value, list) or len(set(map(str, value))) != len(value):
            raise ConfigError(f"{label}: must be a list of distinct options.")
        if any(not isinstance(x, str) or x not in defn.option_values for x in value):
            raise ConfigError(f"{label}: contains an option that is not configured.")
        if len(value) < v.get("min_items", 0) or ("max_items" in v and len(value) > v["max_items"]):
            raise ConfigError(
                f"{label}: choose between {v.get('min_items', 0)} and {v.get('max_items', 'any number of')} options."
            )
        return value
    if t == "reference":
        try:
            ref = uuid.UUID(str(value))
        except ValueError as exc:
            raise ConfigError(f"{label}: must be the id of an existing record.") from exc
        if ref_exists is None or defn.reference_target is None or not ref_exists(defn.reference_target, ref):
            raise ConfigError(f"{label}: that record does not exist in this company.")
        return str(ref)
    raise ConfigError(f"Unknown field type '{t}'.")  # pragma: no cover


def validate_values(
    defs: Sequence[FieldDef], values: Mapping[str, Any], ref_exists: Callable[[str, uuid.UUID], bool] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Validate a whole value set: returns (normalized values, problems). Unknown keys are problems; missing required
    fields are problems; nothing is silently dropped or invented."""
    problems: list[str] = []
    out: dict[str, Any] = {}
    by_key = {d.key: d for d in defs}
    for key in values:
        if key not in by_key:
            problems.append(f"{key}: this field does not apply here.")
    for d in defs:
        if d.key not in values or values[d.key] is None:
            if d.required:
                problems.append(f"{d.label}: required.")
            continue
        try:
            out[d.key] = validate_value(d, values[d.key], ref_exists)
        except ConfigError as exc:
            problems.append(str(exc))
    return out, problems


# ---------------------------------------------------------------- naming rules
BUILTIN_TOKENS: dict[str, str] = {
    "company": "Company name",
    "companycode": "Company code",
    "documenttype": "Document type (kind)",
    "documentnumber": "Document number",
    "documentdate": "Document date (YYYY-MM-DD)",
    "date": "Document date (YYYY-MM-DD)",
    "year": "Calendar year of the document date",
    "month": "Month (01-12) of the document date",
    "period": "Year and month (YYYY-MM)",
    "financialyear": "Financial year (from the company's financial-year start)",
    "supplier": "Supplier",
    "customer": "Customer",
    "project": "Project",
    "branch": "Branch",
    "category": "Document category name",
}
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_NAME_MAX = 150
_SAFE_LITERAL = re.compile(r'^[^/\\:*?"<>|\x00-\x1f\x7f]*$')


def _norm_token(raw: str) -> str:
    return re.sub(r"[_\-\s]", "", raw).lower()


@dataclass(frozen=True)
class NamingToken:
    token: str  # normalized
    fallback: str | None
    raw: str


def parse_pattern(pattern: str, custom_field_keys: Iterable[str] = ()) -> list[str | NamingToken]:
    """Split a naming pattern into literals and tokens; raises ConfigError for anything unsafe or unknown."""
    if not pattern.strip() or len(pattern) > 300:
        raise ConfigError("A naming pattern must be between 1 and 300 characters.")
    custom: dict[str, list[str]] = {}
    for k in custom_field_keys:
        custom.setdefault(_norm_token(k), []).append(k)
    parts: list[str | NamingToken] = []
    pos = 0
    for m in _TOKEN_RE.finditer(pattern):
        parts.extend(_literal(pattern[pos : m.start()]))
        body = m.group(1)
        name, _, fallback = body.partition("|")
        norm = _norm_token(name)
        if not norm:
            raise ConfigError("Empty token '{}' in the naming pattern.")
        if norm not in BUILTIN_TOKENS:
            hits = custom.get(norm, [])
            if not hits:
                raise ConfigError(
                    f"Unknown token '{{{name}}}'. Use a built-in token or the key of an active custom field."
                )
            if len(hits) > 1:
                raise ConfigError(f"Token '{{{name}}}' is ambiguous between fields {sorted(hits)}.")
        if fallback and not _SAFE_LITERAL.match(fallback):
            raise ConfigError("A token fallback contains characters that are not allowed in file names.")
        parts.append(NamingToken(norm, fallback or None, name))
        pos = m.end()
    tail = pattern[pos:]
    if "{" in tail or "}" in tail:
        raise ConfigError("Unbalanced braces in the naming pattern.")
    parts.extend(_literal(tail))
    if not any(isinstance(p, NamingToken) for p in parts):
        raise ConfigError("A naming pattern needs at least one token, otherwise every file gets the same name.")
    return parts


def _literal(text_value: str) -> list[str]:
    if not text_value:
        return []
    if not _SAFE_LITERAL.match(text_value) or ".." in text_value:
        raise ConfigError(
            'Fixed text in a naming pattern cannot contain / \\ : * ? " < > | control characters or "..".'
        )
    return [text_value]


def sanitize_segment(value: str, max_len: int = 80) -> str:
    v = unicodedata.normalize("NFKC", value)
    v = _BAD_NAME.sub("-", v)
    v = re.sub(r"\s+", "_", v.strip())
    v = re.sub(r"\.{2,}", ".", v).strip(". _-") if v else v
    return v[:max_len]


def _financial_year(d: date, start_month: int) -> str:
    if start_month == 1:
        return str(d.year)
    y = d.year if d.month >= start_month else d.year - 1
    return f"{y}-{y + 1}"


def render_name(
    parts: Sequence[str | NamingToken],
    values: Mapping[str, Any],
    *,
    extension: str | None = None,
    financial_year_start_month: int = 1,
) -> tuple[str, list[str]]:
    """Render a file name. `values` maps normalized token names (and custom field keys) to values. Missing tokens use
    their fallback or `NA` and are reported as warnings. The result is always a safe single path segment."""
    norm_values = {_norm_token(k): v for k, v in values.items() if v not in (None, "")}
    when: date | None = None
    for key in ("documentdate", "date"):
        raw = norm_values.get(key)
        if raw:
            try:
                when = date.fromisoformat(str(raw))
            except ValueError:
                when = None
            break
    derived: dict[str, str] = {}
    if when:
        derived = {
            "documentdate": when.isoformat(),
            "date": when.isoformat(),
            "year": f"{when.year:04d}",
            "month": f"{when.month:02d}",
            "period": f"{when.year:04d}-{when.month:02d}",
            "financialyear": _financial_year(when, financial_year_start_month),
        }
    warnings: list[str] = []
    out: list[str] = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
            continue
        raw_val = norm_values.get(p.token) or derived.get(p.token)
        if raw_val in (None, ""):
            warnings.append(f"No value for {{{p.raw}}}; used '{p.fallback or 'NA'}'.")
            raw_val = p.fallback or "NA"
        seg = sanitize_segment(str(raw_val))
        out.append(seg or (p.fallback or "NA"))
    name = "".join(out)
    name = sanitize_segment(name, _NAME_MAX) or "document"
    if name.split(".")[0].upper() in _RESERVED:
        name = f"{name}_"
    if extension:
        ext = re.sub(r"[^a-z0-9]", "", extension.lower().lstrip("."))[:10]
        if ext:
            name = f"{name}.{ext}"
    return name, warnings


def fallback_name(document_kind: str | None, doc_date: str | None, unique_suffix: str, extension: str | None) -> str:
    """Deterministic fallback when no rule applies: <kind>_<date|undated>_<unique suffix>[.ext]."""
    base = f"{sanitize_segment(document_kind or 'document', 40)}_{sanitize_segment(doc_date or 'undated', 10)}_{sanitize_segment(unique_suffix, 16)}"
    if extension:
        ext = re.sub(r"[^a-z0-9]", "", extension.lower().lstrip("."))[:10]
        if ext:
            base = f"{base}.{ext}"
    return base


def ensure_unique_name(name: str, existing_lower: Iterable[str]) -> str:
    """Never overwrite: append _2, _3 ... before the extension until the name is free."""
    used = set(existing_lower)
    if name.lower() not in used:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        cand = f"{stem}_{n}{('.' + ext) if ext else ''}"
        if cand.lower() not in used:
            return cand
        n += 1


# ---------------------------------------------------------------- filing rules
FILING_OPERATORS = ("equals", "not_equals", "in", "contains", "starts_with", "ends_with", "is_present", "is_empty")
_NO_VALUE_OPS = {"is_present", "is_empty"}
_STRING_OPS = {"contains", "starts_with", "ends_with"}
SOURCES = ("mobile", "desktop", "web", "email")


@dataclass(frozen=True)
class RuleCondition:
    field: str  # document_kind | category_id | source | filename | custom:<field key>
    operator: str
    value: Any = None


@dataclass(frozen=True)
class RuleDef:
    id: uuid.UUID
    name: str
    priority: int
    created_at: Any
    conditions: tuple[RuleCondition, ...]
    target_node_id: uuid.UUID
    target_category_id: uuid.UUID | None = None


def validate_conditions(
    raw: Sequence[Mapping[str, Any]], custom_field_keys: Iterable[str], category_ids: Iterable[str]
) -> list[RuleCondition]:
    keys = set(custom_field_keys)
    cats = set(category_ids)
    if not raw or len(raw) > 20:
        raise ConfigError("A rule needs between 1 and 20 conditions.")
    out: list[RuleCondition] = []
    for i, c in enumerate(raw, 1):
        if set(c) - {"field", "operator", "value"}:
            raise ConfigError(f"Condition {i} has unsupported properties.")
        fld, op, val = str(c.get("field", "")), str(c.get("operator", "")), c.get("value")
        if fld.startswith("custom:"):
            if fld[7:] not in keys:
                raise ConfigError(f"Condition {i}: '{fld[7:]}' is not an active custom field of this company.")
        elif fld not in ("document_kind", "category_id", "source", "filename"):
            raise ConfigError(f"Condition {i}: unknown field '{fld}'.")
        if op not in FILING_OPERATORS:
            raise ConfigError(f"Condition {i}: unknown operator '{op}'.")
        if op in _STRING_OPS and fld in ("document_kind", "category_id", "source"):
            raise ConfigError(f"Condition {i}: '{op}' only applies to text and filename conditions.")
        if op in _NO_VALUE_OPS:
            if val is not None:
                raise ConfigError(f"Condition {i}: '{op}' takes no value.")
        else:
            vals: list[Any] = list(val) if op == "in" and isinstance(val, list) else [val]
            if op == "in" and (not isinstance(val, list) or not val or len(val) > 50):
                raise ConfigError(f"Condition {i}: 'in' needs a list of 1-50 values.")
            for item in vals:
                if (
                    isinstance(item, bool)
                    or not isinstance(item, (str, int))
                    or str(item) == ""
                    or len(str(item)) > 200
                ):
                    raise ConfigError(f"Condition {i}: values must be non-empty text or numbers.")
                if fld == "document_kind" and str(item) not in KIND_BY_KEY:
                    raise ConfigError(f"Condition {i}: '{item}' is not a document kind.")
                if fld == "source" and str(item) not in SOURCES:
                    raise ConfigError(f"Condition {i}: source must be one of {', '.join(SOURCES)}.")
                if fld == "category_id" and str(item) not in cats:
                    raise ConfigError(f"Condition {i}: that category does not exist in this company.")
        out.append(RuleCondition(fld, op, val))
    return out


def _cond_matches(c: RuleCondition, attrs: Mapping[str, Any]) -> bool:
    actual = attrs.get(c.field)
    present = actual not in (None, "", [])
    if c.operator == "is_present":
        return present
    if c.operator == "is_empty":
        return not present
    if not present:
        return c.operator == "not_equals"
    a = [str(x).lower() for x in actual] if isinstance(actual, list) else [str(actual).lower()]
    want = [str(x).lower() for x in c.value] if c.operator == "in" else [str(c.value).lower()]
    if c.operator == "equals":
        return any(x == want[0] for x in a)
    if c.operator == "not_equals":
        return all(x != want[0] for x in a)
    if c.operator == "in":
        return any(x in want for x in a)
    if c.operator == "contains":
        return any(want[0] in x for x in a)
    if c.operator == "starts_with":
        return any(x.startswith(want[0]) for x in a)
    if c.operator == "ends_with":
        return any(x.endswith(want[0]) for x in a)
    return False  # pragma: no cover


def evaluate_rules(rules: Sequence[RuleDef], attrs: Mapping[str, Any]) -> tuple[RuleDef | None, list[RuleDef]]:
    """Deterministic resolution: lowest priority number wins; ties break by creation time then id. All conditions of a
    rule must match (AND). Returns (winner, every matching rule in resolution order). Nothing is moved or changed."""
    ordered = sorted(rules, key=lambda r: (r.priority, r.created_at, str(r.id)))
    hits = [r for r in ordered if all(_cond_matches(c, attrs) for c in r.conditions)]
    return (hits[0] if hits else None), hits


def analyze_rules(rules: Sequence[RuleDef]) -> list[dict[str, Any]]:
    """Static checks over a rule set: duplicate priorities and rules shadowed by an earlier, broader rule."""
    findings: list[dict[str, Any]] = []
    ordered = sorted(rules, key=lambda r: (r.priority, r.created_at, str(r.id)))
    seen: dict[int, RuleDef] = {}
    for r in ordered:
        if r.priority in seen:
            findings.append(
                {
                    "code": "duplicate_priority",
                    "rule_id": str(r.id),
                    "other_rule_id": str(seen[r.priority].id),
                    "message": f"'{r.name}' and '{seen[r.priority].name}' share priority {r.priority}; the older rule wins.",
                }
            )
        seen.setdefault(r.priority, r)
    keyed = [(r, {(c.field, c.operator, repr(c.value)) for c in r.conditions}) for r in ordered]
    for i, (later, lset) in enumerate(keyed):
        for earlier, eset in keyed[:i]:
            if eset <= lset and (earlier.target_node_id, earlier.target_category_id) != (
                later.target_node_id,
                later.target_category_id,
            ):
                findings.append(
                    {
                        "code": "shadowed",
                        "rule_id": str(later.id),
                        "other_rule_id": str(earlier.id),
                        "message": f"'{later.name}' can never win: '{earlier.name}' matches everything it matches and comes first.",
                    }
                )
                break
    return findings


# ---------------------------------------------------------------- workspace access evaluation
@dataclass(frozen=True)
class Grant:
    node_id: uuid.UUID | None
    subject_type: str
    user_id: uuid.UUID | None
    role: str | None
    permission: str
    effect: str


@dataclass(frozen=True)
class AccessSubject:
    user_id: uuid.UUID
    org_role: str | None
    base: frozenset[str]  # workspace.read / workspace.manage / workspace.permissions.manage held through the role
    access_mode: str  # open | restricted


# What a role-level permission key allows when no explicit grant decides.
_BASE_ACTIONS: dict[str, tuple[str, ...]] = {
    "workspace.read": ("view",),
    "workspace.manage": ("upload", "edit", "rename", "move", "archive", "delete", "configure"),
    "workspace.permissions.manage": ("manage_permissions",),
    "documents.contribute": ("upload",),  # Phase 6: add and manage documents in folders that allow it
}
READ_ONLY_CEILING = frozenset({"view"})


def decide(
    subject: AccessSubject, chain: Sequence[uuid.UUID], grants: Iterable[Grant], action: str
) -> tuple[bool, str]:
    """Deterministic workspace access decision.

    chain = the node and its ancestors, nearest first (empty for a company-level action).
    1. Holders of workspace.permissions.manage (owner, administrator) are never locked out: always allowed.
    2. read-only members are capped at `view`.
    3. Grants apply to the caller's user id or organization role. Walk nearest node -> root -> company level; the first
       level holding a grant for this action decides: any deny beats allow on the same level.
    4. No grant: role-level permission applies in `open` mode; in `restricted` mode members without workspace.manage
       see/do nothing without an explicit allow."""
    if action not in NODE_ACTIONS:
        return False, "unknown_action"
    if "workspace.permissions.manage" in subject.base:
        return True, "administrator"
    if subject.org_role == "read_only" and action not in READ_ONLY_CEILING:
        return False, "read_only_ceiling"
    mine = [
        g
        for g in grants
        if g.permission == action
        and (
            (g.subject_type == "user" and g.user_id == subject.user_id)
            or (g.subject_type == "role" and g.role == subject.org_role)
        )
    ]
    for level in [*chain, None]:
        here = [g for g in mine if g.node_id == level]
        if here:
            return (False, "explicit_deny") if any(g.effect == "deny" for g in here) else (True, "explicit_allow")
    role_allows = any(action in _BASE_ACTIONS.get(k, ()) for k in subject.base)
    if subject.access_mode == "restricted" and "workspace.manage" not in subject.base:
        return False, "restricted_no_grant"
    return (True, "role") if role_allows else (False, "role_lacks_permission")
