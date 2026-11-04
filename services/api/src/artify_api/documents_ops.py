"""Document operations on stored documents: search, rename, move, archive, delete, purge, versions, content, usage.

Authorization is per folder (workspace node): `view` lists/opens/downloads, `upload` (contribute) changes a document,
`delete` soft-deletes, and permanent purge needs the organization-level `documents.purge` permission, no legal hold and
a waiting period. A document the caller cannot view is a 404 everywhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from artify_api import entitlements
from artify_api.documents import (
    DOC_SELECT,
    FAMILIES,
    DocumentError,
    Env,
    _checked_name,
    _event,
    _live_node,
    _store_field_values,
    cat_ext_check,
    document_out,
    field_values_of,
    get_document,
    node_path,
    validate_metadata,
    with_suppress_delete,
)
from artify_api.errors import NotFound
from artify_api.settings import get_settings
from artify_api.storage import StorageError, get_storage
from artify_api.workspace_engine import ensure_unique_name

SORTS = {
    "name": "lower(d.display_name)",
    "created": "d.created_at",
    "updated": "d.updated_at",
    "size": "v.size_bytes",
    "type": "d.extension",
}


def visible_node_ids(env: Env) -> list[uuid.UUID]:
    return [uuid.UUID(n) for n in env.acc.nodes if env.acc.visible(n)]


# ---------------------------------------------------------------- listing / search
def list_documents(
    env: Env,
    *,
    q: str | None,
    node_id: uuid.UUID | None,
    include_subfolders: bool,
    category_id: uuid.UUID | None,
    status: str,
    family: str | None,
    ext: str | None,
    uploaded_by: uuid.UUID | None,
    created_from: datetime | None,
    created_to: datetime | None,
    fields: list[tuple[str, str]],
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    nodes = visible_node_ids(env)
    where = ["d.company_id = :c", "d.status = :st", "d.node_id = ANY(:vis)"]
    params: dict[str, Any] = {"c": env.company_id, "st": status, "vis": nodes}
    if node_id is not None:
        if not env.acc.visible(str(node_id)):
            raise NotFound("Workspace item not found.")
        if include_subfolders:
            where.append(
                "(d.node_id = CAST(:n AS uuid) OR EXISTS (SELECT 1 FROM workspace_nodes wn WHERE wn.id = d.node_id"
                " AND CAST(:n AS uuid) = ANY(wn.path_ids)))"
            )
        else:
            where.append("d.node_id = :n")
        params["n"] = node_id
    if category_id is not None:
        where.append("d.category_id = :cat")
        params["cat"] = category_id
    if family:
        where.append("d.extension = ANY(:fam)")
        params["fam"] = list(FAMILIES.get(family, ()))
    if ext:
        where.append("d.extension = :ext")
        params["ext"] = ext.lower().lstrip(".")
    if uploaded_by is not None:
        where.append("d.created_by = :ub")
        params["ub"] = uploaded_by
    if created_from is not None:
        where.append("d.created_at >= :cf")
        params["cf"] = created_from
    if created_to is not None:
        where.append("d.created_at <= :ct")
        params["ct"] = created_to
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            "(lower(d.display_name) LIKE lower(:q) ESCAPE '\\' OR lower(d.original_filename) LIKE lower(:q) ESCAPE '\\'"
            " OR EXISTS (SELECT 1 FROM document_field_values f WHERE f.document_id = d.id AND lower(f.value_text) LIKE lower(:q) ESCAPE '\\'))"
        )
        params["q"] = like
    for i, (key, value) in enumerate(fields[:5]):
        where.append(
            f"EXISTS (SELECT 1 FROM document_field_values f{i} WHERE f{i}.document_id = d.id AND f{i}.field_key = :fk{i}"
            f" AND (lower(f{i}.value_text) = lower(:fv{i}) OR f{i}.value_number = (CASE WHEN :fv{i} ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN CAST(:fv{i} AS numeric) END) OR f{i}.value_date::text = :fv{i}))"
        )
        params[f"fk{i}"], params[f"fv{i}"] = key, value
    sql_where = " AND ".join(where)
    order_by = f"{SORTS.get(sort, SORTS['created'])} {'ASC' if order == 'asc' else 'DESC'}, d.id"
    total = env.conn.execute(
        text(
            f"SELECT count(*) FROM documents d JOIN document_versions v ON v.id = d.current_version_id WHERE {sql_where}"
        ),
        params,
    ).scalar_one()
    rows = env.conn.execute(
        text(f"{DOC_SELECT} WHERE {sql_where} ORDER BY {order_by} LIMIT :l OFFSET :o"),
        {**params, "l": limit, "o": offset},
    ).mappings()
    return {"items": [document_out(env, dict(r)) for r in rows], "total": total, "limit": limit, "offset": offset}


def counts(env: Env) -> dict[str, Any]:
    rows = env.conn.execute(
        text(
            "SELECT node_id, status, count(*) AS n, COALESCE(sum(v.size_bytes), 0) AS bytes FROM documents d"
            " JOIN document_versions v ON v.id = d.current_version_id WHERE d.company_id = :c AND d.status IN ('active', 'archived')"
            " AND d.node_id = ANY(:vis) GROUP BY node_id, status"
        ),
        {"c": env.company_id, "vis": visible_node_ids(env)},
    )
    by_node: dict[str, dict[str, int]] = {}
    total = {"active": 0, "archived": 0}
    for r in rows:
        by_node.setdefault(str(r.node_id), {"active": 0, "archived": 0})[r.status] = r.n
        total[r.status] += r.n
    return {"by_node": by_node, "totals": total}


def detail(env: Env, doc_id: uuid.UUID) -> dict[str, Any]:
    doc = get_document(env, doc_id)
    out = document_out(env, doc)
    out["field_values"] = field_values_of(env.conn, doc_id)
    out["path"] = node_path(env.conn, env.company_id, doc["node_id"])
    out["versions"] = versions(env, doc_id)
    return out


# ---------------------------------------------------------------- changes
def _require_change(env: Env, doc: dict[str, Any], action: str = "upload") -> None:
    env.acc.require(action, str(doc["node_id"]))


def _bump(env: Env, doc_id: uuid.UUID, extra: str = "") -> None:
    env.conn.execute(
        text(
            f"UPDATE documents SET updated_by = :u, updated_at = now(), row_version = row_version + 1{extra} WHERE id = :d"
        ),
        {"u": env.user_id, "d": doc_id},
    )


def _check_row_version(doc: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != doc["row_version"]:
        raise DocumentError(
            409,
            "document_changed",
            "The document was changed by someone else; reload it.",
            {"current_row_version": doc["row_version"]},
        )


def _unique_in_node(env: Env, node_id: uuid.UUID, name: str, doc_id: uuid.UUID) -> bool:
    return (
        env.conn.execute(
            text(
                "SELECT 1 FROM documents WHERE company_id = :c AND node_id = :n AND lower(display_name) = lower(:nm)"
                " AND status IN ('active', 'archived') AND id <> :d"
            ),
            {"c": env.company_id, "n": node_id, "nm": name, "d": doc_id},
        ).first()
        is None
    )


def update_document(
    env: Env,
    doc_id: uuid.UUID,
    *,
    display_name: str | None,
    field_values: dict[str, Any] | None,
    row_version: int | None,
) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    _require_change(env, doc)
    if doc["status"] != "active":
        raise DocumentError(409, "document_not_active", "Restore the document before changing it.")
    _check_row_version(doc, row_version)
    if display_name is not None and display_name != doc["display_name"]:
        name = _checked_name(display_name, doc["extension"])
        if not _unique_in_node(env, doc["node_id"], name, doc_id):
            raise DocumentError(409, "name_taken", "Another document in this folder already has that name.")
        env.conn.execute(text("UPDATE documents SET display_name = :n WHERE id = :d"), {"n": name, "d": doc_id})
        _event(
            env, doc_id, "renamed", detail={"from": doc["display_name"], "to": name}, audit_action="document.renamed"
        )
    if field_values is not None:
        valid = validate_metadata(env, doc["category_id"], doc["node_id"], field_values, require_complete=False)
        changed = _store_field_values(env, doc_id, doc["category_id"], doc["node_id"], valid)
        if changed:
            _event(
                env, doc_id, "metadata_changed", detail={"fields": changed}, audit_action="document.metadata_changed"
            )
    _bump(env, doc_id)
    return detail(env, doc_id)


def move_document(
    env: Env, doc_id: uuid.UUID, *, node_id: uuid.UUID, category_id: uuid.UUID | None, row_version: int | None
) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    _require_change(env, doc)
    if doc["status"] != "active":
        raise DocumentError(409, "document_not_active", "Restore the document before moving it.")
    _check_row_version(doc, row_version)
    _live_node(env, node_id)
    env.acc.require("upload", str(node_id))
    cat_ext_check(env, category_id, node_id, doc["extension"])
    name = doc["display_name"]
    if not _unique_in_node(env, node_id, name, doc_id):
        taken = [
            r[0].lower()
            for r in env.conn.execute(
                text(
                    "SELECT display_name FROM documents WHERE company_id = :c AND node_id = :n AND status IN ('active', 'archived')"
                ),
                {"c": env.company_id, "n": node_id},
            )
        ]
        name = ensure_unique_name(name, taken)
    kind = None
    if category_id:
        kind = env.conn.execute(
            text("SELECT document_kind FROM document_categories WHERE id = :i"), {"i": category_id}
        ).scalar()
    old_path = doc["filed_path_snapshot"]
    new_path = node_path(env.conn, env.company_id, node_id)
    env.conn.execute(
        text(
            "UPDATE documents SET node_id = :n, category_id = :cat, display_name = :nm, filed_path_snapshot = :p, kind_at_filing = :k WHERE id = :d"
        ),
        {"n": node_id, "cat": category_id, "nm": name, "p": new_path, "k": kind, "d": doc_id},
    )
    _bump(env, doc_id)
    _event(
        env,
        doc_id,
        "moved",
        detail={"from": old_path, "to": new_path, "from_node": str(doc["node_id"]), "to_node": str(node_id)},
        audit_action="document.moved",
    )
    return detail(env, doc_id)


def set_archived(env: Env, doc_id: uuid.UUID, archived: bool) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    _require_change(env, doc)
    want_from, to = ("active", "archived") if archived else ("archived", "active")
    if doc["status"] != want_from:
        raise DocumentError(
            409, "bad_state", f"A {doc['status']} document cannot be {'archived' if archived else 'restored'}."
        )
    if not archived:
        node = env.conn.execute(
            text("SELECT archived_at FROM workspace_nodes WHERE id = :n"), {"n": doc["node_id"]}
        ).first()
        if node is not None and node.archived_at is not None:
            raise DocumentError(
                409,
                "target_unavailable",
                "Its folder is archived; move the document to an active folder first, or restore the folder.",
            )
    env.conn.execute(
        text(
            "UPDATE documents SET status = :s, archived_at = CASE WHEN :a THEN now() ELSE NULL END, archived_by = CASE WHEN :a THEN CAST(:u AS uuid) ELSE NULL END WHERE id = :d"
        ),
        {"s": to, "a": archived, "u": env.user_id, "d": doc_id},
    )
    _bump(env, doc_id)
    _event(
        env,
        doc_id,
        "archived" if archived else "restored",
        audit_action=f"document.{'archived' if archived else 'restored'}",
    )
    return detail(env, doc_id)


def soft_delete(env: Env, doc_id: uuid.UUID, reason: str) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    env.acc.require("delete", str(doc["node_id"]))
    if doc["status"] not in ("active", "archived"):
        raise DocumentError(409, "bad_state", "This document is already removed.")
    env.conn.execute(
        text(
            "UPDATE documents SET status = 'deleted', deleted_at = now(), deleted_by = :u, delete_reason = :r WHERE id = :d"
        ),
        {"u": env.user_id, "r": reason, "d": doc_id},
    )
    _bump(env, doc_id)
    _event(env, doc_id, "deleted", detail={"reason": reason}, audit_action="document.deleted")
    return {"id": str(doc_id), "status": "deleted", "recoverable_until_purge": True}


def undelete(env: Env, doc_id: uuid.UUID) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    env.acc.require("delete", str(doc["node_id"]))
    if doc["status"] != "deleted":
        raise DocumentError(409, "bad_state", "Only a removed document can be brought back.")
    env.conn.execute(
        text(
            "UPDATE documents SET status = 'archived', deleted_at = NULL, deleted_by = NULL, delete_reason = NULL WHERE id = :d"
        ),
        {"d": doc_id},
    )
    _bump(env, doc_id)
    _event(env, doc_id, "undeleted", audit_action="document.undeleted")
    return detail(env, doc_id)


def set_legal_hold(env: Env, doc_id: uuid.UUID, hold: bool) -> dict[str, Any]:
    doc = get_document(env, doc_id, lock=True)
    if not env.can_purge:
        raise DocumentError(403, "forbidden", "Only an organization owner or administrator can change a legal hold.")
    env.conn.execute(text("UPDATE documents SET legal_hold = :h WHERE id = :d"), {"h": hold, "d": doc_id})
    _bump(env, doc_id)
    _event(env, doc_id, "legal_hold_changed", detail={"to": hold}, audit_action="document.legal_hold_changed")
    return detail(env, doc_id) if doc["status"] != "deleted" else {"id": str(doc_id), "legal_hold": hold}


def purge(env: Env, doc_id: uuid.UUID, confirm: str) -> dict[str, Any]:
    """Permanent removal of every binary of a removed document. Needs `documents.purge`, no legal hold, and the configured
    waiting period after removal. The document row and its history stay as a tombstone (audit trail)."""
    doc = get_document(env, doc_id, lock=True)
    if not env.can_purge:
        raise DocumentError(
            403, "forbidden", "Only an organization owner or administrator can permanently delete documents."
        )
    if doc["status"] != "deleted":
        raise DocumentError(
            409, "bad_state", "Remove the document first; only a removed document can be permanently deleted."
        )
    if doc["legal_hold"]:
        raise DocumentError(409, "legal_hold", "This document is under legal hold and cannot be permanently deleted.")
    if confirm.strip() != doc["display_name"]:
        raise DocumentError(422, "confirmation_mismatch", "Type the document's name to confirm.")
    earliest = doc["deleted_at"] + timedelta(days=get_settings().document_purge_min_days)
    if datetime.now(UTC) < earliest:
        raise DocumentError(
            409, "retention_period", "The retention period has not ended.", {"earliest": earliest.isoformat()}
        )
    storage = get_storage()
    rows = env.conn.execute(
        text("SELECT id, storage_key FROM document_versions WHERE document_id = :d AND purged_at IS NULL"),
        {"d": doc_id},
    ).all()
    for r in rows:
        try:
            storage.delete(r.storage_key)
        except StorageError as exc:
            raise DocumentError(
                503, "storage_error", "A stored file could not be removed; nothing was changed. Retry."
            ) from exc
    env.conn.execute(
        text("UPDATE document_versions SET purged_at = now() WHERE document_id = :d AND purged_at IS NULL"),
        {"d": doc_id},
    )
    env.conn.execute(text("UPDATE documents SET status = 'purged', purged_at = now() WHERE id = :d"), {"d": doc_id})
    _bump(env, doc_id)
    _event(env, doc_id, "purged", detail={"versions": len(rows)}, audit_action="document.purged")
    for k in ("quota.storage_bytes",):
        entitlements.refresh_counter(env.conn, env.org_id, k)
    return {"id": str(doc_id), "status": "purged", "versions_removed": len(rows)}


# ---------------------------------------------------------------- versions
VERSION_COLS = (
    "v.id, v.version_number, v.original_filename, v.extension, v.mime_type, v.size_bytes, v.sha256, v.scan_status, v.change_reason,"
    " v.uploaded_by, u.display_name AS uploaded_by_name, v.created_at, v.purged_at"
)


def versions(env: Env, doc_id: uuid.UUID) -> list[dict[str, Any]]:
    doc = get_document(env, doc_id)
    rows = env.conn.execute(
        text(
            f"SELECT {VERSION_COLS} FROM document_versions v LEFT JOIN users u ON u.id = v.uploaded_by WHERE v.document_id = :d ORDER BY v.version_number DESC"
        ),
        {"d": doc_id},
    ).mappings()
    return [{**dict(r), "is_current": r["id"] == doc["current_version_id"]} for r in rows]


def _version(env: Env, doc: dict[str, Any], number: int) -> dict[str, Any]:
    row = env.conn.execute(
        text(
            f"SELECT {VERSION_COLS}, v.storage_key FROM document_versions v LEFT JOIN users u ON u.id = v.uploaded_by WHERE v.document_id = :d AND v.version_number = :n"
        ),
        {"d": doc["id"], "n": number},
    ).first()
    if row is None:
        raise NotFound("Version not found.")
    return dict(row._mapping)


def restore_version(env: Env, doc_id: uuid.UUID, number: int, reason: str | None) -> dict[str, Any]:
    """Make a previous version current. Nothing is copied or overwritten: the pointer moves and the history records it."""
    doc = get_document(env, doc_id, lock=True)
    _require_change(env, doc)
    if doc["status"] != "active":
        raise DocumentError(409, "document_not_active", "Restore the document before changing its version.")
    v = _version(env, doc, number)
    if v["purged_at"] is not None:
        raise DocumentError(410, "version_purged", "That version's file was permanently deleted.")
    if v["id"] == doc["current_version_id"]:
        raise DocumentError(409, "already_current", "That version is already current.")
    env.conn.execute(
        text("UPDATE documents SET current_version_id = :v, current_version_number = :n WHERE id = :d"),
        {"v": v["id"], "n": number, "d": doc_id},
    )
    _bump(env, doc_id)
    _event(
        env,
        doc_id,
        "version_restored",
        version_id=v["id"],
        detail={"version": number, "from": doc["current_version_number"], "reason": reason},
        audit_action="document.version_restored",
    )
    return detail(env, doc_id)


# ---------------------------------------------------------------- content (authenticated streaming; no public URLs)
def open_content(
    env: Env, doc_id: uuid.UUID, version: int | None, *, preview: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    doc = get_document(env, doc_id)
    if doc["status"] == "deleted":
        raise DocumentError(410, "document_removed", "This document was removed.")
    v = _version(env, doc, version if version is not None else doc["current_version_number"])
    if v["purged_at"] is not None:
        raise DocumentError(410, "version_purged", "That version's file was permanently deleted.")
    if v["scan_status"] == "infected":
        raise DocumentError(403, "quarantined", "This file is quarantined.")
    _event(
        env,
        doc_id,
        "previewed" if preview else "downloaded",
        version_id=v["id"],
        detail={"version": v["version_number"]},
        audit_action=None if preview else "document.downloaded",
    )
    return doc, v


def stream(key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
    try:
        yield from get_storage().read(key, start, end)
    except StorageError as exc:  # pragma: no cover - raised mid-stream
        raise DocumentError(503, "storage_error", "The file could not be read.") from exc


def events(env: Env, doc_id: uuid.UUID, limit: int, offset: int) -> dict[str, Any]:
    get_document(env, doc_id)
    total = env.conn.execute(
        text("SELECT count(*) FROM document_events WHERE document_id = :d"), {"d": doc_id}
    ).scalar_one()
    rows = env.conn.execute(
        text(
            "SELECT e.id, e.event_type, e.created_at, e.actor_id, u.display_name AS actor_name, e.version_id, e.detail FROM document_events e"
            " LEFT JOIN users u ON u.id = e.actor_id WHERE e.document_id = :d ORDER BY e.created_at DESC, e.id DESC LIMIT :l OFFSET :o"
        ),
        {"d": doc_id, "l": limit, "o": offset},
    ).mappings()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------- uploads list, storage usage, reconciliation
def list_sessions(
    env: Env, status: str | None, batch_id: uuid.UUID | None, mine: bool, limit: int, offset: int
) -> dict[str, Any]:
    from artify_api.documents import SESSION_COLS, session_out

    if not mine and not env.acc.can("configure"):  # other people's file names are management information
        raise DocumentError(403, "forbidden", "Only company managers can see other people's uploads.")
    where, params = ["company_id = :c"], {"c": env.company_id, "l": limit, "o": offset}
    if mine:
        where.append("created_by = :u")
        params["u"] = env.user_id
    if status:
        where.append("status = :s")
        params["s"] = status
    if batch_id:
        where.append("batch_id = :b")
        params["b"] = batch_id
    w = " AND ".join(where)
    total = env.conn.execute(text(f"SELECT count(*) FROM document_upload_sessions WHERE {w}"), params).scalar_one()
    rows = env.conn.execute(
        text(
            f"SELECT {SESSION_COLS}, staging_ref, created_by FROM document_upload_sessions WHERE {w} ORDER BY created_at DESC LIMIT :l OFFSET :o"
        ),
        params,
    ).mappings()
    return {"items": [session_out(dict(r)) for r in rows], "total": total, "limit": limit, "offset": offset}


def company_storage(env: Env) -> dict[str, Any]:
    # totals and the by-type breakdown cover EVERY folder of the company, including ones this caller may not view
    env.acc.require("configure")
    r = env.conn.execute(
        text(
            "SELECT count(*) FILTER (WHERE d.status = 'active') AS active, count(*) FILTER (WHERE d.status = 'archived') AS archived,"
            " count(*) FILTER (WHERE d.status = 'deleted') AS removed FROM documents d WHERE d.company_id = :c"
        ),
        {"c": env.company_id},
    ).one()
    b = env.conn.execute(
        text(
            "SELECT COALESCE(sum(size_bytes), 0) AS bytes, count(*) AS versions FROM document_versions WHERE company_id = :c AND purged_at IS NULL"
        ),
        {"c": env.company_id},
    ).one()
    by_type = [
        {"extension": x.extension, "documents": x.n, "bytes": int(x.bytes)}
        for x in env.conn.execute(
            text(
                "SELECT v.extension, count(DISTINCT v.document_id) AS n, sum(v.size_bytes) AS bytes FROM document_versions v WHERE v.company_id = :c AND v.purged_at IS NULL GROUP BY v.extension ORDER BY bytes DESC"
            ),
            {"c": env.company_id},
        )
    ]
    up = env.conn.execute(
        text(
            "SELECT count(*) FILTER (WHERE status = 'completed') AS ok, count(*) FILTER (WHERE status IN ('failed', 'quarantined')) AS failed FROM document_upload_sessions WHERE company_id = :c AND created_at > now() - interval '7 days'"
        ),
        {"c": env.company_id},
    ).one()
    return {
        "documents": {"active": r.active, "archived": r.archived, "removed": r.removed},
        "stored_bytes": int(b.bytes),
        "stored_versions": b.versions,
        "by_type": by_type,
        "uploads_last_7_days": {"completed": up.ok, "failed": up.failed},
    }


def organization_storage(conn: Connection, org_id: uuid.UUID) -> dict[str, Any]:
    used = entitlements.measure(conn, org_id, "quota.storage_bytes")
    lim = entitlements.effective_limit(conn, org_id, "quota.storage_bytes")
    docs = entitlements.measure(conn, org_id, "quota.documents_per_month")
    dl = entitlements.effective_limit(conn, org_id, "quota.documents_per_month")
    per_company = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT c.id AS company_id, c.name AS company_name, COALESCE(s.bytes, 0)::bigint AS stored_bytes, COALESCE(s.docs, 0) AS documents,"
                " COALESCE(u.failed, 0) AS failed_uploads, COALESCE(u.recent, 0) AS uploads_7d FROM companies c"
                " LEFT JOIN (SELECT d.company_id, sum(v.size_bytes) AS bytes, count(DISTINCT d.id) FILTER (WHERE d.status IN ('active', 'archived')) AS docs"
                "   FROM document_versions v JOIN documents d ON d.id = v.document_id WHERE v.purged_at IS NULL GROUP BY d.company_id) s ON s.company_id = c.id"
                " LEFT JOIN (SELECT company_id, count(*) FILTER (WHERE status IN ('failed', 'quarantined')) AS failed,"
                "   count(*) FILTER (WHERE status = 'completed') AS recent FROM document_upload_sessions WHERE created_at > now() - interval '7 days' GROUP BY company_id) u ON u.company_id = c.id"
                " WHERE c.organization_id = :o ORDER BY stored_bytes DESC, c.name"
            ),
            {"o": org_id},
        ).mappings()
    ]
    return {
        "storage": {
            "used_bytes": used,
            "limit_bytes": None if lim.unlimited else lim.value,
            "unlimited": lim.unlimited,
            "available_bytes": None if lim.unlimited or lim.value is None else max(0, lim.value - used),
            "source": lim.source,
        },
        "documents_this_month": {"used": docs, "limit": None if dl.unlimited else dl.value, "unlimited": dl.unlimited},
        "companies": per_company,
        "accounting_model": "Bytes of every stored version of every document whose file has not been permanently deleted. Archived and removed documents and all retained versions count; restoring a version adds nothing; identical files uploaded as separate documents are stored (and counted) separately.",
    }


def reconcile(conn: Connection, org_id: uuid.UUID, *, dry_run: bool, grace_minutes: int = 60) -> dict[str, Any]:
    """Compare storage with the database for ONE organization: orphan objects (no version row, older than the grace period),
    missing objects (a version row whose file is gone), and stale staging files of expired/closed sessions."""
    storage = get_storage()
    prefix = f"o/{org_id.hex}/"
    known = {
        r.storage_key
        for r in conn.execute(
            text("SELECT storage_key FROM document_versions WHERE organization_id = :o AND purged_at IS NULL"),
            {"o": org_id},
        )
    }
    objects = {k for k in storage.list_keys() if k.startswith(prefix)}
    orphans = sorted(objects - known)
    missing = sorted(known - objects)
    removed = 0
    cutoff = datetime.now(UTC).timestamp() - grace_minutes * 60
    if not dry_run:
        for k in orphans:
            p = getattr(storage, "_path", None)
            if p is not None and p(k).stat().st_mtime > cutoff:
                continue  # an in-flight store may not have committed its row yet
            with_suppress_delete(storage, k)
            removed += 1
    stages = {
        r.staging_ref: r.status
        for r in conn.execute(
            text("SELECT staging_ref, status FROM document_upload_sessions WHERE organization_id = :o"),
            {"o": org_id},
        )
    }
    stale = []
    for ref, mtime in storage.list_stages():
        st = stages.get(ref)
        if st is not None and st not in ("completed", "cancelled", "expired", "quarantined"):
            continue
        if mtime < cutoff:
            stale.append(ref)
    cleaned = 0
    if not dry_run:
        for ref in stale:
            storage.stage_discard(ref)
            cleaned += 1
        conn.execute(
            text(
                "UPDATE document_upload_sessions SET status = 'expired', updated_at = now() WHERE organization_id = :o AND status IN ('open', 'uploading') AND expires_at < now()"
            ),
            {"o": org_id},
        )
    return {
        "dry_run": dry_run,
        "orphan_objects": orphans,
        "orphans_removed": removed,
        "missing_objects": missing,
        "stale_staging": len(stale),
        "staging_removed": cleaned,
    }
