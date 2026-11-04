"""Document management engine (Phase 6): resumable uploads, verified storage, documents, versions, search.

Principles (docs/documents/): the database holds metadata and an opaque storage key, never bytes; a document's identity is
its id, its location is a workspace NODE ID (never a name or path); nothing is reported stored until the object exists
with the verified size and SHA-256; original binaries are immutable and every version is kept.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from artify_api import audit, entitlements, outbox
from artify_api import workspace as ws
from artify_api import workspace_config as wc
from artify_api.errors import ApiError, NotFound
from artify_api.file_validation import TYPES, canonical_extension, inspect_file
from artify_api.ids import new_id
from artify_api.scanner import ScanUnavailable, get_scanner
from artify_api.settings import get_settings
from artify_api.storage import StorageError, get_storage
from artify_api.workspace_engine import (
    ConfigError,
    ensure_unique_name,
    evaluate_rules,
    parse_pattern,
    render_name,
    validate_values,
)

FAMILIES: dict[str, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "image": ("jpg", "png", "tif", "bmp", "heic"),
    "excel": ("xls", "xlsx", "csv"),
    "word": ("doc", "docx"),
    "text": ("txt", "csv"),
}
RETRYABLE = {"storage_error", "scan_unavailable", "duplicate_detected", "target_unavailable", "size_mismatch"}
OPEN = ("open", "uploading")


class DocumentError(ApiError):
    """A structured document-domain error; `code` is stable for clients."""

    def __init__(self, status: int, code: str, detail: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail, extra=extra)
        self.status, self.code, self.title = status, code, detail


@dataclass
class Env:
    conn: Connection
    org_id: uuid.UUID
    company_id: uuid.UUID
    user_id: uuid.UUID
    meta: audit.RequestMeta
    acc: ws.Access
    org_role: str | None = None
    can_purge: bool = False


@dataclass(frozen=True)
class Limits:
    max_file_size: int
    batch_max_files: int
    chunk_max_bytes: int
    max_concurrent: int
    allowed_extensions: list[str]


def effective_limits(conn: Connection, org_id: uuid.UUID) -> Limits:
    """Central settings are the ceiling; a plan entitlement can only LOWER them (never raise)."""
    s = get_settings()

    def lowered(key: str, ceiling: int) -> int:
        lim = entitlements.effective_limit(conn, org_id, key)
        if lim.source == "default" or lim.unlimited or lim.value is None:
            return ceiling
        return min(ceiling, lim.value)

    return Limits(
        max_file_size=lowered("limit.max_file_size_bytes", s.max_upload_bytes),
        batch_max_files=lowered("limit.import_batch_files", s.import_batch_max_files),
        chunk_max_bytes=s.upload_chunk_max_bytes,
        max_concurrent=s.max_concurrent_uploads,
        allowed_extensions=sorted({canonical_extension("x." + e) for e in s.allowed_upload_extensions} & set(TYPES)),
    )


# ---------------------------------------------------------------- small helpers
def clean_filename(raw: str) -> str:
    """Last path segment only, NFC-normalised, no control characters. The result is only ever DISPLAYED or used to pick an
    extension; it is never used to build a storage or local path."""
    name = unicodedata.normalize("NFC", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not name or name in (".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in name) or len(name) > 255:
        raise DocumentError(422, "invalid_filename", "The file name is not valid.")
    return name


def node_path(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID) -> str:
    rows = conn.execute(
        text(
            "SELECT n.name FROM workspace_nodes t, unnest(t.path_ids) WITH ORDINALITY AS p(id, ord)"
            " JOIN workspace_nodes n ON n.id = p.id WHERE t.id = :n AND t.company_id = :c ORDER BY p.ord"
        ),
        {"n": node_id, "c": company_id},
    )
    return "/".join(r[0] for r in rows)


def _live_node(env: Env, node_id: uuid.UUID) -> dict[str, Any]:
    row = env.conn.execute(
        text("SELECT id, archived_at, is_active, node_type FROM workspace_nodes WHERE company_id = :c AND id = :n"),
        {"c": env.company_id, "n": node_id},
    ).first()
    if row is None or not env.acc.visible(str(node_id)):
        raise NotFound("Workspace item not found.")
    if row.archived_at is not None or not row.is_active:
        raise DocumentError(
            409,
            "target_unavailable",
            "That folder is archived or inactive. Choose another folder (the workspace may have changed).",
        )
    return dict(row._mapping)


def _event(
    env: Env,
    doc_id: uuid.UUID,
    event: str,
    *,
    version_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    audit_action: str | None = None,
) -> None:
    import json

    env.conn.execute(
        text(
            "INSERT INTO document_events (id, organization_id, company_id, document_id, version_id, event_type, actor_id, detail)"
            " VALUES (:id, :o, :c, :d, :v, :e, :a, CAST(:det AS jsonb))"
        ),
        {
            "id": new_id(),
            "o": env.org_id,
            "c": env.company_id,
            "d": doc_id,
            "v": version_id,
            "e": event,
            "a": env.user_id,
            "det": json.dumps(detail or {}, default=str),
        },
    )
    if audit_action:
        audit.write_audit(
            env.conn,
            action=audit_action,
            actor_id=env.user_id,
            org_id=env.org_id,
            company_id=env.company_id,
            entity_type="document",
            entity_id=doc_id,
            summary={k: v for k, v in (detail or {}).items() if k in ("from", "to", "version", "reason", "fields")},
            meta=env.meta,
        )


DOC_SELECT = (
    "SELECT d.id, d.company_id, d.node_id, d.category_id, d.display_name, d.original_filename, d.extension, d.status,"
    " d.current_version_id, d.current_version_number, d.source, d.kind_at_filing, d.filed_path_snapshot, d.filing_rule_id,"
    " d.legal_hold, d.created_by, cu.display_name AS created_by_name, d.created_at, d.updated_at, d.archived_at,"
    " d.deleted_at, d.purged_at, d.row_version, v.mime_type, v.size_bytes, v.sha256, v.scan_status,"
    " (SELECT max(version_number) FROM document_versions WHERE document_id = d.id) AS latest_version_number"
    " FROM documents d JOIN document_versions v ON v.id = d.current_version_id LEFT JOIN users cu ON cu.id = d.created_by"
)


def get_document(env: Env, doc_id: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    """Load a document the caller may VIEW. Everything else (other company, hidden node, unknown id) is a 404."""
    row = env.conn.execute(
        text(DOC_SELECT + " WHERE d.id = :i AND d.company_id = :c" + (" FOR UPDATE OF d" if lock else "")),
        {"i": doc_id, "c": env.company_id},
    ).first()
    if row is None or not env.acc.visible(str(row.node_id)):
        raise NotFound("Document not found.")
    doc = dict(row._mapping)
    if doc["status"] == "purged":
        raise NotFound("Document not found.")
    return doc


def _can_flags(env: Env, doc: dict[str, Any]) -> dict[str, bool]:
    n = str(doc["node_id"])
    contribute = env.acc.can("upload", n) and doc["status"] in ("active", "archived")
    return {
        "view": True,
        "download": doc["scan_status"] != "infected",
        "edit": contribute and doc["status"] == "active",
        "move": contribute and doc["status"] == "active",
        "archive": contribute,
        "delete": env.acc.can("delete", n) and doc["status"] in ("active", "archived"),
        "purge": env.can_purge and doc["status"] == "deleted" and not doc["legal_hold"],
    }


def document_out(env: Env, doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["can"] = _can_flags(env, doc)
    return out


# ---------------------------------------------------------------- duplicates (hash based, company scoped)
def find_duplicate(env: Env, sha256: str, *, exclude_document: uuid.UUID | None = None) -> dict[str, Any] | None:
    """An existing, non-purged version with the same content IN THIS COMPANY. The reference is returned only when the
    caller may view the document's folder; otherwise the caller learns only that a duplicate exists."""
    row = env.conn.execute(
        text(
            "SELECT d.id, d.display_name, d.node_id, d.status, d.current_version_number FROM document_versions v"
            " JOIN documents d ON d.id = v.document_id WHERE v.company_id = :c AND v.sha256 = :h AND v.purged_at IS NULL"
            " AND d.status IN ('active', 'archived') AND (CAST(:x AS uuid) IS NULL OR d.id <> CAST(:x AS uuid))"
            " ORDER BY d.created_at LIMIT 1"
        ),
        {"c": env.company_id, "h": sha256, "x": exclude_document},
    ).first()
    if row is None:
        return None
    visible = env.acc.visible(str(row.node_id))
    return {
        "exists": True,
        "document": (
            {
                "id": str(row.id),
                "display_name": row.display_name,
                "status": row.status,
                "node_id": str(row.node_id),
                "path": node_path(env.conn, env.company_id, row.node_id),
            }
            if visible
            else None
        ),
    }


