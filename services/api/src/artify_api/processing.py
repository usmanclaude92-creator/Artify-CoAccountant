"""Document processing service (Phase 7): jobs, the OCR -> classify -> extract -> validate -> suggest pipeline,
persistence of every result as versioned records, workspace/metadata suggestions, accountant review and approval.

Rules that matter:
* Phase 6 documents, versions and stored bytes are only READ. Nothing is modified or replaced.
* Every run adds records (OCR result, classification, extraction); reprocessing never deletes earlier ones.
* The pipeline acts as the system, never as a user, so it can never approve. Approval is a human act recorded with
  `approved_by` (a database CHECK refuses an approved extraction without an approver).
* Missing is NULL plus a status; ambiguous values are flagged; totals/balances that do not add up are reported with their
  difference and are never silently corrected.
* Limits are enforced through the EntitlementService; exceeding them stops NEW processing and never deletes data.
"""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from artify_api import audit, entitlements, storage
from artify_api import workspace as ws
from artify_api import workspace_config as wc
from artify_api.context import RequestCtx
from artify_api.doc_extract import (
    CompanyProfile,
    Extraction,
    Line,
    Page,
    Word,
    candidate_for_field,
    parse_date,
    parse_money,
    suggest_splits,
    validate_statement,
)
from artify_api.doc_extract import Found as _Found
from artify_api.doc_taxonomy import SCHEMAS, SUPPORTED_LANGUAGES, TYPES
from artify_api.documents import doc_access, get_document, log_event
from artify_api.errors import ApiError, Conflict, EntitlementExceeded, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.ocr_engine import OcrError, TesseractProvider, count_pages, get_provider, run_ocr
from artify_api.settings import get_settings
from artify_api.understanding import RulesProvider, UnderstandingError, get_understanding
from artify_api.worker import run_in_tenant_context
from artify_api.workspace_engine import ConfigError, evaluate_rules, validate_value

log = logging.getLogger("artify.processing")

ACTIVE = ("queued", "preparing_pages", "ocr_processing", "classifying", "extracting", "validating", "suggesting")
FINAL = ("awaiting_review", "approved", "failed", "cancelled", "needs_manual")
PROGRESS = {
    "queued": 0,
    "preparing_pages": 8,
    "ocr_processing": 15,
    "classifying": 62,
    "extracting": 72,
    "validating": 84,
    "suggesting": 92,
    "awaiting_review": 100,
    "approved": 100,
    "needs_manual": 100,
}
FIELD_STATES = ("extracted", "needs_review", "confirmed", "corrected", "missing", "unreadable", "rejected")
UNRESOLVED = ("needs_review",)
PROCESSABLE_EXT = {"pdf", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "heic", "heif"}


class ProviderUnavailable(ApiError):
    status, code, title = 503, "provider_not_configured", "Document processing is not available"


class ExtractionLocked(Conflict):
    code, title = "extraction_approved", "This extraction is already approved"


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


def _guard(c: Connection, org: uuid.UUID, key: str, delta: int) -> None:
    entitlements.guard_quota(c, org, key, delta)


def _tx(org: uuid.UUID, company: uuid.UUID, fn: Callable[[Connection], Any]) -> Any:
    return run_in_tenant_context(str(org), str(company), fn)


def _event(
    conn: Connection,
    org: Any,
    company: Any,
    document_id: Any,
    etype: str,
    actor: Any,
    summary: dict[str, Any],
    version_no: int | None = None,
    meta: audit.RequestMeta | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO document_events (id, organization_id, company_id, document_id, version_no, actor_id, event_type, summary, ip) VALUES (:i, :o, :c, :d, :v, :a, :t, CAST(:s AS jsonb), CAST(:ip AS inet))"
        ),
        {
            "i": new_id(),
            "o": org,
            "c": company,
            "d": document_id,
            "v": version_no,
            "a": actor,
            "t": etype,
            "s": _j(summary),
            "ip": meta.ip if meta else None,
        },
    )
    audit.write_audit(
        conn,
        action=etype,
        actor_type="user" if actor else "system",
        actor_id=actor,
        org_id=org,
        company_id=company,
        entity_type="document",
        entity_id=document_id,
        summary={**summary, "version_no": version_no},
        meta=meta,
    )


# ---------------------------------------------------------------- providers (resolution honours organization settings)
def resolve_providers(conn: Connection, org: uuid.UUID, company: uuid.UUID) -> dict[str, Any]:
    """Which OCR/understanding provider applies: company setting > organization setting > server default.
    A provider that sends content outside Artify is used only when the organization explicitly allowed it AND the
    plan includes advanced AI."""
    s = get_settings()
    out: dict[str, Any] = {"ocr": s.ocr_provider, "understanding": s.understanding_provider, "blocked": []}
    rows = conn.execute(
        text(
            "SELECT kind, provider_key, enabled, allow_external_content, company_id FROM ai_provider_configurations WHERE organization_id = :o AND (company_id IS NULL OR company_id = :c) ORDER BY company_id NULLS FIRST"
        ),
        {"o": org, "c": company},
    ).all()
    for r in rows:
        if r.enabled:
            out[r.kind] = r.provider_key
            out[f"{r.kind}_consent"] = r.allow_external_content
    if out["understanding"] in ("http", "http_json"):
        if not out.get("understanding_consent"):
            out["blocked"].append("external_content_not_allowed")
        elif not entitlements.has_feature(conn, org, "feature.advanced_ai"):
            out["blocked"].append("advanced_ai_not_in_plan")
        if out["blocked"]:
            out["understanding"] = "rules"  # fall back to the local engine; never send content without consent
    return out


def company_profile(conn: Connection, company: uuid.UUID) -> CompanyProfile:
    r = conn.execute(
        text("SELECT name, legal_name, tax_registration, vat_number FROM companies WHERE id = :c"), {"c": company}
    ).one()
    names = [n for n in (r.name, r.legal_name) if n]
    taxes = tuple(t for t in (r.tax_registration, r.vat_number) if t)
    return (
        CompanyProfile(" / ".join(names[:1]), taxes)
        if len(names) < 2 or names[0] == names[1]
        else CompanyProfile(names[1], taxes)
    )


# ---------------------------------------------------------------- job creation and control
def job_view(r: Any) -> dict[str, Any]:
    d = ws.row_dict(r)
    d["progress"] = _progress(d)
    return {
        k: d.get(k)
        for k in (
            "id",
            "document_id",
            "version_id",
            "version_no",
            "kind",
            "status",
            "languages",
            "pages_total",
            "pages_done",
            "attempt_count",
            "max_attempts",
            "error_code",
            "error_message",
            "providers",
            "reason",
            "created_at",
            "started_at",
            "finished_at",
            "updated_at",
            "progress",
        )
    }


def _progress(d: dict[str, Any]) -> int:
    st = d["status"]
    if st == "ocr_processing" and d.get("pages_total"):
        return int(15 + 45 * (d["pages_done"] or 0) / max(1, d["pages_total"]))
    return PROGRESS.get(st, 100 if st in ("failed", "cancelled") else 0)


def create_job(
    ctx: RequestCtx,
    acc: ws.Access,
    doc_id: uuid.UUID,
    *,
    kind: str,
    languages: list[str],
    reason: str | None,
    idempotency_key: str,
) -> tuple[bool, dict[str, Any]]:
    """Returns (created, job). A repeated key or an already running job returns the existing job (created = False)."""
    assert ctx.org_id is not None and ctx.company_id is not None
    conn, org, cid = ctx.conn, ctx.org_id, ctx.company_id
    s = get_settings()
    langs = languages or list(s.ocr_default_languages)
    bad = [x for x in langs if x not in SUPPORTED_LANGUAGES]
    if bad:
        raise Unprocessable(f"Unsupported OCR language: {', '.join(bad)}. Supported: {', '.join(SUPPORTED_LANGUAGES)}.")
    doc = get_document(ctx, acc, doc_id, action="view")
    if doc["status"] != "active":
        raise Conflict("Only active documents can be processed.")
    if doc["extension"] not in PROCESSABLE_EXT:
        raise Unprocessable(
            f"Files of type '.{doc['extension']}' cannot be read by OCR. Supported: PDF and images.",
            extra={"reason": "unsupported_for_ocr"},
        )
    prov = _ocr_provider_state(languages=langs)
    if prov:
        raise ProviderUnavailable(prov)
    existing = conn.execute(
        text("SELECT * FROM processing_jobs WHERE company_id = :c AND requested_by = :u AND idempotency_key = :k"),
        {"c": cid, "u": ctx.user_id, "k": idempotency_key},
    ).first()
    if existing is not None:
        if str(existing.document_id) != str(doc_id) or existing.kind != kind:
            raise Conflict("This idempotency key was already used for a different request.")
        return False, job_view(existing)
    ver = conn.execute(
        text("SELECT id, version_no FROM document_versions WHERE document_id = :d ORDER BY version_no DESC LIMIT 1"),
        {"d": doc_id},
    ).one()
    active = conn.execute(
        text("SELECT * FROM processing_jobs WHERE version_id = :v AND kind = :k AND status = ANY(:a)"),
        {"v": ver.id, "k": kind, "a": list(ACTIVE)},
    ).first()
    if active is not None:
        return False, job_view(active)
    if (
        kind in ("classify", "extract")
        and conn.execute(text("SELECT 1 FROM ocr_results WHERE version_id = :v"), {"v": ver.id}).first() is None
    ):
        raise Conflict("This document has not been read by OCR yet; run full processing first.")
    entitlements.guard_concurrency(conn, org)
    if kind in ("full", "extract"):
        entitlements.guard_quota(conn, org, "quota.ai_extractions_per_month", 1)
    jid = new_id()
    conn.execute(
        text(
            "INSERT INTO processing_jobs (id, organization_id, company_id, document_id, version_id, version_no, kind, idempotency_key, languages, max_attempts, reason, requested_by) VALUES (:i, :o, :c, :d, :v, :n, :k, :ik, :l, :m, :r, :u)"
        ),
        {
            "i": jid,
            "o": org,
            "c": cid,
            "d": doc_id,
            "v": ver.id,
            "n": ver.version_no,
            "k": kind,
            "ik": idempotency_key,
            "l": langs,
            "m": s.processing_max_attempts,
            "r": reason,
            "u": ctx.user_id,
        },
    )
    log_event(
        ctx,
        doc_id,
        "processing.requested",
        version_no=ver.version_no,
        also_audit=True,
        summary={"job_id": str(jid), "kind": kind, "reason": reason},
    )
    return True, job_view(conn.execute(text("SELECT * FROM processing_jobs WHERE id = :i"), {"i": jid}).one())


