"""Document-understanding providers (classification + field extraction) behind one interface.

* `RulesProvider` — the deterministic local engine in `doc_extract` (default; nothing leaves the server).
* `HttpJsonProvider` — an OpenAI-compatible chat-completions endpoint (cloud or self-hosted model). Opt-in per
  organization (explicit consent for external content), configured only through server-side settings; the API key is
  never stored in the database, logged, or returned. Its answers are SUGGESTIONS: every value must be found in the OCR
  text (otherwise it is flagged `not_found_in_text`), money is parsed with the same Decimal rules, and the deterministic
  validators still run on top. Bank statements always use the deterministic parser.

Neither provider approves anything.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Protocol

from artify_api.doc_extract import (
    Classification,
    CompanyProfile,
    Extraction,
    Found,
    Page,
    classify,
    extract_for,
    locate,
    normalize,
    parse_date,
    parse_money,
    validate_totals,
)
from artify_api.doc_taxonomy import CLASSIFICATION_THRESHOLD, SCHEMAS, TYPES
from artify_api.settings import Settings, get_settings


class UnderstandingError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class UnderstandingProvider(Protocol):
    key: str
    external: bool
    model_version: str
    prompt_version: str

    def classify(self, pages: Sequence[Page], profile: CompanyProfile | None) -> Classification: ...
    def extract(self, type_key: str, pages: Sequence[Page], profile: CompanyProfile | None) -> Extraction | None: ...


class RulesProvider:
    key = "rules"
    external = False
    model_version = "rules-1"
    prompt_version = "n/a"

    def classify(self, pages: Sequence[Page], profile: CompanyProfile | None) -> Classification:
        return classify(pages, profile)

    def extract(self, type_key: str, pages: Sequence[Page], profile: CompanyProfile | None) -> Extraction | None:
        return extract_for(type_key, pages)


PROMPT_VERSION = "http-json-1"


class HttpJsonProvider:
    key = "http_json"
    external = True
    prompt_version = PROMPT_VERSION

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        if not (s.ai_http_base_url and s.ai_http_model and s.ai_http_api_key):
            raise UnderstandingError("provider_not_configured", "The AI provider is not configured on this server.")
        self.base = s.ai_http_base_url.rstrip("/")
        self.model = s.ai_http_model
        self._key = s.ai_http_api_key.get_secret_value()
        self.timeout = s.ai_http_timeout_seconds
        self.model_version = s.ai_http_model

    def _call(self, system: str, user: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310  (base URL comes from server configuration, https expected)
            f"{self.base}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                payload = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            retry = exc.code in (408, 429, 500, 502, 503, 504)
            raise UnderstandingError(
                "provider_http_error", f"The AI provider answered HTTP {exc.code}.", retry
            ) from None
        except TimeoutError as exc:
            raise UnderstandingError("provider_timeout", "The AI provider did not answer in time.", True) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise UnderstandingError("provider_unreachable", "The AI provider could not be reached.", True) from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            out = json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise UnderstandingError(
                "provider_bad_output", "The AI provider returned an answer that could not be read.", False
            ) from exc
        if not isinstance(out, dict):
            raise UnderstandingError(
                "provider_bad_output", "The AI provider returned an answer that could not be read.", False
            )
        return out

    @staticmethod
    def _text(pages: Sequence[Page]) -> str:
        return "\n".join(f"[page {p.page_no}]\n{p.text}" for p in pages)[:60000]

    def classify(self, pages: Sequence[Page], profile: CompanyProfile | None) -> Classification:
        base = classify(pages, profile)  # keep the deterministic direction logic and evidence as the safety net
        keys = [k for k in TYPES if k not in ("sales_invoice", "purchase_invoice", "unclassified")]
        out = self._call(
            'You classify accounting documents. Answer ONLY with JSON: {"type_key": one of the allowed keys or "unclassified", "confidence": number 0-1, "evidence": [short quotes copied from the text]}. Never guess: use "unclassified" when unsure.',
            f"Allowed keys: {', '.join(keys)}\n\nDocument text:\n{self._text(pages)}",
        )
        key = str(out.get("type_key", "unclassified"))
        if key not in TYPES:
            key = "unclassified"
        try:
            conf = max(0.0, min(1.0, float(out.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        quotes = [q for q in out.get("evidence", []) if isinstance(q, str)][:5]
        text = normalize("\n".join(p.text for p in pages)).lower()
        grounded = [q for q in quotes if normalize(q).lower() in text]
        if quotes and len(grounded) < len(quotes):
            conf *= 0.7  # quotes that are not in the document are a hallucination signal
        if key == base.type_key or key in (base.type_key.replace("sales_", "").replace("purchase_", ""),):
            conf = max(conf, base.confidence) if grounded else conf
        out_cls = Classification(
            base.type_key if key in ("invoice", "sales_invoice", "purchase_invoice", "tax_invoice") and base.type_key in ("sales_invoice", "purchase_invoice") else key,
            base.kind if key in ("invoice", "tax_invoice") else TYPES[key].kind,
            round(conf, 3), "llm+rules",
            [{"phrase": q, "grounded": q in grounded} for q in quotes] or base.evidence,
            base.alternatives, conf < CLASSIFICATION_THRESHOLD, base.direction, base.variant,
        )  # fmt: skip
        if out_cls.kind is None and out_cls.type_key in ("credit_note", "debit_note"):
            out_cls.needs_manual = out_cls.needs_manual or base.kind is None
            out_cls.kind = base.kind
        return out_cls

    def extract(self, type_key: str, pages: Sequence[Page], profile: CompanyProfile | None) -> Extraction | None:
        t = TYPES.get(type_key)
        if t is None or t.schema is None:
            return None
        if t.schema == "bank_statement":
            return extract_for(type_key, pages)  # tables are parsed deterministically: arithmetic must be reproducible
        schema = SCHEMAS[t.schema]
        spec = {f.key: f.label + f" ({f.kind})" for f in schema.fields}
        out = self._call(
            'You extract fields from accounting documents. Answer ONLY with JSON {"fields": {key: {"value": string or null, "confidence": 0-1}}}. Copy values exactly as printed; use null when a value is not visible; never invent or calculate values. Dates as printed, amounts as printed.',
            f"Fields: {json.dumps(spec)}\n\nDocument text:\n{self._text(pages)}",
        )
        fields_raw = out.get("fields")
        raw: dict[str, Any] = fields_raw if isinstance(fields_raw, dict) else {}
        fields: list[Found] = []
        for fd in schema.fields:
            item = raw.get(fd.key) if raw else None
            item = item if isinstance(item, dict) else {}
            val = item.get("value")
            if val in (None, "") or not isinstance(val, str):
                fields.append(Found(fd.key, None, None))
                continue
            try:
                conf = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            flags: list[str] = ["llm_suggestion"]
            norm_val: str | None = val.strip()
            if fd.kind == "date":
                norm_val, fl = parse_date(val)
                flags += fl
            elif fd.kind == "money":
                dv, fl = parse_money(val)
                norm_val = None if dv is None else format(dv, "f")
                flags += [x for x in fl if x != "negative"]
            if norm_val is None:
                fields.append(Found(fd.key, None, 0.2, None, None, val, "llm", ["unreadable_value"]))
                continue
            where = next(
                (
                    (ln, p.page_no)
                    for p in pages
                    for ln in p.lines
                    if normalize(val).lower() in normalize(ln.text).lower()
                ),
                None,
            )
            if where is None:
                flags.append("not_found_in_text")
                conf = min(conf, 0.4)
                fields.append(Found(fd.key, norm_val, conf, None, None, None, "llm", flags))
            else:
                ln, pg = where
                fields.append(
                    Found(
                        fd.key, norm_val, round(min(conf, 0.9), 3), pg, locate(ln, val.strip()), ln.text, "llm", flags
                    )
                )
        ex = Extraction(schema, fields, [], [])
        by = {f.key: f for f in fields}
        if t.schema == "invoice":
            ex.issues += validate_totals(by, [])
        for fd, f in zip(schema.fields, fields, strict=True):
            if f.value is None and fd.required:
                from artify_api.doc_extract import Issue

                ex.issues.append(Issue("required_missing", "warning", f"{fd.label} was not found.", {"field": fd.key}))
        return ex


def get_understanding(key: str | None = None, settings: Settings | None = None) -> UnderstandingProvider:
    s = settings or get_settings()
    k = key or s.understanding_provider
    if k == "rules":
        return RulesProvider()
    if k in ("http", "http_json"):
        return HttpJsonProvider(s)
    raise UnderstandingError("provider_not_configured", f"Provider '{k}' is not available.")