# ---------------------------------------------------------------- suggestions: filing rule + naming rule (no AI, no guessing)
def _naming_pattern(env: Env, category_id: uuid.UUID | None) -> str | None:
    q = (
        "SELECT pattern FROM naming_rules WHERE company_id = :c AND archived_at IS NULL AND is_active"
        " AND category_id IS NOT DISTINCT FROM CAST(:cat AS uuid)"
    )
    row = env.conn.execute(text(q), {"c": env.company_id, "cat": category_id}).first()
    if row is None and category_id is not None:
        row = env.conn.execute(text(q), {"c": env.company_id, "cat": None}).first()
    return row.pattern if row else None


def suggest(
    env: Env,
    *,
    filename: str,
    category_id: uuid.UUID | None,
    node_id: uuid.UUID | None,
    source: str,
    field_values: dict[str, Any],
    document_date: str | None,
) -> dict[str, Any]:
    """Filing and naming suggestions from CONFIGURED rules and values the user supplied. Missing information stays missing:
    a name is suggested only when every token has a value."""
    ext = canonical_extension(filename)
    attrs: dict[str, Any] = {"source": source, "filename": filename}
    cat = None
    if category_id:
        cat = env.conn.execute(
            text(
                "SELECT id, name, document_kind, node_id FROM document_categories WHERE id = :i AND company_id = :c AND archived_at IS NULL AND is_active"
            ),
            {"i": category_id, "c": env.company_id},
        ).first()
        if cat is None:
            raise DocumentError(422, "invalid_category", "That category is not available in this company.")
        attrs.update({"category_id": str(cat.id), "document_kind": cat.document_kind})
    for k, v in field_values.items():
        attrs[f"custom:{k}"] = v
    winner, hits = evaluate_rules(wc.rule_defs(env.conn, env.company_id), attrs)
    filing: dict[str, Any] = {"rule": None, "matches": [], "target_node_id": None, "category_id": None, "path": None}
    for r in hits:
        if env.acc.can("upload", str(r.target_node_id)) and env.acc.visible(str(r.target_node_id)):
            filing["matches"].append({"rule_id": str(r.id), "name": r.name, "priority": r.priority})
    if winner and env.acc.can("upload", str(winner.target_node_id)):
        filing.update(
            rule={"id": str(winner.id), "name": winner.name, "priority": winner.priority},
            target_node_id=str(winner.target_node_id),
            category_id=str(winner.target_category_id) if winner.target_category_id else None,
            path=node_path(env.conn, env.company_id, winner.target_node_id),
        )
    # naming
    pattern = _naming_pattern(env, category_id)
    naming: dict[str, Any] = {"pattern": pattern, "name": None, "warnings": [], "complete": False}
    if pattern:
        co = env.conn.execute(
            text("SELECT name, code, financial_year_start_month FROM companies WHERE id = :c"), {"c": env.company_id}
        ).one()
        values = {**field_values, "company": co.name, "companycode": co.code}
        if cat:
            values.update({"category": cat.name, "documenttype": cat.document_kind})
        if document_date:
            values["documentdate"] = document_date
        try:
            parts = parse_pattern(pattern, wc.active_field_keys(env.conn, env.company_id))
            name, warnings = render_name(
                parts, values, extension=ext, financial_year_start_month=co.financial_year_start_month
            )
        except ConfigError as exc:
            raise DocumentError(422, "invalid_naming_rule", str(exc)) from exc
        naming.update(name=name, warnings=warnings, complete=not warnings)
    fields = []
    if node_id or category_id:
        chain = [str(i) for i in env.acc.chain(str(node_id))] if node_id else []
        fields = wc.applicable_fields(env.conn, env.company_id, category_id, chain)
    return {"filing": filing, "naming": naming, "fields": fields, "original_filename": filename}