def _ocr_provider_state(languages: list[str]) -> str | None:
    try:
        p = get_provider()
        v = p.version()  # raises when the binary is missing
        _ = v
        if hasattr(p, "installed_languages"):
            missing = [x for x in languages if x not in p.installed_languages()]
            if missing:
                return f"The OCR language data for {', '.join(missing)} is not installed on this server."
    except OcrError as exc:
        return exc.message
    return None


def dispatch(org: uuid.UUID, company: uuid.UUID, job_id: str) -> None:
    """Hand a COMMITTED job to the worker. In production this is a Celery task; in single-process development it runs as a
    FastAPI background task (the caller registers this function)."""
    if get_settings().processing_mode == "celery":
        from artify_api.worker import celery_app

        celery_app.send_task("artify.process_document", args=[str(org), str(company), str(job_id)])
    else:
        run_job(str(org), str(company), str(job_id))


def get_job(ctx: RequestCtx, job_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        text("SELECT * FROM processing_jobs WHERE company_id = :c AND id = :i"), {"c": ctx.company_id, "i": job_id}
    ).first()
    if row is None:
        raise NotFound("Processing job not found.")
    return job_view(row)


def _job_doc_access(ctx: RequestCtx, acc: ws.Access, job_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        text("SELECT * FROM processing_jobs WHERE company_id = :c AND id = :i"), {"c": ctx.company_id, "i": job_id}
    ).first()
    if row is None:
        raise NotFound("Processing job not found.")
    get_document(ctx, acc, row.document_id, action="view")  # hidden documents hide their jobs too
    return ws.row_dict(row)


def list_jobs(
    ctx: RequestCtx, acc: ws.Access, *, document_id: uuid.UUID | None, status: str | None, limit: int, offset: int
) -> dict[str, Any]:
    vis = [uuid.UUID(n) for n in acc.nodes if acc.visible(n)]
    where = ["j.company_id = :c", "d.node_id = ANY(:vis)"]
    params: dict[str, Any] = {"c": ctx.company_id, "vis": vis}
    if document_id:
        where.append("j.document_id = :d")
        params["d"] = document_id
    if status:
        where.append("j.status = :s")
        params["s"] = status
    w = " AND ".join(where)
    total = ctx.conn.execute(
        text(f"SELECT count(*) FROM processing_jobs j JOIN documents d ON d.id = j.document_id WHERE {w}"), params
    ).scalar_one()
    rows = ctx.conn.execute(
        text(
            f"SELECT j.*, d.display_name FROM processing_jobs j JOIN documents d ON d.id = j.document_id WHERE {w} ORDER BY j.created_at DESC, j.id LIMIT :l OFFSET :o"
        ),
        {**params, "l": limit, "o": offset},
    ).all()
    return {
        "items": [{**job_view(r), "document_name": r.display_name} for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def job_history(ctx: RequestCtx, acc: ws.Access, job_id: uuid.UUID) -> dict[str, Any]:
    j = _job_doc_access(ctx, acc, job_id)
    attempts = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT attempt_no, outcome, error_code, error_message, started_at, finished_at FROM processing_attempts WHERE job_id = :j ORDER BY attempt_no"
            ),
            {"j": job_id},
        )
    ]
    return {"job": job_view(j), "attempts": attempts}


def cancel_job(ctx: RequestCtx, acc: ws.Access, job_id: uuid.UUID) -> dict[str, Any]:
    j = _job_doc_access(ctx, acc, job_id)
    if j["status"] not in ACTIVE:
        raise Conflict(f"The job is {j['status']} and cannot be cancelled.")
    ctx.conn.execute(
        text(
            "UPDATE processing_jobs SET status = 'cancelled', finished_at = now(), updated_at = now() WHERE id = :i AND status = ANY(:a)"
        ),
        {"i": job_id, "a": list(ACTIVE)},
    )
    log_event(ctx, j["document_id"], "processing.cancelled", also_audit=True, summary={"job_id": str(job_id)})
    return get_job(ctx, job_id)


def retry_job(ctx: RequestCtx, acc: ws.Access, job_id: uuid.UUID) -> dict[str, Any]:
    """Retry a FAILED job: same record, attempt counter continues, earlier attempts stay in the history."""
    j = _job_doc_access(ctx, acc, job_id)
    if j["status"] not in ("failed", "needs_manual"):
        raise Conflict("Only failed jobs can be retried.")
    if j["error_code"] in ("provider_not_configured", "too_many_pages", "unreadable_document", "unsupported_for_ocr"):
        # a retry without a change would fail the same way: explain instead of looping
        raise Conflict("This job failed for a reason a retry cannot fix.", extra={"error_code": j["error_code"]})
    assert ctx.org_id is not None
    entitlements.guard_concurrency(ctx.conn, ctx.org_id)
    ctx.conn.execute(
        text(
            "UPDATE processing_jobs SET status = 'queued', error_code = NULL, error_message = NULL, finished_at = NULL, max_attempts = attempt_count + :m, updated_at = now() WHERE id = :i"
        ),
        {"i": job_id, "m": get_settings().processing_max_attempts},
    )
    log_event(ctx, j["document_id"], "processing.retried", also_audit=True, summary={"job_id": str(job_id)})
    return get_job(ctx, job_id)


