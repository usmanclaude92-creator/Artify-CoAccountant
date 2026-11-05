"""Deterministic document intelligence: classification, field extraction, bank-statement parsing, validation, splitting.

PURE module (no database, no web framework, no network, no clock). It works on OCR output (`Page`/`Line`/`Word` with
normalised bounding boxes) and returns structured results with, for every value, its confidence, source page, source
box, the text it came from, the method, and flags. Rules:

* a value that is not found is `None` with status "missing": nothing is invented or assumed;
* an ambiguous value (date format, decimal separator, several currencies...) is flagged and starts as "needs_review";
* money is `Decimal` and leaves this module as a string; nothing is ever adjusted to make totals add up — a mismatch
  is reported with its computed difference and the rows that may be missing or misread;
* AI never approves: this module only suggests; approval is a separate human act in the service layer.
"""

# ruff: noqa: RUF001  (Arabic-Indic digits, NBSP and full-width colon are deliberate OCR inputs)
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from artify_api.doc_taxonomy import (
    CLASSIFICATION_THRESHOLD,
    DIRECTIONAL_KIND,
    FIELD_REVIEW_THRESHOLD,
    SCHEMAS,
    TYPES,
    DocType,
    Schema,
)

Box = tuple[float, float, float, float]
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
MONTHS = {
    m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])
}
CURRENCIES = ("OMR", "AED", "SAR", "USD", "EUR", "GBP", "KWD", "BHD", "QAR", "INR", "PKR", "EGP", "JOD")
_SYMBOL_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "ر.ع": "OMR", "ر.ع.": "OMR", "د.إ": "AED", "ر.س": "SAR"}


# ---------------------------------------------------------------- input model
@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    conf: float | None = None


@dataclass(frozen=True)
class Line:
    page: int
    idx: int
    text: str
    bbox: Box
    conf: float | None = None
    words: tuple[Word, ...] = ()


@dataclass(frozen=True)
class Page:
    page_no: int
    lines: tuple[Line, ...]
    blank: bool = False

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class CompanyProfile:
    """What is known about the company that owns the document: used ONLY to decide the direction (sales/purchase)."""

    name: str = ""
    tax_numbers: tuple[str, ...] = ()


# ---------------------------------------------------------------- output model
@dataclass
class Found:
    key: str
    value: str | None
    confidence: float | None
    page: int | None = None
    bbox: Box | None = None
    source_text: str | None = None
    method: str = "rules"
    flags: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.value is None:
            return "missing"
        if (self.confidence or 0) < FIELD_REVIEW_THRESHOLD or self.flags:
            return "needs_review"
        return "extracted"


@dataclass
class LineItem:
    line_no: int
    values: dict[str, str | None]
    page: int | None
    bbox: Box | None
    source_text: str
    confidence: float
    flags: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "needs_review" if self.flags or self.confidence < FIELD_REVIEW_THRESHOLD else "extracted"


@dataclass
class Issue:
    code: str
    severity: str  # error | warning | info
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Classification:
    type_key: str
    kind: str | None
    confidence: float
    method: str
    evidence: list[dict[str, Any]]
    alternatives: list[dict[str, Any]]
    needs_manual: bool
    direction: str | None = None
    variant: str | None = None


@dataclass
class Transaction:
    row_no: int
    page: int | None
    bbox: Box | None
    source_text: str
    tx_date: str | None
    value_date: str | None
    description: str
    reference: str | None
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None
    confidence: float
    flags: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "needs_review" if self.flags or self.confidence < FIELD_REVIEW_THRESHOLD else "extracted"


@dataclass
class Extraction:
    schema: Schema
    fields: list[Found]
    items: list[LineItem]
    issues: list[Issue]
    transactions: list[Transaction] = field(default_factory=list)
    statement: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- primitives
def normalize(s: str) -> str:
    return s.translate(_ARABIC_DIGITS)


def money_str(d: Decimal | None) -> str | None:
    return None if d is None else format(d, "f")


_NUM_RE = re.compile(r"\(?-?\d[\d,.'  ]*\d\)?|\(?-?\d\)?")