# ---------------------------------------------------------------- metadata
def _field_defs(env: Env, category_id: uuid.UUID | None, node_id: uuid.UUID) -> list[Any]:
    chain = [str(i) for i in env.acc.chain(str(node_id))]
    return [wc.to_field_def(f) for f in wc.applicable_fields(env.conn, env.company_id, category_id, chain)]


def validate_metadata(
    env: Env, category_id: uuid.UUID | None, node_id: uuid.UUID, values: dict[str, Any], *, require_complete: bool
) -> dict[str, Any]:
    defs = _field_defs(env, category_id, node_id)
    if not require_complete:  # early feedback: validate what was given, do not demand the rest yet
        defs = [d for d in defs if d.key in values]
    out, problems = validate_values(defs, values, wc.ref_exists_fn(env.conn, env.company_id))
    if problems:
        raise DocumentError(422, "invalid_metadata", "Some metadata is not valid.", {"problems": problems})
    return out


def _store_field_values(
    env: Env, doc_id: uuid.UUID, category_id: uuid.UUID | None, node_id: uuid.UUID, values: dict[str, Any]
) -> dict[str, Any]:
    import json
    from decimal import Decimal

    defs = {
        f["key"]: f
        for f in wc.applicable_fields(
            env.conn, env.company_id, category_id, [str(i) for i in env.acc.chain(str(node_id))]
        )
    }
    before = {
        r.field_key: r.value
        for r in env.conn.execute(
            text("SELECT field_key, value FROM document_field_values WHERE document_id = :d"), {"d": doc_id}
        )
    }
    for key, value in values.items():
        f = defs[key]
        t = f["data_type"]
        v_text = v_num = v_date = None
        if t in ("number", "decimal"):
            v_num = Decimal(str(value))
        elif t == "currency":
            v_num = Decimal(str(value["amount"]))
            v_text = value.get("currency")
        elif t == "date":
            v_date = datetime.fromisoformat(str(value)).date() if isinstance(value, str) else None
        elif isinstance(value, list):
            v_text = ", ".join(str(x) for x in value)
        elif isinstance(value, str):
            v_text = value
        elif isinstance(value, bool):
            v_text = "true" if value else "false"
        env.conn.execute(
            text(
                "INSERT INTO document_field_values (id, organization_id, company_id, document_id, field_id, field_key, data_type,"
                " value, value_text, value_number, value_date, updated_by) VALUES (:id, :o, :c, :d, :f, :k, :t, CAST(:v AS jsonb),"
                " :vt, :vn, :vd, :u) ON CONFLICT (document_id, field_id) DO UPDATE SET value = EXCLUDED.value,"
                " value_text = EXCLUDED.value_text, value_number = EXCLUDED.value_number, value_date = EXCLUDED.value_date,"
                " updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {
                "id": new_id(),
                "o": env.org_id,
                "c": env.company_id,
                "d": doc_id,
                "f": uuid.UUID(f["id"]),
                "k": key,
                "t": t,
                "v": json.dumps(value),
                "vt": v_text,
                "vn": v_num,
                "vd": v_date,
                "u": env.user_id,
            },
        )
    return {k: {"from": before.get(k), "to": v} for k, v in values.items() if before.get(k) != v}