# ---------------------------------------------------------------- the pipeline (runs as the SYSTEM)
def _set(org: uuid.UUID, company: uuid.UUID, job_id: str, **cols: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    _tx(
        org,
        company,
        lambda c: c.execute(
            text(f"UPDATE processing_jobs SET {sets}, updated_at = now() WHERE id = :_id"), {**cols, "_id": job_id}
        ),
    )


def _status(org: uuid.UUID, company: uuid.UUID, job_id: str) -> str:
    return str(
        _tx(
            org,
            company,
            lambda c: c.execute(text("SELECT status FROM processing_jobs WHERE id = :i"), {"i": job_id}).scalar(),
        )
    )


def run_job(org_id: str, company_id: str, job_id: str) -> str:
    org, company = uuid.UUID(org_id), uuid.UUID(company_id)
    backoff = get_settings().processing_retry_backoff_seconds
    while True:
        begun = _tx(org, company, lambda c: _begin_attempt(c, job_id))
        if begun is None:
            return "skipped"
        attempt_no, job = begun
        try:
            _pipeline(org, company, job)
            _end_attempt(org, company, job_id, attempt_no, "succeeded")
            return "done"
        except _Cancelled:
            _end_attempt(org, company, job_id, attempt_no, "cancelled")
            return "cancelled"
        except (OcrError, UnderstandingError) as exc:
            code, msg, retryable = exc.code, exc.message, exc.retryable
        except EntitlementExceeded as exc:
            code, msg, retryable = "entitlement_exceeded", str(exc.detail), False
        except Exception:
            log.exception("processing job %s failed unexpectedly", job_id)
            code, msg, retryable = (
                "internal_error",
                "Processing failed unexpectedly. The incident was logged; retry later or process this document manually.",
                True,
            )
        _end_attempt(org, company, job_id, attempt_no, "timeout" if code.endswith("timeout") else "failed", code, msg)
        left = _tx(
            org,
            company,
            lambda c: c.execute(
                text("SELECT max_attempts - attempt_count FROM processing_jobs WHERE id = :i"), {"i": job_id}
            ).scalar_one(),
        )
        if retryable and left > 0:
            _set(org, company, job_id, status="queued", error_code=code, error_message=msg[:500])
            time.sleep(backoff * attempt_no if backoff > 0 else 0)
            continue
        _set(org, company, job_id, status="failed", error_code=code, error_message=msg[:500], finished_at=_now())
        _record_failure(org, company, job, code)
        return "failed"


def _end_attempt(
    org: uuid.UUID,
    company: uuid.UUID,
    job_id: str,
    attempt_no: int,
    outcome: str,
    code: str | None = None,
    msg: str | None = None,
) -> None:
    params = {"o": outcome, "c": code, "m": (msg or "")[:500] or None, "j": job_id, "n": attempt_no}
    _tx(
        org,
        company,
        lambda c: c.execute(
            text(
                "UPDATE processing_attempts SET outcome = :o, error_code = :c, error_message = :m, finished_at = now() WHERE job_id = :j AND attempt_no = :n"
            ),
            params,
        ),
    )


def _record_failure(org: uuid.UUID, company: uuid.UUID, job: dict[str, Any], code: str) -> None:
    _tx(
        org,
        company,
        lambda c: _event(
            c,
            org,
            company,
            job["document_id"],
            "processing.failed",
            None,
            {"job_id": job["id"], "error_code": code},
            job["version_no"],
        ),
    )


def _now() -> Any:
    return _tx_now()


def _tx_now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


class _Cancelled(Exception):
    pass


def _begin_attempt(c: Connection, job_id: str) -> tuple[int, dict[str, Any]] | None:
    row = c.execute(text("SELECT * FROM processing_jobs WHERE id = :i FOR UPDATE"), {"i": job_id}).first()
    if row is None or row.status not in ("queued",):
        return None
    n = row.attempt_count + 1
    c.execute(
        text(
            "UPDATE processing_jobs SET status = 'preparing_pages', attempt_count = :n, started_at = COALESCE(started_at, now()), error_code = NULL, error_message = NULL, updated_at = now() WHERE id = :i"
        ),
        {"n": n, "i": job_id},
    )
    c.execute(
        text(
            "INSERT INTO processing_attempts (id, organization_id, company_id, job_id, attempt_no) VALUES (:i, :o, :c, :j, :n)"
        ),
        {"i": new_id(), "o": row.organization_id, "c": row.company_id, "j": job_id, "n": n},
    )
    return n, ws.row_dict(row)


def _check(org: uuid.UUID, company: uuid.UUID, job_id: str) -> None:
    if _status(org, company, job_id) == "cancelled":
        raise _Cancelled


def _start_ocr(c: Connection, org: uuid.UUID, jid: str, n_pages: int, prov_info: dict[str, Any]) -> None:
    _guard(c, org, "quota.ocr_pages_per_month", n_pages)
    c.execute(
        text(
            "UPDATE processing_jobs SET pages_total = :n, pages_done = 0, status = 'ocr_processing', providers = CAST(:p AS jsonb), updated_at = now() WHERE id = :i"
        ),
        {"n": n_pages, "i": jid, "p": _j(prov_info)},
    )


def _pipeline(org: uuid.UUID, company: uuid.UUID, job: dict[str, Any]) -> None:
    jid, kind = job["id"], job["kind"]
    s = get_settings()

    def load(c: Connection) -> dict[str, Any]:
        v = c.execute(
            text(
                "SELECT v.extension, so.storage_key, so.state, v.mime_type FROM document_versions v JOIN storage_objects so ON so.id = v.storage_object_id WHERE v.id = :v"
            ),
            {"v": job["version_id"]},
        ).one()
        return {
            "ext": v.extension,
            "key": v.storage_key,
            "state": v.state,
            "profile": company_profile(c, company),
            "providers": resolve_providers(c, org, company),
        }

    info = _tx(org, company, load)
    providers = info["providers"]
    done = _tx(
        org,
        company,
        lambda c: c.execute(text("SELECT 1 FROM extractions WHERE job_id = :j LIMIT 1"), {"j": jid}).first(),
    )
    if done is not None:  # a retry after the results were already stored: nothing is produced twice
        _set(org, company, jid, status="awaiting_review", finished_at=_now(), error_code=None, error_message=None)
        return
    pages: list[Page] = []
    existing_ocr = _tx(
        org, company, lambda c: c.execute(text("SELECT id FROM ocr_results WHERE job_id = :j"), {"j": jid}).scalar()
    )
    if existing_ocr is not None:  # the OCR of this job was stored by an earlier attempt: reuse it
        pages = _tx(org, company, lambda c: load_pages(c, existing_ocr))
    elif kind in ("full", "ocr"):
        if info["state"] == "missing":
            raise OcrError("source_missing", "The stored document is missing; reconcile storage first.", False)
        try:
            data = b"".join(storage.get_storage().open_range(info["key"]))
        except storage.StorageError as exc:
            raise OcrError("storage_unavailable", "The document could not be read from storage.", True) from exc
        n_pages = count_pages(data, info["ext"])
        if n_pages > s.ocr_max_pages:
            raise OcrError(
                "too_many_pages", f"The document has {n_pages} pages; the limit is {s.ocr_max_pages}.", False
            )
        prov_info = {
            "ocr": providers["ocr"],
            "understanding": providers["understanding"],
            "blocked": providers["blocked"],
        }
        _tx(org, company, lambda c: _start_ocr(c, org, jid, n_pages, prov_info))
        _check(org, company, jid)
        provider = get_provider(providers["ocr"])
        done = {"n": 0}

        def progress(i: int, total: int) -> None:
            done["n"] = i
            _set(org, company, jid, pages_done=i)

        run = run_ocr(
            data,
            info["ext"],
            list(job["languages"]),
            provider,
            max_pages=s.ocr_max_pages,
            dpi=s.ocr_render_dpi,
            progress=progress,
        )
        _check(org, company, jid)
        _set(org, company, jid, status="ocr_processing", pages_done=len(run.pages))
        _tx(org, company, lambda c: _persist_ocr(c, org, company, job, run))
        pages = [Page(p.page_no, tuple(p.lines), p.blank) for p in run.pages]
    else:
        _ocr_id, pages = _tx(org, company, lambda c: _load_pages(c, job["version_id"]))
    if kind == "ocr":
        _set(org, company, jid, status="awaiting_review", finished_at=_now())
        return
    # classification
    _check(org, company, jid)
    _set(org, company, jid, status="classifying")
    understanding = get_understanding(providers["understanding"])
    cls = understanding.classify(pages, info["profile"])
    if kind == "extract":
        prev = _tx(
            org,
            company,
            lambda c: c.execute(
                text(
                    "SELECT type_key FROM document_classifications WHERE document_id = :d AND review_status IN ('confirmed','corrected') ORDER BY created_at DESC LIMIT 1"
                ),
                {"d": job["document_id"]},
            ).scalar(),
        )
        if prev:
            cls.type_key, cls.needs_manual, cls.confidence = prev, False, max(cls.confidence, 0.99)
            cls.kind = TYPES[prev].kind if prev in TYPES else cls.kind
    cls_id = _tx(org, company, lambda c: _persist_classification(c, org, company, job, cls, understanding))
    _check(org, company, jid)
    _set(org, company, jid, status="extracting")
    ex = understanding.extract(cls.type_key, pages, info["profile"]) if cls.type_key != "unclassified" else None
    if ex is None:
        _tx(
            org,
            company,
            lambda c: _event(
                c,
                org,
                company,
                job["document_id"],
                "processing.needs_manual",
                None,
                {"job_id": jid, "type_key": cls.type_key},
                job["version_no"],
            ),
        )
        _set(
            org,
            company,
            jid,
            status="needs_manual",
            finished_at=_now(),
            error_code="classification_uncertain",
            error_message="The document type could not be determined with enough confidence. Classify it manually.",
        )
        return
    _set(org, company, jid, status="validating")
    if ex.schema.key == "bank_statement":
        entitlements_ok = _tx(
            org, company, lambda c: entitlements.guard_quota(c, org, "quota.bank_statements_per_month", 1)
        )
        _ = entitlements_ok
    _check(org, company, jid)
    _set(org, company, jid, status="suggesting")

    def persist(c: Connection) -> None:
        eid = _persist_extraction(c, org, company, job, cls_id, cls, ex, understanding)
        split = suggest_splits(pages)
        if split:
            c.execute(
                text(
                    "INSERT INTO split_proposals (id, organization_id, company_id, job_id, document_id, version_id, segments, evidence) VALUES (:i, :o, :c, :j, :d, :v, CAST(:s AS jsonb), CAST(:e AS jsonb)) ON CONFLICT (job_id) DO NOTHING"
                ),
                {
                    "i": new_id(),
                    "o": org,
                    "c": company,
                    "j": jid,
                    "d": job["document_id"],
                    "v": job["version_id"],
                    "s": _j(split["segments"]),
                    "e": _j(split["evidence"]),
                },
            )
        _suggest(c, org, company, eid, cls, ex, pages)
        c.execute(
            text(
                "UPDATE processing_jobs SET status = 'awaiting_review', finished_at = now(), updated_at = now() WHERE id = :i AND status <> 'cancelled'"
            ),
            {"i": jid},
        )
        _event(
            c,
            org,
            company,
            job["document_id"],
            "processing.completed",
            None,
            {
                "job_id": jid,
                "extraction_id": str(eid),
                "type_key": cls.type_key,
                "classification_confidence": cls.confidence,
            },
            job["version_no"],
        )

    _tx(org, company, persist)


# ---------------------------------------------------------------- persistence
def _persist_ocr(c: Connection, org: uuid.UUID, company: uuid.UUID, job: dict[str, Any], run: Any) -> uuid.UUID:
    rid = new_id()
    confs = [p.avg_confidence for p in run.pages if p.avg_confidence is not None]
    full = "\n\n".join(f"--- page {p.page_no} ---\n{p.text}" for p in run.pages)
    c.execute(text("INSERT INTO ocr_results (id, organization_id, company_id, job_id, document_id, version_id, provider_key, engine_version, languages, page_count, avg_confidence, full_text) VALUES (:i, :o, :c, :j, :d, :v, :p, :e, :l, :n, :a, :t)"),
              {"i": rid, "o": org, "c": company, "j": job["id"], "d": job["document_id"], "v": job["version_id"], "p": run.provider, "e": run.engine_version, "l": run.languages, "n": len(run.pages), "a": round(sum(confs) / len(confs), 2) if confs else None, "t": full})  # fmt: skip
    be = storage.get_storage()
    for p in run.pages:
        obj_id = new_id()
        key = storage.object_key(org, company, obj_id)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "page.png"
            f.write_bytes(p.image_png)
            be.put_file(key, f)
        import hashlib

        c.execute(text("INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256, state, verified_at, purpose) VALUES (:i, :o, :c, :b, :k, :sz, :h, 'verified', now(), 'derived')"),
                  {"i": obj_id, "o": org, "c": company, "b": be.name, "k": key, "sz": max(1, len(p.image_png)), "h": hashlib.sha256(p.image_png).hexdigest()})  # fmt: skip
        pid = new_id()
        c.execute(text("INSERT INTO ocr_pages (id, organization_id, company_id, ocr_result_id, page_no, width, height, rotation_applied, deskew_angle, source, is_blank, avg_confidence, text, quality, processed_object_id) VALUES (:i, :o, :c, :r, :n, :w, :h, :ro, :da, :s, :b, :a, :t, CAST(:q AS jsonb), :po)"),
                  {"i": pid, "o": org, "c": company, "r": rid, "n": p.page_no, "w": p.width, "h": p.height, "ro": p.rotation_applied, "da": Decimal(str(p.deskew_angle)), "s": p.source, "b": p.blank, "a": p.avg_confidence, "t": p.text, "q": _j(p.quality), "po": obj_id})  # fmt: skip
        for ln in p.lines:
            c.execute(text("INSERT INTO ocr_blocks (id, organization_id, company_id, page_id, block_no, text, confidence, bbox, words) VALUES (:i, :o, :c, :p, :n, :t, :cf, CAST(:b AS jsonb), CAST(:w AS jsonb))"),
                      {"i": new_id(), "o": org, "c": company, "p": pid, "n": ln.idx, "t": ln.text, "cf": None if ln.conf is None else round(ln.conf * 100, 2), "b": _j(list(ln.bbox)), "w": _j([[w.text, w.x0, w.x1, w.conf] for w in ln.words])})  # fmt: skip
    return rid


def _load_pages(c: Connection, version_id: Any) -> tuple[uuid.UUID, list[Page]]:
    r = c.execute(
        text("SELECT id FROM ocr_results WHERE version_id = :v ORDER BY created_at DESC LIMIT 1"), {"v": version_id}
    ).one()
    return r.id, load_pages(c, r.id)


def load_pages(c: Connection, ocr_id: Any) -> list[Page]:
    pages: list[Page] = []
    for p in c.execute(
        text("SELECT id, page_no, is_blank FROM ocr_pages WHERE ocr_result_id = :r ORDER BY page_no"), {"r": ocr_id}
    ).all():
        lines = []
        for b in c.execute(
            text("SELECT block_no, text, confidence, bbox, words FROM ocr_blocks WHERE page_id = :p ORDER BY block_no"),
            {"p": p.id},
        ):
            words = tuple(
                Word(w[0], float(w[1]), float(w[2]), None if w[3] is None else float(w[3])) for w in (b.words or [])
            )
            bb = b.bbox
            lines.append(
                Line(
                    p.page_no,
                    b.block_no,
                    b.text,
                    (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])),
                    None if b.confidence is None else float(b.confidence) / 100,
                    words,
                )
            )
        pages.append(Page(p.page_no, tuple(lines), p.is_blank))
    return pages


def _persist_classification(
    c: Connection, org: uuid.UUID, company: uuid.UUID, job: dict[str, Any], cls: Any, prov: Any
) -> uuid.UUID:
    cid = new_id()
    c.execute(text("INSERT INTO document_classifications (id, organization_id, company_id, job_id, document_id, version_id, type_key, kind_key, confidence, method, evidence, alternatives, needs_manual, provider_key, model_version) VALUES (:i, :o, :c, :j, :d, :v, :t, :k, :cf, :m, CAST(:e AS jsonb), CAST(:a AS jsonb), :nm, :p, :mv)"),
              {"i": cid, "o": org, "c": company, "j": job["id"], "d": job["document_id"], "v": job["version_id"], "t": cls.type_key, "k": cls.kind, "cf": Decimal(str(cls.confidence)), "m": cls.method, "e": _j(cls.evidence), "a": _j(cls.alternatives), "nm": cls.needs_manual, "p": prov.key, "mv": prov.model_version})  # fmt: skip
    return cid