def parse_money(raw: str) -> tuple[Decimal | None, list[str]]:
    """Parse one monetary token. Returns (magnitude, flags); a negative amount carries the flag `negative`.

    Separator handling is explicit: with both `,` and `.` the last one is the decimal point; a lone separator followed
    by exactly three digits is ambiguous (thousands vs three-decimal currency) and is flagged, never silently decided."""
    s = normalize(raw).strip()
    flags: list[str] = []
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[()\-\s' ]", "", s)
    if not s or not re.fullmatch(r"[\d.,]+", s):
        return None, ["not_a_number"]
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        if len(parts) > 2:
            s = "".join(parts)  # repeated separator: thousands
        elif sep == "," and len(parts[1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0":
            s = "".join(parts)  # "1,234": thousands separator in most documents, but a 3-decimal currency is possible
            flags.append("ambiguous_separator")
        else:
            s = ".".join(
                parts
            )  # "160.000" / "12,50": a lone dot is the decimal point; a lone comma before 1-2 digits too
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None, ["not_a_number"]
    if neg:
        flags.append("negative")
    return value, flags


def parse_date(raw: str, dayfirst: bool = True) -> tuple[str | None, list[str]]:
    s = normalize(raw).strip()
    flags: list[str] = []
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.search(r"(\d{1,2})[-/. ]+([A-Za-z]{3,9})[a-z]*[-/. ,]+(\d{2,4})", s)
        if m and m[2][:3].lower() in MONTHS:
            d, mo, y = int(m[1]), MONTHS[m[2][:3].lower()], int(m[3])
        else:
            m = re.search(r"([A-Za-z]{3,9})[a-z]*\.? +(\d{1,2}),? +(\d{4})", s)
            if m and m[1][:3].lower() in MONTHS:
                mo, d, y = MONTHS[m[1][:3].lower()], int(m[2]), int(m[3])
            else:
                m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
                if not m:
                    return None, ["no_date"]
                a, b, y = int(m[1]), int(m[2]), int(m[3])
                d, mo = (a, b) if dayfirst else (b, a)
                if a <= 12 and b <= 12 and a != b:
                    flags.append("ambiguous_date_format")
                if a > 12 and b <= 12:
                    d, mo = a, b
                elif b > 12 and a <= 12:
                    d, mo = b, a
    if y < 100:
        y += 2000
        flags.append("two_digit_year")
    try:
        return date(y, mo, d).isoformat(), flags
    except ValueError:
        return None, ["invalid_date"]


def _union(boxes: Iterable[Box]) -> Box:
    bs = list(boxes)
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def locate(line: Line, value: str) -> Box:
    """Box of `value` inside `line`: the words that make it up when known, otherwise the whole line."""
    target = normalize(value).replace(" ", "")
    if not line.words or not target:
        return line.bbox
    words = line.words
    for i in range(len(words)):
        acc = ""
        for j in range(i, len(words)):
            acc += normalize(words[j].text).replace(" ", "")
            if acc == target:
                x0, x1 = words[i].x0, words[j].x1
                return (x0, line.bbox[1], x1, line.bbox[3])
            if len(acc) > len(target) or not target.startswith(acc):
                break
    return line.bbox


def _all_lines(pages: Sequence[Page]) -> list[Line]:
    return [ln for p in pages for ln in p.lines]


def _norm_line(line: Line) -> str:
    return normalize(line.text)


def _confidence(line: Line, base: float) -> float:
    """Combine how certain the RULE is with how well the line was read."""
    if line.conf is None:
        return round(base, 3)
    return round(base * max(0.0, min(1.0, line.conf)), 3)


# ---------------------------------------------------------------- classification
_ARABIC_RE = re.compile(r"[؀-ۿ]")


def classify(
    pages: Sequence[Page], profile: CompanyProfile | None = None, types: Mapping[str, DocType] = TYPES
) -> Classification:
    lines = _all_lines(pages)
    if not lines:
        return Classification("unclassified", None, 0.0, "keyword_rules", [], [], True)
    scores: dict[str, float] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}

    def add(key: str, pts: float, phrase: str, ln: Line) -> None:
        scores[key] = scores.get(key, 0.0) + pts
        evidence.setdefault(key, []).append({"phrase": phrase, "page": ln.page, "line": ln.idx, "text": ln.text[:120]})

    for ln in lines:
        low = ln.text.lower()
        top = ln.idx < 12
        for key, t in types.items():
            if key in ("sales_invoice", "purchase_invoice", "invoice", "unclassified"):
                continue
            for phrase in t.keywords_en + t.keywords_ar:
                if phrase.lower() in low:
                    strong = phrase in t.strong
                    add(key, (4.0 if strong and top else 2.0 if strong else 1.0), phrase, ln)
        if re.search(r"\binvoice\b|فاتورة", low):
            add("invoice", 4.0 if top else 1.0, "invoice", ln)
    n_money_dates = sum(
        1
        for ln in lines
        if re.match(r"\s*\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", _norm_line(ln))
        and len(_NUM_RE.findall(_norm_line(ln))) >= 3
    )
    if n_money_dates >= 3:  # many dated rows with several amounts: a transaction table
        add("bank_statement", min(8.0, n_money_dates * 0.8), f"{n_money_dates} dated rows with amounts", lines[0])
    # a receipt/invoice with explicit tax wording is still an invoice family
    tax_words = [k for k in ("tax_invoice", "simplified_tax_invoice") if k in scores]
    if tax_words:
        scores["invoice"] = scores.get("invoice", 0.0) + sum(scores[k] for k in tax_words) * 0.5
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < 2.0:
        return Classification(
            "unclassified",
            None,
            round(min(0.3, (ranked[0][1] / 10) if ranked else 0.0), 3),
            "keyword_rules",
            [],
            [],
            True,
        )
    family = ("invoice", "tax_invoice", "simplified_tax_invoice")
    outside = [kv for kv in ranked if kv[0] not in family]
    if ranked[0][0] in family and outside and outside[0][1] >= 4.0 and outside[0][1] >= 0.6 * ranked[0][1]:
        top_key, top_score = outside[
            0
        ]  # a specific type (credit note, receipt, statement...) beats the generic invoice signal
        second = max([v for k, v in ranked if k != top_key and k not in family] or [0.0])
    elif ranked[0][0] in family:
        top_key, top_score = "invoice", max(scores.get(k, 0.0) for k in family)
        second = outside[0][1] if outside else 0.0
    else:
        top_key, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
    conf = min(0.97, 0.4 + 0.06 * top_score)
    margin = (top_score - second) / top_score if top_score else 0
    if margin < 0.35:
        conf *= 0.7
    low_read = [ln.conf for ln in lines if ln.conf is not None]
    if low_read and sum(low_read) / len(low_read) < 0.6:
        conf *= 0.85
    direction, dir_evidence = (
        _direction(pages, profile)
        if top_key in ("invoice", "tax_invoice", "simplified_tax_invoice", "credit_note", "debit_note")
        else (None, [])
    )
    variant = None
    type_key = top_key
    if top_key in ("invoice", "tax_invoice", "simplified_tax_invoice"):
        variant = (
            "simplified_tax_invoice"
            if "simplified_tax_invoice" in scores
            else ("tax_invoice" if "tax_invoice" in scores else None)
        )
        if direction in ("sales", "purchase"):
            type_key = f"{direction}_invoice"
        elif top_key == "invoice":
            type_key = "invoice"
    kind = types[type_key].kind if type_key in types else None
    if kind is None and direction is not None and (type_key, direction) in DIRECTIONAL_KIND:
        kind = DIRECTIONAL_KIND[(type_key, direction)]
    if type_key in ("sales_invoice", "purchase_invoice"):
        kind = type_key
    needs_manual = conf < CLASSIFICATION_THRESHOLD
    needs_direction = (
        type_key in ("invoice", "credit_note", "debit_note", "tax_invoice", "simplified_tax_invoice") and kind is None
    )
    ev = (evidence.get(top_key) or [])[:6] + dir_evidence
    if needs_direction:
        ev.append(
            {
                "phrase": "direction undetermined",
                "note": "could not tell whether the company is the supplier or the customer",
            }
        )
    alts = [{"type_key": k, "score": round(v, 2)} for k, v in ranked[1:4]]
    return Classification(
        type_key, kind, round(conf, 3), "keyword_rules", ev, alts, needs_manual or needs_direction, direction, variant
    )


_CUSTOMER_MARKERS = re.compile(
    r"\b(bill(?:ed)? to|sold to|invoice to|customer|ship to|client|buyer)\b|العميل|المطلوب من", re.I
)
_SUPPLIER_MARKERS = re.compile(r"\b(supplier|vendor|seller|from|issued by)\b|المورد|البائع", re.I)


def _canon(s: str) -> str:
    return re.sub(r"[^\w]+", "", s.lower())


def _direction(pages: Sequence[Page], profile: CompanyProfile | None) -> tuple[str | None, list[dict[str, Any]]]:
    """Is the company the SUPPLIER (sales document) or the CUSTOMER (purchase document)? Decided only from where the
    company's own name / tax number appears; otherwise undetermined."""
    if profile is None or not (profile.name or profile.tax_numbers):
        return None, []
    lines = _all_lines(pages)
    name = _canon(profile.name)
    taxes = [_canon(t) for t in profile.tax_numbers if t]
    cust_idx = [ln.idx for ln in lines if _CUSTOMER_MARKERS.search(ln.text)]
    where: list[Line] = []
    for ln in lines:
        c = _canon(ln.text)
        if (name and len(name) >= 4 and name in c) or any(t and t in c for t in taxes):
            where.append(ln)
    if not where:
        return None, []
    ev = [{"phrase": "company found on page", "page": w.page, "line": w.idx, "text": w.text[:100]} for w in where[:2]]
    first = where[0]
    after_customer = any(0 <= first.idx - c <= 5 for c in cust_idx)
    if after_customer:
        return "purchase", ev
    sup_marker = any(
        0 <= first.idx - ln.idx <= 4 for ln in lines if _SUPPLIER_MARKERS.search(ln.text) and ln.idx != first.idx
    )
    if first.idx < 10 or sup_marker:
        return "sales", ev
    return None, ev


# ---------------------------------------------------------------- label/value search
@dataclass(frozen=True)
class Hit:
    value: str
    line: Line
    method: str


def find_label(pages: Sequence[Page], labels: Sequence[str], below: bool = True, first_only: bool = True) -> list[Hit]:
    hits: list[Hit] = []
    for p in pages:
        for i, ln in enumerate(p.lines):
            text = normalize(ln.text)
            for lab in labels:
                m = re.search(rf"(?i)(?<![A-Za-z]){lab}\s*[:：#.\-]*\s*(?P<v>.*)$", text)
                if not m:
                    continue
                v = m.group("v").strip()
                if v:
                    hits.append(Hit(v, ln, "label_same_line"))
                elif below and i + 1 < len(p.lines):
                    nxt = p.lines[i + 1]
                    hits.append(Hit(normalize(nxt.text).strip(), nxt, "label_next_line"))
                break
            if hits and first_only:
                return hits
    return hits


def _first(hits: list[Hit]) -> Hit | None:
    return hits[0] if hits else None


def _text_field(key: str, hit: Hit | None, base: float = 0.9, take_first_token: bool = False) -> Found:
    if hit is None:
        return Found(key, None, None)
    v = hit.value
    if take_first_token:
        v = re.split(r"\s{2,}|\s(?=(?:date|due|po|vat|tax|ref)\b)", v, maxsplit=1, flags=re.I)[0].strip()
    v = v.strip(" :;,")
    if not v:
        return Found(key, None, None)
    return Found(
        key, v, _confidence(hit.line, base if hit.method == "label_same_line" else base - 0.1), hit.line.page,
        locate(hit.line, v), hit.line.text, hit.method,
    )  # fmt: skip


def _date_field(key: str, hit: Hit | None) -> Found:
    if hit is None:
        return Found(key, None, None)
    iso, flags = parse_date(hit.value)
    if iso is None:
        return Found(key, None, 0.2, hit.line.page, hit.line.bbox, hit.line.text, hit.method, ["unreadable_date"])
    base = 0.92 if hit.method == "label_same_line" else 0.8
    return Found(
        key,
        iso,
        _confidence(hit.line, base - (0.15 if flags else 0)),
        hit.line.page,
        hit.line.bbox,
        hit.line.text,
        hit.method,
        flags,
    )


def _money_in(text: str) -> list[tuple[Decimal, list[str], str]]:
    out = []
    for tok in _NUM_RE.findall(normalize(text)):
        v, fl = parse_money(tok)
        if v is not None:
            out.append((v, fl, tok.strip()))
    return out


def _amount_field(
    key: str, labels: Sequence[str], pages: Sequence[Page], pick: str = "last", exclude: re.Pattern[str] | None = None
) -> Found:
    best: Found | None = None
    for p in pages:
        for ln in p.lines:
            text = normalize(ln.text)
            if exclude and exclude.search(text):
                continue
            for lab in labels:
                m = re.search(rf"(?i)(?<![A-Za-z]){lab}(?![A-Za-z])(?P<rest>.*)$", text)
                if not m:
                    continue
                nums = [
                    n
                    for n in _money_in(m.group("rest"))
                    if "%"
                    not in m.group("rest")[m.group("rest").find(n[2]) : m.group("rest").find(n[2]) + len(n[2]) + 2]
                ]
                if not nums:
                    continue
                v, fl, tok = nums[-1] if pick == "last" else nums[0]
                f = Found(
                    key,
                    money_str(v),
                    _confidence(ln, 0.9 - (0.15 if fl else 0)),
                    ln.page,
                    locate(ln, tok),
                    ln.text,
                    "label_amount",
                    [x for x in fl if x != "negative"],
                )
                if v < 0 or "negative" in fl:
                    f.value = money_str(-v)
                best = f  # later totals (e.g. "Grand total") override earlier subtotals for the same key
                break
    return best or Found(key, None, None)


def _currency(pages: Sequence[Page]) -> Found:
    seen: dict[str, Line] = {}
    for ln in _all_lines(pages):
        up = normalize(ln.text).upper()
        for c in CURRENCIES:
            if re.search(rf"(?<![A-Z]){c}(?![A-Z])", up):
                seen.setdefault(c, ln)
        for sym, c in _SYMBOL_CURRENCY.items():
            if sym in ln.text:
                seen.setdefault(c, ln)
    if not seen:
        return Found("currency", None, None)
    if len(seen) > 1:
        c, ln = next(iter(seen.items()))
        return Found(
            "currency", c, 0.5, ln.page, ln.bbox, ln.text, "currency_scan", ["multiple_currencies:" + ",".join(seen)]
        )
    c, ln = next(iter(seen.items()))
    return Found("currency", c, _confidence(ln, 0.9), ln.page, locate(ln, c), ln.text, "currency_scan")


_ID_VALUE = r"[A-Za-z0-9][A-Za-z0-9\-/]{1,}"


def _id_field(key: str, pages: Sequence[Page], labels: Sequence[str], base: float = 0.9) -> Found:
    for p in pages:
        for ln in p.lines:
            text = normalize(ln.text)
            for lab in labels:
                m = re.search(
                    rf"(?i)(?<![A-Za-z]){lab}\s*(?:no\.?|number|num|#|:|ref\.?|id)*\s*[:#\-]*\s*(?P<v>{_ID_VALUE})",
                    text,
                )
                if m and re.search(r"\d", m.group("v")):
                    return Found(
                        key, m.group("v"), _confidence(ln, base), ln.page, locate(ln, m.group("v")), ln.text, "label_id"
                    )
    return Found(key, None, None)


# ---------------------------------------------------------------- invoices / receipts / notes
_COL_NAMES = {
    "description": r"description|item|particulars|details|product|service|البيان|الوصف",
    "quantity": r"qty|quantity|units|الكمية",
    "unit_price": r"unit price|price|rate|unit cost|سعر",
    "discount": r"discount|disc",
    "vat_amount": r"vat|tax",
    "amount": r"amount|total|line total|net|value|المبلغ|الإجمالي",
}


def _line_items(pages: Sequence[Page]) -> tuple[list[LineItem], list[Issue]]:
    items: list[LineItem] = []
    issues: list[Issue] = []
    n = 0  # one running line number for the whole document (tables may continue over pages)
    for p in pages:
        lines = list(p.lines)
        for hi, ln in enumerate(lines):
            low = normalize(ln.text).lower()
            hits = [c for c, pat in _COL_NAMES.items() if re.search(pat, low)]
            if "description" in hits and len(hits) >= 3:
                cols = _header_columns(ln)
                for row in lines[hi + 1 :]:
                    t = normalize(row.text)
                    if re.match(r"(?i)\s*(sub\s*-?total|total|vat|tax|net\b|grand|amount due|balance)", t):
                        break
                    nums = _money_in(t)
                    if not nums:
                        continue
                    n += 1
                    items.append(_parse_row(row, cols, n, nums))
                break
    # tolerance: qty * unit - discount == amount
    for it in items:
        q, u, a = (_dec(it.values.get(k)) for k in ("quantity", "unit_price", "amount"))
        d = _dec(it.values.get("discount")) or Decimal("0")
        if q is not None and u is not None and a is not None:
            exp = q * u - d
            if abs(exp - a) > Decimal("0.011"):
                it.flags.append("line_math_mismatch")
                issues.append(
                    Issue(
                        "line_math_mismatch",
                        "warning",
                        f"Line {it.line_no}: quantity x unit price - discount = {format(exp.quantize(Decimal('0.001')), 'f')}, the line says {format(a, 'f')}.",
                        {"line": it.line_no, "expected": money_str(exp), "found": money_str(a)},
                    )
                )
    return items, issues


def _header_columns(ln: Line) -> list[tuple[str, float]]:
    cols: list[tuple[str, float]] = []
    for w in ln.words:
        low = normalize(w.text).lower()
        for c, pat in _COL_NAMES.items():
            if re.fullmatch(pat, low) or re.fullmatch(rf"(?:{pat})s?", low):
                cols.append((c, (w.x0 + w.x1) / 2))
                break
    return cols


def _is_amount_word(t: str) -> bool:
    return bool(re.fullmatch(r"\(?-?\d[\d,.]*\)?", normalize(t).strip()))


def _dec(v: str | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _parse_row(
    row: Line, cols: list[tuple[str, float]], n: int, nums: list[tuple[Decimal, list[str], str]]
) -> LineItem:
    flags: list[str] = []
    values: dict[str, str | None] = dict.fromkeys(
        ("description", "quantity", "unit_price", "discount", "vat_amount", "amount")
    )
    numeric_words = [w for w in row.words if _is_amount_word(w.text)]
    numeric_cols = [c for c in cols if c[0] != "description"]
    if numeric_words and numeric_cols:
        for w in numeric_words:
            cx = (w.x0 + w.x1) / 2
            col = min(numeric_cols, key=lambda c: abs(c[1] - cx))
            v, fl = parse_money(w.text)
            if v is not None and values[col[0]] is None:
                values[col[0]] = money_str(v)
                flags += [f for f in fl if f != "negative"]
    else:
        # no column geometry: the LAST number is the line amount; with 3+ numbers read them as qty, unit price, amount
        vals = [(money_str(v), fl) for v, fl, _ in nums]
        if len(vals) >= 3:
            values["quantity"], values["unit_price"], values["amount"] = vals[-3][0], vals[-2][0], vals[-1][0]
        elif len(vals) == 2:
            values["unit_price"], values["amount"] = vals[-2][0], vals[-1][0]
            flags.append("column_guess")
        else:
            values["amount"] = vals[-1][0]
            flags.append("column_guess")
        for _, fl in vals:
            flags += [f for f in fl if f not in ("negative",) and f not in flags]
    if row.words:
        ws = list(row.words)
        while ws and _is_amount_word(
            ws[-1].text
        ):  # figures sit at the end of the row; the description is everything before them
            ws.pop()
        desc = " ".join(w.text for w in ws).strip(" -:|")
    else:
        desc = re.sub(r"[\s\d.,()-]+$", "", normalize(row.text)).strip(" -:|")
    values["description"] = desc or row.text
    return LineItem(n, values, row.page, row.bbox, row.text, _confidence(row, 0.82), sorted(set(flags)))


def _description(items: list[LineItem], pages: Sequence[Page]) -> Found:
    hit = find_label(pages, ["description", "details", "bill for", "for"])
    if hit:
        return _text_field("description", hit[0], 0.8)
    if items and items[0].values.get("description"):
        it = items[0]
        return Found(
            "description",
            it.values["description"],
            0.6,
            it.page,
            it.bbox,
            it.source_text,
            "first_line_item",
            ["derived_from_first_line"],
        )
    return Found("description", None, None)


def _party_blocks(pages: Sequence[Page]) -> dict[str, Found]:
    """Supplier = the document header (first meaningful line); customer = text after a 'bill to' style marker."""
    out: dict[str, Found] = {}
    lines = _all_lines(pages)
    skip = re.compile(
        r"(?i)invoice|receipt|tax|statement|credit note|debit note|page \d|date|total|vat|tel|phone|fax|www\.|@|^\W*$|فاتورة"
    )
    for ln in lines[:10]:
        t = ln.text.strip()
        if len(t) >= 3 and not skip.search(t) and not re.fullmatch(r"[\d\W]+", t):
            out["supplier_name"] = Found(
                "supplier_name",
                t,
                _confidence(ln, 0.55),
                ln.page,
                ln.bbox,
                ln.text,
                "header_heuristic",
                ["heuristic_party"],
            )
            sup_addr = []
            for nxt in lines[ln.idx + 1 : ln.idx + 3]:
                if nxt.text.strip() and not skip.search(nxt.text) and not re.search(r"\d{7,}", nxt.text):
                    sup_addr.append(nxt)
            if sup_addr:
                out["supplier_address"] = Found(
                    "supplier_address",
                    ", ".join(x.text.strip() for x in sup_addr),
                    _confidence(sup_addr[0], 0.5),
                    sup_addr[0].page,
                    _union(x.bbox for x in sup_addr),
                    " / ".join(x.text for x in sup_addr),
                    "header_heuristic",
                    ["heuristic_party"],
                )
            break
    for i, ln in enumerate(lines):
        m = _CUSTOMER_MARKERS.search(ln.text)
        if not m:
            continue
        rest = ln.text[m.end() :].strip(" :-")
        cand = [ln] if rest else []
        val = rest
        if not rest:
            for nxt in lines[i + 1 : i + 4]:
                if nxt.text.strip() and not _CUSTOMER_MARKERS.search(nxt.text):
                    val = nxt.text.strip()
                    cand = [nxt]
                    follow = [
                        x
                        for x in lines[nxt.idx + 1 : nxt.idx + 3]
                        if x.text.strip() and not re.search(r"(?i)vat|tax|trn|date|invoice|no\.", x.text)
                    ]
                    if follow:
                        out["customer_address"] = Found(
                            "customer_address",
                            ", ".join(x.text.strip() for x in follow),
                            _confidence(follow[0], 0.5),
                            follow[0].page,
                            _union(x.bbox for x in follow),
                            " / ".join(x.text for x in follow),
                            "marker_block",
                            ["heuristic_party"],
                        )
                    break
        if val and cand:
            out["customer_name"] = Found(
                "customer_name",
                val,
                _confidence(cand[0], 0.7),
                cand[0].page,
                cand[0].bbox,
                cand[0].text,
                "marker_block",
                [],
            )
            break
    return out


def _tax_numbers(pages: Sequence[Page]) -> list[Found]:
    found: list[Found] = []
    for ln in _all_lines(pages):
        m = re.search(
            r"(?i)(?:vat|trn|tax)\s*(?:reg(?:istration)?\.?)?\s*(?:no\.?|number|num|id|#)?\s*[:#\-]?\s*(?P<v>[A-Z]{0,3}\d[\dA-Z\-]{7,})",
            normalize(ln.text),
        )
        if m:
            found.append(
                Found(
                    "tax_number",
                    m.group("v"),
                    _confidence(ln, 0.88),
                    ln.page,
                    locate(ln, m.group("v")),
                    ln.text,
                    "label_id",
                )
            )
    return found


def extract_invoice(pages: Sequence[Page], customer_first_tax: bool = False) -> Extraction:
    schema = SCHEMAS["invoice"]
    flds: dict[str, Found] = {}
    parties = _party_blocks(pages)
    flds.update(parties)
    taxes = _tax_numbers(pages)
    cust_line = flds["customer_name"].source_text if "customer_name" in flds else None
    for t in taxes:
        is_cust = (
            bool(cust_line)
            and t.page is not None
            and "customer_name" in flds
            and flds["customer_name"].page == t.page
            and t.bbox
            and flds["customer_name"].bbox
            and 0 <= t.bbox[1] - flds["customer_name"].bbox[1] < 0.15
        )
        key = "customer_tax_number" if is_cust else "supplier_tax_number"
        if key not in flds:
            flds[key] = Found(key, t.value, t.confidence, t.page, t.bbox, t.source_text, t.method)
    flds["invoice_number"] = _id_field(
        "invoice_number", pages, ["invoice", "inv", "tax invoice", "bill", "رقم الفاتورة"]
    )
    flds["invoice_date"] = _date_field(
        "invoice_date",
        _first(find_label(pages, ["invoice date", "date of issue", "issue date", "date", "تاريخ الفاتورة", "التاريخ"])),
    )
    flds["due_date"] = _date_field(
        "due_date", _first(find_label(pages, ["due date", "payment due", "pay by", "تاريخ الاستحقاق"]))
    )
    flds["po_reference"] = _id_field("po_reference", pages, ["p\\.?o\\.?", "purchase order", "order ref"], 0.85)
    flds["currency"] = _currency(pages)
    items, issues = _line_items(pages)
    flds["description"] = _description(items, pages)
    net = _amount_field(
        "net_amount",
        [
            "sub\\s*-?total",
            "net amount",
            "total excl\\w*(?: vat| tax)?",
            "amount before (?:vat|tax)",
            "المجموع الفرعي",
            "الصافي",
        ],
        pages,
    )
    vat = _amount_field(
        "vat_amount",
        ["vat(?: amount)?", "tax(?: amount)?", "ضريبة القيمة المضافة"],
        pages,
        exclude=re.compile(r"(?i)(vat|tax)\s*(no|number|reg|id|trn)|trn|invoice|^.*total\s*(incl|excl)"),
    )
    gross = _amount_field(
        "gross_amount",
        [
            "grand total",
            "total incl\\w*(?: vat| tax)?",
            "amount due",
            "balance due",
            "total amount",
            "total",
            "الإجمالي",
            "المجموع",
        ],
        pages,
        exclude=re.compile(r"(?i)sub\s*-?total|excl|before"),
    )
    flds.update({"net_amount": net, "vat_amount": vat, "gross_amount": gross})
    rate = Found("vat_rate", None, None)
    for ln in _all_lines(pages):
        m = re.search(r"(?i)(?:vat|tax|ضريبة)[^%\d]{0,20}(\d{1,2}(?:[.,]\d+)?)\s*%", normalize(ln.text)) or re.search(
            r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:vat|tax)", normalize(ln.text), re.I
        )
        if m:
            rate = Found(
                "vat_rate",
                m.group(1).replace(",", "."),
                _confidence(ln, 0.9),
                ln.page,
                locate(ln, m.group(1)),
                ln.text,
                "label_percent",
            )
            break
    flds["vat_rate"] = rate
    flds["payment_terms"] = _text_field(
        "payment_terms", _first(find_label(pages, ["payment terms", "terms of payment", "terms"])), 0.85
    )
    pay = [
        ln for ln in _all_lines(pages) if re.search(r"(?i)\biban\b|account (?:no|number)|bank\s*:|swift|bic\b", ln.text)
    ]
    if pay:
        flds["payment_details"] = Found(
            "payment_details",
            "; ".join(x.text.strip() for x in pay[:3]),
            0.75,
            pay[0].page,
            _union(x.bbox for x in pay[:3]),
            " / ".join(x.text for x in pay[:3]),
            "label_scan",
        )
    issues += validate_totals(flds, items)
    return _finish(schema, flds, items, issues)


def validate_totals(
    flds: Mapping[str, Found],
    items: Sequence[LineItem],
    net_key: str = "net_amount",
    vat_key: str = "vat_amount",
    gross_key: str = "gross_amount",
) -> list[Issue]:
    issues: list[Issue] = []
    net, vat, gross = (_dec(flds[k].value) if k in flds else None for k in (net_key, vat_key, gross_key))
    tol = Decimal("0.011")
    if net is not None and vat is not None and gross is not None and abs(net + vat - gross) > tol:
        issues.append(
            Issue(
                "totals_mismatch",
                "error",
                f"Net {format(net, 'f')} + VAT {format(vat, 'f')} = {format(net + vat, 'f')} but the gross amount is {format(gross, 'f')}.",
                {
                    "expected_gross": money_str(net + vat),
                    "found_gross": money_str(gross),
                    "difference": money_str(gross - (net + vat)),
                },
            )
        )
        for k in (net_key, vat_key, gross_key):
            flds[k].flags.append("totals_mismatch")
    rate = _dec(flds["vat_rate"].value) if "vat_rate" in flds else None
    if net is not None and vat is not None and rate is not None:
        exp = net * rate / Decimal(100)
        if abs(exp - vat) > Decimal("0.05"):
            issues.append(
                Issue(
                    "vat_rate_mismatch",
                    "warning",
                    f"{format(rate, 'f')}% of {format(net, 'f')} is {format(exp.quantize(Decimal('0.001'), ROUND_HALF_UP), 'f')} but the VAT is {format(vat, 'f')}.",
                    {"expected": money_str(exp), "found": money_str(vat)},
                )
            )
            flds["vat_amount"].flags.append("vat_rate_mismatch")
    amounts = [_dec(i.values.get("amount")) for i in items]
    if items and all(a is not None for a in amounts) and net is not None:
        s = sum((a for a in amounts if a is not None), Decimal(0))
        if abs(s - net) > tol and abs(s - (gross or net)) > tol:
            issues.append(
                Issue(
                    "lines_total_mismatch",
                    "warning",
                    f"The line amounts add up to {format(s, 'f')} but the net amount is {format(net, 'f')}.",
                    {"lines_total": money_str(s), "net": money_str(net)},
                )
            )
    return issues


def extract_receipt(pages: Sequence[Page]) -> Extraction:
    schema = SCHEMAS["receipt"]
    lines = _all_lines(pages)
    f: dict[str, Found] = {}
    skip = re.compile(r"(?i)receipt|tax|invoice|tel|phone|www|@|date|^\W*$|\d{5,}")
    for ln in lines[:6]:
        if len(ln.text.strip()) >= 3 and not skip.search(ln.text):
            f["merchant_name"] = Found(
                "merchant_name",
                ln.text.strip(),
                _confidence(ln, 0.6),
                ln.page,
                ln.bbox,
                ln.text,
                "header_heuristic",
                ["heuristic_party"],
            )
            break
    f["receipt_number"] = _id_field(
        "receipt_number", pages, ["receipt", "rcpt", "ticket", "transaction", "txn", "bill"]
    )
    d = find_label(pages, ["date", "transaction date", "التاريخ"]) or []
    f["transaction_date"] = _date_field("transaction_date", d[0] if d else None)
    if f["transaction_date"].value is None:  # a date without a label
        for ln in lines:
            iso, fl = parse_date(ln.text)
            if iso:
                f["transaction_date"] = Found(
                    "transaction_date",
                    iso,
                    _confidence(ln, 0.7 - (0.15 if fl else 0)),
                    ln.page,
                    ln.bbox,
                    ln.text,
                    "date_scan",
                    fl,
                )
                break
    for ln in lines:
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*(am|pm)?\b", normalize(ln.text), re.I)
        if m:
            f["transaction_time"] = Found(
                "transaction_time",
                m.group(0).strip(),
                _confidence(ln, 0.85),
                ln.page,
                locate(ln, m.group(0).strip()),
                ln.text,
                "time_scan",
            )
            break
    f["currency"] = _currency(pages)
    f["net_amount"] = _amount_field("net_amount", ["sub\\s*-?total", "net(?: amount)?", "before (?:vat|tax)"], pages)
    f["vat_amount"] = _amount_field(
        "vat_amount", ["vat(?: amount)?", "tax(?: amount)?"], pages, exclude=re.compile(r"(?i)trn|no\.|number|reg")
    )
    f["total_amount"] = _amount_field(
        "total_amount",
        ["grand total", "total due", "amount paid", "total amount", "total", "الإجمالي"],
        pages,
        exclude=re.compile(r"(?i)sub\s*-?total|excl|before"),
    )
    for ln in lines:
        m = re.search(
            r"(?i)\b(cash|visa|mastercard|master card|mada|amex|knet|card|credit card|debit card|apple pay|bank transfer|cheque|check)\b",
            ln.text,
        )
        if m:
            f["payment_method"] = Found(
                "payment_method",
                m.group(1).title(),
                _confidence(ln, 0.85),
                ln.page,
                locate(ln, m.group(1)),
                ln.text,
                "keyword",
            )
            break
    f["reference_number"] = _id_field("reference_number", pages, ["ref", "reference", "auth", "approval", "rrn"])
    issues = validate_totals(f, [], gross_key="total_amount")
    return _finish(schema, f, [], issues)


def extract_adjustment_note(pages: Sequence[Page]) -> Extraction:
    schema = SCHEMAS["adjustment_note"]
    f: dict[str, Found] = {}
    f["document_number"] = _id_field(
        "document_number", pages, ["credit note", "debit note", "credit memo", "debit memo", "note", "إشعار"]
    )
    f["original_invoice_reference"] = _id_field(
        "original_invoice_reference",
        pages,
        ["original invoice", "against invoice", "invoice ref\\w*", "ref invoice", "relating to invoice", "invoice"],
        0.85,
    )
    parties = _party_blocks(pages)
    if "supplier_name" in parties:
        f["supplier_name"] = parties["supplier_name"]
    if "customer_name" in parties:
        f["customer_name"] = parties["customer_name"]
    f["issue_date"] = _date_field(
        "issue_date", _first(find_label(pages, ["issue date", "date of issue", "date", "التاريخ"]))
    )
    f["currency"] = _currency(pages)
    f["reason"] = _text_field(
        "reason", _first(find_label(pages, ["reason for (?:credit|debit|adjustment)", "reason", "سبب"])), 0.85
    )
    f["net_adjustment"] = _amount_field(
        "net_adjustment", ["sub\\s*-?total", "net(?: amount)?", "net adjustment", "amount before (?:vat|tax)"], pages
    )
    f["vat_adjustment"] = _amount_field(
        "vat_adjustment", ["vat(?: amount)?", "tax(?: amount)?"], pages, exclude=re.compile(r"(?i)trn|no\.|number|reg")
    )
    f["gross_adjustment"] = _amount_field(
        "gross_adjustment",
        ["grand total", "total credit", "total debit", "total amount", "total", "gross"],
        pages,
        exclude=re.compile(r"(?i)sub\s*-?total|excl|before"),
    )
    issues = validate_totals(f, [], "net_adjustment", "vat_adjustment", "gross_adjustment")
    return _finish(schema, f, [], issues)


def _finish(schema: Schema, flds: dict[str, Found], items: list[LineItem], issues: list[Issue]) -> Extraction:
    ordered = [flds.get(fd.key) or Found(fd.key, None, None) for fd in schema.fields]
    for fd, fnd in zip(schema.fields, ordered, strict=True):
        if fnd.value is None and fd.required:
            issues.append(Issue("required_missing", "warning", f"{fd.label} was not found.", {"field": fd.key}))
    return Extraction(schema, ordered, items, issues)


# ---------------------------------------------------------------- bank statements
_DATE_AT_START = re.compile(
    r"^\s*(?P<d>\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[ \-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ \-,]*\d{2,4})",
    re.I,
)
_PAGE_NOISE = re.compile(r"(?i)^\s*(page\s*\d+(\s*(of|/)\s*\d+)?|continued|statement of account|printed on|الصفحة)\b")
_BANK_HEADER_ROLES = {
    "date": r"date|التاريخ",
    "value_date": r"value|val\.?|تاريخ القيمة",
    "description": r"description|details|narration|particulars|transaction|البيان",
    "reference": r"ref(?:erence)?\.?|chq|cheque",
    "debit": r"debits?|withdrawals?|dr\.?|paid out|مدين|سحب",
    "credit": r"credits?|deposits?|cr\.?|paid in|دائن|إيداع",
    "amount": r"amount|المبلغ",
    "balance": r"balance|الرصيد",
}


def _bank_columns(ln: Line) -> dict[str, tuple[float, float]] | None:
    cols: dict[str, tuple[float, float]] = {}
    for w in ln.words:
        low = normalize(w.text).lower().strip(":.")
        for role, pat in _BANK_HEADER_ROLES.items():
            if re.fullmatch(f"(?:{pat})", low) and role not in cols:
                cols[role] = (w.x0, w.x1)
                break
    if "balance" in cols and ("debit" in cols or "credit" in cols or "amount" in cols) and "date" in cols:
        return cols
    return None


def _num_word(w: Word) -> bool:
    t = normalize(w.text)
    return (
        bool(re.fullmatch(r"\(?-?\d[\d,.]*\d?\)?-?(?:CR|DR|Cr|Dr)?", t))
        and bool(re.search(r"\d", t))
        and not _DATE_AT_START.match(t)
    )


def parse_bank_statement(pages: Sequence[Page]) -> Extraction:
    schema = SCHEMAS["bank_statement"]
    lines = _all_lines(pages)
    f: dict[str, Found] = {}
    bank = next(
        (
            ln
            for ln in lines[:12]
            if re.search(r"(?i)\bbank\b|مصرف|بنك", ln.text) and not re.search(r"(?i)bank statement|statement", ln.text)
        ),
        None,
    ) or next((ln for ln in lines[:12] if re.search(r"(?i)\bbank\b|مصرف|بنك", ln.text)), None)
    if bank:
        f["bank_name"] = Found(
            "bank_name", bank.text.strip(), _confidence(bank, 0.75), bank.page, bank.bbox, bank.text, "header_scan"
        )
    holder = find_label(pages, ["account holder", "customer name", "client name", "name of account", "اسم العميل"])
    if holder:
        f["account_holder"] = _text_field("account_holder", holder[0], 0.85)
    aname = find_label(pages, ["account name", "اسم الحساب"])
    if aname:
        f["account_name"] = _text_field("account_name", aname[0], 0.85)
    for ln in lines:
        t = normalize(ln.text)
        m = re.search(r"(?i)\biban\b\s*[:#]?\s*(?P<v>[A-Z]{2}\d{2}[A-Z0-9 ]{10,34})", t)
        if m and "iban" not in f:
            raw = m.group("v").replace(" ", "")
            f["iban"] = Found(
                "iban",
                _mask(raw),
                _confidence(ln, 0.9),
                ln.page,
                locate(ln, m.group("v").strip()),
                re.sub(r"[A-Z0-9]{6,}", lambda x: _mask(x.group(0)), ln.text),
                "label_id",
                ["masked"],
            )
        m = re.search(r"(?i)(?:account|a/c)\s*(?:no\.?|number|num|#)\s*[:#]?\s*(?P<v>[\d\- ]{6,24}\d)", t)
        if m and "account_number" not in f:
            raw = re.sub(r"\D", "", m.group("v"))
            f["account_number"] = Found(
                "account_number",
                _mask(raw),
                _confidence(ln, 0.9),
                ln.page,
                locate(ln, m.group("v").strip()),
                re.sub(r"\d{5,}", lambda x: _mask(x.group(0)), ln.text),
                "label_id",
                ["masked"],
            )
    f["currency"] = _currency(pages)
    # period
    for ln in lines:
        t = normalize(ln.text)
        if re.search(r"(?i)period|from|statement date|للفترة", t):
            parsed = [
                parse_date(m.group(0))
                for m in re.finditer(
                    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2} [A-Za-z]{3,9} \d{4}", t
                )
            ]
            ds = [d for d in parsed if d[0]]
            if len(ds) >= 2:
                fl = sorted({x for d in ds[:2] for x in d[1]})
                f["period_start"] = Found(
                    "period_start",
                    ds[0][0],
                    _confidence(ln, 0.88 - (0.15 if fl else 0)),
                    ln.page,
                    ln.bbox,
                    ln.text,
                    "label_range",
                    fl,
                )
                f["period_end"] = Found(
                    "period_end",
                    ds[1][0],
                    _confidence(ln, 0.88 - (0.15 if fl else 0)),
                    ln.page,
                    ln.bbox,
                    ln.text,
                    "label_range",
                    fl,
                )
                break
    opening = _amount_field(
        "opening_balance",
        [
            "opening balance",
            "balance brought forward",
            "previous balance",
            "balance b/f",
            "brought forward",
            "الرصيد الافتتاحي",
        ],
        pages,
    )
    closing = _amount_field(
        "closing_balance",
        [
            "closing balance",
            "balance carried forward",
            "ending balance",
            "balance c/f",
            "carried forward",
            "الرصيد الختامي",
        ],
        pages,
    )
    f["opening_balance"], f["closing_balance"] = opening, closing
    txs, tissues = _bank_rows(pages)
    issues = tissues
    if txs:
        if "period_start" not in f:
            dates = [t.tx_date for t in txs if t.tx_date]
            if dates:
                f["period_start"] = Found(
                    "period_start",
                    min(dates),
                    0.5,
                    txs[0].page,
                    txs[0].bbox,
                    txs[0].source_text,
                    "derived_from_rows",
                    ["derived_from_transactions"],
                )
                f["period_end"] = Found(
                    "period_end",
                    max(dates),
                    0.5,
                    txs[-1].page,
                    txs[-1].bbox,
                    txs[-1].source_text,
                    "derived_from_rows",
                    ["derived_from_transactions"],
                )
        issues += validate_statement(f, txs)
    else:
        issues.append(Issue("no_transactions", "error", "No transaction rows could be read from this statement.", {}))
    ex = _finish(schema, f, [], issues)
    ex.transactions = txs
    ex.statement = {"row_count": len(txs)}
    return ex


def _mask(raw: str) -> str:
    return ("*" * max(0, len(raw) - 4)) + raw[-4:]


def _bank_rows(pages: Sequence[Page]) -> tuple[list[Transaction], list[Issue]]:
    txs: list[Transaction] = []
    issues: list[Issue] = []
    cols: dict[str, tuple[float, float]] | None = None
    prev_balance: Decimal | None = None
    for p in pages:
        for ln in p.lines:
            detected = _bank_columns(ln)
            if detected:
                cols = detected
                continue
            text = normalize(ln.text)
            if _PAGE_NOISE.match(text):
                continue
            m = _DATE_AT_START.match(text)
            nums = [w for w in ln.words if _num_word(w)]
            if cols:  # figures left of the amount columns belong to the description (e.g. "POS ref 99812")
                left = min(a for r, (a, _b) in cols.items() if r in ("debit", "credit", "amount", "balance")) - 0.02
                nums = [w for w in nums if w.x1 >= left]
            if not m:
                # continuation of the previous description (wrapped narrative text, no figures)
                if (
                    txs
                    and not nums
                    and ln.text.strip()
                    and not re.search(r"(?i)opening|closing|total|balance|brought|carried", ln.text)
                ):
                    txs[-1].description = (txs[-1].description + " " + ln.text.strip()).strip()
                    txs[-1].source_text += " / " + ln.text
                continue
            if (
                re.search(r"(?i)opening balance|brought forward|closing balance|carried forward", text)
                and len(nums) <= 1
            ):
                continue
            tx = _bank_row(ln, text, m.group("d"), nums, cols, prev_balance, len(txs) + 1)
            txs.append(tx)
            if tx.balance is not None:
                prev_balance = tx.balance
    return txs, issues


def _bank_row(
    ln: Line,
    text: str,
    date_raw: str,
    nums: list[Word],
    cols: dict[str, tuple[float, float]] | None,
    prev_balance: Decimal | None,
    row_no: int,
) -> Transaction:
    flags: list[str] = []
    _pending: list[Decimal | None] = [None]
    tx_date, dfl = parse_date(date_raw)
    flags += dfl
    if tx_date is None:
        flags.append("unreadable_date")
    rest = text[len(date_raw) :].strip() if text.startswith(date_raw) else text
    value_date = None
    m2 = _DATE_AT_START.match(rest)
    if m2:
        value_date, vfl = parse_date(m2.group("d"))
        flags += [f for f in vfl if f not in flags]
        rest = rest[len(m2.group("d")) :].strip()
    amounts: list[tuple[Word, Decimal, str | None]] = []  # (word, signed value, "CR" | "DR" | None)
    for w in nums:
        t = normalize(w.text)
        if re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}", t):
            continue  # a date, not an amount
        suffix = re.search(r"(?i)(CR|DR)$", t)
        v, fl = parse_money(re.sub(r"(?i)(CR|DR)$", "", t))
        if v is None:
            continue
        if "ambiguous_separator" in fl and "ambiguous_separator" not in flags:
            flags.append("ambiguous_separator")
        amounts.append((w, -v if "negative" in fl else v, suffix.group(1).upper() if suffix else None))
    debit = credit = balance = None
    if cols and amounts:
        for w, v, sfx in amounts:
            cx, rx = (w.x0 + w.x1) / 2, w.x1
            role = min(
                (
                    (r, min(abs(cx - (a + b) / 2), abs(rx - b)))
                    for r, (a, b) in cols.items()
                    if r in ("debit", "credit", "amount", "balance")
                ),
                key=lambda x: x[1],
            )[0]
            if role == "debit" and debit is None:
                debit = abs(v)
            elif role == "credit" and credit is None:
                credit = abs(v)
            elif role == "balance" and balance is None:
                balance = v
            elif role == "amount" and debit is None and credit is None:
                # one signed amount column: sign or DR/CR decides; otherwise the running balance decides (below)
                if v < 0 or sfx == "DR":
                    debit = abs(v)
                elif sfx == "CR" or "+" in text:
                    credit = abs(v)
                else:
                    flags.append("sign_unclear")
                    _pending[0] = abs(v)
        if _pending[0] is not None and balance is not None and prev_balance is not None:
            amt = _pending[0]
            if abs(prev_balance - amt - balance) <= Decimal("0.005"):
                debit = amt
                flags.remove("sign_unclear")
            elif abs(prev_balance + amt - balance) <= Decimal("0.005"):
                credit = amt
                flags.remove("sign_unclear")
    elif amounts:
        flags.append("no_column_layout")
        if len(amounts) >= 2:
            balance = amounts[-1][1]
            amt = amounts[-2][1]
            if prev_balance is not None and balance is not None:
                if abs(prev_balance - abs(amt) - balance) <= Decimal("0.005"):
                    debit = abs(amt)
                elif abs(prev_balance + abs(amt) - balance) <= Decimal("0.005"):
                    credit = abs(amt)
                else:
                    flags.append("amount_direction_unknown")
            else:
                flags.append("amount_direction_unknown")
        else:
            flags.append("amount_direction_unknown")
    if "sign_unclear" in flags and debit is None and credit is None:
        flags.append("amount_direction_unknown")
    if debit is None and credit is None and "amount_direction_unknown" not in flags:
        flags.append("no_amount")
    if prev_balance is not None and balance is not None and (debit is not None or credit is not None):
        expect = prev_balance + (credit or Decimal(0)) - (debit or Decimal(0))
        if abs(expect - balance) > Decimal("0.005"):
            flags.append("balance_mismatch")
    desc_words = [w.text for w in ln.words if not _num_word(w) and not _DATE_AT_START.match(normalize(w.text))]
    desc = " ".join(desc_words).strip() or re.sub(r"[\d,.\-()/]+", " ", rest).strip()
    ref = None
    mref = re.search(r"(?i)\b(?:ref|chq|cheque|trx|txn)\.?\s*[:#]?\s*([A-Z0-9\-/]{4,})", desc)
    if mref:
        ref = mref.group(1)
    return Transaction(
        row_no,
        ln.page,
        ln.bbox,
        ln.text,
        tx_date,
        value_date,
        desc,
        ref,
        debit,
        credit,
        balance,
        _confidence(ln, 0.85 - 0.1 * len([f for f in flags if f != "no_column_layout"])),
        sorted(set(flags)),
    )


