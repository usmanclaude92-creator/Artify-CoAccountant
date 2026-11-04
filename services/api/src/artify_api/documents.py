"""Document management service (Phase 6): import/upload sessions, documents, versions, lifecycle, search, usage.

Design rules (docs/documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md):
* Binary content lives in private object storage (`storage.py`); this module owns the metadata and the rules.
* A document is filed in a workspace node by id. Renaming or moving a folder never touches documents or storage.
* Authorization is two-stage: the route requires a `documents.*` permission on the company, then every operation is
  checked against the document's workspace node with the Phase 4 allow/deny engine. Hidden documents answer 404.
* Originals are immutable: a correction is a new version. Nothing is overwritten.
* Nothing is reported as stored until the object store confirmed it and its size was verified.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from artify_api import audit, entitlements, scanner, storage
from artify_api import workspace as ws
from artify_api import workspace_config as wc
from artify_api.context import RequestCtx
from artify_api.errors import ApiError, Conflict, EntitlementExceeded, NotFound, Unprocessable
from artify_api.file_safety import (
    ALLOWED,
    PREVIEWABLE,
    FileRejected,
    inspect_file,
    sanitize_filename,
    split_extension,
)
from artify_api.ids import new_id
from artify_api.permissions import SYSTEM_ROLES
from artify_api.settings import get_settings
from artify_api.workspace_engine import (
    ensure_unique_name,
    evaluate_rules,
    fallback_name,
    parse_pattern,
    render_name,
    validate_values,
)

#: rejections that mean "this file is dangerous" (shown as Quarantined; nothing is stored)
QUARANTINE_CODES = frozenset(
    {
        "executable_content",
        "macro_content",
        "active_content",
        "archive_bomb",
        "unsafe_archive_entry",
        "malware_detected",
    }
)
#: failures worth retrying with the same file; every other failure is about the content and would simply repeat
TRANSIENT_CODES = frozenset({"storage_unavailable", "scan_unavailable", "storage_verification_failed"})
_DANGEROUS_EXT = frozenset(
    [
        "exe",
        "bat",
        "cmd",
        "com",
        "scr",
        "js",
        "jse",
        "vbs",
        "vbe",
        "ps1",
        "msi",
        "dll",
        "lnk",
        "jar",
        "sh",
        "html",
        "htm",
        "svg",
        "hta",
        "reg",
        "cpl",
        "wsf",
        "pif",
        "zip",
        "rar",
        "7z",
    ]
)
DOC_STATUSES = ("active", "archived", "deleted")
_DOC_BASE_KEYS = (
    "documents.read",
    "documents.upload",
    "documents.manage",
    "documents.delete",
    "workspace.manage",
    "workspace.permissions.manage",
)


# ---------------------------------------------------------------- errors
class FileNotAccepted(Unprocessable):
    code, title = "file_not_accepted", "File not accepted"


class DuplicateDocument(Conflict):
    code, title = "duplicate_document", "A document with identical content already exists"


class VersionConflict(Conflict):
    code, title = "version_conflict", "The document was changed by someone else"


class RetentionHold(Conflict):
    code, title = "retention_hold", "The document is on retention hold"


class StorageUnavailable(ApiError):
    status, code, title = 503, "storage_unavailable", "Document storage is unavailable"


@dataclass
class Outcome:
    """A service result that must be persisted even when the HTTP answer is an error (e.g. a failed upload)."""

    status: int
    body: dict[str, Any]


# ---------------------------------------------------------------- helpers
def _j(value: Any) -> str:
    return json.dumps(value, default=str)


def _now() -> datetime:
    return datetime.now(UTC)


def doc_access(ctx: RequestCtx) -> ws.Access:
    assert ctx.principal is not None and ctx.company_id is not None
    p = ctx.principal
    perms = set(SYSTEM_ROLES[p.org_role][2]) if p.org_role else set()
    cr = p.company_roles.get(ctx.company_id)
    if cr:
        perms |= SYSTEM_ROLES[cr][2]
    base = frozenset(k for k in _DOC_BASE_KEYS if k in perms)
    return ws.load_access(ctx.conn, ctx.company_id, ctx.user_id, p.org_role, base)


def log_event(
    ctx: RequestCtx,
    document_id: uuid.UUID | str | None,
    event_type: str,
    *,
    version_no: int | None = None,
    summary: dict[str, Any] | None = None,
    also_audit: bool = False,
) -> None:
    assert ctx.org_id is not None and ctx.company_id is not None
    ctx.conn.execute(
        text(
            "INSERT INTO document_events (id, organization_id, company_id, document_id, version_no, actor_id,"
            " event_type, summary, ip) VALUES (:id, :o, :c, :d, :v, :a, :t, CAST(:s AS jsonb), CAST(:ip AS inet))"
        ),
        {
            "id": new_id(),
            "o": ctx.org_id,
            "c": ctx.company_id,
            "d": document_id,
            "v": version_no,
            "a": ctx.user_id,
            "t": event_type,
            "s": _j(summary or {}),
            "ip": ctx.meta.ip,
        },
    )
    if also_audit:
        audit.write_audit(
            ctx.conn,
            action=event_type,
            actor_type="user",
            actor_id=ctx.user_id,
            org_id=ctx.org_id,
            company_id=ctx.company_id,
            entity_type="document",
            entity_id=document_id,
            summary={**(summary or {}), "version_no": version_no},
            meta=ctx.meta,
        )


def _node_ok_for_filing(conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID | str) -> dict[str, Any]:
    status = conn.execute(text("SELECT status FROM companies WHERE id = :c"), {"c": company_id}).scalar()
    if status != "active":
        raise Conflict(
            "Documents can only be added to or restored in an active company.", extra={"company_status": status}
        )
    node = ws.get_node(conn, company_id, uuid.UUID(str(node_id)))
    if node["archived_at"] is not None or not node["is_active"]:
        raise Conflict("This workspace location is archived or inactive; choose another destination.")
    return node


def _check_category(
    conn: Connection, company_id: uuid.UUID, category_id: Any, node_chain: list[str], ext: str
) -> dict[str, Any] | None:
    if category_id is None:
        return None
    cat = conn.execute(
        text(
            "SELECT id, key, name, document_kind, node_id, allowed_file_types FROM document_categories"
            " WHERE company_id = :c AND id = :i AND archived_at IS NULL AND is_active"
        ),
        {"c": company_id, "i": category_id},
    ).first()
    if cat is None:
        raise Unprocessable("The selected category does not exist or is no longer active.")
    if cat.node_id is not None and str(cat.node_id) not in node_chain:
        raise Unprocessable("This category does not apply to the selected workspace location.")
    allowed = [t.lower().lstrip(".") for t in (cat.allowed_file_types or [])]
    if allowed and ext not in allowed:
        raise FileNotAccepted(
            f"Files of type '.{ext}' are not allowed in the '{cat.name}' category.",
            extra={"reason": "file_type_not_allowed_for_category", "allowed": allowed},
        )
    return ws.row_dict(cat)


def _validate_metadata(
    conn: Connection, company_id: uuid.UUID, category_id: Any, node_chain: list[str], values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, uuid.UUID]]:
    fields = wc.applicable_fields(conn, company_id, category_id, node_chain)
    defs = [wc.to_field_def(f) for f in fields]
    normalized, problems = validate_values(defs, values, wc.ref_exists_fn(conn, company_id))
    if problems:
        raise Unprocessable("The metadata is not valid.", extra={"problems": problems})
    return normalized, {f["key"]: uuid.UUID(f["id"]) for f in fields}


def _lock_node_names(conn: Connection, node_id: uuid.UUID | str) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 7))"), {"k": f"docname:{node_id}"})


def unique_display_name(
    conn: Connection, company_id: uuid.UUID, node_id: uuid.UUID | str, name: str, exclude: uuid.UUID | str | None = None
) -> str:
    """Never overwrite: a taken name gets _2, _3 ... before the extension (same rule as the naming engine)."""
    _lock_node_names(conn, node_id)
    rows = conn.execute(
        text(
            "SELECT lower(display_name) FROM documents WHERE company_id = :c AND node_id = :n AND status = 'active'"
            " AND purged_at IS NULL AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"
        ),
        {"c": company_id, "n": node_id, "x": exclude},
    )
    return ensure_unique_name(name, [r[0] for r in rows])


def _with_ext(name: str, ext: str) -> str:
    cleaned = sanitize_filename(name)
    _stem, e = split_extension(cleaned)
    if e == ext:
        return cleaned
    if e and (e in ALLOWED or e in _DANGEROUS_EXT):
        raise Unprocessable(f"The extension must stay '.{ext}'.")
    return sanitize_filename(f"{cleaned}.{ext}")


# ---------------------------------------------------------------- reads
DOC_SELECT = (
    "SELECT d.id, d.company_id, d.node_id, d.category_id, d.display_name, d.original_filename, d.extension,"
    " d.mime_type, d.size_bytes, d.content_sha256, d.current_version_no, d.status, d.source, d.import_batch_id,"
    " d.filing_rule_id, d.retention_hold, d.created_by, d.created_at, d.updated_by, d.updated_at, d.archived_at,"
    " d.deleted_at, d.row_version, u.display_name AS created_by_name FROM documents d"
    " LEFT JOIN users u ON u.id = d.created_by"
)


def doc_summary(r: Any) -> dict[str, Any]:
    d = ws.row_dict(r)
    d["previewable"] = d["extension"] in {e for e, (_m, fam) in ALLOWED.items() if fam in PREVIEWABLE}
    return d


def get_document(
    ctx: RequestCtx,
    acc: ws.Access,
    doc_id: uuid.UUID,
    *,
    action: str = "view",
    lock: bool = False,
    allow_deleted: bool = False,
) -> dict[str, Any]:
    """Load a document the caller may act on. Unknown, out-of-scope, hidden, purged and (unless asked) deleted
    documents all answer 404; a visible document the caller may not change answers 403."""
    assert ctx.company_id is not None
    row = ctx.conn.execute(
        text(
            f"{DOC_SELECT} WHERE d.company_id = :c AND d.id = :i AND d.purged_at IS NULL"
            + (" FOR UPDATE OF d" if lock else "")
        ),
        {"c": ctx.company_id, "i": doc_id},
    ).first()
    if row is None or str(row.node_id) not in acc.nodes or not acc.visible(str(row.node_id)):
        raise NotFound("Document not found.")
    if row.status == "deleted" and not (allow_deleted and acc.can("delete", str(row.node_id))):
        raise NotFound("Document not found.")
    acc.require(action, str(row.node_id))
    return doc_summary(row)


def visible_node_ids(acc: ws.Access) -> list[uuid.UUID]:
    return [uuid.UUID(n) for n in acc.nodes if acc.visible(n)]


def subtree_ids(acc: ws.Access, node_id: str) -> list[str]:
    out = [node_id]
    frontier = [node_id]
    while frontier:
        nxt = [i for i, n in acc.nodes.items() if n["parent_id"] in frontier]
        out += nxt
        frontier = nxt
    return out


_SORTS = {
    "name": "lower(d.display_name)",
    "created": "d.created_at",
    "updated": "d.updated_at",
    "size": "d.size_bytes",
    "type": "d.extension",
}


def list_documents(ctx: RequestCtx, acc: ws.Access, f: dict[str, Any]) -> dict[str, Any]:
    assert ctx.company_id is not None
    visible = {str(n) for n in visible_node_ids(acc)}
    if f.get("node_id"):
        nid = str(f["node_id"])
        if nid not in visible:
            raise NotFound("Workspace item not found.")
        scope = subtree_ids(acc, nid) if f.get("include_subfolders") else [nid]
        node_ids = [uuid.UUID(i) for i in scope if i in visible]
    else:
        node_ids = [uuid.UUID(i) for i in visible]
    status = f.get("status") or "active"
    where = ["d.company_id = :c", "d.purged_at IS NULL", "d.node_id = ANY(:nodes)", "d.status = :st"]
    params: dict[str, Any] = {"c": ctx.company_id, "nodes": node_ids, "st": status}
    if status == "deleted":
        # the deleted-items view is limited to nodes where the caller may delete
        params["nodes"] = [n for n in node_ids if acc.can("delete", str(n))]
    simple = {
        "category_id": "d.category_id = :category_id",
        "extension": "d.extension = :extension",
        "source": "d.source = :source",
        "created_by": "d.created_by = :created_by",
        "min_size": "d.size_bytes >= :min_size",
        "max_size": "d.size_bytes <= :max_size",
        "created_from": "d.created_at >= :created_from",
        "created_to": "d.created_at < :created_to",
        "sha256": "d.content_sha256 = :sha256",
        "import_batch_id": "d.import_batch_id = :import_batch_id",
    }
    for key, clause in simple.items():
        if f.get(key) is not None:
            where.append(clause)
            params[key] = f[key].lower() if key in ("extension", "sha256") else f[key]
    q = (f.get("q") or "").strip()
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" if q else None
    if pattern:
        where.append(
            "(lower(d.display_name) LIKE lower(:q) ESCAPE '\\' OR lower(d.original_filename) LIKE lower(:q) ESCAPE '\\')"
        )
        params["q"] = pattern
    if f.get("field_key") and f.get("field_value") is not None:
        where.append(
            "EXISTS (SELECT 1 FROM document_metadata_values m JOIN custom_field_definitions fd ON fd.id = m.field_id"
            " WHERE m.document_id = d.id AND fd.key = :fkey AND (m.value #>> '{}') = :fval)"
        )
        params["fkey"], params["fval"] = f["field_key"], str(f["field_value"])
    sql_where = " AND ".join(where)
    col = _SORTS.get(f.get("sort") or "created", _SORTS["created"])
    direction = "ASC" if (f.get("order") or "desc").lower() == "asc" else "DESC"
    total = ctx.conn.execute(text(f"SELECT count(*) FROM documents d WHERE {sql_where}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(f"{DOC_SELECT} WHERE {sql_where} ORDER BY {col} {direction}, d.id ASC LIMIT :lim OFFSET :off"),
        {**params, "lim": f["limit"], "off": f["offset"]},
    )
    return {"items": [doc_summary(r) for r in rows], "total": int(total), "limit": f["limit"], "offset": f["offset"]}


def node_counts(ctx: RequestCtx, acc: ws.Access) -> dict[str, Any]:
    assert ctx.company_id is not None
    visible = {str(n) for n in visible_node_ids(acc)}
    rows = ctx.conn.execute(
        text(
            "SELECT node_id, count(*) FILTER (WHERE status = 'active') AS active,"
            " count(*) FILTER (WHERE status = 'archived') AS archived,"
            " count(*) FILTER (WHERE status = 'deleted') AS deleted,"
            " COALESCE(sum(size_bytes) FILTER (WHERE status <> 'deleted'), 0) AS bytes"
            " FROM documents WHERE company_id = :c AND purged_at IS NULL GROUP BY node_id"
        ),
        {"c": ctx.company_id},
    )
    own = {str(r.node_id): r for r in rows if str(r.node_id) in visible}
    nodes: dict[str, dict[str, int]] = {}
    for nid in visible:
        ids = [i for i in subtree_ids(acc, nid) if i in own]
        nodes[nid] = {
            "own_active": int(own[nid].active) if nid in own else 0,
            "active": sum(int(own[i].active) for i in ids),
            "archived": sum(int(own[i].archived) for i in ids),
            "deleted": sum(int(own[i].deleted) for i in ids) if acc.can("delete", nid) else 0,
        }
    return {
        "nodes": nodes,
        "totals": {
            "active": sum(int(r.active) for r in own.values()),
            "archived": sum(int(r.archived) for r in own.values()),
            "bytes": sum(int(r.bytes) for r in own.values()),
        },
    }


def find_duplicates(
    ctx: RequestCtx, acc: ws.Access, sha256: str, exclude_id: uuid.UUID | str | None = None
) -> list[dict[str, Any]]:
    """Same-company documents with identical content that the caller may see. Never crosses companies (RLS plus the
    company filter) and never reveals documents in hidden locations."""
    assert ctx.company_id is not None
    rows = ctx.conn.execute(
        text(
            "SELECT id, node_id, display_name, status, created_at FROM documents WHERE company_id = :c"
            " AND content_sha256 = :h AND purged_at IS NULL AND status <> 'deleted'"
            " AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid)) ORDER BY created_at LIMIT 20"
        ),
        {"c": ctx.company_id, "h": sha256, "x": exclude_id},
    )
    return [
        {**ws.row_dict(r), "created_at": r.created_at.isoformat()}
        for r in rows
        if str(r.node_id) in acc.nodes and acc.visible(str(r.node_id))
    ]


def document_detail(ctx: RequestCtx, acc: ws.Access, doc: dict[str, Any]) -> dict[str, Any]:
    assert ctx.company_id is not None
    nid = doc["node_id"]
    versions = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT v.id, v.version_no, v.original_filename, v.extension, v.mime_type, v.size_bytes, v.sha256,"
                " v.uploaded_by, u.display_name AS uploaded_by_name, v.uploaded_at, v.change_reason,"
                " v.restored_from_version, v.scan_status FROM document_versions v"
                " LEFT JOIN users u ON u.id = v.uploaded_by WHERE v.document_id = :d ORDER BY v.version_no DESC"
            ),
            {"d": doc["id"]},
        )
    ]
    meta = [
        ws.row_dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT fd.id AS field_id, fd.key, fd.label, fd.data_type, m.value, m.updated_at FROM document_metadata_values m"
                " JOIN custom_field_definitions fd ON fd.id = m.field_id WHERE m.document_id = :d ORDER BY fd.display_order, fd.key"
            ),
            {"d": doc["id"]},
        )
    ]
    chain = [str(c) for c in acc.chain(nid)]
    applicable = {f["key"] for f in wc.applicable_fields(ctx.conn, ctx.company_id, doc["category_id"], chain)}
    for m in meta:
        m["applies"] = m["key"] in applicable
    node = acc.nodes[nid]
    path = [
        (ws.row_dict(r)["display_name"] or ws.row_dict(r)["name"])
        for r in ctx.conn.execute(
            text("SELECT name, display_name FROM workspace_nodes WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": "{" + ",".join(reversed(chain)) + "}"},
        )
    ]
    return {
        **doc,
        "versions": versions,
        "metadata": meta,
        "duplicates": find_duplicates(ctx, acc, doc["content_sha256"], doc["id"]),
        "node_archived": node["archived_at"] is not None,
        "location": path,
        "can": {a: acc.can(a, nid) for a in ("edit", "rename", "move", "archive", "delete", "upload")},
        "scan_status": versions[0]["scan_status"] if versions else "not_scanned",
    }


def list_events(
    ctx: RequestCtx, acc: ws.Access, *, document_id: uuid.UUID | None, limit: int, offset: int
) -> dict[str, Any]:
    assert ctx.company_id is not None
    if document_id is not None:
        get_document(ctx, acc, document_id, allow_deleted=True)
        where, params = "e.document_id = :d", {"d": str(document_id)}
    else:
        # company-wide feed: only events of documents the caller can see (or company-level events without a document)
        vis = [str(n) for n in visible_node_ids(acc)]
        where = "(e.document_id IS NULL OR e.document_id IN (SELECT id FROM documents WHERE company_id = :c AND node_id = ANY(CAST(:vis AS uuid[]))))"
        params = {"vis": "{" + ",".join(vis) + "}"}
    params["c"] = str(ctx.company_id)
    total = ctx.conn.execute(
        text(f"SELECT count(*) FROM document_events e WHERE e.company_id = :c AND {where}"), params
    ).scalar_one()
    rows = ctx.conn.execute(
        text(
            "SELECT e.id, e.document_id, e.version_no, e.actor_id, u.display_name AS actor_name, e.event_type,"
            " e.summary, e.created_at FROM document_events e LEFT JOIN users u ON u.id = e.actor_id"
            f" WHERE e.company_id = :c AND {where} ORDER BY e.created_at DESC, e.id DESC LIMIT :lim OFFSET :off"
        ),
        {**params, "lim": limit, "off": offset},
    )
    return {"items": [ws.row_dict(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}


# ---------------------------------------------------------------- upload sessions
def op_view(r: Any) -> dict[str, Any]:
    d = ws.row_dict(r)
    keys = (
        "id", "batch_id", "target_document_id", "status", "original_filename", "declared_size", "declared_sha256",
        "node_id", "category_id", "display_name", "duplicate_policy", "error_code", "error_message", "attempts",
        "expires_at", "created_at", "updated_at", "completed_at", "result_document_id", "result_version_id",
        "received_size", "scan_status", "verified_mime", "change_reason", "idempotency_key",
    )  # fmt: skip
    return {k: d.get(k) for k in keys}


def _op_row(ctx: RequestCtx, op_id: uuid.UUID, *, lock: bool = False) -> Any:
    assert ctx.company_id is not None
    row = ctx.conn.execute(
        text(
            "SELECT * FROM document_upload_operations WHERE company_id = :c AND id = :i AND created_by = :u"
            + (" FOR UPDATE" if lock else "")
        ),
        {"c": ctx.company_id, "i": op_id, "u": ctx.user_id},
    ).first()
    if row is None:
        raise NotFound("Upload not found.")
    return row


def get_upload(ctx: RequestCtx, op_id: uuid.UUID) -> dict[str, Any]:
    return op_view(_op_row(ctx, op_id))


def list_uploads(
    ctx: RequestCtx, *, status: str | None, batch_id: uuid.UUID | None, limit: int, offset: int, mine: bool = True
) -> dict[str, Any]:
    assert ctx.company_id is not None
    where = ["company_id = :c"]
    params: dict[str, Any] = {"c": ctx.company_id}
    if mine:
        where.append("created_by = :u")
        params["u"] = ctx.user_id
    if status:
        where.append("status = :s")
        params["s"] = status
    if batch_id:
        where.append("batch_id = :b")
        params["b"] = batch_id
    w = " AND ".join(where)
    total = ctx.conn.execute(text(f"SELECT count(*) FROM document_upload_operations WHERE {w}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(
            f"SELECT * FROM document_upload_operations WHERE {w} ORDER BY created_at DESC, id DESC LIMIT :l OFFSET :o"
        ),
        {**params, "l": limit, "o": offset},
    )
    return {"items": [op_view(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}


def create_batch(ctx: RequestCtx, total_files: int, source: str, client_key: str | None) -> dict[str, Any]:
    assert ctx.org_id is not None and ctx.company_id is not None
    if total_files > get_settings().max_import_batch_files:
        raise Unprocessable(f"A batch may contain at most {get_settings().max_import_batch_files} files.")
    bid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO document_import_batches (id, organization_id, company_id, created_by, source, total_files,"
            " client_batch_key) VALUES (:i, :o, :c, :u, :s, :t, :k)"
        ),
        {
            "i": bid,
            "o": ctx.org_id,
            "c": ctx.company_id,
            "u": ctx.user_id,
            "s": source,
            "t": total_files,
            "k": client_key,
        },
    )
    log_event(ctx, None, "import.batch_created", summary={"batch_id": str(bid), "total_files": total_files})
    return {"id": str(bid), "total_files": total_files, "source": source}


def batch_status(ctx: RequestCtx, batch_id: uuid.UUID) -> dict[str, Any]:
    assert ctx.company_id is not None
    b = ctx.conn.execute(
        text(
            "SELECT id, total_files, source, created_at, created_by FROM document_import_batches WHERE company_id = :c AND id = :i"
        ),
        {"c": ctx.company_id, "i": batch_id},
    ).first()
    if b is None:
        raise NotFound("Import batch not found.")
    counts = {
        r.status: int(r.n)
        for r in ctx.conn.execute(
            text(
                "SELECT status, count(*) AS n FROM document_upload_operations WHERE batch_id = :b AND company_id = :c GROUP BY status"
            ),
            {"b": batch_id, "c": ctx.company_id},
        )
    }
    return {**ws.row_dict(b), "counts": counts, "registered": sum(counts.values())}


def init_upload(ctx: RequestCtx, acc: ws.Access, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    assert ctx.org_id is not None and ctx.company_id is not None and ctx.principal is not None
    conn, org, cid, settings = ctx.conn, ctx.org_id, ctx.company_id, get_settings()
    target_doc: dict[str, Any] | None = None
    safe_name = _safe_name(body["filename"])
    _stem, ext = split_extension(safe_name)
    existing = conn.execute(
        text(
            "SELECT * FROM document_upload_operations WHERE company_id = :c AND created_by = :u AND idempotency_key = :k"
        ),
        {"c": cid, "u": ctx.user_id, "k": body["idempotency_key"]},
    ).first()
    if existing is not None:
        same = (
            existing.original_filename == safe_name
            and existing.declared_size == body["size"]
            and (existing.declared_sha256 or None) == (body.get("sha256") or None)
            and str(existing.node_id or "") == str(body.get("node_id") or "")
            and str(existing.target_document_id or "") == str(body.get("target_document_id") or "")
        )
        if not same:
            raise Conflict("This idempotency key was already used for a different upload.")
        return 200, op_view(existing)

    if ext not in ALLOWED:
        raise FileNotAccepted(
            f"Files of type '.{ext or '(none)'}' are not accepted. Allowed: " + ", ".join(sorted(ALLOWED)) + ".",
            extra={"reason": "unsupported_type", "allowed": sorted(ALLOWED)},
        )
    if body["size"] > settings.max_file_size_ceiling_bytes:
        raise FileNotAccepted(
            f"The file is larger than the system maximum of {settings.max_file_size_ceiling_bytes} bytes.",
            extra={"reason": "file_too_large", "max_bytes": settings.max_file_size_ceiling_bytes},
        )
    plan_max = entitlements.max_file_size(conn, org, settings.max_file_size_ceiling_bytes)
    if body["size"] > plan_max:
        raise EntitlementExceeded(key="limit.file_size_bytes", limit=plan_max, current=0, requested=body["size"])

    if body.get("target_document_id"):
        target_doc = get_document(ctx, acc, body["target_document_id"], action="upload")
        if target_doc["status"] != "active":
            raise Conflict("New versions can only be added to active documents.")
        if target_doc["extension"] != ext:
            raise FileNotAccepted(
                f"A new version must keep the file type '.{target_doc['extension']}'.",
                extra={"reason": "extension_mismatch"},
            )
        node_id, category_id = target_doc["node_id"], target_doc["category_id"]
        _node_ok_for_filing(conn, cid, node_id)
    else:
        if not body.get("node_id"):
            raise Unprocessable("Choose a destination in the workspace.")
        node_id = str(body["node_id"])
        acc.require("upload", node_id)
        _node_ok_for_filing(conn, cid, node_id)
        category_id = body.get("category_id")
    chain = [str(c) for c in acc.chain(str(node_id))]
    _check_category(conn, cid, category_id, chain, ext)
    metadata: dict[str, Any] = {}
    if target_doc is None:
        metadata, _ids = _validate_metadata(conn, cid, category_id, chain, body.get("metadata") or {})
    filing_rule_id = body.get("filing_rule_id")
    if (
        filing_rule_id
        and conn.execute(
            text("SELECT 1 FROM filing_rules WHERE company_id = :c AND id = :i"), {"c": cid, "i": filing_rule_id}
        ).first()
        is None
    ):
        raise Unprocessable("The referenced filing rule does not exist.")
    if (
        body.get("batch_id")
        and conn.execute(
            text("SELECT 1 FROM document_import_batches WHERE company_id = :c AND id = :i"),
            {"c": cid, "i": body["batch_id"]},
        ).first()
        is None
    ):
        raise NotFound("Import batch not found.")

    sha = (body.get("sha256") or "").lower() or None
    if sha and target_doc is None and body["duplicate_policy"] == "block":
        dups = find_duplicates(ctx, acc, sha)
        if dups:
            raise DuplicateDocument(
                "A document with identical content already exists in this company.", extra={"duplicates": dups}
            )
    entitlements.guard_quota(conn, org, "quota.storage_bytes", body["size"])
    if target_doc is None:
        entitlements.guard_quota(conn, org, "quota.documents_per_month", 1)

    display = _with_ext(body["display_name"], ext) if body.get("display_name") else safe_name
    op_id = new_id()
    conn.execute(
        text(
            "INSERT INTO document_upload_operations (id, organization_id, company_id, batch_id, target_document_id,"
            " status, idempotency_key, original_filename, declared_size, declared_sha256, declared_mime, node_id,"
            " category_id, display_name, metadata, filing_rule_id, duplicate_policy, change_reason, expires_at,"
            " created_by) VALUES (:id, :o, :c, :b, :t, 'awaiting_upload', :k, :fn, :sz, :sha, :mime, :n, :cat, :dn,"
            " CAST(:meta AS jsonb), :fr, :dp, :cr, now() + make_interval(secs => :ttl), :u)"
        ),
        {
            "id": op_id, "o": org, "c": cid, "b": body.get("batch_id"), "t": body.get("target_document_id"),
            "k": body["idempotency_key"], "fn": safe_name, "sz": body["size"], "sha": sha, "mime": body.get("mime"),
            "n": node_id, "cat": category_id, "dn": display, "meta": _j(metadata), "fr": filing_rule_id,
            "dp": body["duplicate_policy"], "cr": body.get("change_reason"), "ttl": settings.upload_session_ttl_seconds,
            "u": ctx.user_id,
        },
    )  # fmt: skip
    log_event(
        ctx,
        body.get("target_document_id"),
        "upload.registered",
        summary={"operation_id": str(op_id), "filename": safe_name, "size": body["size"]},
    )
    return 201, op_view(_op_row(ctx, op_id))


def _safe_name(filename: str) -> str:
    try:
        return sanitize_filename(filename)
    except FileRejected as exc:
        raise FileNotAccepted(exc.message, extra={"reason": exc.code}) from exc


def begin_content(ctx: RequestCtx, acc: ws.Access, op_id: uuid.UUID) -> tuple[dict[str, Any], bool]:
    """Lock the session and move it to `uploading`. Returns (operation, already_received): when the content was
    already received (a retry after a lost response) the caller must not read the body again."""
    row = _op_row(ctx, op_id, lock=True)
    if row.status in ("uploaded", "verifying", "completed"):
        return op_view(row), True
    if row.expires_at < _now() and row.status in ("awaiting_upload", "uploading"):
        ctx.conn.execute(
            text("UPDATE document_upload_operations SET status = 'expired', updated_at = now() WHERE id = :i"),
            {"i": op_id},
        )
        raise Conflict("This upload session expired. Start the upload again.", extra={"status": "expired"})
    if row.status not in ("awaiting_upload", "uploading"):
        raise Conflict(f"This upload is {row.status} and cannot receive content.", extra={"status": row.status})
    acc.require("upload", str(row.node_id))  # permissions may have changed since registration
    _node_ok_for_filing(ctx.conn, ctx.company_id, row.node_id)  # type: ignore[arg-type]
    ctx.conn.execute(
        text(
            "UPDATE document_upload_operations SET status = 'uploading', attempts = attempts + 1, error_code = NULL,"
            " error_message = NULL, updated_at = now() WHERE id = :i"
        ),
        {"i": op_id},
    )
    return op_view(_op_row(ctx, op_id)), False


def _fail(
    ctx: RequestCtx,
    row: Any,
    code: str,
    message: str,
    status: str,
    http: int,
    size: int,
    sha: str,
    extra: dict[str, Any] | None = None,
) -> Outcome:
    ctx.conn.execute(
        text(
            "UPDATE document_upload_operations SET status = :s, error_code = :c, error_message = :m, received_size = :sz,"
            " received_sha256 = :h, updated_at = now() WHERE id = :i"
        ),
        {"s": status, "c": code, "m": message, "sz": size, "h": sha, "i": row.id},
    )
    log_event(
        ctx, row.target_document_id, f"upload.{status}",
        summary={"operation_id": str(row.id), "filename": row.original_filename, "reason": code},
        also_audit=status == "quarantined",
    )  # fmt: skip
    op = op_view(_op_row(ctx, row.id))
    return Outcome(
        http,
        {"title": "Upload failed", "status": http, "detail": message, "code": code, "operation": op, **(extra or {})},
    )


def finish_content(ctx: RequestCtx, acc: ws.Access, op_id: uuid.UUID, tmp: Path, size: int, sha: str) -> Outcome:
    assert ctx.org_id is not None and ctx.company_id is not None
    conn, org, cid = ctx.conn, ctx.org_id, ctx.company_id
    row = _op_row(ctx, op_id, lock=True)
    if row.declared_size != size:
        return _fail(
            ctx,
            row,
            "size_mismatch",
            f"Expected {row.declared_size} bytes but received {size}.",
            "failed",
            422,
            size,
            sha,
        )
    if row.declared_sha256 and row.declared_sha256 != sha:
        return _fail(
            ctx,
            row,
            "checksum_mismatch",
            "The received content does not match the checksum announced by the client.",
            "failed",
            422,
            size,
            sha,
        )
    try:
        insp = inspect_file(tmp, row.original_filename, declared_mime=row.declared_mime)
    except FileRejected as exc:
        quarantined = exc.code in QUARANTINE_CODES
        return _fail(ctx, row, exc.code, exc.message, "quarantined" if quarantined else "failed", 422, size, sha)
    if row.target_document_id is None and row.duplicate_policy == "block":
        dups = find_duplicates(ctx, acc, sha)
        if dups:
            return _fail(
                ctx, row, "duplicate_document", "A document with identical content already exists in this company.",
                "failed", 409, size, sha, {"duplicates": dups},
            )  # fmt: skip
    if row.target_document_id is not None:
        cur = conn.execute(
            text("SELECT content_sha256 FROM documents WHERE id = :i"), {"i": row.target_document_id}
        ).scalar()
        if cur == sha:
            return _fail(
                ctx, row, "identical_version", "The file is identical to the current version.", "failed", 409, size, sha
            )
    entitlements.guard_quota(conn, org, "quota.storage_bytes", size)
    scan = scanner.get_scanner().scan_file(tmp)
    if scan.status == "infected":
        return _fail(
            ctx, row, "malware_detected", f"Malware was detected ({scan.detail}).", "quarantined", 422, size, sha
        )
    if scan.status == "error":
        return _fail(
            ctx,
            row,
            "scan_unavailable",
            "The malware scanner could not examine the file; try again later.",
            "failed",
            503,
            size,
            sha,
        )
    backend = storage.get_storage()
    obj_id = new_id()
    key = storage.object_key(org, cid, obj_id)
    try:
        backend.put_file(key, tmp)
        stored = backend.stat(key)
    except storage.StorageError:
        return _fail(
            ctx,
            row,
            "storage_unavailable",
            "The document store could not be reached; try again.",
            "failed",
            503,
            size,
            sha,
        )
    if stored != size:
        with contextlib.suppress(storage.StorageError):
            backend.delete(key)
        return _fail(
            ctx,
            row,
            "storage_verification_failed",
            "The stored object did not match the uploaded size.",
            "failed",
            503,
            size,
            sha,
        )
    conn.execute(
        text(
            "INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256, state)"
            " VALUES (:i, :o, :c, :b, :k, :sz, :h, 'stored')"
        ),
        {"i": obj_id, "o": org, "c": cid, "b": backend.name, "k": key, "sz": size, "h": sha},
    )
    conn.execute(
        text(
            "UPDATE document_upload_operations SET status = 'uploaded', storage_object_id = :so, received_size = :sz,"
            " received_sha256 = :h, verified_mime = :m, scan_status = :sc, updated_at = now() WHERE id = :i"
        ),
        {"so": obj_id, "sz": size, "h": sha, "m": insp.mime_type, "sc": scan.status, "i": row.id},
    )
    log_event(
        ctx,
        row.target_document_id,
        "upload.received",
        summary={"operation_id": str(row.id), "size": size, "sha256": sha, "scan": scan.status},
    )
    return Outcome(200, op_view(_op_row(ctx, row.id)))


def complete_upload(ctx: RequestCtx, acc: ws.Access, op_id: uuid.UUID) -> Outcome:
    assert ctx.org_id is not None and ctx.company_id is not None
    conn, org, cid = ctx.conn, ctx.org_id, ctx.company_id
    row = _op_row(ctx, op_id, lock=True)
    if row.status == "completed":
        return Outcome(200, _result(ctx, acc, row))
    if row.status != "uploaded":
        raise Conflict(
            f"The upload is {row.status}; the content must be uploaded before it can be completed.",
            extra={"status": row.status},
        )
    acc.require("upload", str(row.node_id))
    _node_ok_for_filing(conn, cid, row.node_id)
    obj = conn.execute(text("SELECT * FROM storage_objects WHERE id = :i"), {"i": row.storage_object_id}).one()
    try:
        actual = storage.get_storage().stat(obj.storage_key)
    except storage.StorageError:
        raise StorageUnavailable("The document store could not be reached; try completing again.") from None
    if actual != obj.size_bytes:
        conn.execute(text("UPDATE storage_objects SET state = 'missing' WHERE id = :i"), {"i": obj.id})
        return _fail(
            ctx,
            row,
            "storage_verification_failed",
            "The stored object could not be verified.",
            "failed",
            503,
            obj.size_bytes,
            obj.sha256,
        )
    conn.execute(
        text("UPDATE storage_objects SET state = 'verified', verified_at = now() WHERE id = :i"), {"i": obj.id}
    )
    ext = split_extension(row.original_filename)[1]
    mime = row.verified_mime or ALLOWED[ext][0]
    if row.target_document_id is not None:
        doc = get_document(ctx, acc, row.target_document_id, action="upload", lock=True)
        if doc["status"] != "active":
            raise Conflict("The document is no longer active; new versions cannot be added.")
        version_no = _add_version(ctx, doc, obj, row.original_filename, mime, row.scan_status, row.change_reason, None)
        doc_id = uuid.UUID(doc["id"])
        event = "document.version_added"
    else:
        entitlements.guard_quota(conn, org, "quota.documents_per_month", 1)
        doc_id = new_id()
        name = unique_display_name(conn, cid, row.node_id, row.display_name or row.original_filename)
        conn.execute(
            text(
                "INSERT INTO documents (id, organization_id, company_id, node_id, category_id, display_name,"
                " original_filename, extension, mime_type, size_bytes, content_sha256, source, import_batch_id,"
                " filing_rule_id, created_by, updated_by) VALUES (:i, :o, :c, :n, :cat, :dn, :fn, :ext, :mime, :sz,"
                " :h, 'desktop', :b, :fr, :u, :u)"
            ),
            {
                "i": doc_id, "o": org, "c": cid, "n": row.node_id, "cat": row.category_id, "dn": name,
                "fn": row.original_filename, "ext": ext, "mime": mime, "sz": obj.size_bytes, "h": obj.sha256,
                "b": row.batch_id, "fr": row.filing_rule_id, "u": ctx.user_id,
            },
        )  # fmt: skip
        version_no = _insert_version(ctx, doc_id, 1, obj, row.original_filename, ext, mime, row.scan_status, None, None)
        meta = row.metadata or {}
        if meta:
            ids = {
                f["key"]: f["id"]
                for f in wc.applicable_fields(conn, cid, row.category_id, [str(c) for c in acc.chain(str(row.node_id))])
            }
            for key, value in meta.items():
                if key in ids:
                    conn.execute(
                        text(
                            "INSERT INTO document_metadata_values (id, organization_id, company_id, document_id, field_id, value, updated_by)"
                            " VALUES (:i, :o, :c, :d, :f, CAST(:v AS jsonb), :u)"
                        ),
                        {
                            "i": new_id(),
                            "o": org,
                            "c": cid,
                            "d": doc_id,
                            "f": ids[key],
                            "v": _j(value),
                            "u": ctx.user_id,
                        },
                    )
        event = "document.created"
    conn.execute(
        text(
            "UPDATE document_upload_operations SET status = 'completed', completed_at = now(), updated_at = now(),"
            " result_document_id = :d, result_version_id = (SELECT id FROM document_versions WHERE document_id = :d AND version_no = :v)"
            " WHERE id = :i"
        ),
        {"d": doc_id, "v": version_no, "i": row.id},
    )
    log_event(
        ctx, doc_id, event, version_no=version_no, also_audit=True,
        summary={"operation_id": str(row.id), "filename": row.original_filename, "size": obj.size_bytes, "sha256": obj.sha256, "node_id": str(row.node_id)},
    )  # fmt: skip
    entitlements.refresh_counter(conn, org, "quota.storage_bytes")
    entitlements.refresh_counter(conn, org, "quota.documents_per_month")
    return Outcome(200, _result(ctx, acc, _op_row(ctx, op_id)))


def _result(ctx: RequestCtx, acc: ws.Access, row: Any) -> dict[str, Any]:
    doc = get_document(ctx, acc, row.result_document_id, allow_deleted=True)
    return {"operation": op_view(row), "document": doc}


def _insert_version(
    ctx: RequestCtx, doc_id: uuid.UUID | str, version_no: int, obj: Any, filename: str, ext: str, mime: str,
    scan_status: str, reason: str | None, restored_from: int | None,
) -> int:  # fmt: skip
    assert ctx.org_id is not None and ctx.company_id is not None
    ctx.conn.execute(
        text(
            "INSERT INTO document_versions (id, organization_id, company_id, document_id, version_no, original_filename,"
            " extension, mime_type, size_bytes, sha256, storage_object_id, uploaded_by, change_reason,"
            " restored_from_version, scan_status) VALUES (:i, :o, :c, :d, :v, :fn, :ext, :mime, :sz, :h, :so, :u, :cr, :rf, :sc)"
        ),
        {
            "i": new_id(), "o": ctx.org_id, "c": ctx.company_id, "d": doc_id, "v": version_no, "fn": filename,
            "ext": ext, "mime": mime, "sz": obj.size_bytes, "h": obj.sha256, "so": obj.id, "u": ctx.user_id,
            "cr": reason, "rf": restored_from, "sc": scan_status,
        },
    )  # fmt: skip
    return version_no


def _add_version(
    ctx: RequestCtx, doc: dict[str, Any], obj: Any, filename: str, mime: str, scan_status: str, reason: str | None,
    restored_from: int | None,
) -> int:  # fmt: skip
    version_no = int(doc["current_version_no"]) + 1
    _insert_version(
        ctx, doc["id"], version_no, obj, filename, doc["extension"], mime, scan_status, reason, restored_from
    )
    ctx.conn.execute(
        text(
            "UPDATE documents SET current_version_no = :v, size_bytes = :sz, content_sha256 = :h, mime_type = :m,"
            " original_filename = :fn, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {
            "v": version_no,
            "sz": obj.size_bytes,
            "h": obj.sha256,
            "m": mime,
            "fn": filename,
            "u": ctx.user_id,
            "i": doc["id"],
        },
    )
    return version_no


def cancel_upload(ctx: RequestCtx, op_id: uuid.UUID) -> dict[str, Any]:
    row = _op_row(ctx, op_id, lock=True)
    if row.status == "completed":
        raise Conflict("The upload already completed; archive or delete the document instead.")
    if row.status in ("cancelled", "expired"):
        return op_view(row)
    _discard_object(ctx, row)
    ctx.conn.execute(
        text("UPDATE document_upload_operations SET status = 'cancelled', updated_at = now() WHERE id = :i"),
        {"i": row.id},
    )
    log_event(
        ctx,
        row.target_document_id,
        "upload.cancelled",
        summary={"operation_id": str(row.id), "filename": row.original_filename},
    )
    return op_view(_op_row(ctx, op_id))


def retry_upload(ctx: RequestCtx, op_id: uuid.UUID) -> dict[str, Any]:
    row = _op_row(ctx, op_id, lock=True)
    if row.status != "failed" or row.error_code not in TRANSIENT_CODES:
        raise Conflict(
            "Only uploads that failed for a transient reason can be retried.",
            extra={"status": row.status, "error_code": row.error_code},
        )
    if row.expires_at < _now():
        raise Conflict("This upload session expired. Start the upload again.", extra={"status": "expired"})
    _discard_object(ctx, row)
    ctx.conn.execute(
        text(
            "UPDATE document_upload_operations SET status = 'awaiting_upload', error_code = NULL, error_message = NULL,"
            " storage_object_id = NULL, updated_at = now() WHERE id = :i"
        ),
        {"i": row.id},
    )
    return op_view(_op_row(ctx, op_id))


def _discard_object(ctx: RequestCtx, row: Any) -> None:
    """Remove content that was received but never attached to a document."""
    if row.storage_object_id is None:
        return
    obj = ctx.conn.execute(
        text("SELECT id, storage_key FROM storage_objects WHERE id = :i"), {"i": row.storage_object_id}
    ).first()
    if obj is None:
        return
    ctx.conn.execute(
        text("UPDATE storage_objects SET state = 'purged', purged_at = now() WHERE id = :i"), {"i": obj.id}
    )
    with contextlib.suppress(storage.StorageError):  # reconciliation removes a leftover object (its row is 'purged')
        storage.get_storage().delete(obj.storage_key)


# ---------------------------------------------------------------- document operations
def _bump(ctx: RequestCtx, doc: dict[str, Any], base_row_version: int | None) -> None:
    if base_row_version is not None and base_row_version != doc["row_version"]:
        raise VersionConflict(
            "The document was changed by someone else. Reload and try again.",
            extra={"current_row_version": doc["row_version"], "base_row_version": base_row_version},
        )


def update_document(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, patch: dict[str, Any]) -> dict[str, Any]:
    assert ctx.company_id is not None
    conn, cid = ctx.conn, ctx.company_id
    changes: dict[str, Any] = {}
    doc = get_document(ctx, acc, doc_id, action="edit", lock=True)
    _bump(ctx, doc, patch.get("base_row_version"))
    if doc["status"] != "active":
        raise Conflict("Only active documents can be edited; restore the document first.")
    nid = doc["node_id"]
    if patch.get("display_name") is not None:
        acc.require("rename", nid)
        new_name = _with_ext(patch["display_name"], doc["extension"])
        if new_name.lower() != doc["display_name"].lower():
            final = unique_display_name(conn, cid, nid, new_name, exclude=doc["id"])
            if final != new_name:
                raise Conflict(f"'{new_name}' already exists here.", extra={"suggestion": final})
            changes["name"] = {"from": doc["display_name"], "to": new_name}
            conn.execute(text("UPDATE documents SET display_name = :n WHERE id = :i"), {"n": new_name, "i": doc["id"]})
        elif new_name != doc["display_name"]:
            conn.execute(text("UPDATE documents SET display_name = :n WHERE id = :i"), {"n": new_name, "i": doc["id"]})
    chain = [str(c) for c in acc.chain(nid)]
    if "category_id" in patch:
        _check_category(conn, cid, patch["category_id"], chain, doc["extension"])
        changes["category"] = {
            "from": doc["category_id"],
            "to": str(patch["category_id"]) if patch["category_id"] else None,
        }
        conn.execute(
            text("UPDATE documents SET category_id = :c WHERE id = :i"), {"c": patch["category_id"], "i": doc["id"]}
        )
        doc["category_id"] = str(patch["category_id"]) if patch["category_id"] else None
    if patch.get("metadata") is not None:
        changes["metadata"] = _set_metadata(ctx, doc, chain, patch["metadata"])
    conn.execute(
        text("UPDATE documents SET updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"),
        {"u": ctx.user_id, "i": doc["id"]},
    )
    if changes:
        log_event(ctx, doc["id"], "document.updated", summary=changes, also_audit="name" in changes)
    return document_detail(ctx, acc, get_document(ctx, acc, doc_id))


def _set_metadata(ctx: RequestCtx, doc: dict[str, Any], chain: list[str], patch: dict[str, Any]) -> dict[str, Any]:
    assert ctx.org_id is not None and ctx.company_id is not None
    conn, cid = ctx.conn, ctx.company_id
    fields = wc.applicable_fields(conn, cid, doc["category_id"], chain)
    keys = {f["key"]: f["id"] for f in fields}
    unknown = sorted(set(patch) - set(keys))
    if unknown:
        raise Unprocessable(
            "Some fields do not apply to this document.",
            extra={"problems": [{"field": k, "message": "does not apply"} for k in unknown]},
        )
    current = {
        r.key: r.value
        for r in conn.execute(
            text(
                "SELECT fd.key, m.value FROM document_metadata_values m JOIN custom_field_definitions fd ON fd.id = m.field_id WHERE m.document_id = :d"
            ),
            {"d": doc["id"]},
        )
        if r.key in keys
    }
    merged = {**current, **patch}
    merged = {k: v for k, v in merged.items() if v is not None}
    normalized, problems = validate_values([wc.to_field_def(f) for f in fields], merged, wc.ref_exists_fn(conn, cid))
    if problems:
        raise Unprocessable("The metadata is not valid.", extra={"problems": problems})
    for key, fid in keys.items():
        conn.execute(
            text("DELETE FROM document_metadata_values WHERE document_id = :d AND field_id = :f"),
            {"d": doc["id"], "f": fid},
        )
        if key in normalized and normalized[key] is not None:
            conn.execute(
                text(
                    "INSERT INTO document_metadata_values (id, organization_id, company_id, document_id, field_id, value, updated_by)"
                    " VALUES (:i, :o, :c, :d, :f, CAST(:v AS jsonb), :u)"
                ),
                {
                    "i": new_id(),
                    "o": ctx.org_id,
                    "c": cid,
                    "d": doc["id"],
                    "f": fid,
                    "v": _j(normalized[key]),
                    "u": ctx.user_id,
                },
            )
    return {"fields": sorted(patch)}


def move_document(
    ctx: RequestCtx,
    acc: ws.Access,
    doc_id: uuid.UUID,
    node_id: uuid.UUID,
    category_id: Any,
    base_row_version: int | None,
    has_category: bool,
) -> dict[str, Any]:
    assert ctx.company_id is not None
    conn, cid = ctx.conn, ctx.company_id
    doc = get_document(ctx, acc, doc_id, action="move", lock=True)
    _bump(ctx, doc, base_row_version)
    if doc["status"] != "active":
        raise Conflict("Only active documents can be moved.")
    target = str(node_id)
    if target not in acc.nodes or not acc.visible(target):
        raise NotFound("Workspace item not found.")
    acc.require("upload", target)
    _node_ok_for_filing(conn, cid, target)
    if target == doc["node_id"]:
        raise Conflict("The document is already in this location.")
    chain = [str(c) for c in acc.chain(target)]
    cat = category_id if has_category else doc["category_id"]
    _check_category(conn, cid, cat, chain, doc["extension"])
    name = unique_display_name(conn, cid, target, doc["display_name"], exclude=doc["id"])
    conn.execute(
        text(
            "UPDATE documents SET node_id = :n, category_id = :c, display_name = :dn, updated_by = :u, updated_at = now(),"
            " row_version = row_version + 1 WHERE id = :i"
        ),
        {"n": target, "c": cat, "dn": name, "u": ctx.user_id, "i": doc["id"]},
    )
    log_event(
        ctx,
        doc["id"],
        "document.moved",
        also_audit=True,
        summary={"from": doc["node_id"], "to": target, "renamed": name != doc["display_name"], "name": name},
    )
    return document_detail(ctx, acc, get_document(ctx, acc, doc_id))


def set_status(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, action: str, base_row_version: int | None = None
) -> dict[str, Any]:
    """archive | restore | delete | undelete — each is reversible; none touches the stored bytes."""
    assert ctx.company_id is not None
    needs = {"archive": "archive", "restore": "archive", "delete": "delete", "undelete": "delete"}[action]
    doc = get_document(ctx, acc, doc_id, action=needs, lock=True, allow_deleted=action == "undelete")
    _bump(ctx, doc, base_row_version)
    st = doc["status"]
    if action == "archive" and st != "active":
        raise Conflict("Only active documents can be archived.")
    if action == "restore" and st != "archived":
        raise Conflict("Only archived documents can be restored.")
    if action == "delete" and st not in ("active", "archived"):
        raise Conflict("The document is already deleted.")
    if action == "undelete" and st != "deleted":
        raise Conflict("The document is not in the deleted items.")
    new_status = {"archive": "archived", "restore": "active", "delete": "deleted", "undelete": "active"}[action]
    name = doc["display_name"]
    if new_status == "active":
        _node_ok_for_filing(
            ctx.conn, ctx.company_id, doc["node_id"]
        )  # a document cannot come back into an archived folder
        name = unique_display_name(ctx.conn, ctx.company_id, doc["node_id"], name, exclude=doc["id"])
    ctx.conn.execute(
        text(
            "UPDATE documents SET status = :s, display_name = :n, updated_by = :u, updated_at = now(), row_version = row_version + 1,"
            " archived_at = CASE WHEN :s = 'archived' THEN now() ELSE NULL END,"
            " deleted_at = CASE WHEN :s = 'deleted' THEN now() ELSE NULL END,"
            " deleted_by = CASE WHEN :s = 'deleted' THEN CAST(:u AS uuid) ELSE NULL END WHERE id = :i"
        ),
        {"s": new_status, "n": name, "u": ctx.user_id, "i": doc["id"]},
    )
    log_event(
        ctx,
        doc["id"],
        f"document.{ {'archive': 'archived', 'restore': 'restored', 'delete': 'deleted', 'undelete': 'undeleted'}[action] }",
        also_audit=True,
        summary={"from": st, "to": new_status, "renamed": name != doc["display_name"]},
    )
    return document_detail(ctx, acc, get_document(ctx, acc, doc_id, allow_deleted=True))


def set_hold(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, hold: bool, reason: str | None) -> dict[str, Any]:
    doc = get_document(ctx, acc, doc_id, action="delete", lock=True, allow_deleted=True)
    ctx.conn.execute(
        text(
            "UPDATE documents SET retention_hold = :h, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"h": hold, "u": ctx.user_id, "i": doc["id"]},
    )
    log_event(
        ctx,
        doc["id"],
        "document.hold_set" if hold else "document.hold_released",
        also_audit=True,
        summary={"reason": reason},
    )
    return document_detail(ctx, acc, get_document(ctx, acc, doc_id, allow_deleted=True))


def purge_document(ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID) -> dict[str, Any]:
    """Permanent deletion: only from the deleted items, never under retention hold. Removes every stored object of the
    document, scrubs its names and metadata, and keeps a tombstone plus the immutable activity trail."""
    assert ctx.org_id is not None and ctx.company_id is not None
    conn = ctx.conn
    doc = get_document(ctx, acc, doc_id, action="delete", lock=True, allow_deleted=True)
    if doc["status"] != "deleted":
        raise Conflict("Move the document to the deleted items before deleting it permanently.")
    if doc["retention_hold"]:
        raise RetentionHold("The document is on retention hold and cannot be permanently deleted.")
    objs = conn.execute(
        text(
            "SELECT DISTINCT so.id, so.storage_key, so.size_bytes FROM document_versions v JOIN storage_objects so ON so.id = v.storage_object_id"
            " WHERE v.document_id = :d AND so.state <> 'purged'"
        ),
        {"d": doc["id"]},
    ).all()
    conn.execute(text("DELETE FROM document_metadata_values WHERE document_id = :d"), {"d": doc["id"]})
    conn.execute(
        text("UPDATE storage_objects SET state = 'purged', purged_at = now() WHERE id = ANY(:ids)"),
        {"ids": [o.id for o in objs]},
    )
    conn.execute(
        text(
            "UPDATE documents SET purged_at = now(), display_name = :ph, original_filename = :ph2, updated_by = :u, updated_at = now(),"
            " row_version = row_version + 1 WHERE id = :i"
        ),
        {"ph": f"purged-{str(doc['id'])[:8]}", "ph2": f"purged-{str(doc['id'])[:8]}", "u": ctx.user_id, "i": doc["id"]},
    )
    leftovers = 0
    for o in objs:
        try:
            storage.get_storage().delete(o.storage_key)
        except storage.StorageError:
            leftovers += 1  # the row is 'purged'; reconciliation removes the leftover object
    log_event(
        ctx,
        doc["id"],
        "document.purged",
        also_audit=True,
        summary={"objects": len(objs), "bytes": sum(o.size_bytes for o in objs), "leftover_objects": leftovers},
    )
    entitlements.refresh_counter(conn, ctx.org_id, "quota.storage_bytes")
    return {
        "id": doc["id"],
        "purged": True,
        "objects_removed": len(objs) - leftovers,
        "objects_pending_cleanup": leftovers,
    }


def duplicate_document(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, node_id: uuid.UUID | None, display_name: str | None
) -> dict[str, Any]:
    """An independent copy (own storage object, version 1, copied metadata). Counts against quotas like any upload."""
    assert ctx.org_id is not None and ctx.company_id is not None
    conn, org, cid = ctx.conn, ctx.org_id, ctx.company_id
    src = get_document(ctx, acc, doc_id, action="view")
    if src["status"] == "deleted":
        raise Conflict("Restore the document before duplicating it.")
    target = str(node_id) if node_id else src["node_id"]
    if target not in acc.nodes or not acc.visible(target):
        raise NotFound("Workspace item not found.")
    acc.require("upload", target)
    _node_ok_for_filing(conn, cid, target)
    entitlements.guard_quota(conn, org, "quota.storage_bytes", src["size_bytes"])
    entitlements.guard_quota(conn, org, "quota.documents_per_month", 1)
    chain = [str(c) for c in acc.chain(target)]
    cat = src["category_id"]
    try:
        _check_category(conn, cid, cat, chain, src["extension"])
    except Unprocessable:
        cat = None
    ver = conn.execute(
        text(
            "SELECT so.* FROM document_versions v JOIN storage_objects so ON so.id = v.storage_object_id"
            " WHERE v.document_id = :d AND v.version_no = :n"
        ),
        {"d": src["id"], "n": src["current_version_no"]},
    ).one()
    backend = storage.get_storage()
    obj_id = new_id()
    key = storage.object_key(org, cid, obj_id)
    with tempfile.TemporaryDirectory(dir=get_settings().storage_tmp_dir) as td:
        tmp = Path(td) / "copy"
        try:
            with tmp.open("wb") as f:
                for chunk in backend.open_range(ver.storage_key):
                    f.write(chunk)
            backend.put_file(key, tmp)
            if backend.stat(key) != ver.size_bytes:
                raise storage.StorageError("size mismatch after copy")
        except storage.StorageError:
            raise StorageUnavailable("The document store could not copy the file; try again.") from None
    conn.execute(
        text(
            "INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256, state, verified_at)"
            " VALUES (:i, :o, :c, :b, :k, :sz, :h, 'verified', now())"
        ),
        {"i": obj_id, "o": org, "c": cid, "b": backend.name, "k": key, "sz": ver.size_bytes, "h": ver.sha256},
    )
    stem, ext = src["display_name"].rpartition(".")[0], src["extension"]
    base = (display_name and _with_ext(display_name, ext)) or f"{stem} - copy.{ext}"
    name = unique_display_name(conn, cid, target, base)
    new_doc = new_id()
    conn.execute(
        text(
            "INSERT INTO documents (id, organization_id, company_id, node_id, category_id, display_name, original_filename, extension,"
            " mime_type, size_bytes, content_sha256, source, created_by, updated_by) VALUES (:i, :o, :c, :n, :cat, :dn, :fn, :ext, :m, :sz, :h,"
            " 'desktop', :u, :u)"
        ),
        {
            "i": new_doc,
            "o": org,
            "c": cid,
            "n": target,
            "cat": cat,
            "dn": name,
            "fn": src["original_filename"],
            "ext": ext,
            "m": src["mime_type"],
            "sz": ver.size_bytes,
            "h": ver.sha256,
            "u": ctx.user_id,
        },
    )
    _insert_version(
        ctx,
        new_doc,
        1,
        ver_to_obj(obj_id, ver),
        src["original_filename"],
        ext,
        src["mime_type"],
        "not_scanned",
        f"Duplicate of {src['display_name']}",
        None,
    )
    _copy_meta(ctx, src["id"], new_doc)
    log_event(
        ctx,
        new_doc,
        "document.created",
        also_audit=True,
        summary={"duplicate_of": src["id"], "filename": name, "size": ver.size_bytes, "sha256": ver.sha256},
    )
    entitlements.refresh_counter(conn, org, "quota.storage_bytes")
    entitlements.refresh_counter(conn, org, "quota.documents_per_month")
    return document_detail(ctx, acc, get_document(ctx, acc, new_doc))


@dataclass
class _Obj:
    id: uuid.UUID
    size_bytes: int
    sha256: str


def ver_to_obj(obj_id: uuid.UUID, ver: Any) -> _Obj:
    return _Obj(obj_id, ver.size_bytes, ver.sha256)


def _copy_meta(ctx: RequestCtx, src_id: Any, dst_id: uuid.UUID) -> None:
    assert ctx.org_id is not None and ctx.company_id is not None
    for r in ctx.conn.execute(
        text("SELECT field_id, value FROM document_metadata_values WHERE document_id = :d"), {"d": src_id}
    ).all():
        ctx.conn.execute(
            text(
                "INSERT INTO document_metadata_values (id, organization_id, company_id, document_id, field_id, value, updated_by)"
                " VALUES (:i, :o, :c, :d, :f, CAST(:v AS jsonb), :u)"
            ),
            {
                "i": new_id(),
                "o": ctx.org_id,
                "c": ctx.company_id,
                "d": dst_id,
                "f": r.field_id,
                "v": _j(r.value),
                "u": ctx.user_id,
            },
        )


def restore_version(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, version_no: int, reason: str | None
) -> dict[str, Any]:
    """Make an older version current again by adding a NEW version that points at the same stored object."""
    doc = get_document(ctx, acc, doc_id, action="upload", lock=True)
    if doc["status"] != "active":
        raise Conflict("Only active documents can receive a restored version.")
    v = ctx.conn.execute(
        text(
            "SELECT v.*, so.id AS oid FROM document_versions v JOIN storage_objects so ON so.id = v.storage_object_id WHERE v.document_id = :d AND v.version_no = :n"
        ),
        {"d": doc["id"], "n": version_no},
    ).first()
    if v is None:
        raise NotFound("Version not found.")
    if v.version_no == doc["current_version_no"] or v.sha256 == doc["content_sha256"]:
        raise Conflict("That version is already the current content.")
    new_no = _add_version(
        ctx,
        doc,
        _Obj(v.oid, v.size_bytes, v.sha256),
        v.original_filename,
        v.mime_type,
        v.scan_status,
        reason or f"Restored version {version_no}",
        version_no,
    )
    log_event(
        ctx,
        doc["id"],
        "document.version_restored",
        version_no=new_no,
        also_audit=True,
        summary={"restored_from": version_no},
    )
    return document_detail(ctx, acc, get_document(ctx, acc, doc_id))


def version_content_source(
    ctx: RequestCtx, acc: ws.Access, doc_id: uuid.UUID, version_no: int | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    doc = get_document(ctx, acc, doc_id, action="view")
    no = version_no or doc["current_version_no"]
    v = ctx.conn.execute(
        text(
            "SELECT v.version_no, v.original_filename, v.extension, v.mime_type, v.size_bytes, v.sha256, so.storage_key, so.state"
            " FROM document_versions v JOIN storage_objects so ON so.id = v.storage_object_id WHERE v.document_id = :d AND v.version_no = :n"
        ),
        {"d": doc["id"], "n": no},
    ).first()
    if v is None or v.state == "purged":
        raise NotFound("Version not found.")
    if v.state == "missing":
        raise StorageUnavailable("The stored file is missing. Contact support; the incident has been recorded.")
    return doc, ws.row_dict(v)


# ---------------------------------------------------------------- naming and filing suggestions (advisory only)
def suggest(ctx: RequestCtx, acc: ws.Access, body: dict[str, Any]) -> dict[str, Any]:
    """Which workspace location and which name would the company's rules choose? Nothing is filed or renamed: the
    caller decides, and may record the rule id on the upload for traceability. Deterministic; no AI involved."""
    assert ctx.company_id is not None
    conn, cid = ctx.conn, ctx.company_id
    filename = _safe_name(body["filename"])
    _stem, ext = split_extension(filename)
    attrs: dict[str, Any] = {
        "filename": filename,
        "source": body.get("source") or "desktop",
        **(body.get("attributes") or {}),
    }
    category_id = body.get("category_id")
    if category_id:
        cat = conn.execute(
            text(
                "SELECT document_kind FROM document_categories WHERE company_id = :c AND id = :i AND archived_at IS NULL"
            ),
            {"c": cid, "i": category_id},
        ).first()
        if cat is None:
            raise Unprocessable("The selected category does not exist.")
        attrs.setdefault("document_kind", cat.document_kind)
        attrs.setdefault("category_id", str(category_id))
    winner, hits = evaluate_rules(wc.rule_defs(conn, cid), attrs)
    target_node = str(winner.target_node_id) if winner else None
    target_cat = (
        str(winner.target_category_id)
        if winner and winner.target_category_id
        else (str(category_id) if category_id else None)
    )
    if target_node and (target_node not in acc.nodes or not acc.can("upload", target_node)):
        winner, target_node = None, None  # never suggest a place the caller cannot file into
    node_for_name = target_node or (str(body["node_id"]) if body.get("node_id") else None)
    pattern = None
    for cond in ("category_id IS NOT DISTINCT FROM CAST(:cat AS uuid)", "category_id IS NULL"):
        r = conn.execute(
            text(
                f"SELECT pattern FROM naming_rules WHERE company_id = :c AND archived_at IS NULL AND is_active AND {cond}"
            ),
            {"c": cid, "cat": target_cat},
        ).first()
        if r:
            pattern = r.pattern
            break
    comp = conn.execute(
        text("SELECT financial_year_start_month, name, code FROM companies WHERE id = :c"), {"c": cid}
    ).one()
    values = {"company": comp.name, "companycode": comp.code, **(body.get("values") or {})}
    warnings: list[str] = []
    if pattern is None:
        generated = fallback_name(
            values.get("documenttype") or attrs.get("document_kind"),
            values.get("documentdate"),
            uuid.uuid4().hex[:8],
            ext,
        )
        warnings.append("No naming rule applies; the deterministic fallback name is used.")
        used_rule = False
    else:
        try:
            parts = parse_pattern(pattern, wc.active_field_keys(conn, cid))
        except Exception as exc:  # a stored pattern should always parse; stay honest if it does not
            raise Unprocessable(f"The naming rule could not be applied: {exc}") from exc
        generated, warnings = render_name(
            parts, values, extension=ext, financial_year_start_month=comp.financial_year_start_month
        )
        used_rule = True
    final = generated
    if node_for_name and node_for_name in acc.nodes:
        final = unique_display_name(conn, cid, node_for_name, generated)
    return {
        "original_filename": filename,
        "filing": {
            "rule_id": str(winner.id) if winner else None,
            "rule_name": winner.name if winner else None,
            "node_id": target_node,
            "category_id": target_cat,
            "ambiguous": len({(r.target_node_id, r.target_category_id) for r in hits}) > 1,
            "matches": [str(r.id) for r in hits],
        },
        "naming": {
            "suggested_name": final,
            "used_naming_rule": used_rule,
            "renamed_to_avoid_overwrite": final != generated,
            "warnings": warnings,
        },
        "advisory": True,
    }


# ---------------------------------------------------------------- storage usage and reconciliation (organization level)
def storage_overview(ctx: RequestCtx) -> dict[str, Any]:
    assert ctx.org_id is not None
    conn, org = ctx.conn, ctx.org_id
    limit = entitlements.effective_limit(conn, org, "quota.storage_bytes")
    docs_limit = entitlements.effective_limit(conn, org, "quota.documents_per_month")
    used = entitlements.measure(conn, org, "quota.storage_bytes")
    month_docs = entitlements.measure(conn, org, "quota.documents_per_month")
    per_company = [
        ws.row_dict(r)
        for r in conn.execute(
            text(
                "SELECT c.id AS company_id, c.name, CAST(COALESCE(s.bytes, 0) AS bigint) AS stored_bytes, COALESCE(d.active, 0) AS active_documents,"
                " COALESCE(d.archived, 0) AS archived_documents, COALESCE(d.deleted, 0) AS deleted_documents"
                " FROM companies c LEFT JOIN (SELECT company_id, sum(size_bytes) AS bytes FROM storage_objects WHERE state IN ('stored','verified') GROUP BY company_id) s ON s.company_id = c.id"
                " LEFT JOIN (SELECT company_id, count(*) FILTER (WHERE status='active') AS active, count(*) FILTER (WHERE status='archived') AS archived,"
                " count(*) FILTER (WHERE status='deleted') AS deleted FROM documents WHERE purged_at IS NULL GROUP BY company_id) d ON d.company_id = c.id"
                " WHERE c.status <> 'archived' ORDER BY COALESCE(s.bytes, 0) DESC, c.name"
            )
        )
    ]
    uploads = {
        r.status: int(r.n)
        for r in conn.execute(
            text(
                "SELECT status, count(*) AS n FROM document_upload_operations WHERE created_at > now() - interval '7 days' GROUP BY status"
            )
        )
    }
    failed = [
        ws.row_dict(r)
        for r in conn.execute(
            text(
                "SELECT o.id, o.company_id, c.name AS company_name, o.original_filename, o.status, o.error_code, o.error_message, o.updated_at"
                " FROM document_upload_operations o JOIN companies c ON c.id = o.company_id WHERE o.status IN ('failed','quarantined')"
                " AND o.updated_at > now() - interval '7 days' ORDER BY o.updated_at DESC LIMIT 20"
            )
        )
    ]
    return {
        "storage": {
            "used_bytes": used,
            "limit_bytes": None if limit.unlimited else limit.value,
            "unlimited": limit.unlimited,
            "source": limit.source,
        },
        "documents_this_month": {
            "used": month_docs,
            "limit": None if docs_limit.unlimited else docs_limit.value,
            "unlimited": docs_limit.unlimited,
        },
        "max_file_size_bytes": entitlements.max_file_size(conn, org, get_settings().max_file_size_ceiling_bytes),
        "accounting": "Stored bytes count every version object that has not been permanently deleted, including archived and deleted-item documents.",
        "backend": storage.get_storage().name,
        "scanner": {"name": scanner.get_scanner().name, "active": scanner.get_scanner().active},
        "companies": per_company,
        "uploads_last_7_days": uploads,
        "recent_failures": failed,
    }


def reconcile_storage(ctx: RequestCtx, *, repair: bool, min_age_seconds: int) -> dict[str, Any]:
    """Compare the database and the object store for this organization.

    * rows whose object is gone are marked `missing` (never silently deleted);
    * objects without a live row (or whose row is purged) older than `min_age_seconds` are orphans, removed only
      when `repair` is true;
    * upload sessions past their expiry are marked expired and their unattached content is discarded (repair only)."""
    assert ctx.org_id is not None
    conn, org = ctx.conn, ctx.org_id
    backend = storage.get_storage()
    rows = conn.execute(
        text("SELECT id, company_id, storage_key, size_bytes, state FROM storage_objects WHERE organization_id = :o"),
        {"o": org},
    ).all()
    by_key = {r.storage_key: r for r in rows}
    missing: list[str] = []
    size_mismatch: list[str] = []
    for r in rows:
        if r.state not in ("stored", "verified"):
            continue
        actual = backend.stat(r.storage_key)
        if actual is None:
            missing.append(r.storage_key)
            if repair:
                conn.execute(text("UPDATE storage_objects SET state = 'missing' WHERE id = :i"), {"i": r.id})
        elif actual != r.size_bytes:
            size_mismatch.append(r.storage_key)
            if repair:
                conn.execute(text("UPDATE storage_objects SET state = 'missing' WHERE id = :i"), {"i": r.id})
    now = datetime.now(UTC).timestamp()
    orphans: list[str] = []
    for o in backend.list_objects(f"t/{org}/"):
        row = by_key.get(o.key)
        if (row is None or row.state == "purged") and now - o.modified >= min_age_seconds:
            orphans.append(o.key)
            if repair:
                backend.delete(o.key)
    expired = conn.execute(
        text(
            "SELECT id, storage_object_id FROM document_upload_operations WHERE organization_id = :o AND expires_at < now()"
            " AND status IN ('awaiting_upload','uploading','uploaded','failed')"
        ),
        {"o": org},
    ).all()
    if repair:
        for e in expired:
            if e.storage_object_id:
                obj = conn.execute(
                    text("SELECT id, storage_key FROM storage_objects WHERE id = :i"), {"i": e.storage_object_id}
                ).first()
                if obj:
                    conn.execute(
                        text("UPDATE storage_objects SET state = 'purged', purged_at = now() WHERE id = :i"),
                        {"i": obj.id},
                    )
                    with contextlib.suppress(storage.StorageError):
                        backend.delete(obj.storage_key)
            conn.execute(
                text("UPDATE document_upload_operations SET status = 'expired', updated_at = now() WHERE id = :i"),
                {"i": e.id},
            )
        entitlements.refresh_counter(conn, org, "quota.storage_bytes")
    summary = {
        "repair": repair,
        "objects_in_database": len(rows),
        "missing_objects": len(missing),
        "size_mismatches": len(size_mismatch),
        "orphan_objects": len(orphans),
        "expired_uploads": len(expired),
    }
    audit.write_audit(
        conn,
        action="storage.reconciled",
        actor_type="user",
        actor_id=ctx.user_id,
        org_id=org,
        entity_type="storage",
        summary=summary,
        meta=ctx.meta,
    )
    return {
        **summary,
        "missing_sample": missing[:20],
        "size_mismatch_sample": size_mismatch[:20],
        "orphan_sample": orphans[:20],
    }