def _dec_or_none(v: str | None) -> Decimal | None:
    try:
        return None if v is None else Decimal(v)
    except Exception:
        return None


def _persist_extraction(
    c: Connection,
    org: uuid.UUID,
    company: uuid.UUID,
    job: dict[str, Any],
    cls_id: uuid.UUID,
    cls: Any,
    ex: Extraction,
    prov: Any,
) -> uuid.UUID:
    eid = new_id()
    no = c.execute(
        text("SELECT COALESCE(max(extraction_no), 0) + 1 FROM extractions WHERE document_id = :d"),
        {"d": job["document_id"]},
    ).scalar_one()
    # earlier drafts for the same document are superseded (kept); approved extractions stay approved (history)
    c.execute(
        text(
            "UPDATE extractions SET status = 'superseded', row_version = row_version + 1 WHERE document_id = :d AND status = 'draft'"
        ),
        {"d": job["document_id"]},
    )
    summary = {
        "type_key": cls.type_key,
        "kind": cls.kind,
        "fields": len(ex.fields),
        "missing": sum(1 for f in ex.fields if f.value is None),
        "needs_review": sum(1 for f in ex.fields if f.status == "needs_review"),
        "line_items": len(ex.items),
        "transactions": len(ex.transactions),
    }
    c.execute(text("INSERT INTO extractions (id, organization_id, company_id, job_id, document_id, version_id, extraction_no, classification_id, type_key, schema_key, schema_version, provider_key, model_version, prompt_version, validation, summary) VALUES (:i, :o, :c, :j, :d, :v, :n, :cl, :t, :sk, :sv, :p, :mv, :pv, CAST(:val AS jsonb), CAST(:sum AS jsonb))"),
              {"i": eid, "o": org, "c": company, "j": job["id"], "d": job["document_id"], "v": job["version_id"], "n": no, "cl": cls_id, "t": cls.type_key, "sk": ex.schema.key, "sv": ex.schema.version, "p": prov.key, "mv": prov.model_version, "pv": prov.prompt_version, "val": _j([asdict(i) for i in ex.issues]), "sum": _j(summary)})  # fmt: skip
    for f in ex.fields:
        c.execute(text("INSERT INTO extracted_fields (id, organization_id, company_id, extraction_id, field_key, value, original_value, confidence, page_no, bbox, source_text, method, review_status, flags) VALUES (:i, :o, :c, :e, :k, :v, :v, :cf, :pg, CAST(:bb AS jsonb), :st, :m, :rs, CAST(:fl AS jsonb))"),
                  {"i": new_id(), "o": org, "c": company, "e": eid, "k": f.key, "v": f.value, "cf": None if f.confidence is None else Decimal(str(f.confidence)), "pg": f.page, "bb": None if f.bbox is None else _j(list(f.bbox)), "st": f.source_text, "m": f.method, "rs": f.status, "fl": _j(f.flags)})  # fmt: skip
    for it in ex.items:
        c.execute(text('INSERT INTO extracted_line_items (id, organization_id, company_id, extraction_id, line_no, page_no, bbox, source_text, "values", confidence, review_status, flags) VALUES (:i, :o, :c, :e, :n, :pg, CAST(:bb AS jsonb), :st, CAST(:v AS jsonb), :cf, :rs, CAST(:fl AS jsonb))'),
                  {"i": new_id(), "o": org, "c": company, "e": eid, "n": it.line_no, "pg": it.page, "bb": None if it.bbox is None else _j(list(it.bbox)), "st": it.source_text, "v": _j(it.values), "cf": Decimal(str(it.confidence)), "rs": it.status, "fl": _j(it.flags)})  # fmt: skip
    if ex.schema.key == "bank_statement":
        by = {f.key: f for f in ex.fields}
        sid = new_id()
        val = [
            asdict(i) for i in ex.issues if i.code.startswith(("statement_", "running_", "balances_", "rows_", "last_"))
        ]
        c.execute(text("INSERT INTO bank_statements (id, organization_id, company_id, extraction_id, bank_name, account_holder, account_name, account_number_masked, iban_masked, currency, period_start, period_end, opening_balance, closing_balance, validation) VALUES (:i, :o, :c, :e, :bn, :ah, :an, :am, :im, :cu, :ps, :pe, :ob, :cb, CAST(:v AS jsonb))"),
                  {"i": sid, "o": org, "c": company, "e": eid, "bn": by["bank_name"].value if "bank_name" in by else None, "ah": by["account_holder"].value if "account_holder" in by else None, "an": by["account_name"].value if "account_name" in by else None,
                   "am": by["account_number"].value if "account_number" in by else None, "im": by["iban"].value if "iban" in by else None, "cu": by["currency"].value if "currency" in by else None,
                   "ps": by["period_start"].value if "period_start" in by else None, "pe": by["period_end"].value if "period_end" in by else None,
                   "ob": _dec_or_none(by["opening_balance"].value) if "opening_balance" in by else None, "cb": _dec_or_none(by["closing_balance"].value) if "closing_balance" in by else None, "v": _j(val)})  # fmt: skip
        for t in ex.transactions:
            c.execute(text("INSERT INTO bank_statement_transactions (id, organization_id, company_id, statement_id, row_no, page_no, bbox, source_text, tx_date, value_date, description, reference, debit, credit, balance, currency, confidence, review_status, flags) VALUES (:i, :o, :c, :s, :n, :pg, CAST(:bb AS jsonb), :st, :td, :vd, :d, :r, :db, :cr, :b, :cu, :cf, :rs, CAST(:fl AS jsonb))"),
                      {"i": new_id(), "o": org, "c": company, "s": sid, "n": t.row_no, "pg": t.page, "bb": None if t.bbox is None else _j(list(t.bbox)), "st": t.source_text, "td": t.tx_date, "vd": t.value_date, "d": t.description, "r": t.reference, "db": t.debit, "cr": t.credit, "b": t.balance, "cu": by["currency"].value if "currency" in by else None, "cf": Decimal(str(t.confidence)), "rs": t.status, "fl": _j(t.flags)})  # fmt: skip
    return eid


def _suggest(
    c: Connection, org: uuid.UUID, company: uuid.UUID, eid: uuid.UUID, cls: Any, ex: Extraction, pages: list[Page]
) -> None:
    attrs: dict[str, Any] = {"source": "desktop", "document_kind": cls.kind}
    for f in ex.fields:
        if f.value is not None:
            attrs[f"custom:{f.key}"] = f.value
    seen: set[Any] = set()
    sug: list[dict[str, Any]] = []
    winner, hits = evaluate_rules(wc.rule_defs(c, company), attrs) if cls.kind else (None, [])
    for r in ([winner] if winner else []) + [h for h in hits if h is not winner]:
        key = (r.target_node_id, r.target_category_id)
        if key in seen:
            continue
        seen.add(key)
        sug.append(
            {
                "node": r.target_node_id,
                "cat": r.target_category_id,
                "conf": 0.9 if r is winner else 0.6,
                "method": "filing_rule",
                "rule": r.id,
                "ev": [{"rule": r.name, "priority": r.priority, "matched": "your filing rule"}],
            }
        )
    if cls.kind:
        for cat in c.execute(
            text(
                "SELECT c.id, c.name, c.node_id FROM document_categories c JOIN workspace_nodes n ON n.id = c.node_id WHERE c.company_id = :c AND c.document_kind = :k AND c.archived_at IS NULL AND c.is_active AND c.node_id IS NOT NULL AND n.archived_at IS NULL AND n.is_active ORDER BY c.created_at"
            ),
            {"c": company, "k": cls.kind},
        ):
            if (cat.node_id, cat.id) in seen or any(s["node"] == cat.node_id and s["cat"] is None for s in sug):
                continue
            seen.add((cat.node_id, cat.id))
            sug.append(
                {
                    "node": cat.node_id,
                    "cat": cat.id,
                    "conf": 0.7,
                    "method": "category_kind",
                    "rule": None,
                    "ev": [
                        {
                            "category": cat.name,
                            "document_kind": cls.kind,
                            "matched": "a category of yours is mapped to this document type",
                        }
                    ],
                }
            )
    for i, sg in enumerate(sug[:5], 1):
        conf = Decimal(str(round(sg["conf"] * (cls.confidence if cls.confidence < 1 else 1), 3)))
        c.execute(text("INSERT INTO workspace_suggestions (id, organization_id, company_id, extraction_id, rank, node_id, category_id, confidence, method, rule_id, evidence) VALUES (:i, :o, :c, :e, :r, :n, :ca, :cf, :m, :ru, CAST(:ev AS jsonb))"),
                  {"i": new_id(), "o": org, "c": company, "e": eid, "r": i, "n": sg["node"], "ca": sg["cat"], "cf": conf, "m": sg["method"], "ru": sg["rule"], "ev": _j(sg["ev"])})  # fmt: skip
    if not sug:
        why = (
            "The document type could not be determined."
            if not cls.kind
            else "No folder of this company is mapped to this document type and no filing rule matches. Choose a location yourself; nothing is created automatically."
        )
        c.execute(text("INSERT INTO workspace_suggestions (id, organization_id, company_id, extraction_id, rank, node_id, category_id, confidence, method, evidence, status) VALUES (:i, :o, :c, :e, 1, NULL, NULL, 0, 'none', CAST(:ev AS jsonb), 'unavailable')"),
                  {"i": new_id(), "o": org, "c": company, "e": eid, "ev": _j([{"note": why}])})  # fmt: skip
    # custom metadata for the best destination (or company-wide fields when there is no destination)
    top = sug[0] if sug else None
    chain = [n["id"] for n in _chain(c, company, top["node"])] if top and top["node"] else []
    fields = wc.applicable_fields(c, company, top["cat"] if top else None, chain)
    for fdef in fields:
        cand = candidate_for_field(pages, fdef["key"], fdef["label"])
        if cand is None:
            continue
        raw, line = cand
        value, problems, mconf = _coerce_field(fdef, raw)
        c.execute(text("INSERT INTO metadata_suggestions (id, organization_id, company_id, extraction_id, field_id, value, confidence, evidence, problems) VALUES (:i, :o, :c, :e, :f, CAST(:v AS jsonb), :cf, CAST(:ev AS jsonb), CAST(:p AS jsonb)) ON CONFLICT (extraction_id, field_id) DO NOTHING"),
                  {"i": new_id(), "o": org, "c": company, "e": eid, "f": fdef["id"], "v": None if value is None else _j(value), "cf": Decimal(str(mconf)), "ev": _j([{"page": line.page, "text": line.text, "bbox": list(line.bbox)}]), "p": _j(problems)})  # fmt: skip