def field_values_of(conn: Connection, doc_id: uuid.UUID) -> dict[str, Any]:
    return {
        r.field_key: r.value
        for r in conn.execute(
            text("SELECT field_key, value FROM document_field_values WHERE document_id = :d"), {"d": doc_id}
        )
    }


# ---------------------------------------------------------------- upload sessions
SESSION_COLS = (
    "id, company_id, batch_id, node_id, category_id, document_id, original_filename, declared_size, declared_sha256,"
    " received_bytes, status, source, display_name, field_values, change_reason, duplicate_policy, filing_rule_id,"
    " result_document_id, result_version_id, error_code, error_message, expires_at, created_at, updated_at"
)


def _session(env: Env, session_id: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    row = env.conn.execute(
        text(
            f"SELECT {SESSION_COLS}, staging_ref, created_by FROM document_upload_sessions WHERE id = :i AND company_id = :c"
            + (" FOR UPDATE" if lock else "")
        ),
        {"i": session_id, "c": env.company_id},
    ).first()
    if row is None or row.created_by != env.user_id:  # an upload belongs to the person who started it
        raise NotFound("Upload not found.")
    return dict(row._mapping)


def session_out(s: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in s.items() if k not in ("staging_ref", "created_by")}


def init_upload(
    env: Env,
    *,
    idem_key: str,
    filename: str,
    size: int,
    sha256: str | None,
    node_id: uuid.UUID | None,
    category_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    batch_id: uuid.UUID | None,
    source: str,
    display_name: str | None,
    field_values: dict[str, Any],
    change_reason: str | None,
    duplicate_policy: str,
    filing_rule_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
) -> tuple[dict[str, Any], bool]:
    import json

    filename = clean_filename(filename)
    sha256 = sha256.lower() if sha256 else None
    existing = env.conn.execute(
        text(
            f"SELECT {SESSION_COLS}, staging_ref, created_by FROM document_upload_sessions"
            " WHERE company_id = :c AND created_by = :u AND idempotency_key = :k"
        ),
        {"c": env.company_id, "u": env.user_id, "k": idem_key},
    ).first()
    if existing is not None:
        if (existing.original_filename, existing.declared_size, existing.declared_sha256) != (filename, size, sha256):
            raise DocumentError(409, "idempotency_mismatch", "This Idempotency-Key was used for a different file.")
        return dict(existing._mapping), False
    limits = effective_limits(env.conn, env.org_id)
    ext = canonical_extension(filename)
    if ext not in limits.allowed_extensions:
        raise DocumentError(
            422,
            "unsupported_type",
            f"Files of this type are not accepted (.{ext or 'none'}).",
            {"allowed": limits.allowed_extensions},
        )
    if size <= 0:
        raise DocumentError(422, "empty_file", "The file is empty.")
    if size > limits.max_file_size:
        raise DocumentError(
            413,
            "file_too_large",
            "The file is larger than your plan allows.",
            {"limit": limits.max_file_size, "size": size},
        )
    open_n = env.conn.execute(
        text("SELECT count(*) FROM document_upload_sessions WHERE created_by = :u AND status IN ('open', 'uploading')"),
        {"u": env.user_id},
    ).scalar_one()
    if open_n >= get_settings().open_upload_sessions_per_user:
        raise DocumentError(429, "too_many_uploads", "Too many uploads are in progress; finish or cancel some first.")
    if document_id is not None:  # a new version of an existing document
        doc = get_document(env, document_id)
        env.acc.require("upload", str(doc["node_id"]))
        if doc["status"] != "active":
            raise DocumentError(409, "document_not_active", "Only an active document can receive a new version.")
        if ext != doc["extension"]:
            raise DocumentError(422, "extension_changed", f"A new version must keep the .{doc['extension']} type.")
        node_id, category_id = doc["node_id"], doc["category_id"]
        if sha256 and sha256 == doc["sha256"]:
            raise DocumentError(409, "identical_to_current_version", "This file is identical to the current version.")
    else:
        if node_id is None:
            raise DocumentError(422, "node_required", "Choose where to file the document.")
        _live_node(env, node_id)
        env.acc.require("upload", str(node_id))
        if sha256 and duplicate_policy == "block":
            dup = find_duplicate(env, sha256)
            if dup:
                raise DocumentError(
                    409, "duplicate_detected", "This exact file is already stored in this company.", dup
                )
    cat_ext_check(env, category_id, node_id, ext)
    if field_values and node_id is not None:
        field_values = validate_metadata(env, category_id, node_id, field_values, require_complete=False)
    if (
        batch_id is not None
        and not env.conn.execute(
            text("SELECT 1 FROM document_import_batches WHERE id = :b AND company_id = :c"),
            {"b": batch_id, "c": env.company_id},
        ).first()
    ):
        raise NotFound("Import batch not found.")
    if (
        filing_rule_id is not None
        and not env.conn.execute(
            text("SELECT 1 FROM filing_rules WHERE id = :r AND company_id = :c AND target_node_id = :n"),
            {"r": filing_rule_id, "c": env.company_id, "n": node_id},
        ).first()
    ):
        filing_rule_id = None  # only a rule that really targets the chosen folder may be recorded
    entitlements.guard_amount(env.conn, env.org_id, "quota.storage_bytes", size)
    sid = new_id()
    env.conn.execute(
        text(
            "INSERT INTO document_upload_sessions (id, organization_id, company_id, batch_id, created_by, device_id, node_id,"
            " category_id, document_id, original_filename, declared_size, declared_sha256, staging_ref, source, display_name,"
            " field_values, change_reason, duplicate_policy, filing_rule_id, idempotency_key, expires_at)"
            " VALUES (:id, :o, :c, :b, :u, :dev, :n, :cat, :doc, :fn, :sz, :h, :st, :src, :dn, CAST(:fv AS jsonb), :cr, :dp, :fr, :ik, :ex)"
        ),
        {
            "id": sid,
            "o": env.org_id,
            "c": env.company_id,
            "b": batch_id,
            "u": env.user_id,
            "dev": device_id,
            "n": node_id,
            "cat": category_id,
            "doc": document_id,
            "fn": filename,
            "sz": size,
            "h": sha256,
            "st": uuid.uuid4().hex,
            "src": source,
            "dn": display_name,
            "fv": json.dumps(field_values),
            "cr": change_reason,
            "dp": duplicate_policy,
            "fr": filing_rule_id,
            "ik": idem_key,
            "ex": datetime.now(UTC) + timedelta(seconds=get_settings().upload_session_ttl_seconds),
        },
    )
    return _session(env, sid), True


def cat_ext_check(env: Env, category_id: uuid.UUID | None, node_id: uuid.UUID | None, ext: str) -> None:
    if category_id is None:
        return
    cat = env.conn.execute(
        text(
            "SELECT node_id, allowed_file_types FROM document_categories WHERE id = :i AND company_id = :c AND archived_at IS NULL AND is_active"
        ),
        {"i": category_id, "c": env.company_id},
    ).first()
    if cat is None:
        raise DocumentError(422, "invalid_category", "That category is not available in this company.")
    if cat.allowed_file_types and ext not in {canonical_extension("x." + t) for t in cat.allowed_file_types}:
        raise DocumentError(422, "type_not_allowed_for_category", f"This category does not accept .{ext} files.")
    if (
        cat.node_id is not None
        and node_id is not None
        and str(cat.node_id) not in [str(i) for i in env.acc.chain(str(node_id))]
    ):
        raise DocumentError(422, "category_not_in_folder", "That category belongs to a different folder.")


def update_upload(env: Env, session_id: uuid.UUID, patch: dict[str, Any]) -> dict[str, Any]:
    """Correct the destination, category, name, metadata or duplicate decision before the upload is finalised."""
    import json

    s = _session(env, session_id, lock=True)
    if s["status"] not in (*OPEN, "failed"):
        raise DocumentError(409, "session_closed", "This upload can no longer be changed.")
    node_id = patch.get("node_id", s["node_id"])
    category_id = patch["category_id"] if "category_id" in patch else s["category_id"]
    if s["document_id"] is None:
        _live_node(env, node_id)
        env.acc.require("upload", str(node_id))
    cat_ext_check(env, category_id, node_id, canonical_extension(s["original_filename"]))
    fv = patch.get("field_values", s["field_values"])
    if "field_values" in patch:
        fv = validate_metadata(env, category_id, node_id, fv, require_complete=False)
    env.conn.execute(
        text(
            "UPDATE document_upload_sessions SET node_id = :n, category_id = :cat, display_name = :dn, field_values = CAST(:fv AS jsonb),"
            " duplicate_policy = :dp, status = CASE WHEN status = 'failed' AND error_code = ANY(:retry) THEN 'open' ELSE status END,"
            " error_code = CASE WHEN status = 'failed' AND error_code = ANY(:retry) THEN NULL ELSE error_code END,"
            " error_message = CASE WHEN status = 'failed' AND error_code = ANY(:retry) THEN NULL ELSE error_message END,"
            " updated_at = now() WHERE id = :i"
        ),
        {
            "n": node_id,
            "cat": category_id,
            "dn": patch.get("display_name", s["display_name"]),
            "fv": json.dumps(fv),
            "dp": patch.get("duplicate_policy", s["duplicate_policy"]),
            "retry": sorted(RETRYABLE),
            "i": session_id,
        },
    )
    return session_out(_session(env, session_id))


def append_chunk(env: Env, session_id: uuid.UUID, offset: int, data: bytes) -> dict[str, Any]:
    s = _session(env, session_id, lock=True)
    if s["expires_at"] < datetime.now(UTC) and s["status"] in OPEN:
        env.conn.execute(
            text("UPDATE document_upload_sessions SET status = 'expired', updated_at = now() WHERE id = :i"),
            {"i": session_id},
        )
        raise DocumentError(410, "session_expired", "This upload expired; start it again.")
    if s["status"] not in OPEN:
        raise DocumentError(409, "session_closed", "This upload is not accepting data.", {"status": s["status"]})
    limits = effective_limits(env.conn, env.org_id)
    if not data or len(data) > limits.chunk_max_bytes:
        raise DocumentError(
            413 if data else 422,
            "bad_chunk",
            "The chunk is empty or larger than the allowed chunk size.",
            {"chunk_max_bytes": limits.chunk_max_bytes},
        )
    if offset != s["received_bytes"]:
        raise DocumentError(
            409,
            "offset_mismatch",
            "The upload offset does not match what the server has.",
            {"received_bytes": s["received_bytes"]},
        )
    if s["received_bytes"] + len(data) > s["declared_size"]:
        raise DocumentError(422, "too_much_data", "More data than the declared file size.")
    try:
        new_size = get_storage().stage_append(s["staging_ref"], offset, data)
    except StorageError as exc:
        raise DocumentError(503, "storage_error", "The storage service could not accept the data; retry.") from exc
    env.conn.execute(
        text(
            "UPDATE document_upload_sessions SET received_bytes = :r, status = 'uploading', updated_at = now() WHERE id = :i"
        ),
        {"r": new_size, "i": session_id},
    )
    return {"received_bytes": new_size, "remaining": s["declared_size"] - new_size}


def cancel_upload(env: Env, session_id: uuid.UUID) -> dict[str, Any]:
    s = _session(env, session_id, lock=True)
    if s["status"] in ("completed",):
        raise DocumentError(409, "session_closed", "This upload already completed.")
    if s["status"] in OPEN or s["status"] == "failed":
        get_storage().stage_discard(s["staging_ref"])
        env.conn.execute(
            text("UPDATE document_upload_sessions SET status = 'cancelled', updated_at = now() WHERE id = :i"),
            {"i": session_id},
        )
    return session_out(_session(env, session_id))


def retry_upload(env: Env, session_id: uuid.UUID) -> dict[str, Any]:
    """Reopen a failed upload. A failure that kept the staged bytes (storage/scan/duplicate/target) resumes at the staged
    size; anything else starts again from zero. The server never trusts the client's idea of the offset."""
    s = _session(env, session_id, lock=True)
    if s["status"] not in ("failed", "expired", "cancelled"):
        raise DocumentError(409, "not_retryable", "Only failed, cancelled or expired uploads can be retried.")
    if s["status"] == "cancelled" or s["error_code"] not in RETRYABLE:
        get_storage().stage_discard(s["staging_ref"])
        received = 0
    else:
        received = get_storage().stage_size(s["staging_ref"])
    env.conn.execute(
        text(
            "UPDATE document_upload_sessions SET status = 'open', received_bytes = :r, error_code = NULL, error_message = NULL,"
            " expires_at = :ex, updated_at = now() WHERE id = :i"
        ),
        {
            "r": received,
            "ex": datetime.now(UTC) + timedelta(seconds=get_settings().upload_session_ttl_seconds),
            "i": session_id,
        },
    )
    return session_out(_session(env, session_id))


class SessionFailed(Exception):
    def __init__(
        self, code: str, message: str, status: str = "failed", http: int = 422, extra: dict[str, Any] | None = None
    ) -> None:
        self.code, self.message, self.status, self.http, self.extra = code, message, status, http, extra or {}


def _sha256_of(fh: Any) -> str:
    h = hashlib.sha256()
    fh.seek(0)
    while chunk := fh.read(1024 * 1024):
        h.update(chunk)
    return h.hexdigest()


def complete_upload(env: Env, session_id: uuid.UUID) -> tuple[dict[str, Any], SessionFailed | None]:
    """Verify and store. Returns (session, None) on success or (session, failure) after RECORDING the failure; the caller
    returns the failure as an error response so the recorded state commits. Nothing is `completed` before the object has
    been stored, its size confirmed and the database rows written in the same transaction."""
    s = _session(env, session_id, lock=True)
    if s["status"] == "completed":
        return session_out(s), None
    if s["status"] not in (*OPEN, "failed"):
        raise DocumentError(409, "session_closed", "This upload cannot be completed.", {"status": s["status"]})
    if s["expires_at"] < datetime.now(UTC):
        env.conn.execute(
            text("UPDATE document_upload_sessions SET status = 'expired', updated_at = now() WHERE id = :i"),
            {"i": session_id},
        )
        raise DocumentError(410, "session_expired", "This upload expired; start it again.")
    storage = get_storage()
    received = storage.stage_size(s["staging_ref"])
    if received != s["declared_size"]:  # not finished: report it, change nothing, let the client continue
        raise DocumentError(
            409, "size_mismatch", f"Received {received} of {s['declared_size']} bytes.", {"received_bytes": received}
        )
    try:
        failure = _finalize(env, s, storage)
    except SessionFailed as f:
        failure = f
    if failure is not None:
        if failure.code not in RETRYABLE:
            storage.stage_discard(s["staging_ref"])
        env.conn.execute(
            text(
                "UPDATE document_upload_sessions SET status = :st, error_code = :ec, error_message = :em, updated_at = now() WHERE id = :i"
            ),
            {"st": failure.status, "ec": failure.code, "em": failure.message, "i": session_id},
        )
        return session_out(_session(env, session_id)), failure
    return session_out(_session(env, session_id)), None


def _finalize(env: Env, s: dict[str, Any], storage: Any) -> SessionFailed | None:
    ref = s["staging_ref"]
    size = storage.stage_size(ref)
    if size != s["declared_size"]:
        return SessionFailed(
            "size_mismatch", f"Received {size} of {s['declared_size']} bytes.", http=409, extra={"received_bytes": size}
        )
    limits = effective_limits(env.conn, env.org_id)
    if size > limits.max_file_size:
        return SessionFailed(
            "file_too_large",
            "The file is larger than your plan allows.",
            http=413,
            extra={"limit": limits.max_file_size},
        )
    with storage.stage_open(ref) as fh:
        digest = _sha256_of(fh)
        if s["declared_sha256"] and digest != s["declared_sha256"]:
            return SessionFailed(
                "checksum_mismatch", "The received data does not match the file's checksum; upload it again."
            )
        verdict = inspect_file(fh, size, s["original_filename"], limits.allowed_extensions)
        if not verdict.ok:
            return SessionFailed("invalid_file", " ".join(verdict.problems))
        if verdict.quarantine:
            return SessionFailed(
                "quarantined",
                " ".join(verdict.quarantine) + " It was held and not stored as a document.",
                status="quarantined",
                http=422,
            )
        scan_status = "not_scanned"
        scanner = get_scanner()
        if scanner is not None:
            try:
                clean, signature = scanner.scan(fh)
            except ScanUnavailable:
                return SessionFailed(
                    "scan_unavailable", "The malware scanner is not available right now; retry shortly.", http=503
                )
            if not clean:
                return SessionFailed(
                    "malware_detected",
                    "The file was rejected by the malware scanner.",
                    status="quarantined",
                    http=422,
                    extra={"signature": signature},
                )
            scan_status = "clean"
    assert verdict.extension and verdict.mime
    doc_id = s["document_id"]
    is_new = doc_id is None
    node_id: uuid.UUID = s["node_id"]
    if is_new:
        try:
            _live_node(env, node_id)
            env.acc.require("upload", str(node_id))
        except NotFound:
            return SessionFailed(
                "target_unavailable", "That folder is no longer available to you; choose another.", http=409
            )
        except DocumentError as exc:
            return SessionFailed(exc.code, exc.detail, http=409)
        dup = find_duplicate(env, digest) if s["duplicate_policy"] == "block" else None
        if dup:
            return SessionFailed(
                "duplicate_detected", "This exact file is already stored in this company.", http=409, extra=dup
            )
    else:
        doc = get_document(env, doc_id, lock=True)
        if doc["sha256"] == digest:
            return SessionFailed(
                "identical_to_current_version", "This file is identical to the current version.", http=409
            )
        if doc["status"] != "active":
            return SessionFailed("document_not_active", "Only an active document can receive a new version.", http=409)
    fields = {}
    if s["field_values"] or is_new:
        try:
            fields = validate_metadata(env, s["category_id"], node_id, dict(s["field_values"]), require_complete=is_new)
        except DocumentError as exc:
            return SessionFailed(
                exc.code, exc.detail + " " + "; ".join(exc.extra.get("problems", [])), http=422, extra=exc.extra
            )
    try:
        entitlements.guard_amount(env.conn, env.org_id, "quota.storage_bytes", size)
        if is_new:
            entitlements.guard_amount(env.conn, env.org_id, "quota.documents_per_month", 1)
    except ApiError as exc:
        return SessionFailed(exc.code, exc.detail, http=exc.status, extra=exc.extra)
    # ---- store, then record. If recording fails the object is removed again (and reconciliation catches a crash between).
    key = storage.new_key(env.org_id, env.company_id)
    try:
        storage.commit_stage(ref, key)
        if not storage.exists(key) or storage.size(key) != size:
            raise StorageError("stored object failed verification")
    except StorageError:
        with_suppress_delete(storage, key)
        return SessionFailed("storage_error", "The file could not be stored safely; retry.", http=503)
    version_id = new_id()
    try:
        with env.conn.begin_nested():
            if is_new:
                doc_id = new_id()
                category = None
                if s["category_id"]:
                    category = env.conn.execute(
                        text("SELECT document_kind FROM document_categories WHERE id = :i"), {"i": s["category_id"]}
                    ).first()
                display = _display_name(env, s, verdict.extension, node_id, fields)
                env.conn.execute(
                    text(
                        "INSERT INTO documents (id, organization_id, company_id, node_id, category_id, display_name, original_filename,"
                        " extension, status, current_version_id, current_version_number, source, import_batch_id, kind_at_filing,"
                        " filed_path_snapshot, filing_rule_id, created_by, updated_by) VALUES (:id, :o, :c, :n, :cat, :dn, :fn, :ext,"
                        " 'active', :v, 1, :src, :b, :k, :p, :fr, :u, :u)"
                    ),
                    {
                        "id": doc_id,
                        "o": env.org_id,
                        "c": env.company_id,
                        "n": node_id,
                        "cat": s["category_id"],
                        "dn": display,
                        "fn": s["original_filename"],
                        "ext": verdict.extension,
                        "v": version_id,
                        "src": s["source"],
                        "b": s["batch_id"],
                        "k": category.document_kind if category else None,
                        "p": node_path(env.conn, env.company_id, node_id),
                        "fr": s["filing_rule_id"],
                        "u": env.user_id,
                    },
                )
                number = 1
            else:
                number = env.conn.execute(
                    text("SELECT max(version_number) + 1 FROM document_versions WHERE document_id = :d"), {"d": doc_id}
                ).scalar_one()
            env.conn.execute(
                text(
                    "INSERT INTO document_versions (id, organization_id, company_id, document_id, version_number, original_filename,"
                    " extension, mime_type, size_bytes, sha256, storage_driver, storage_key, scan_status, change_reason, uploaded_by)"
                    " VALUES (:id, :o, :c, :d, :n, :fn, :ext, :mime, :sz, :h, :drv, :key, :scan, :cr, :u)"
                ),
                {
                    "id": version_id,
                    "o": env.org_id,
                    "c": env.company_id,
                    "d": doc_id,
                    "n": number,
                    "fn": s["original_filename"],
                    "ext": verdict.extension,
                    "mime": verdict.mime,
                    "sz": size,
                    "h": digest,
                    "drv": storage.name,
                    "key": key,
                    "scan": scan_status,
                    "cr": s["change_reason"],
                    "u": env.user_id,
                },
            )
            if not is_new:
                env.conn.execute(
                    text(
                        "UPDATE documents SET current_version_id = :v, current_version_number = :n, updated_by = :u, updated_at = now(),"
                        " row_version = row_version + 1 WHERE id = :d"
                    ),
                    {"v": version_id, "n": number, "u": env.user_id, "d": doc_id},
                )
            if fields:
                _store_field_values(env, doc_id, s["category_id"], node_id, fields)
            if is_new and s["duplicate_policy"] == "allow_copy":
                dup = find_duplicate(env, digest, exclude_document=doc_id)
                if dup and dup["document"]:
                    env.conn.execute(
                        text(
                            "INSERT INTO document_duplicate_candidates (id, organization_id, company_id, document_id, duplicate_of_document_id,"
                            " sha256, decision, decided_by) VALUES (:id, :o, :c, :d, :x, :h, 'kept_separate', :u)"
                        ),
                        {
                            "id": new_id(),
                            "o": env.org_id,
                            "c": env.company_id,
                            "d": doc_id,
                            "x": uuid.UUID(dup["document"]["id"]),
                            "h": digest,
                            "u": env.user_id,
                        },
                    )
                    _event(
                        env,
                        doc_id,
                        "duplicate_kept",
                        version_id=version_id,
                        detail={"duplicate_of": dup["document"]["id"]},
                    )
            _event(
                env,
                doc_id,
                "uploaded" if is_new else "version_created",
                version_id=version_id,
                detail={"version": number, "size": size, "source": s["source"], "reason": s["change_reason"]},
                audit_action="document.uploaded" if is_new else "document.version_created",
            )
            outbox.enqueue(
                env.conn,
                event_type="document.version_created",
                org_id=env.org_id,
                company_id=env.company_id,
                payload={
                    "document_id": str(doc_id),
                    "version_id": str(version_id),
                    "version": number,
                    "sha256": digest,
                },
            )
            env.conn.execute(
                text(
                    "UPDATE document_upload_sessions SET status = 'completed', received_bytes = :sz, result_document_id = :d,"
                    " result_version_id = :v, error_code = NULL, error_message = NULL, updated_at = now() WHERE id = :i"
                ),
                {"sz": size, "d": doc_id, "v": version_id, "i": s["id"]},
            )
    except Exception:
        with_suppress_delete(storage, key)
        raise
    for k in ("quota.storage_bytes", "quota.documents_per_month"):
        entitlements.refresh_counter(env.conn, env.org_id, k)
    return None


def with_suppress_delete(storage: Any, key: str) -> None:
    import contextlib

    with contextlib.suppress(StorageError):
        storage.delete(key)


def _display_name(env: Env, s: dict[str, Any], ext: str, node_id: uuid.UUID, fields: dict[str, Any]) -> str:
    """User-supplied name > complete naming-rule suggestion > the ORIGINAL file name. Missing metadata is never invented;
    the result is made unique within its folder and never overwrites another document."""
    base: str | None = None
    if s["display_name"]:
        base = _checked_name(s["display_name"], ext)
    else:
        sug = suggest(
            env,
            filename=s["original_filename"],
            category_id=s["category_id"],
            node_id=node_id,
            source=s["source"],
            field_values=fields,
            document_date=str(fields.get("documentdate") or fields.get("date") or "") or None,
        )
        if sug["naming"]["complete"]:
            base = sug["naming"]["name"]
    if base is None:
        base = _checked_name(s["original_filename"], ext)
    taken = [
        r[0].lower()
        for r in env.conn.execute(
            text(
                "SELECT display_name FROM documents WHERE company_id = :c AND node_id = :n AND status IN ('active', 'archived')"
            ),
            {"c": env.company_id, "n": node_id},
        )
    ]
    return ensure_unique_name(base, taken)


_BAD = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def _checked_name(name: str, ext: str) -> str:
    name = _BAD.sub("_", unicodedata.normalize("NFC", name)).strip().strip(".")[:200]
    if not name:
        name = "document"
    if not name.lower().endswith("." + ext):
        name = f"{name}.{ext}"
    return name