def validate_statement(
    f: Mapping[str, Found], txs: Sequence[Transaction], tol: Decimal = Decimal("0.01")
) -> list[Issue]:
    """Deterministic balance checks. Nothing is adjusted: the difference and the suspect rows are REPORTED."""
    issues: list[Issue] = []
    opening = _dec(f["opening_balance"].value) if "opening_balance" in f else None
    closing = _dec(f["closing_balance"].value) if "closing_balance" in f else None
    debits = sum((t.debit for t in txs if t.debit is not None), Decimal(0))
    credits = sum((t.credit for t in txs if t.credit is not None), Decimal(0))
    unknown = [t.row_no for t in txs if t.debit is None and t.credit is None]
    if unknown:
        issues.append(
            Issue(
                "rows_without_amount",
                "error",
                f"{len(unknown)} row(s) have no readable debit or credit amount.",
                {"rows": unknown},
            )
        )
    # running-balance chain: where does it first break, and by how much?
    breaks = []
    prev: Decimal | None = opening
    for t in txs:
        if t.balance is None:
            continue
        if prev is not None and (t.debit is not None or t.credit is not None):
            expect = prev + (t.credit or Decimal(0)) - (t.debit or Decimal(0))
            if abs(expect - t.balance) > tol:
                breaks.append(
                    {
                        "row": t.row_no,
                        "page": t.page,
                        "expected_balance": money_str(expect),
                        "found_balance": money_str(t.balance),
                        "difference": money_str(t.balance - expect),
                    }
                )
        prev = t.balance
    if breaks:
        issues.append(
            Issue(
                "running_balance_break",
                "error",
                f"The running balance does not follow the movements at {len(breaks)} row(s); rows may be missing or misread.",
                {"breaks": breaks[:20]},
            )
        )
    if opening is not None and closing is not None:
        calc = opening + credits - debits
        diff = closing - calc
        if abs(diff) > tol:
            issues.append(
                Issue(
                    "statement_unreconciled",
                    "error",
                    f"Opening {format(opening, 'f')} + credits {format(credits, 'f')} - debits {format(debits, 'f')} = {format(calc, 'f')}, but the closing balance is {format(closing, 'f')} (difference {format(diff, 'f')}).",
                    {
                        "opening": money_str(opening),
                        "credits": money_str(credits),
                        "debits": money_str(debits),
                        "calculated_closing": money_str(calc),
                        "closing": money_str(closing),
                        "difference": money_str(diff),
                        "suspect_rows": [b["row"] for b in breaks[:5]] or unknown[:5],
                    },
                )
            )
        else:
            issues.append(
                Issue(
                    "statement_reconciled",
                    "info",
                    f"Opening balance plus movements equals the closing balance (difference {format(diff, 'f')}).",
                    {"difference": money_str(diff), "debits": money_str(debits), "credits": money_str(credits)},
                )
            )
    else:
        issues.append(
            Issue(
                "balances_incomplete",
                "warning",
                "The opening and/or closing balance was not found, so the statement could not be checked.",
                {},
            )
        )
    last_bal = next((t.balance for t in reversed(txs) if t.balance is not None), None)
    if closing is not None and last_bal is not None and abs(last_bal - closing) > tol:
        issues.append(
            Issue(
                "last_balance_differs",
                "warning",
                f"The last running balance {format(last_bal, 'f')} differs from the closing balance {format(closing, 'f')}.",
                {"last_balance": money_str(last_bal), "closing": money_str(closing)},
            )
        )
    return issues