def _chain(c: Connection, company: uuid.UUID, node_id: Any) -> list[dict[str, Any]]:
    out = []
    cur = str(node_id)
    nodes = {
        str(r.id): r
        for r in c.execute(text("SELECT id, parent_id FROM workspace_nodes WHERE company_id = :c"), {"c": company})
    }
    while cur and cur in nodes:
        out.append({"id": cur})
        cur = str(nodes[cur].parent_id) if nodes[cur].parent_id else ""
    return out


def _coerce_field(fdef: dict[str, Any], raw: str) -> tuple[Any, list[str], float]:
    """Turn the text next to a label into a value of the configured type and validate it with the Phase 4 rules.
    Never invents a dropdown option or a reference record."""
    t = fdef["data_type"]
    problems: list[str] = []
    value: Any = raw.strip()
    if t == "reference":
        return None, ["Reference fields need a person to pick the record; nothing was guessed."], 0.0
    if t in ("dropdown", "multi_select"):
        opts = {o["value"].lower(): o["value"] for o in fdef.get("options", []) if o.get("archived_at") is None}
        opts.update({o["label"].lower(): o["value"] for o in fdef.get("options", []) if o.get("archived_at") is None})
        parts = [p.strip() for p in raw.split(",")] if t == "multi_select" else [raw.strip()]
        matched = [opts[p.lower()] for p in parts if p.lower() in opts]
        if len(matched) != len(parts):
            return None, [f"“{raw.strip()}” is not one of the configured options."], 0.3
        value = matched if t == "multi_select" else matched[0]
    elif t in ("number", "decimal", "currency"):
        dv, fl = parse_money(raw.split()[0] if raw.split() else raw)
        if dv is None:
            return None, ["The value is not a number."], 0.2
        value = format(dv, "f")
        problems += [f for f in fl if f != "negative"]
    elif t == "date":
        iso, fl = parse_date(raw)
        if iso is None:
            return None, ["The value is not a readable date."], 0.2
        value = iso
        problems += fl
    elif t == "boolean":
        low = raw.strip().lower()
        if low in ("yes", "true", "y", "نعم"):
            value = True
        elif low in ("no", "false", "n", "لا"):
            value = False
        else:
            return None, ["The value is not clearly yes or no."], 0.2
    from artify_api.workspace_engine import FieldDef

    fd = FieldDef(
        fdef["key"],
        fdef["label"],
        t,
        False,
        fdef["validation"],
        None,
        fdef["reference_target"],
        frozenset(o["value"] for o in fdef.get("options", []) if o.get("archived_at") is None),
    )
    try:
        value = validate_value(fd, value)
    except ConfigError as exc:
        return None, [str(exc)], 0.2
    return value, problems, 0.55 if problems else 0.7


# ---------------------------------------------------------------- reading results
def _doc_of_extraction(ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID) -> Any:
    e = ctx.conn.execute(
        text("SELECT * FROM extractions WHERE company_id = :c AND id = :i"), {"c": ctx.company_id, "i": eid}
    ).first()
    if e is None:
        raise NotFound("Extraction not found.")
    get_document(ctx, acc, e.document_id, action="view")
    return e


def latest_view(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID) -> dict[str, Any]:
    get_document(ctx, acc, doc_id, action="view")
    j = ctx.conn.execute(
        text("SELECT * FROM processing_jobs WHERE document_id = :d ORDER BY created_at DESC LIMIT 1"), {"d": doc_id}
    ).first()
    cls = ctx.conn.execute(
        text("SELECT * FROM document_classifications WHERE document_id = :d ORDER BY created_at DESC LIMIT 1"),
        {"d": doc_id},
    ).first()
    ex = ctx.conn.execute(
        text(
            "SELECT id FROM extractions WHERE document_id = :d AND status IN ('draft','approved') ORDER BY (status = 'draft') DESC, extraction_no DESC LIMIT 1"
        ),
        {"d": doc_id},
    ).first()
    return {
        "job": job_view(j) if j else None,
        "classification": _cls_view(cls) if cls else None,
        "extraction_id": str(ex.id) if ex else None,
    }


def _cls_view(r: Any) -> dict[str, Any]:
    d = ws.row_dict(r)
    d["confidence"] = float(d["confidence"])
    return {
        k: d[k]
        for k in (
            "id",
            "type_key",
            "kind_key",
            "confidence",
            "method",
            "evidence",
            "alternatives",
            "needs_manual",
            "provider_key",
            "model_version",
            "review_status",
            "created_at",
        )
    } | {"type_name": TYPES[d["type_key"]].name if d["type_key"] in TYPES else d["type_key"]}


def list_extractions(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID) -> list[dict[str, Any]]:
    get_document(ctx, acc, doc_id, action="view")
    return [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT id, extraction_no, version_id, type_key, schema_key, status, provider_key, model_version, prompt_version, summary, created_at, approved_at FROM extractions WHERE document_id = :d ORDER BY extraction_no DESC"
            ),
            {"d": doc_id},
        )
    ]


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def extraction_view(ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID) -> dict[str, Any]:
    e = _doc_of_extraction(ctx, acc, eid)
    c = ctx.conn
    fields = [
        ws.row_dict(r)
        for r in c.execute(
            text(
                "SELECT id, field_key, value, original_value, confidence, page_no, bbox, source_text, method, review_status, flags FROM extracted_fields WHERE extraction_id = :e"
            ),
            {"e": eid},
        )
    ]
    schema = SCHEMAS.get(e.schema_key)
    order = {f.key: i for i, f in enumerate(schema.fields)} if schema else {}
    labels = {f.key: (f.label, f.kind, f.required) for f in schema.fields} if schema else {}
    for f in fields:
        f["confidence"] = _f(f["confidence"])
        lab = labels.get(f["field_key"], (f["field_key"], "text", False))
        f["label"], f["kind"], f["required"] = lab
    fields.sort(key=lambda f: order.get(f["field_key"], 99))
    items = [
        ws.row_dict(r)
        for r in c.execute(
            text(
                'SELECT id, line_no, page_no, bbox, source_text, "values", confidence, review_status, flags FROM extracted_line_items WHERE extraction_id = :e ORDER BY line_no'
            ),
            {"e": eid},
        )
    ]
    for i in items:
        i["confidence"] = _f(i["confidence"])
    out: dict[str, Any] = {
        "id": str(e.id),
        "document_id": str(e.document_id),
        "version_id": str(e.version_id),
        "extraction_no": e.extraction_no,
        "type_key": e.type_key,
        "schema_key": e.schema_key,
        "schema_version": e.schema_version,
        "status": e.status,
        "provider_key": e.provider_key,
        "model_version": e.model_version,
        "prompt_version": e.prompt_version,
        "validation": e.validation,
        "summary": e.summary,
        "created_at": e.created_at,
        "approved_at": e.approved_at,
        "row_version": e.row_version,
        "fields": fields,
        "line_items": items,
        "line_fields": [
            {"key": f.key, "label": f.label, "kind": f.kind} for f in (schema.line_fields if schema else ())
        ],
        "classification": _cls_view(
            c.execute(text("SELECT * FROM document_classifications WHERE id = :i"), {"i": e.classification_id}).one()
        )
        if e.classification_id
        else None,
    }
    st = c.execute(text("SELECT * FROM bank_statements WHERE extraction_id = :e"), {"e": eid}).first()
    if st is not None:
        d = ws.row_dict(st)
        txs = [
            ws.row_dict(r)
            for r in c.execute(
                text(
                    "SELECT id, row_no, page_no, bbox, source_text, tx_date, value_date, description, reference, debit, credit, balance, currency, tx_type, confidence, review_status, manual, flags FROM bank_statement_transactions WHERE statement_id = :s ORDER BY row_no"
                ),
                {"s": st.id},
            )
        ]
        for t in txs:
            t["confidence"] = _f(t["confidence"])
            for k in ("debit", "credit", "balance"):
                t[k] = None if t[k] is None else format(t[k], "f")
        for k in ("opening_balance", "closing_balance"):
            d[k] = None if d[k] is None else format(st._mapping[k], "f")
        out["statement"] = {
            k: d[k]
            for k in (
                "id",
                "bank_name",
                "account_holder",
                "account_name",
                "account_number_masked",
                "iban_masked",
                "currency",
                "period_start",
                "period_end",
                "opening_balance",
                "closing_balance",
                "validation",
            )
        }
        out["transactions"] = txs
    ws_sug = []
    for r in c.execute(
        text(
            "SELECT s.*, n.name AS node_name, n.display_name AS node_display, n.archived_at AS node_archived, cat.name AS category_name FROM workspace_suggestions s LEFT JOIN workspace_nodes n ON n.id = s.node_id LEFT JOIN document_categories cat ON cat.id = s.category_id WHERE s.extraction_id = :e ORDER BY s.rank"
        ),
        {"e": eid},
    ):
        d = ws.row_dict(r)
        node = d["node_id"]
        allowed = bool(node) and node in acc.nodes and acc.can("upload", node) and d["node_archived"] is None
        ws_sug.append(
            {
                "id": d["id"],
                "rank": d["rank"],
                "node_id": node,
                "node_label": d["node_display"] or d["node_name"],
                "category_id": d["category_id"],
                "category_name": d["category_name"],
                "confidence": _f(d["confidence"]),
                "method": d["method"],
                "evidence": d["evidence"],
                "status": d["status"] if (allowed or not node) else "unavailable",
                "you_can_file_here": allowed,
            }
        )
    out["workspace_suggestions"] = ws_sug
    out["metadata_suggestions"] = [
        {**ws.row_dict(r), "confidence": _f(r.confidence)}
        for r in c.execute(
            text(
                "SELECT m.id, m.field_id, f.key AS field_key, f.label, m.value, m.confidence, m.evidence, m.problems, m.status FROM metadata_suggestions m JOIN custom_field_definitions f ON f.id = m.field_id WHERE m.extraction_id = :e"
            ),
            {"e": eid},
        )
    ]
    sp = c.execute(
        text("SELECT id, segments, evidence, status FROM split_proposals WHERE job_id = :j"), {"j": e.job_id}
    ).first()
    out["split_proposal"] = ws.row_dict(sp) if sp else None
    out["unresolved"] = _unresolved(c, eid)
    return out


def _unresolved(c: Connection, eid: Any) -> dict[str, int]:
    f = c.execute(
        text("SELECT count(*) FROM extracted_fields WHERE extraction_id = :e AND review_status = 'needs_review'"),
        {"e": eid},
    ).scalar_one()
    i = c.execute(
        text("SELECT count(*) FROM extracted_line_items WHERE extraction_id = :e AND review_status = 'needs_review'"),
        {"e": eid},
    ).scalar_one()
    t = c.execute(
        text(
            "SELECT count(*) FROM bank_statement_transactions x JOIN bank_statements s ON s.id = x.statement_id WHERE s.extraction_id = :e AND x.review_status = 'needs_review'"
        ),
        {"e": eid},
    ).scalar_one()
    return {"fields": int(f), "line_items": int(i), "transactions": int(t)}


def ocr_view(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, *, page_from: int, page_to: int, version: int | None = None
) -> dict[str, Any]:
    get_document(ctx, acc, doc_id, action="view")
    r = ctx.conn.execute(
        text(
            "SELECT r.* FROM ocr_results r JOIN document_versions v ON v.id = r.version_id WHERE r.document_id = :d AND (CAST(:n AS int) IS NULL OR v.version_no = :n) ORDER BY r.created_at DESC LIMIT 1"
        ),
        {"d": doc_id, "n": version},
    ).first()
    if r is None:
        raise NotFound("This document has not been read by OCR yet.")
    pages = []
    for p in ctx.conn.execute(
        text("SELECT * FROM ocr_pages WHERE ocr_result_id = :r AND page_no BETWEEN :a AND :b ORDER BY page_no"),
        {"r": r.id, "a": page_from, "b": page_to},
    ):
        blocks = [
            {"no": b.block_no, "text": b.text, "confidence": _f(b.confidence), "bbox": b.bbox}
            for b in ctx.conn.execute(
                text("SELECT block_no, text, confidence, bbox FROM ocr_blocks WHERE page_id = :p ORDER BY block_no"),
                {"p": p.id},
            )
        ]
        pages.append(
            {
                "page_no": p.page_no,
                "width": p.width,
                "height": p.height,
                "source": p.source,
                "is_blank": p.is_blank,
                "avg_confidence": _f(p.avg_confidence),
                "rotation_applied": p.rotation_applied,
                "deskew_angle": _f(p.deskew_angle),
                "quality": p.quality,
                "text": p.text,
                "blocks": blocks,
            }
        )
    return {
        "id": str(r.id),
        "job_id": str(r.job_id),
        "provider_key": r.provider_key,
        "engine_version": r.engine_version,
        "languages": r.languages,
        "page_count": r.page_count,
        "avg_confidence": _f(r.avg_confidence),
        "created_at": r.created_at,
        "pages": pages,
    }


def page_image(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, page_no: int) -> bytes:
    get_document(ctx, acc, doc_id, action="view")
    r = ctx.conn.execute(
        text(
            "SELECT so.storage_key FROM ocr_pages p JOIN ocr_results r ON r.id = p.ocr_result_id JOIN storage_objects so ON so.id = p.processed_object_id WHERE r.document_id = :d AND p.page_no = :n ORDER BY r.created_at DESC LIMIT 1"
        ),
        {"d": doc_id, "n": page_no},
    ).first()
    if r is None:
        raise NotFound("Page image not found.")
    try:
        return b"".join(storage.get_storage().open_range(r.storage_key))
    except storage.StorageError:
        raise NotFound("Page image not found.") from None


# ---------------------------------------------------------------- review (human actions only)
def _editable(ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID) -> Any:
    e = _doc_of_extraction(ctx, acc, eid)
    get_document(ctx, acc, e.document_id, action="edit")
    if e.status != "draft":
        raise ExtractionLocked(
            f"This extraction is {e.status}; reprocess the document to produce a new version to review."
        )
    return e


def _correction(
    ctx: RequestCtx,
    e: Any,
    target: str,
    target_id: Any,
    key: str | None,
    action: str,
    old: Any,
    new: Any,
    reason: str | None,
) -> None:
    assert ctx.org_id is not None and ctx.company_id is not None
    ctx.conn.execute(text("INSERT INTO field_corrections (id, organization_id, company_id, extraction_id, target_type, target_id, field_key, action, old_value, new_value, reason, actor_id) VALUES (:i, :o, :c, :e, :t, :ti, :k, :a, :ov, :nv, :r, :u)"),
                     {"i": new_id(), "o": ctx.org_id, "c": ctx.company_id, "e": e.id, "t": target, "ti": target_id, "k": key, "a": action, "ov": None if old is None else str(old), "nv": None if new is None else str(new), "r": reason, "u": ctx.user_id})  # fmt: skip
    log_event(
        ctx,
        e.document_id,
        f"extraction.{action}",
        also_audit=True,
        summary={"extraction_id": str(e.id), "target": target, "field": key},
    )


_ACTIONS = {"confirm": "confirmed", "reject": "rejected", "missing": "missing", "unreadable": "unreadable"}


def review_field(
    ctx: RequestCtx,
    acc: ws.Access,
    eid: uuid.UUID,
    field_key: str,
    *,
    action: str,
    value: str | None,
    reason: str | None,
) -> dict[str, Any]:
    e = _editable(ctx, acc, eid)
    f = ctx.conn.execute(
        text("SELECT * FROM extracted_fields WHERE extraction_id = :e AND field_key = :k FOR UPDATE"),
        {"e": eid, "k": field_key},
    ).first()
    if f is None:
        raise NotFound("Field not found.")
    schema = SCHEMAS[e.schema_key]
    kind = next((fd.kind for fd in schema.fields if fd.key == field_key), "text")
    new_value, status = f.value, f.review_status
    if action == "correct":
        if value is None or not value.strip():
            raise Unprocessable("A corrected value is required.")
        new_value = _normalize_value(kind, value)
        status = "corrected"
    elif action in _ACTIONS:
        status = _ACTIONS[action]
        if action == "confirm" and f.value is None:
            raise Conflict("There is no value to confirm; mark the field missing or enter a value.")
        if action in ("missing", "unreadable"):
            new_value = None
    else:
        raise Unprocessable("Unknown review action.")
    ctx.conn.execute(
        text(
            "UPDATE extracted_fields SET value = :v, review_status = :s, reviewed_by = :u, reviewed_at = now() WHERE id = :i"
        ),
        {"v": new_value, "s": status, "u": ctx.user_id, "i": f.id},
    )
    ctx.conn.execute(text("UPDATE extractions SET row_version = row_version + 1 WHERE id = :e"), {"e": eid})
    _correction(ctx, e, "field", f.id, field_key, action, f.value, new_value, reason)
    return next(x for x in extraction_view(ctx, acc, eid)["fields"] if x["field_key"] == field_key)


def _normalize_value(kind: str, value: str) -> str:
    v = value.strip()
    if kind == "money":
        d, fl = parse_money(v)
        if d is None:
            raise Unprocessable("The amount is not a valid number.")
        return format(-d if "negative" in fl else d, "f")
    if kind == "date":
        iso, _ = parse_date(v)
        if iso is None:
            raise Unprocessable("The date is not valid. Use YYYY-MM-DD.")
        return iso
    if kind == "currency":
        if not (len(v) == 3 and v.isalpha()):
            raise Unprocessable("A currency is a 3-letter code such as OMR.")
        return v.upper()
    if kind == "percent":
        d, _ = parse_money(v.rstrip("% "))
        if d is None:
            raise Unprocessable("The rate is not a valid number.")
        return format(d, "f")
    return v


def review_item(
    ctx: RequestCtx,
    acc: ws.Access,
    eid: uuid.UUID,
    item_id: uuid.UUID,
    *,
    action: str,
    values: dict[str, Any] | None,
    reason: str | None,
) -> dict[str, Any]:
    e = _editable(ctx, acc, eid)
    it = ctx.conn.execute(
        text(
            'SELECT id, "values", review_status FROM extracted_line_items WHERE id = :i AND extraction_id = :e FOR UPDATE'
        ),
        {"i": item_id, "e": eid},
    ).first()
    if it is None:
        raise NotFound("Line item not found.")
    new_vals, status = dict(it.values), it.review_status
    if action == "correct":
        schema = SCHEMAS[e.schema_key]
        allowed = {f.key: f.kind for f in schema.line_fields}
        for k, v in (values or {}).items():
            if k not in allowed:
                raise Unprocessable(f"Unknown line field '{k}'.")
            new_vals[k] = (
                None if v in (None, "") else _normalize_value("money" if allowed[k] == "money" else "text", str(v))
            )
        status = "corrected"
    elif action in _ACTIONS:
        status = _ACTIONS[action]
    else:
        raise Unprocessable("Unknown review action.")
    ctx.conn.execute(
        text('UPDATE extracted_line_items SET "values" = CAST(:v AS jsonb), review_status = :s WHERE id = :i'),
        {"v": _j(new_vals), "s": status, "i": item_id},
    )
    _correction(ctx, e, "line_item", item_id, None, action, _j(it.values), _j(new_vals), reason)
    return {"id": str(item_id), "values": new_vals, "review_status": status}


def review_transaction(
    ctx: RequestCtx,
    acc: ws.Access,
    eid: uuid.UUID,
    tx_id: uuid.UUID,
    *,
    action: str,
    patch: dict[str, Any] | None,
    reason: str | None,
) -> dict[str, Any]:
    e = _editable(ctx, acc, eid)
    t = ctx.conn.execute(
        text(
            "SELECT x.* FROM bank_statement_transactions x JOIN bank_statements s ON s.id = x.statement_id WHERE x.id = :i AND s.extraction_id = :e FOR UPDATE OF x"
        ),
        {"i": tx_id, "e": eid},
    ).first()
    if t is None:
        raise NotFound("Transaction not found.")
    sets, params, status = {}, {"i": tx_id}, t.review_status
    old = {
        k: (None if t._mapping[k] is None else str(t._mapping[k]))
        for k in ("tx_date", "value_date", "description", "reference", "debit", "credit", "balance")
    }
    if action == "correct":
        for k, v in (patch or {}).items():
            if k in ("debit", "credit", "balance"):
                sets[k] = None if v in (None, "") else _normalize_value("money", str(v))
            elif k in ("tx_date", "value_date"):
                sets[k] = None if v in (None, "") else _normalize_value("date", str(v))
            elif k in ("description", "reference"):
                sets[k] = None if v in (None, "") else str(v).strip()
            else:
                raise Unprocessable(f"Unknown transaction field '{k}'.")
        if sets.get("debit") not in (None, "") and sets.get("credit") not in (None, ""):
            raise Unprocessable("A transaction is either a debit or a credit, not both.")
        status = "corrected"
    elif action in _ACTIONS:
        status = _ACTIONS[action]
    else:
        raise Unprocessable("Unknown review action.")
    cols = ", ".join(f"{k} = :{k}" for k in sets)
    ctx.conn.execute(
        text(f"UPDATE bank_statement_transactions SET {cols + ', ' if cols else ''}review_status = :s WHERE id = :i"),
        {**params, **sets, "s": status},
    )
    _correction(ctx, e, "transaction", tx_id, None, action, _j(old), _j(sets), reason)
    _revalidate_statement(ctx, eid)
    return next(x for x in extraction_view(ctx, acc, eid)["transactions"] if x["id"] == str(tx_id))