# ---------------------------------------------------------------- splitting and metadata candidates
def suggest_splits(pages: Sequence[Page], per_page_types: Mapping[int, str] | None = None) -> dict[str, Any] | None:
    """Possible document boundaries inside one file. A PROPOSAL only: the original is never changed."""
    if len(pages) < 2:
        return None
    starts: list[int] = []
    evidence: list[dict[str, Any]] = []
    seen_numbers: set[str] = set()
    for p in pages:
        if p.blank:
            evidence.append({"page": p.page_no, "signal": "blank_page"})
            continue
        head = " ".join(ln.text for ln in p.lines[:8])
        num = _id_field("n", [p], ["invoice", "receipt", "credit note", "debit note", "document"]).value
        newdoc = bool(re.search(r"(?i)page\s*1\s*(of|/)", head)) or (
            num is not None and num not in seen_numbers and bool(seen_numbers)
        )
        if p.page_no == 1 or newdoc:
            starts.append(p.page_no)
            if num:
                seen_numbers.add(num)
            if p.page_no != 1:
                evidence.append({"page": p.page_no, "signal": "new_document_header", "number": num})
        elif num:
            seen_numbers.add(num)
    starts = sorted(set(starts))
    if len(starts) < 2:
        return None
    segments = []
    for i, s in enumerate(starts):
        e = (starts[i + 1] - 1) if i + 1 < len(starts) else pages[-1].page_no
        segments.append({"start_page": s, "end_page": e, "type_key": (per_page_types or {}).get(s)})
    return {"segments": segments, "evidence": evidence}


def candidate_for_field(pages: Sequence[Page], key: str, label: str) -> tuple[str, Line] | None:
    """Raw text printed after a label that matches a company-defined field (by label or key). The label must START the
    line and be followed by ':' or '-' so a word inside another field's value (e.g. "Cost Center: Warehouse 9") is not
    mistaken for a label. The service validates the value against the field's rules."""
    for lab in dict.fromkeys((re.escape(label), re.escape(key.replace("_", " ")))):
        pat = re.compile(rf"(?i)^\s*{lab}\s*[:：\-]\s*(?P<v>\S.*)$")
        for p in pages:
            for ln in p.lines:
                m = pat.match(normalize(ln.text))
                if m:
                    return m.group("v").strip(), ln
    return None


def extract_for(type_key: str, pages: Sequence[Page]) -> Extraction | None:
    t = TYPES.get(type_key)
    if t is None or t.schema is None:
        return None
    if t.schema == "bank_statement":
        return parse_bank_statement(pages)
    if t.schema == "receipt":
        return extract_receipt(pages)
    if t.schema == "adjustment_note":
        return extract_adjustment_note(pages)
    return extract_invoice(pages)