def add_transaction(
    ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, *, after_row: int | None, data: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    e = _editable(ctx, acc, eid)
    st = ctx.conn.execute(text("SELECT id, currency FROM bank_statements WHERE extraction_id = :e"), {"e": eid}).first()
    if st is None:
        raise Conflict("This extraction is not a bank statement.")
    if data.get("debit") and data.get("credit"):
        raise Unprocessable("A transaction is either a debit or a credit, not both.")
    nxt = ctx.conn.execute(
        text("SELECT COALESCE(max(row_no), 0) + 1 FROM bank_statement_transactions WHERE statement_id = :s"),
        {"s": st.id},
    ).scalar_one()
    tid = new_id()
    ctx.conn.execute(text("INSERT INTO bank_statement_transactions (id, organization_id, company_id, statement_id, row_no, tx_date, value_date, description, reference, debit, credit, balance, currency, confidence, review_status, manual, flags) VALUES (:i, :o, :c, :s, :n, :td, :vd, :d, :r, :db, :cr, :b, :cu, 1, 'corrected', true, '[\"added_manually\"]'::jsonb)"),
                     {"i": tid, "o": ctx.org_id, "c": ctx.company_id, "s": st.id, "n": nxt, "td": _normalize_value("date", data["tx_date"]) if data.get("tx_date") else None, "vd": None, "d": (data.get("description") or "").strip(), "r": data.get("reference"),
                      "db": _normalize_value("money", str(data["debit"])) if data.get("debit") else None, "cr": _normalize_value("money", str(data["credit"])) if data.get("credit") else None, "b": _normalize_value("money", str(data["balance"])) if data.get("balance") else None, "cu": st.currency})  # fmt: skip
    _correction(ctx, e, "transaction", tid, None, "added", None, _j(data), reason)
    _ = after_row
    _revalidate_statement(ctx, eid)
    return next(x for x in extraction_view(ctx, acc, eid)["transactions"] if x["id"] == str(tid))


def _revalidate_statement(ctx: RequestCtx, eid: uuid.UUID) -> None:
    """Re-run the deterministic balance checks on the CURRENT (possibly corrected) rows."""
    from artify_api.doc_extract import Transaction

    st = ctx.conn.execute(text("SELECT * FROM bank_statements WHERE extraction_id = :e"), {"e": eid}).one()
    rows = ctx.conn.execute(
        text(
            "SELECT * FROM bank_statement_transactions WHERE statement_id = :s AND review_status NOT IN ('rejected', 'unreadable') ORDER BY row_no"
        ),
        {"s": st.id},
    ).all()
    txs = [
        Transaction(
            r.row_no,
            r.page_no,
            None,
            r.source_text or "",
            None,
            None,
            r.description or "",
            r.reference,
            r.debit,
            r.credit,
            r.balance,
            1.0,
            [],
        )
        for r in rows
    ]
    f = {
        "opening_balance": _Found(
            "opening_balance", None if st.opening_balance is None else format(st.opening_balance, "f"), 1.0
        ),
        "closing_balance": _Found(
            "closing_balance", None if st.closing_balance is None else format(st.closing_balance, "f"), 1.0
        ),
    }
    issues = validate_statement(f, txs)
    ctx.conn.execute(
        text("UPDATE bank_statements SET validation = CAST(:v AS jsonb) WHERE id = :i"),
        {"v": _j([asdict(i) for i in issues]), "i": st.id},
    )


def review_statement_header(
    ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, patch: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    """Opening/closing balances live in the field list; keep the statement row in step and re-validate."""
    st = ctx.conn.execute(text("SELECT id FROM bank_statements WHERE extraction_id = :e"), {"e": eid}).first()
    if st is None:
        raise Conflict("This extraction is not a bank statement.")
    for k in ("opening_balance", "closing_balance"):
        if k in patch:
            review_field(ctx, acc, eid, k, action="correct", value=str(patch[k]), reason=reason)
            ctx.conn.execute(
                text(f"UPDATE bank_statements SET {k} = :v WHERE id = :i"),
                {"v": _normalize_value("money", str(patch[k])), "i": st.id},
            )
    _revalidate_statement(ctx, eid)
    return extraction_view(ctx, acc, eid)


def review_classification(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, *, type_key: str | None, action: str, reason: str | None
) -> dict[str, Any]:
    get_document(ctx, acc, doc_id, action="edit")
    cls = ctx.conn.execute(
        text(
            "SELECT * FROM document_classifications WHERE document_id = :d ORDER BY created_at DESC LIMIT 1 FOR UPDATE"
        ),
        {"d": doc_id},
    ).first()
    if cls is None:
        raise NotFound("This document has not been classified yet.")
    if action == "confirm":
        ctx.conn.execute(
            text(
                "UPDATE document_classifications SET review_status = 'confirmed', needs_manual = false, reviewed_by = :u, reviewed_at = now() WHERE id = :i"
            ),
            {"u": ctx.user_id, "i": cls.id},
        )
        new = cls.type_key
    elif action == "correct":
        if not type_key or type_key not in TYPES or False:
            raise Unprocessable("Choose one of the known document types.")
        kind = TYPES[type_key].kind or cls.kind_key
        ctx.conn.execute(
            text(
                "UPDATE document_classifications SET type_key = :t, kind_key = :k, review_status = 'corrected', needs_manual = false, reviewed_by = :u, reviewed_at = now() WHERE id = :i"
            ),
            {"t": type_key, "k": kind, "u": ctx.user_id, "i": cls.id},
        )
        new = type_key
    elif action == "reject":
        ctx.conn.execute(
            text(
                "UPDATE document_classifications SET review_status = 'rejected', needs_manual = true, reviewed_by = :u, reviewed_at = now() WHERE id = :i"
            ),
            {"u": ctx.user_id, "i": cls.id},
        )
        new = cls.type_key
    else:
        raise Unprocessable("Unknown review action.")
    log_event(
        ctx,
        doc_id,
        f"classification.{action}",
        also_audit=True,
        summary={"from": cls.type_key, "to": new, "reason": reason},
    )
    return _cls_view(
        ctx.conn.execute(text("SELECT * FROM document_classifications WHERE id = :i"), {"i": cls.id}).one()
    )


def approve_extraction(
    ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, *, acknowledge: bool, note: str | None, actor_is_user: bool = True
) -> dict[str, Any]:
    """The only way an extraction becomes approved. Requires a user (never the system/AI), the documents.approve_extraction
    permission (route), `edit` on the document's node, and every flagged item resolved."""
    if not actor_is_user or ctx.user_id is None:
        raise Forbidden("Only a person can approve extracted data.")
    e = _editable(ctx, acc, eid)
    un = _unresolved(ctx.conn, eid)
    if any(un.values()):
        raise Conflict(
            "Resolve every item that needs review (confirm, correct, reject, or mark it missing/unreadable) before approving.",
            extra={"unresolved": un},
        )
    st = ctx.conn.execute(text("SELECT validation FROM bank_statements WHERE extraction_id = :e"), {"e": eid}).first()
    problems = [
        i for i in (st.validation if st else e.validation) if isinstance(i, dict) and i.get("severity") == "error"
    ]
    if problems and not acknowledge:
        raise Conflict(
            "The extraction has validation errors (for example totals or balances that do not add up). Review them, or approve explicitly with acknowledge=true and a note.",
            extra={"errors": [{"code": p["code"], "message": p["message"]} for p in problems]},
        )
    if problems and not (note and note.strip()):
        raise Unprocessable(
            "A note explaining why the extraction is approved despite the validation errors is required."
        )
    ctx.conn.execute(
        text(
            "UPDATE extracted_fields SET review_status = 'confirmed', reviewed_by = :u, reviewed_at = now() WHERE extraction_id = :e AND review_status = 'extracted'"
        ),
        {"u": ctx.user_id, "e": eid},
    )
    ctx.conn.execute(
        text(
            "UPDATE extracted_line_items SET review_status = 'confirmed' WHERE extraction_id = :e AND review_status = 'extracted'"
        ),
        {"e": eid},
    )
    ctx.conn.execute(
        text(
            "UPDATE bank_statement_transactions SET review_status = 'confirmed' WHERE review_status = 'extracted' AND statement_id IN (SELECT id FROM bank_statements WHERE extraction_id = :e)"
        ),
        {"e": eid},
    )
    ctx.conn.execute(
        text(
            "UPDATE extractions SET status = 'approved', approved_by = :u, approved_at = now(), row_version = row_version + 1 WHERE id = :e"
        ),
        {"u": ctx.user_id, "e": eid},
    )
    ctx.conn.execute(
        text(
            "UPDATE processing_jobs SET status = 'approved', updated_at = now() WHERE id = :j AND status = 'awaiting_review'"
        ),
        {"j": e.job_id},
    )
    _correction(ctx, e, "field", None, None, "approved", None, "approved", note)
    return extraction_view(ctx, acc, eid)


def reject_extraction(ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, reason: str | None) -> dict[str, Any]:
    e = _editable(ctx, acc, eid)
    ctx.conn.execute(
        text("UPDATE extractions SET status = 'rejected', row_version = row_version + 1 WHERE id = :e"), {"e": eid}
    )
    _correction(ctx, e, "field", None, None, "rejected", None, "rejected", reason)
    return extraction_view(ctx, acc, eid)


def accept_workspace(
    ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, suggestion_id: uuid.UUID, move: bool
) -> dict[str, Any]:
    """User-triggered filing: moves the document through the Phase 6 move operation (all its checks apply)."""
    from artify_api.documents import move_document

    e = _editable(ctx, acc, eid)
    s = ctx.conn.execute(
        text("SELECT * FROM workspace_suggestions WHERE id = :i AND extraction_id = :e"), {"i": suggestion_id, "e": eid}
    ).first()
    if s is None or s.node_id is None:
        raise NotFound("Suggestion not found or it names no destination.")
    if move:
        move_document(ctx, acc, e.document_id, s.node_id, s.category_id, None, s.category_id is not None)
    ctx.conn.execute(text("UPDATE workspace_suggestions SET status = 'accepted' WHERE id = :i"), {"i": suggestion_id})
    ctx.conn.execute(
        text(
            "UPDATE workspace_suggestions SET status = 'rejected' WHERE extraction_id = :e AND id <> :i AND status = 'suggested'"
        ),
        {"e": eid, "i": suggestion_id},
    )
    _correction(ctx, e, "workspace", suggestion_id, None, "workspace_accepted", None, str(s.node_id), None)
    return {"accepted": str(suggestion_id), "moved": move}


def accept_metadata(
    ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, suggestion_id: uuid.UUID, value: Any | None
) -> dict[str, Any]:
    from artify_api.documents import update_document

    e = _editable(ctx, acc, eid)
    m = ctx.conn.execute(
        text(
            "SELECT m.*, f.key FROM metadata_suggestions m JOIN custom_field_definitions f ON f.id = m.field_id WHERE m.id = :i AND m.extraction_id = :e"
        ),
        {"i": suggestion_id, "e": eid},
    ).first()
    if m is None:
        raise NotFound("Suggestion not found.")
    final = value if value is not None else m.value
    if final is None:
        raise Unprocessable("There is no valid suggested value; enter one.")
    try:
        update_document(ctx, acc, e.document_id, {"metadata": {m.key: final}})
    except Unprocessable as exc:
        if any(p.get("message") == "does not apply" for p in exc.extra.get("problems", [])):
            raise Conflict(
                "This field applies to the suggested category. File the document there first, then apply the value."
            ) from None
        raise
    ctx.conn.execute(text("UPDATE metadata_suggestions SET status = 'accepted' WHERE id = :i"), {"i": suggestion_id})
    _correction(ctx, e, "metadata", suggestion_id, m.key, "metadata_accepted", None, _j(final), None)
    return {"accepted": str(suggestion_id), "field": m.key, "value": final}


def decide_split(ctx: RequestCtx, acc: ws.Access, eid: uuid.UUID, approve: bool) -> dict[str, Any]:
    e = _doc_of_extraction(ctx, acc, eid)
    get_document(ctx, acc, e.document_id, action="edit")
    sp = ctx.conn.execute(text("SELECT id, status FROM split_proposals WHERE job_id = :j"), {"j": e.job_id}).first()
    if sp is None:
        raise NotFound("There is no split proposal for this document.")
    ctx.conn.execute(
        text("UPDATE split_proposals SET status = :s, decided_by = :u, decided_at = now() WHERE id = :i"),
        {"s": "approved" if approve else "rejected", "u": ctx.user_id, "i": sp.id},
    )
    log_event(
        ctx,
        e.document_id,
        "split.approved" if approve else "split.rejected",
        also_audit=True,
        summary={"extraction_id": str(eid)},
    )
    return {
        "status": "approved" if approve else "rejected",
        "executed": False,
        "note": "The decision is recorded. Creating separate documents from the pages is not part of this release; the original file is unchanged.",
    }


def review_queue(ctx: RequestCtx, acc: ws.Access, *, limit: int, offset: int) -> dict[str, Any]:
    """Documents with a draft extraction waiting for a person, newest first (only documents the caller may see)."""
    vis = [uuid.UUID(n) for n in acc.nodes if acc.visible(n)]
    base = "FROM extractions e JOIN documents d ON d.id = e.document_id WHERE e.company_id = :c AND e.status = 'draft' AND d.status = 'active' AND d.node_id = ANY(:vis)"
    p = {"c": ctx.company_id, "vis": vis}
    total = ctx.conn.execute(text(f"SELECT count(*) {base}"), p).scalar_one()
    rows = ctx.conn.execute(
        text(
            f"SELECT e.id, e.document_id, d.display_name, e.type_key, e.summary, e.created_at, (SELECT confidence FROM document_classifications WHERE id = e.classification_id) AS confidence {base} ORDER BY e.created_at DESC LIMIT :l OFFSET :o"
        ),
        {**p, "l": limit, "o": offset},
    ).all()
    items = []
    for r in rows:
        d = ws.row_dict(r)
        d["confidence"] = _f(d["confidence"])
        d["type_name"] = TYPES[d["type_key"]].name if d["type_key"] in TYPES else d["type_key"]
        items.append(d)
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


# ---------------------------------------------------------------- Control Center
def provider_status(settings: Any = None) -> list[dict[str, Any]]:
    """Safe-to-show state of every provider (no secrets, no URLs with credentials)."""
    s = settings or get_settings()
    t = TesseractProvider(s.tesseract_cmd, s.ocr_timeout_seconds)
    langs: list[str] = []
    ver = None
    if t.available():
        try:
            ver, langs = t.version(), sorted(t.installed_languages() - {"osd"})
        except Exception:  # a broken install is reported as unavailable below
            ver = None
    http_ok = bool(s.ai_http_base_url and s.ai_http_model and s.ai_http_api_key)
    return [
        {"kind": "ocr", "key": "tesseract", "external": False, "configured": ver is not None, "active_default": s.ocr_provider == "tesseract", "version": ver, "languages": langs,
         "note": None if ver else "The Tesseract OCR engine is not installed on this server."},
        {"kind": "understanding", "key": "rules", "external": False, "configured": True, "active_default": s.understanding_provider == "rules", "version": RulesProvider.model_version, "languages": ["eng", "ara"], "note": "Deterministic local rules; not a language model."},
        {"kind": "understanding", "key": "http_json", "external": True, "configured": http_ok, "active_default": s.understanding_provider in ("http", "http_json"), "version": s.ai_http_model if http_ok else None, "languages": [],
         "note": None if http_ok else "No AI provider is configured on this server (ARTIFY_AI_HTTP_BASE_URL, ARTIFY_AI_HTTP_MODEL, ARTIFY_AI_HTTP_API_KEY)."},
    ]  # fmt: skip


def overview(ctx: RequestCtx) -> dict[str, Any]:
    assert ctx.org_id is not None
    c, org = ctx.conn, ctx.org_id
    by_status = {
        r.status: int(r.n)
        for r in c.execute(
            text(
                "SELECT status, count(*) AS n FROM processing_jobs WHERE organization_id = :o AND created_at > now() - interval '30 days' GROUP BY status"
            ),
            {"o": org},
        )
    }
    awaiting = [
        ws.row_dict(r)
        for r in c.execute(
            text(
                "SELECT j.id, j.document_id, d.display_name, co.name AS company_name, j.company_id, j.finished_at FROM processing_jobs j JOIN documents d ON d.id = j.document_id JOIN companies co ON co.id = j.company_id WHERE j.status = 'awaiting_review' ORDER BY j.finished_at LIMIT 20"
            )
        )
    ]
    failed = [
        ws.row_dict(r)
        for r in c.execute(
            text(
                "SELECT j.id, j.document_id, d.display_name, co.name AS company_name, j.company_id, j.error_code, j.error_message, j.updated_at, j.attempt_count FROM processing_jobs j JOIN documents d ON d.id = j.document_id JOIN companies co ON co.id = j.company_id WHERE j.status IN ('failed','needs_manual') AND j.updated_at > now() - interval '30 days' ORDER BY j.updated_at DESC LIMIT 20"
            )
        )
    ]
    per_company = [
        ws.row_dict(r)
        for r in c.execute(
            text(
                "SELECT co.id AS company_id, co.name, count(j.id) FILTER (WHERE j.status = 'awaiting_review') AS awaiting_review, count(j.id) FILTER (WHERE j.status IN ('failed','needs_manual')) AS failed, count(j.id) AS jobs_30d FROM companies co LEFT JOIN processing_jobs j ON j.company_id = co.id AND j.created_at > now() - interval '30 days' WHERE co.status <> 'archived' GROUP BY co.id, co.name ORDER BY co.name"
            )
        )
    ]
    usage = []
    for key in ("quota.ocr_pages_per_month", "quota.ai_extractions_per_month", "quota.bank_statements_per_month"):
        lim = entitlements.effective_limit(c, org, key)
        usage.append(
            {
                "key": key,
                "label": entitlements.BY_KEY[key].description,
                "used": entitlements.measure(c, org, key),
                "limit": None if lim.unlimited else lim.value,
                "unlimited": lim.unlimited,
            }
        )
    return {
        "jobs_by_status": by_status,
        "awaiting_review": awaiting,
        "failed": failed,
        "companies": per_company,
        "usage": usage,
        "providers": provider_status(),
        "window_days": 30,
    }


def set_provider_config(
    ctx: RequestCtx, *, company_id: uuid.UUID | None, kind: str, provider_key: str, enabled: bool, allow_external: bool
) -> dict[str, Any]:
    assert ctx.org_id is not None
    if kind == "ocr" and provider_key != "tesseract":
        raise Unprocessable("Only the local Tesseract OCR provider is available in this release.")
    if kind == "understanding" and provider_key not in ("rules", "http_json"):
        raise Unprocessable("Unknown provider.")
    if provider_key == "http_json" and allow_external:
        s = get_settings()
        if not (s.ai_http_base_url and s.ai_http_model and s.ai_http_api_key):
            raise Conflict(
                "No external AI provider is configured on this server, so consent cannot be recorded for it."
            )
    if (
        company_id is not None
        and ctx.conn.execute(text("SELECT 1 FROM companies WHERE id = :c"), {"c": company_id}).first() is None
    ):
        raise NotFound("Company not found.")
    ctx.conn.execute(text("INSERT INTO ai_provider_configurations (id, organization_id, company_id, kind, provider_key, enabled, allow_external_content, updated_by) VALUES (:i, :o, :c, :k, :p, :e, :a, :u) ON CONFLICT (organization_id, company_id, kind) DO UPDATE SET provider_key = EXCLUDED.provider_key, enabled = EXCLUDED.enabled, allow_external_content = EXCLUDED.allow_external_content, updated_by = EXCLUDED.updated_by, updated_at = now()"),
                     {"i": new_id(), "o": ctx.org_id, "c": company_id, "k": kind, "p": provider_key, "e": enabled, "a": allow_external, "u": ctx.user_id})  # fmt: skip
    audit.write_audit(
        ctx.conn,
        action="ai_provider.configured",
        actor_type="user",
        actor_id=ctx.user_id,
        org_id=ctx.org_id,
        company_id=company_id,
        entity_type="ai_provider",
        summary={"kind": kind, "provider": provider_key, "enabled": enabled, "allow_external_content": allow_external},
        meta=ctx.meta,
    )
    return {
        "kind": kind,
        "provider_key": provider_key,
        "enabled": enabled,
        "allow_external_content": allow_external,
        "company_id": str(company_id) if company_id else None,
    }


def provider_configs(ctx: RequestCtx) -> list[dict[str, Any]]:
    return [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT kind, provider_key, enabled, allow_external_content, company_id, updated_at FROM ai_provider_configurations ORDER BY company_id NULLS FIRST, kind"
            )
        )
    ]


_ = (contextlib, doc_access, _Found)
