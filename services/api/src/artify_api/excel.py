"""Excel template, mapping, export and report service (Phase 9).

The accountant's own workbook is the product: a company registers its templates, maps governed accounting fields onto them,
previews, and exports APPROVED Phase 8 data. Everything here is company-scoped; template versions, activated mappings and
finished exports are immutable records. Nothing in an uploaded workbook is executed, and no export is reported as complete
until the generated file has been re-opened, verified and stored."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from artify_api import accounting as acc
from artify_api import audit, entitlements, storage
from artify_api import xl_catalogue as cat
from artify_api import xl_data as data
from artify_api import xl_engine as eng
from artify_api import xl_mapping as mp
from artify_api import xl_writer as wr
from artify_api.accounting import must, one, rows, ser
from artify_api.context import RequestCtx
from artify_api.errors import Conflict, EntitlementExceeded, Forbidden, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.settings import get_settings
from artify_api.worker import run_in_tenant_context

log = logging.getLogger("artify.excel")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME = "application/vnd.ms-excel.sheet.macroEnabled.12"


def _co(ctx: RequestCtx) -> tuple[uuid.UUID, uuid.UUID]:
    assert ctx.org_id is not None and ctx.company_id is not None
    return ctx.org_id, ctx.company_id


def U(v: Any) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _audit(
    ctx: RequestCtx, action: str, entity_type: str, entity_id: Any, summary: dict[str, Any] | None = None
) -> None:
    acc.audit_event(ctx, action, entity_type, entity_id, summary)


def _category(value: str) -> str:
    v = value.strip().lower().replace(" ", "_").replace("-", "_")
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,59}", v):
        raise Unprocessable("A category uses letters, digits and underscores (2 to 60 characters).")
    return v


def _guard_limit(ctx: RequestCtx, key: str, current: int) -> None:
    org, _ = _co(ctx)
    status = entitlements.subscription_status(ctx.conn, org)
    if status in (None, "suspended"):
        raise entitlements.SubscriptionRestricted(
            "The subscription is suspended or missing; this action is not available.",
            extra={"subscription_status": status},
        )
    lim = entitlements.effective_limit(ctx.conn, org, key)
    if not lim.unlimited and current + 1 > (lim.value or 0):
        raise EntitlementExceeded(key=key, limit=lim.value or 0, current=current, requested=1)


# ================================================================== catalogue
def catalogue() -> dict[str, Any]:
    return {
        **cat.catalogue_view(),
        "categories": list(eng.CATEGORIES),
        "note": "Categories are suggestions; you may use your own.",
    }


# ================================================================== templates
TEMPLATE_COLS = (
    "t.id, t.name, t.description, t.category, t.file_kind, t.status, t.current_version_no, t.source_template_id, t.created_at, t.updated_at, t.archived_at, t.row_version,"
    " (SELECT u.display_name FROM users u WHERE u.id = t.created_by) AS created_by_name,"
    " (SELECT m.mapping_no FROM xl_mappings m WHERE m.template_id = t.id AND m.status = 'active') AS active_mapping_no,"
    " (SELECT m.id FROM xl_mappings m WHERE m.template_id = t.id AND m.status = 'active') AS active_mapping_id,"
    " (SELECT v.risk_level FROM xl_template_versions v WHERE v.template_id = t.id AND v.version_no = t.current_version_no) AS risk_level,"
    " (SELECT v.original_filename FROM xl_template_versions v WHERE v.template_id = t.id AND v.version_no = t.current_version_no) AS filename"
)


def list_templates(
    ctx: RequestCtx, *, status: str | None = None, category: str | None = None, q: str | None = None
) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    p: dict[str, Any] = {"c": co}
    sql = f"SELECT {TEMPLATE_COLS} FROM xl_templates t WHERE t.company_id = :c"
    if status:
        sql += " AND t.status = :s"
        p["s"] = status
    else:
        sql += " AND t.status <> 'archived'"
    if category:
        sql += " AND t.category = :cat"
        p["cat"] = _category(category)
    if q:
        sql += " AND t.name ILIKE :q"
        p["q"] = f"%{q}%"
    return ser(rows(ctx, sql + " ORDER BY lower(t.name)", **p))


def _template(ctx: RequestCtx, tid: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    _, co = _co(ctx)
    t = one(
        ctx,
        f"SELECT {TEMPLATE_COLS} FROM xl_templates t WHERE t.company_id = :c AND t.id = :i"
        + (" FOR UPDATE OF t" if lock else ""),
        c=co,
        i=tid,
    )
    if t is None:
        raise NotFound("Template not found.")
    return t


def get_template(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    t = _template(ctx, tid)
    t["versions"] = rows(
        ctx,
        "SELECT v.version_no, v.original_filename, v.file_kind, v.size_bytes, v.sha256, v.risk_level, v.change_note, v.created_at, u.display_name AS uploaded_by_name,"
        " (SELECT count(*) FROM xl_mappings m WHERE m.version_id = v.id) AS mappings FROM xl_template_versions v LEFT JOIN users u ON u.id = v.uploaded_by WHERE v.template_id = :t ORDER BY v.version_no DESC",
        t=tid,
    )
    t["mappings"] = rows(
        ctx,
        "SELECT m.id, m.mapping_no, m.status, v.version_no AS template_version, m.created_at, m.activated_at, m.note FROM xl_mappings m JOIN xl_template_versions v ON v.id = m.version_id WHERE m.template_id = :t ORDER BY m.mapping_no DESC",
        t=tid,
    )
    t["exports"] = one(
        ctx, "SELECT count(*) AS n, max(created_at) AS last_at FROM xl_exports WHERE template_id = :t", t=tid
    )
    return ser(t)


def _put_object(ctx: RequestCtx, path: Path, size: int, sha: str, purpose: str) -> uuid.UUID:
    org, co = _co(ctx)
    be = storage.get_storage()
    oid = new_id()
    key = storage.object_key(org, co, oid)
    be.put_file(key, path)
    if be.stat(key) != size:
        be.delete(key)
        raise Conflict("The file could not be stored completely. Try again.")
    ctx.conn.execute(
        text(
            "INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256, state, verified_at, purpose) VALUES (:i, :o, :c, :b, :k, :s, :h, 'verified', now(), :p)"
        ),
        {"i": oid, "o": org, "c": co, "b": be.name, "k": key, "s": size, "h": sha, "p": purpose},
    )
    return oid


def _open_object(ctx: RequestCtx, object_id: Any) -> tuple[str, int]:
    r = one(
        ctx,
        "SELECT storage_key, size_bytes FROM storage_objects WHERE id = :i AND company_id = :c AND state IN ('stored', 'verified')",
        i=U(object_id),
        c=ctx.company_id,
    )
    if r is None:
        raise NotFound("The stored file is not available.")
    return r["storage_key"], int(r["size_bytes"])


def upload_template(
    ctx: RequestCtx,
    tmp: Path,
    *,
    filename: str,
    name: str | None,
    category: str | None,
    description: str | None,
    template_id: uuid.UUID | None,
    change_note: str | None,
) -> dict[str, Any]:
    """Register a workbook as a new template, or as a new version of an existing one. The file was already streamed to `tmp`."""
    org, co = _co(ctx)
    s = get_settings()
    macros = entitlements.has_feature(ctx.conn, org, "feature.macro_templates")
    try:
        info = eng.inspect_upload(tmp, filename, max_bytes=s.excel_max_template_bytes, allow_macros=macros)
        tmp = tmp.rename(
            tmp.with_suffix(f".{info.kind}")
        )  # the library decides by extension; `kind` was validated above
        analysis = eng.analyze(tmp, info.kind)
    except eng.WorkbookError as e:
        raise Unprocessable(e.message, extra={"code": e.code}) from None
    except Exception:  # the library failed on a hostile or damaged file: refuse, never crash
        log.exception("workbook analysis failed")
        raise Unprocessable(
            "The workbook could not be read. It may be damaged or use unsupported features.",
            extra={"code": "unreadable_workbook"},
        ) from None
    analysis["upload_warnings"] = info.warnings
    entitlements.guard_quota(ctx.conn, org, "quota.storage_bytes", info.size)
    if template_id is None:
        if not name or not name.strip():
            raise Unprocessable("A template needs a name.")
        before = entitlements.company_usage(ctx.conn, org, co)
        if one(
            ctx,
            "SELECT 1 AS x FROM xl_templates WHERE company_id = :c AND lower(name) = lower(:n) AND status <> 'archived'",
            c=co,
            n=name.strip(),
        ):
            raise Conflict("A template with that name already exists in this company.")
        tid = new_id()
        ctx.conn.execute(
            text(
                "INSERT INTO xl_templates (id, organization_id, company_id, name, description, category, file_kind, status, current_version_no, created_by, updated_by)"
                " VALUES (:id, :o, :c, :n, :d, :cat, :k, 'draft', 1, :u, :u)"
            ),
            {
                "id": tid,
                "o": org,
                "c": co,
                "n": name.strip(),
                "d": description,
                "cat": _category(category or "custom_report"),
                "k": info.kind,
                "u": ctx.user_id,
            },
        )
        entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.excel_templates_per_company"])
        version_no = 1
    else:
        t = _template(ctx, template_id, lock=True)
        if t["status"] == "archived":
            raise Conflict("Restore the template before replacing its file.")
        if t["file_kind"] != info.kind:
            raise Unprocessable("A new version must be the same file type as the template.")
        tid = template_id
        count = one(ctx, "SELECT count(*) AS n FROM xl_template_versions WHERE template_id = :t", t=tid)
        _guard_limit(ctx, "limit.excel_template_versions_per_template", int(count["n"]) if count else 0)
        version_no = int(t["current_version_no"]) + 1
    oid = _put_object(ctx, tmp, info.size, info.sha256, "template")
    vid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO xl_template_versions (id, organization_id, company_id, template_id, version_no, storage_object_id, original_filename, file_kind, size_bytes, sha256, analysis, risk_level, change_note, uploaded_by)"
            " VALUES (:id, :o, :c, :t, :v, :obj, :fn, :k, :sz, :h, CAST(:a AS jsonb), :r, :n, :u)"
        ),
        {
            "id": vid,
            "o": org,
            "c": co,
            "t": tid,
            "v": version_no,
            "obj": oid,
            "fn": Path(filename.replace("\\", "/")).name[:255],
            "k": info.kind,
            "sz": info.size,
            "h": info.sha256,
            "a": json.dumps(analysis),
            "r": analysis["risk_level"],
            "n": change_note,
            "u": ctx.user_id,
        },
    )
    if template_id is not None:
        ctx.conn.execute(
            text(
                "UPDATE xl_templates SET current_version_no = :v, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            {"v": version_no, "u": ctx.user_id, "i": tid},
        )
        prev = one(
            ctx, "SELECT definition FROM xl_mappings WHERE template_id = :t AND status = 'active'", t=tid
        ) or one(
            ctx, "SELECT definition FROM xl_mappings WHERE template_id = :t ORDER BY mapping_no DESC LIMIT 1", t=tid
        )
        if prev:
            _insert_mapping(
                ctx,
                tid,
                vid,
                prev["definition"],
                analysis,
                "Copied from the previous mapping; review it against the new version.",
            )
    _audit(
        ctx,
        "excel.template_uploaded" if template_id is None else "excel.template_version_added",
        "xl_template",
        tid,
        {"version": version_no, "sha256": info.sha256, "risk": analysis["risk_level"], "size": info.size},
    )
    out = get_template(ctx, tid)
    out["analysis_summary"] = {"risk_level": analysis["risk_level"], "warnings": info.warnings}
    return out


def update_template(
    ctx: RequestCtx, tid: uuid.UUID, patch: dict[str, Any], base_row_version: int | None
) -> dict[str, Any]:
    _, co = _co(ctx)
    t = _template(ctx, tid, lock=True)
    if base_row_version is not None and base_row_version != t["row_version"]:
        raise Conflict("The template changed since you opened it. Reload and try again.")
    sets: dict[str, Any] = {}
    if patch.get("name") is not None:
        n = patch["name"].strip()
        if one(
            ctx,
            "SELECT 1 AS x FROM xl_templates WHERE company_id = :c AND lower(name) = lower(:n) AND id <> :i AND status <> 'archived'",
            c=co,
            n=n,
            i=tid,
        ):
            raise Conflict("A template with that name already exists in this company.")
        sets["name"] = n
    if "description" in patch:
        sets["description"] = patch["description"]
    if patch.get("category") is not None:
        sets["category"] = _category(patch["category"])
    if sets:
        ctx.conn.execute(
            text(
                f"UPDATE xl_templates SET {', '.join(f'{k} = :{k}' for k in sets)}, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            {**sets, "u": ctx.user_id, "i": tid},
        )
        _audit(ctx, "excel.template_updated", "xl_template", tid, {"fields": sorted(sets)})
    return get_template(ctx, tid)


def set_template_status(ctx: RequestCtx, tid: uuid.UUID, action: str) -> dict[str, Any]:
    t = _template(ctx, tid, lock=True)
    has_active = t["active_mapping_id"] is not None
    cur = t["status"]
    if action == "activate":
        if cur == "archived":
            raise Conflict("Restore the template first.")
        if not has_active:
            raise Conflict("Activate a validated mapping before activating the template.")
        new = "active"
    elif action == "deactivate":
        if cur == "archived":
            raise Conflict("The template is archived.")
        new = "inactive"
    elif action == "archive":
        new = "archived"
    elif action == "restore":
        if cur != "archived":
            raise Conflict("Only an archived template can be restored.")
        org, co = _co(ctx)
        if one(
            ctx,
            "SELECT 1 AS x FROM xl_templates WHERE company_id = :c AND lower(name) = lower(:n) AND status <> 'archived' AND id <> :i",
            c=co,
            n=t["name"],
            i=tid,
        ):
            raise Conflict("Another template now uses this name; rename one of them first.")
        before = entitlements.company_usage(ctx.conn, org, co)
        new = "inactive" if has_active else "draft"
        ctx.conn.execute(
            text(
                "UPDATE xl_templates SET status = :s, archived_at = NULL, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
            ),
            {"s": new, "u": ctx.user_id, "i": tid},
        )
        entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.excel_templates_per_company"])
        _audit(ctx, "excel.template_restored", "xl_template", tid, {})
        return get_template(ctx, tid)
    else:
        raise Unprocessable("Unknown action.")
    ctx.conn.execute(
        text(
            "UPDATE xl_templates SET status = :s, archived_at = CASE WHEN :s = 'archived' THEN now() ELSE NULL END, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"s": new, "u": ctx.user_id, "i": tid},
    )
    _audit(
        ctx, f"excel.template_{action}d" if action.endswith("e") else f"excel.template_{action}", "xl_template", tid, {}
    )
    return get_template(ctx, tid)


def duplicate_template(ctx: RequestCtx, tid: uuid.UUID, name: str) -> dict[str, Any]:
    """A new template in the SAME company with its own copy of the file and a draft copy of the mapping. Nothing is shared."""
    src = _template(ctx, tid)
    ver = must(
        ctx,
        "SELECT id, storage_object_id, original_filename, file_kind, size_bytes, sha256, analysis FROM xl_template_versions WHERE template_id = :t AND version_no = :v",
        t=tid,
        v=src["current_version_no"],
    )
    key, _size = _open_object(ctx, ver["storage_object_id"])
    with tempfile.TemporaryDirectory(dir=get_settings().storage_tmp_dir) as td:
        tmp = Path(td) / "copy.xlsx"
        with tmp.open("wb") as f:
            for chunk in storage.get_storage().open_range(key):
                f.write(chunk)
        if eng.sha256_of(tmp) != ver["sha256"]:
            raise Conflict("The stored template file failed its checksum; it was not copied.")
        out = upload_template(
            ctx,
            tmp,
            filename=ver["original_filename"],
            name=name,
            category=src["category"],
            description=src["description"],
            template_id=None,
            change_note=f"Duplicate of “{src['name']}”",
        )
    new_id_ = U(out["id"])
    ctx.conn.execute(text("UPDATE xl_templates SET source_template_id = :s WHERE id = :i"), {"s": tid, "i": new_id_})
    prev = one(ctx, "SELECT definition FROM xl_mappings WHERE template_id = :t AND status = 'active'", t=tid) or one(
        ctx, "SELECT definition FROM xl_mappings WHERE template_id = :t ORDER BY mapping_no DESC LIMIT 1", t=tid
    )
    if prev:
        vid = must(
            ctx, "SELECT id, analysis FROM xl_template_versions WHERE template_id = :t AND version_no = 1", t=new_id_
        )
        _insert_mapping(ctx, new_id_, U(vid["id"]), prev["definition"], vid["analysis"], f"Copied from “{src['name']}”")
    _audit(ctx, "excel.template_duplicated", "xl_template", new_id_, {"source": str(tid)})
    return get_template(ctx, new_id_)


def delete_template(ctx: RequestCtx, tid: uuid.UUID) -> dict[str, Any]:
    """Only a template that was never exported can be deleted; anything else is archived so history stays whole."""
    _template(ctx, tid, lock=True)
    if one(ctx, "SELECT 1 AS x FROM xl_exports WHERE template_id = :t LIMIT 1", t=tid) or one(
        ctx, "SELECT 1 AS x FROM xl_mappings WHERE template_id = :t AND status <> 'draft' LIMIT 1", t=tid
    ):
        raise Conflict(
            "This template has been used (an activated mapping or an export exists). Archive it instead; its history is kept."
        )
    objs = rows(ctx, "SELECT storage_object_id FROM xl_template_versions WHERE template_id = :t", t=tid)
    ctx.conn.execute(text("UPDATE xl_reports SET template_id = NULL WHERE template_id = :t"), {"t": tid})
    ctx.conn.execute(text("DELETE FROM xl_mappings WHERE template_id = :t"), {"t": tid})
    ctx.conn.execute(text("DELETE FROM xl_template_versions WHERE template_id = :t"), {"t": tid})
    ctx.conn.execute(text("DELETE FROM xl_templates WHERE id = :t"), {"t": tid})
    for o in objs:
        key, _ = _open_object(ctx, o["storage_object_id"])
        ctx.conn.execute(
            text("UPDATE storage_objects SET state = 'purged', purged_at = now() WHERE id = :i"),
            {"i": U(o["storage_object_id"])},
        )
        try:
            storage.get_storage().delete(key)
        except storage.StorageError:
            log.warning(
                "template object %s could not be removed now; reconciliation will find it", o["storage_object_id"]
            )
    _audit(ctx, "excel.template_deleted", "xl_template", tid, {})
    return {"deleted": True}


def download_version(ctx: RequestCtx, tid: uuid.UUID, version_no: int) -> tuple[Iterator[bytes], str, int, str]:
    _template(ctx, tid)
    v = one(
        ctx,
        "SELECT storage_object_id, original_filename, file_kind, size_bytes FROM xl_template_versions WHERE template_id = :t AND version_no = :v",
        t=tid,
        v=version_no,
    )
    if v is None:
        raise NotFound("Template version not found.")
    key, size = _open_object(ctx, v["storage_object_id"])
    _audit(ctx, "excel.template_downloaded", "xl_template", tid, {"version": version_no})
    return (
        storage.get_storage().open_range(key),
        v["original_filename"],
        size,
        XLSM_MIME if v["file_kind"] == "xlsm" else XLSX_MIME,
    )


def analysis_of(ctx: RequestCtx, tid: uuid.UUID, version_no: int | None = None) -> dict[str, Any]:
    t = _template(ctx, tid)
    v = one(
        ctx,
        "SELECT version_no, analysis, risk_level FROM xl_template_versions WHERE template_id = :t AND version_no = :v",
        t=tid,
        v=version_no or t["current_version_no"],
    )
    if v is None:
        raise NotFound("Template version not found.")
    return ser({"version_no": v["version_no"], "risk_level": v["risk_level"], **v["analysis"]})


# ================================================================== mappings
def _insert_mapping(
    ctx: RequestCtx,
    tid: uuid.UUID,
    vid: uuid.UUID,
    definition: dict[str, Any],
    analysis: dict[str, Any],
    note: str | None,
) -> uuid.UUID:
    org, co = _co(ctx)
    n = one(ctx, "SELECT COALESCE(max(mapping_no), 0) + 1 AS n FROM xl_mappings WHERE template_id = :t", t=tid)
    issues = mp.validate_mapping(definition, analysis)
    mid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO xl_mappings (id, organization_id, company_id, template_id, version_id, mapping_no, status, definition, validation, validated_at, note, created_by)"
            " VALUES (:id, :o, :c, :t, :v, :n, 'draft', CAST(:d AS jsonb), CAST(:val AS jsonb), now(), :note, :u)"
        ),
        {
            "id": mid,
            "o": org,
            "c": co,
            "t": tid,
            "v": vid,
            "n": n["n"] if n else 1,
            "d": json.dumps(definition),
            "val": json.dumps(issues),
            "note": note,
            "u": ctx.user_id,
        },
    )
    return mid


def _mapping(ctx: RequestCtx, mid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    m = one(
        ctx,
        "SELECT m.*, v.version_no AS template_version, v.analysis FROM xl_mappings m JOIN xl_template_versions v ON v.id = m.version_id WHERE m.company_id = :c AND m.id = :i",
        c=co,
        i=mid,
    )
    if m is None:
        raise NotFound("Mapping not found.")
    return m


def _mapping_view(m: dict[str, Any], *, with_analysis: bool = False) -> dict[str, Any]:
    out = {
        k: m[k]
        for k in (
            "id",
            "template_id",
            "mapping_no",
            "status",
            "definition",
            "validation",
            "validated_at",
            "acknowledged_risks",
            "note",
            "created_at",
            "activated_at",
            "template_version",
        )
    }
    out["ready"] = mp.is_ready(m["validation"] or [])
    if with_analysis:
        out["analysis"] = m["analysis"]
    return ser(out)


def list_mappings(ctx: RequestCtx, tid: uuid.UUID) -> list[dict[str, Any]]:
    _template(ctx, tid)
    return [
        _mapping_view(_mapping(ctx, U(r["id"])))
        for r in rows(ctx, "SELECT id FROM xl_mappings WHERE template_id = :t ORDER BY mapping_no DESC", t=tid)
    ]


def get_mapping(ctx: RequestCtx, mid: uuid.UUID) -> dict[str, Any]:
    return _mapping_view(_mapping(ctx, mid), with_analysis=True)


def save_mapping(
    ctx: RequestCtx, tid: uuid.UUID, definition: dict[str, Any], note: str | None, mapping_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Save a DRAFT mapping (incomplete drafts are allowed; activation and export are not). A saved mapping is validated at once."""
    t = _template(ctx, tid, lock=True)
    if t["status"] == "archived":
        raise Conflict("The template is archived.")
    v = must(
        ctx,
        "SELECT id, analysis FROM xl_template_versions WHERE template_id = :t AND version_no = :v",
        t=tid,
        v=t["current_version_no"],
    )
    if len(json.dumps(definition)) > 400_000:
        raise Unprocessable("The mapping is too large.")
    issues = mp.validate_mapping(definition, v["analysis"])
    if mapping_id is not None:
        m = _mapping(ctx, mapping_id)
        if m["template_id"] != str(tid):
            raise NotFound("Mapping not found.")
        if m["status"] != "draft":
            raise Conflict("An activated mapping is history. Save your changes as a new mapping version.")
        ctx.conn.execute(
            text(
                "UPDATE xl_mappings SET definition = CAST(:d AS jsonb), validation = CAST(:v AS jsonb), validated_at = now(), note = :n, version_id = :vid WHERE id = :i"
            ),
            {"d": json.dumps(definition), "v": json.dumps(issues), "n": note, "vid": v["id"], "i": mapping_id},
        )
        mid = mapping_id
    else:
        mid = _insert_mapping(ctx, tid, U(v["id"]), definition, v["analysis"], note)
    _audit(
        ctx,
        "excel.mapping_saved",
        "xl_mapping",
        mid,
        {"template": str(tid), "errors": sum(1 for i in issues if i["severity"] == "error")},
    )
    return _mapping_view(_mapping(ctx, mid))


def validate_mapping_endpoint(ctx: RequestCtx, mid: uuid.UUID) -> dict[str, Any]:
    m = _mapping(ctx, mid)
    issues = mp.validate_mapping(m["definition"], m["analysis"])
    if m["status"] == "draft":
        ctx.conn.execute(
            text("UPDATE xl_mappings SET validation = CAST(:v AS jsonb), validated_at = now() WHERE id = :i"),
            {"v": json.dumps(issues), "i": mid},
        )
    return ser({"issues": issues, "ready": mp.is_ready(issues)})


def activate_mapping(ctx: RequestCtx, mid: uuid.UUID, acknowledge_risks: bool) -> dict[str, Any]:
    """Make a validated mapping the template's active one. Risky workbook features need an explicit acknowledgement."""
    m = _mapping(ctx, mid)
    t = _template(ctx, U(m["template_id"]), lock=True)
    if t["status"] == "archived":
        raise Conflict("The template is archived.")
    if m["status"] != "draft":
        raise Conflict("Only a draft mapping can be activated.")
    issues = mp.validate_mapping(m["definition"], m["analysis"])
    if not mp.is_ready(issues):
        raise Conflict("The mapping has errors and cannot be activated.", extra={"issues": ser(issues)})
    risk = m["analysis"].get("risk_level", "none")
    if risk != "none" and not acknowledge_risks:
        raise Conflict(
            "This workbook uses features that may not survive being filled in. Review the compatibility report and confirm to continue.",
            extra={"fidelity": ser([f for f in m["analysis"]["fidelity"] if f["status"] != "preserved"])},
        )
    ctx.conn.execute(
        text("UPDATE xl_mappings SET status = 'superseded' WHERE template_id = :t AND status = 'active'"),
        {"t": U(m["template_id"])},
    )
    ctx.conn.execute(
        text(
            "UPDATE xl_mappings SET status = 'active', validation = CAST(:v AS jsonb), validated_at = now(), acknowledged_risks = :a, activated_by = :u, activated_at = now() WHERE id = :i"
        ),
        {"v": json.dumps(issues), "a": acknowledge_risks, "u": ctx.user_id, "i": mid},
    )
    ctx.conn.execute(
        text(
            "UPDATE xl_templates SET status = 'active', updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i AND status IN ('draft', 'inactive')"
        ),
        {"u": ctx.user_id, "i": U(m["template_id"])},
    )
    _audit(
        ctx,
        "excel.mapping_activated",
        "xl_mapping",
        mid,
        {"template": m["template_id"], "mapping_no": m["mapping_no"], "risks_acknowledged": acknowledge_risks},
    )
    return _mapping_view(_mapping(ctx, mid))


def _sheet_params(ds: cat.Dataset, params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    applied = {k: v for k, v in params.items() if k in ds.filters and v not in (None, "", [], False)}
    ignored = [
        k for k, v in params.items() if k not in ds.filters and k != "period_id" and v not in (None, "", [], False)
    ]
    return applied, ignored


def _resolve_period(ctx: RequestCtx, params: dict[str, Any]) -> dict[str, Any]:
    p = {k: v for k, v in params.items() if v is not None}
    pid = p.pop("period_id", None)
    if pid:
        _, co = _co(ctx)
        per = one(
            ctx, "SELECT start_date, end_date FROM acct_periods WHERE id = :i AND company_id = :c", i=U(pid), c=co
        )
        if per is None:
            raise NotFound("Accounting period not found.")
        p["date_from"], p["date_to"] = per["start_date"], per["end_date"]
    return p


def preview_mapping(ctx: RequestCtx, mid: uuid.UUID, params: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    """Run the mapping against REAL approved data (first rows) without generating a file. Uses the export code path."""
    m = _mapping(ctx, mid)
    p = _resolve_period(ctx, params)
    return _build_preview(ctx, m["definition"], p, limit, perms=acc.user_perms(ctx))


# ================================================================== export validation / preview
def _meta_for(
    ctx_conn: Connection, company: uuid.UUID, template_name: str, res: data.DatasetResult, user_name: str | None
) -> dict[str, Any]:
    return {**res.meta, "export_date": date.today(), "template_name": template_name, "prepared_by": user_name}


def _collect(
    conn: Connection,
    company: uuid.UUID,
    definition: dict[str, Any],
    params: dict[str, Any],
    *,
    include_unapproved: bool,
    limit: int,
) -> tuple[dict[str, data.DatasetResult], dict[str, Any]]:
    results: dict[str, data.DatasetResult] = {}
    info: dict[str, Any] = {}
    for sh in definition["sheets"]:
        ds = cat.DATASETS[sh["dataset"]]
        applied, ignored = _sheet_params(ds, params)
        if include_unapproved and "include_unapproved" in ds.filters:
            applied["include_unapproved"] = True
        f = data.parse_filters(sh["dataset"], applied)
        res = data.fetch(conn, company, sh["dataset"], f, limit=limit)
        results[sh["sheet"]] = res
        info[sh["sheet"]] = {
            "dataset": sh["dataset"],
            "applied_filters": f.as_dict(),
            "ignored_filters": ignored,
            "rows": len(res.rows),
            "truncated": res.truncated,
            "note": ds.note,
        }
    return results, info


def _build_preview(
    ctx: RequestCtx, definition: dict[str, Any], params: dict[str, Any], limit: int, *, perms: set[str]
) -> dict[str, Any]:
    _, co = _co(ctx)
    if params.get("include_unapproved") and "excel.export_unapproved" not in perms:
        raise Forbidden("You do not have permission to include unapproved transactions.")
    results, info = _collect(
        ctx.conn,
        co,
        definition,
        params,
        include_unapproved=bool(params.get("include_unapproved")),
        limit=get_settings().excel_max_export_rows + 1,
    )
    sheets = []
    for sh in definition["sheets"]:
        res = results[sh["sheet"]]
        grid = []
        for i, row in enumerate(res.rows[:limit]):
            grid.append(
                {
                    "row": int(sh["start_row"]) + i,
                    "cells": {c["column"].upper(): mp.fmt_preview(mp.resolve_column(c, row)) for c in sh["columns"]},
                }
            )
        sheets.append(
            {
                "sheet": sh["sheet"],
                **info[sh["sheet"]],
                "start_row": sh["start_row"],
                "columns": [c["column"].upper() for c in sh["columns"]],
                "sample": grid,
                "missing_required": mp.missing_required(sh, res.rows),
            }
        )
    fixed = []
    for fc in definition.get("fixed_cells", []):
        if fc["kind"] == "static":
            val: Any = fc.get("value")
        elif fc["kind"] == "field":
            val = (results.get(fc["sheet"]) or next(iter(results.values()))).meta.get(fc["field"])
        else:
            src = results.get(fc.get("source_sheet", fc["sheet"]))
            val = mp.aggregate(fc["op"], fc.get("of"), src.rows if src else [])
        fixed.append({"sheet": fc["sheet"], "cell": fc["cell"].upper(), "value": mp.fmt_preview(val)})
    return ser({"sheets": sheets, "fixed_cells": fixed, "preview_is_generated_from_the_same_rows": True})


def _previous_exports(
    conn: Connection, company: uuid.UUID, template_id: uuid.UUID, tx_ids: list[str]
) -> list[dict[str, Any]]:
    if not tx_ids:
        return []
    ids = [uuid.UUID(t) for t in tx_ids]
    out = conn.execute(
        text(
            "SELECT e.id, e.created_at, e.filename, v.version_no AS template_version, m.mapping_no, count(DISTINCT r.transaction_id) AS transactions FROM xl_export_rows r JOIN xl_exports e ON e.id = r.export_id"
            " JOIN xl_template_versions v ON v.id = e.version_id JOIN xl_mappings m ON m.id = e.mapping_id"
            " WHERE e.company_id = :c AND e.template_id = :t AND e.status = 'completed' AND r.transaction_id = ANY(:ids) GROUP BY e.id, e.created_at, e.filename, v.version_no, m.mapping_no ORDER BY e.created_at DESC"
        ),
        {"c": company, "t": template_id, "ids": ids},
    )
    return [
        {
            k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v)
            for k, v in dict(r._mapping).items()
        }
        for r in out
    ]


def _export_inputs(
    ctx: RequestCtx, body: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(template, version, mapping, params) for an export request; refuses inactive or unmapped templates."""
    _, co = _co(ctx)
    t = _template(ctx, U(body["template_id"]))
    if t["status"] != "active":
        raise Conflict(f"The template is {t['status']}; only an active template can be used for an export.")
    mid = body.get("mapping_id")
    m = (
        _mapping(ctx, U(mid))
        if mid
        else one(ctx, "SELECT id FROM xl_mappings WHERE template_id = :t AND status = 'active'", t=U(t["id"]))
    )
    if m is None:
        raise Conflict("The template has no active mapping.")
    if not mid:
        m = _mapping(ctx, U(m["id"]))
    if m["template_id"] != t["id"] or m["status"] != "active":
        raise Conflict("Only the template's active mapping can be used for an export.")
    v = must(ctx, "SELECT * FROM xl_template_versions WHERE id = :i AND company_id = :c", i=U(m["version_id"]), c=co)
    return t, v, m, _resolve_period(ctx, dict(body.get("params") or {}))


def validate_export(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    """Everything that could go wrong, before a file is made: ownership, status, mapping version, data, required fields, repeats."""
    org, co = _co(ctx)
    t, v, m, params = _export_inputs(ctx, body)
    perms = acc.user_perms(ctx)
    issues: list[dict[str, str]] = []
    definition = m["definition"]
    include_unapproved = bool(params.get("include_unapproved"))
    if include_unapproved and "excel.export_unapproved" not in perms:
        raise Forbidden("You do not have permission to include unapproved transactions.")
    limit = get_settings().excel_max_export_rows
    results, info = _collect(ctx.conn, co, definition, params, include_unapproved=include_unapproved, limit=limit + 1)
    tx_ids: list[str] = []
    total_rows = 0
    for sh in definition["sheets"]:
        res = results[sh["sheet"]]
        total_rows += len(res.rows)
        if len(res.rows) > limit:
            issues.append(
                mp.issue(
                    "too_many_rows",
                    f"Worksheet “{sh['sheet']}” has more than {limit} rows; narrow the filters.",
                    sh["sheet"],
                )
            )
        if not res.rows:
            issues.append(
                mp.issue("no_rows", f"Worksheet “{sh['sheet']}” has no rows for these filters.", sh["sheet"], "warning")
            )
        miss = mp.missing_required(sh, res.rows)
        if miss:
            issues.append(
                mp.issue(
                    "missing_required_values",
                    f"{len(miss)}+ row(s) on “{sh['sheet']}” have no value for a required column (first: row {miss[0]['row']}, column {miss[0]['column']}).",
                    sh["sheet"],
                )
            )
        tx_ids += [str(r["transaction_id"]) for r in res.rows if r.get("transaction_id")]
        if any(r.get("status") in data.UNAPPROVED for r in res.rows):
            issues.append(
                mp.issue(
                    "unapproved_included",
                    "Unapproved transactions are included; the export is marked as unapproved.",
                    sh["sheet"],
                    "warning",
                )
            )
        no_acct = [r for r in res.rows if "account_code" in r and not r.get("account_code")][:1]
        if no_acct:
            issues.append(
                mp.issue(
                    "missing_account", f"Some rows on “{sh['sheet']}” have no account code.", sh["sheet"], "warning"
                )
            )
    ver_issues = mp.validate_mapping(definition, v["analysis"])
    issues += [i for i in ver_issues if i["severity"] == "error"]
    per_id = (body.get("params") or {}).get("period_id")
    if per_id:
        per = one(ctx, "SELECT name, status FROM acct_periods WHERE id = :i AND company_id = :c", i=U(per_id), c=co)
        if per and per["status"] == "open":
            issues.append(
                mp.issue(
                    "period_open",
                    f"Accounting period {per['name']} is still open; later approvals can change it.",
                    "",
                    "info",
                )
            )
    tx_unique = sorted(set(tx_ids))
    repeats = _previous_exports(ctx.conn, co, U(t["id"]), tx_unique)
    repeat_tx = one(
        ctx,
        "SELECT count(DISTINCT r.transaction_id) AS n FROM xl_export_rows r JOIN xl_exports e ON e.id = r.export_id WHERE e.company_id = :c AND e.template_id = :t AND e.status = 'completed' AND r.transaction_id = ANY(:ids)",
        c=co,
        t=U(t["id"]),
        ids=[uuid.UUID(x) for x in tx_unique] or [uuid.UUID(int=0)],
    )
    blocking = [i for i in issues if i["severity"] == "error"]
    return ser(
        {
            "template": {
                "id": t["id"],
                "name": t["name"],
                "version_no": v["version_no"],
                "mapping_no": m["mapping_no"],
                "mapping_id": m["id"],
                "status": t["status"],
            },
            "params": {k: (val.isoformat() if isinstance(val, date) else val) for k, val in params.items()},
            "sheets": [{"sheet": s, **i} for s, i in info.items()],
            "total_rows": total_rows,
            "transactions": len(tx_unique),
            "issues": issues,
            "repeat": {
                "transactions": int(repeat_tx["n"]) if repeat_tx else 0,
                "exports": repeats,
                "needs_acknowledgement": bool(repeats),
            },
            "include_unapproved": include_unapproved,
            "can_generate": not blocking,
            "organization_id": str(org),
        }
    )


def preview_export(ctx: RequestCtx, body: dict[str, Any], limit: int = 50) -> dict[str, Any]:
    t, _v, m, params = _export_inputs(ctx, body)
    out = {
        "validation": validate_export(ctx, body),
        "preview": _build_preview(ctx, m["definition"], params, limit, perms=acc.user_perms(ctx)),
    }
    out["template"] = {"id": t["id"], "name": t["name"]}
    return out


# ================================================================== export creation and the worker
def create_export(
    ctx: RequestCtx, body: dict[str, Any], *, source: dict[str, Any] | None = None
) -> tuple[bool, dict[str, Any]]:
    """Queue an export. Returns (created, export). A repeated request with the same idempotency key returns the first export."""
    org, co = _co(ctx)
    key = body["idempotency_key"]
    prev = one(
        ctx,
        "SELECT id FROM xl_exports WHERE company_id = :c AND created_by = :u AND idempotency_key = :k",
        c=co,
        u=ctx.user_id,
        k=key,
    )
    if prev:
        return False, get_export(ctx, U(prev["id"]))
    val = validate_export(ctx, body)
    if not val["can_generate"]:
        raise Conflict(
            "The export has blocking problems. Fix them and validate again.", extra={"issues": val["issues"]}
        )
    rep = val["repeat"]
    explicit_repeat = bool(source) or bool(body.get("acknowledge_repeat"))
    if rep["needs_acknowledgement"] and not explicit_repeat:
        raise Conflict(
            "Some of these transactions were already exported with this template. Confirm that a repeat export is intended.",
            extra={"repeat": rep},
        )
    if rep["needs_acknowledgement"] and not (body.get("reason") or "").strip() and not source:
        raise Unprocessable("A repeat export needs a short reason.")
    if source is None:
        entitlements.guard_quota(ctx.conn, org, "quota.excel_exports_per_month", 1)
    t, v, m, params = _export_inputs(ctx, body)
    eid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO xl_exports (id, organization_id, company_id, template_id, version_id, mapping_id, idempotency_key, params, include_unapproved, source_export_id, reason, repeat_acknowledged, validation, created_by)"
            " VALUES (:id, :o, :c, :t, :v, :m, :k, CAST(:p AS jsonb), :iu, :src, :r, :ra, CAST(:val AS jsonb), :u)"
        ),
        {
            "id": eid,
            "o": org,
            "c": co,
            "t": U(t["id"]),
            "v": U(v["id"]),
            "m": U(m["id"]),
            "k": key,
            "p": json.dumps(ser({k: (x.isoformat() if isinstance(x, date) else x) for k, x in params.items()})),
            "iu": bool(params.get("include_unapproved")),
            "src": U(source["id"]) if source else None,
            "r": body.get("reason"),
            "ra": bool(rep["needs_acknowledgement"]),
            "val": json.dumps(val["issues"]),
            "u": ctx.user_id,
        },
    )
    _audit(
        ctx,
        "excel.export_requested",
        "xl_export",
        eid,
        {
            "template": t["id"],
            "rows": val["total_rows"],
            "repeat": bool(rep["needs_acknowledgement"]),
            "unapproved": bool(params.get("include_unapproved")),
            "regenerated_from": source["id"] if source else None,
        },
    )
    return True, get_export(ctx, eid)


def dispatch(org: uuid.UUID, company: uuid.UUID, export_id: str) -> None:
    if get_settings().processing_mode == "celery":
        from artify_api.worker import celery_app

        celery_app.send_task("artify.excel_export", args=[str(org), str(company), str(export_id)])
    else:
        run_export(str(org), str(company), str(export_id))


def _fail(org: str, company: str, export_id: str, code: str, message: str, attempts: int) -> None:
    def work(c: Connection) -> None:
        c.execute(
            text(
                "UPDATE xl_exports SET status = 'failed', error_code = :c, error_message = :m, attempt_count = :a, finished_at = now() WHERE id = :i AND status IN ('queued', 'running')"
            ),
            {"c": code, "m": message[:500], "a": attempts, "i": uuid.UUID(export_id)},
        )
        audit.write_audit(
            c,
            action="excel.export_failed",
            actor_type="system",
            org_id=uuid.UUID(org),
            company_id=uuid.UUID(company),
            entity_type="xl_export",
            entity_id=export_id,
            summary={"code": code},
        )

    run_in_tenant_context(org, company, work)


def run_export(org: str, company: str, export_id: str) -> None:
    """Generate, verify and store the workbook as the system. The export becomes `completed` only after the file was re-opened,
    checked and stored. Recoverable storage failures are retried; everything else fails with a reason and leaves no half-made file."""
    s = get_settings()
    eid = uuid.UUID(export_id)
    attempts = 0
    while True:
        attempts += 1

        def start(c: Connection, attempts: int = attempts) -> dict[str, Any] | None:
            r = c.execute(
                text(
                    "SELECT e.*, t.name AS template_name, t.file_kind, v.storage_object_id, m.definition, so.storage_key FROM xl_exports e JOIN xl_templates t ON t.id = e.template_id JOIN xl_template_versions v ON v.id = e.version_id JOIN xl_mappings m ON m.id = e.mapping_id JOIN storage_objects so ON so.id = v.storage_object_id WHERE e.id = :i"
                ),
                {"i": eid},
            ).first()
            if r is None or r.status in ("completed", "failed"):
                return None
            c.execute(
                text(
                    "UPDATE xl_exports SET status = 'running', progress = 10, started_at = COALESCE(started_at, now()), attempt_count = :a WHERE id = :i"
                ),
                {"a": attempts, "i": eid},
            )
            return dict(r._mapping)

        job = run_in_tenant_context(org, company, start)
        if job is None:
            return
        try:
            _generate(org, company, eid, job, attempts)
            return
        except storage.StorageError as exc:
            if attempts < s.excel_export_max_attempts:
                time.sleep(min(2.0 * attempts, 5.0) if s.processing_retry_backoff_seconds else 0)
                continue
            _fail(org, company, export_id, "storage_unavailable", f"The file store was not available: {exc}", attempts)
            return
        except wr.WriteError as exc:
            _fail(org, company, export_id, exc.code, exc.message, attempts)
            return
        except Unprocessable as exc:
            _fail(
                org, company, export_id, "invalid_request", str(exc.detail if hasattr(exc, "detail") else exc), attempts
            )
            return
        except Exception:
            log.exception("excel export %s failed", export_id)
            _fail(
                org,
                company,
                export_id,
                "export_failed",
                "The workbook could not be generated. Nothing was stored.",
                attempts,
            )
            return


def _generate(org: str, company: str, eid: uuid.UUID, job: dict[str, Any], attempts: int) -> None:
    s = get_settings()
    co = uuid.UUID(company)
    definition: dict[str, Any] = job["definition"]
    params = dict(job["params"])
    for k in ("date_from", "date_to", "as_of"):
        if params.get(k):
            params[k] = date.fromisoformat(str(params[k])[:10])
    with tempfile.TemporaryDirectory(dir=s.storage_tmp_dir) as td:
        tdir = Path(td)
        kind = job["file_kind"]
        tpl = tdir / f"template.{kind}"
        with tpl.open("wb") as f:
            for chunk in storage.get_storage().open_range(job["storage_key"]):
                f.write(chunk)

        def progress(c: Connection, pct: int) -> None:
            c.execute(text("UPDATE xl_exports SET progress = :p WHERE id = :i"), {"p": pct, "i": eid})

        run_in_tenant_context(org, company, lambda c: progress(c, 30))
        holder: dict[str, Any] = {}

        def read(c: Connection) -> None:
            results, info = _collect(
                c,
                co,
                definition,
                params,
                include_unapproved=bool(job["include_unapproved"]),
                limit=s.excel_max_export_rows + 1,
            )
            for sh_name, res in results.items():
                if len(res.rows) > s.excel_max_export_rows:
                    raise Unprocessable(f"Worksheet “{sh_name}” exceeds the row limit.")
            user = (
                c.execute(text("SELECT display_name FROM users WHERE id = :u"), {"u": job["created_by"]}).first()
                if job["created_by"]
                else None
            )
            scale = int(
                c.execute(
                    text("SELECT amount_scale FROM acct_configurations WHERE company_id = :c"), {"c": co}
                ).scalar_one()
            )
            holder.update(results=results, info=info, user=user.display_name if user else None, scale=scale)

        run_in_tenant_context(org, company, read)
        results = holder["results"]
        run_in_tenant_context(org, company, lambda c: progress(c, 55))
        first = next(iter(results.values()))
        meta = {
            **first.meta,
            "export_date": date.today(),
            "template_name": job["template_name"],
            "prepared_by": holder["user"],
        }
        for res in results.values():
            for k, v in res.meta.items():
                meta.setdefault(k, v)
        sheet_rows = {name: res.rows for name, res in results.items()}
        out = tdir / f"export.{kind}"
        report = wr.generate(tpl, kind, definition, sheet_rows, meta, out, scale=holder["scale"])
        problems = wr.verify_output(out, tpl, kind, definition, sheet_rows)
        if problems:
            raise wr.WriteError(
                "verification_failed", f"The generated workbook failed verification: {problems[0]['message']}"
            )
        size = out.stat().st_size
        sha = eng.sha256_of(out)
        stamp = date.today().strftime("%Y%m%d")
        base = Path(job["template_name"]).name.replace(" ", "_")[:60] or "export"
        filename = f"{base}_{stamp}_{str(eid)[:8]}.{kind}"
        run_in_tenant_context(org, company, lambda c: progress(c, 80))
        be = storage.get_storage()
        oid = new_id()
        key = storage.object_key(uuid.UUID(org), co, oid)
        be.put_file(key, out)
        if be.stat(key) != size:
            be.delete(key)
            raise storage.StorageError("The stored file does not match the generated file.")
        fidelity = [
            {
                "feature": "formulas",
                "status": "not_recalculated",
                "detail": "Formulas were kept; Excel recalculates them when the file is opened.",
            },
            *report["warnings"],
        ]
        counts = {n: len(r) for n, r in sheet_rows.items()}

        def finish(c: Connection) -> None:
            c.execute(
                text(
                    "INSERT INTO storage_objects (id, organization_id, company_id, backend, storage_key, size_bytes, sha256, state, verified_at, purpose) VALUES (:i, :o, :c, :b, :k, :s, :h, 'verified', now(), 'export')"
                ),
                {"i": oid, "o": uuid.UUID(org), "c": co, "b": be.name, "k": key, "s": size, "h": sha},
            )
            for sh in definition["sheets"]:
                start = int(sh["start_row"])
                for i, r in enumerate(sheet_rows[sh["sheet"]]):
                    if r.get("transaction_id"):
                        c.execute(
                            text(
                                "INSERT INTO xl_export_rows (id, organization_id, company_id, export_id, transaction_id, journal_id, sheet, row_no) VALUES (:i, :o, :c, :e, :t, :j, :s, :r)"
                            ),
                            {
                                "i": new_id(),
                                "o": uuid.UUID(org),
                                "c": co,
                                "e": eid,
                                "t": uuid.UUID(str(r["transaction_id"])),
                                "j": uuid.UUID(str(r["journal_id"])) if r.get("journal_id") else None,
                                "s": sh["sheet"],
                                "r": start + i,
                            },
                        )
            c.execute(
                text(
                    "UPDATE xl_exports SET status = 'completed', progress = 100, filename = :f, storage_object_id = :o, sha256 = :h, size_bytes = :s, row_counts = CAST(:rc AS jsonb), fidelity = CAST(:fd AS jsonb), attempt_count = :a, finished_at = now() WHERE id = :i"
                ),
                {
                    "f": filename,
                    "o": oid,
                    "h": sha,
                    "s": size,
                    "rc": json.dumps(counts),
                    "fd": json.dumps(fidelity),
                    "a": attempts,
                    "i": eid,
                },
            )
            audit.write_audit(
                c,
                action="excel.export_completed",
                actor_type="system",
                org_id=uuid.UUID(org),
                company_id=co,
                entity_type="xl_export",
                entity_id=str(eid),
                summary={
                    "rows": sum(counts.values()),
                    "sha256": sha,
                    "requested_by": str(job["created_by"]) if job["created_by"] else None,
                },
            )

        try:
            run_in_tenant_context(org, company, finish)
        except Exception:
            be.delete(key)  # never leave a stored file that no completed export refers to
            raise


# ================================================================== export history
EXPORT_COLS = (
    "e.id, e.template_id, t.name AS template_name, v.version_no AS template_version, m.mapping_no, e.status, e.progress, e.params, e.include_unapproved, e.source_export_id, e.reason,"
    " e.repeat_acknowledged, e.filename, e.sha256, e.size_bytes, e.row_counts, e.validation, e.fidelity, e.error_code, e.error_message, e.attempt_count, e.created_at, e.started_at,"
    " e.finished_at, u.display_name AS created_by_name"
)
_EXPORT_FROM = " FROM xl_exports e JOIN xl_templates t ON t.id = e.template_id JOIN xl_template_versions v ON v.id = e.version_id JOIN xl_mappings m ON m.id = e.mapping_id LEFT JOIN users u ON u.id = e.created_by"


def get_export(ctx: RequestCtx, eid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    e = one(ctx, f"SELECT {EXPORT_COLS}{_EXPORT_FROM} WHERE e.company_id = :c AND e.id = :i", c=co, i=eid)
    if e is None:
        raise NotFound("Export not found.")
    e["download_available"] = e["status"] == "completed"
    e["transactions_included"] = int(
        (
            one(ctx, "SELECT count(DISTINCT transaction_id) AS n FROM xl_export_rows WHERE export_id = :e", e=eid)
            or {"n": 0}
        )["n"]
    )
    return ser(e)


def list_exports(
    ctx: RequestCtx, *, template_id: uuid.UUID | None, status: str | None, limit: int, offset: int
) -> dict[str, Any]:
    _, co = _co(ctx)
    where = "e.company_id = :c"
    p: dict[str, Any] = {"c": co}
    if template_id:
        where += " AND e.template_id = :t"
        p["t"] = template_id
    if status:
        where += " AND e.status = :s"
        p["s"] = status
    total = one(ctx, f"SELECT count(*) AS n FROM xl_exports e WHERE {where}", **p)
    items = rows(
        ctx,
        f"SELECT {EXPORT_COLS}{_EXPORT_FROM} WHERE {where} ORDER BY e.created_at DESC LIMIT :l OFFSET :o",
        **p,
        l=limit,
        o=offset,
    )
    return ser({"items": items, "total": total["n"] if total else 0, "limit": limit, "offset": offset})


def export_transactions(ctx: RequestCtx, eid: uuid.UUID, limit: int, offset: int) -> dict[str, Any]:
    get_export(ctx, eid)
    total = one(ctx, "SELECT count(*) AS n FROM xl_export_rows WHERE export_id = :e", e=eid)
    items = rows(
        ctx,
        "SELECT r.sheet, r.row_no, t.id AS transaction_id, t.number, t.txn_type, t.transaction_date, t.doc_number, t.gross_amount, t.currency, j.number AS journal_number FROM xl_export_rows r"
        " JOIN acct_transactions t ON t.id = r.transaction_id LEFT JOIN acct_journals j ON j.id = r.journal_id WHERE r.export_id = :e ORDER BY r.sheet, r.row_no LIMIT :l OFFSET :o",
        e=eid,
        l=limit,
        o=offset,
    )
    return ser({"items": items, "total": total["n"] if total else 0, "limit": limit, "offset": offset})


def transaction_exports(ctx: RequestCtx, transaction_id: uuid.UUID) -> list[dict[str, Any]]:
    """Which exports (any template) included a transaction, for traceability from the accounting side."""
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            "SELECT e.id AS export_id, e.created_at, e.filename, t.name AS template_name, r.sheet, r.row_no FROM xl_export_rows r JOIN xl_exports e ON e.id = r.export_id JOIN xl_templates t ON t.id = e.template_id"
            " WHERE e.company_id = :c AND r.transaction_id = :t AND e.status = 'completed' ORDER BY e.created_at DESC",
            c=co,
            t=transaction_id,
        )
    )


def download_export(ctx: RequestCtx, eid: uuid.UUID) -> tuple[Iterator[bytes], str, int, str, str]:
    _, co = _co(ctx)
    e = one(
        ctx,
        "SELECT e.status, e.filename, e.storage_object_id, e.sha256, t.file_kind FROM xl_exports e JOIN xl_templates t ON t.id = e.template_id WHERE e.company_id = :c AND e.id = :i",
        c=co,
        i=eid,
    )
    if e is None:
        raise NotFound("Export not found.")
    if e["status"] != "completed":
        raise Conflict("The export has not completed; there is no file to download.")
    key, size = _open_object(ctx, e["storage_object_id"])
    if storage.get_storage().stat(key) != size:
        raise NotFound("The stored file is missing or damaged. Re-generate the export.")
    _audit(ctx, "excel.export_downloaded", "xl_export", eid, {"sha256": e["sha256"]})
    return (
        storage.get_storage().open_range(key),
        e["filename"],
        size,
        XLSM_MIME if e["file_kind"] == "xlsm" else XLSX_MIME,
        e["sha256"],
    )


def verify_export(ctx: RequestCtx, eid: uuid.UUID) -> dict[str, Any]:
    """Re-read the stored file and compare its checksum with the one recorded when it was generated."""
    _, co = _co(ctx)
    e = one(
        ctx, "SELECT status, sha256, storage_object_id FROM xl_exports WHERE company_id = :c AND id = :i", c=co, i=eid
    )
    if e is None:
        raise NotFound("Export not found.")
    if e["status"] != "completed":
        raise Conflict("The export has not completed.")
    key, size = _open_object(ctx, e["storage_object_id"])
    h = hashlib.sha256()
    n = 0
    for chunk in storage.get_storage().open_range(key):
        h.update(chunk)
        n += len(chunk)
    return {
        "ok": h.hexdigest() == e["sha256"] and n == size,
        "recorded_sha256": e["sha256"],
        "actual_sha256": h.hexdigest(),
        "size_bytes": n,
    }


def regenerate_export(ctx: RequestCtx, eid: uuid.UUID, body: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """A new export with the same template version, mapping and parameters. It is explicit, reasoned and never replaces the first."""
    src = get_export(ctx, eid)
    if not (body.get("reason") or "").strip():
        raise Unprocessable("A re-export needs a short reason.")
    row = must(ctx, "SELECT mapping_id, template_id FROM xl_exports WHERE id = :i", i=eid)
    params = dict(src["params"])
    return create_export(
        ctx,
        {
            "template_id": str(row["template_id"]),
            "mapping_id": str(row["mapping_id"]),
            "params": params,
            "idempotency_key": body["idempotency_key"],
            "reason": body["reason"],
            "acknowledge_repeat": True,
        },
        source=src,
    )


def compare_exports(ctx: RequestCtx, a: uuid.UUID, b: uuid.UUID) -> dict[str, Any]:
    ea, eb = get_export(ctx, a), get_export(ctx, b)
    diff: list[dict[str, Any]] = []
    for k in (
        "template_name",
        "template_version",
        "mapping_no",
        "include_unapproved",
        "status",
        "filename",
        "sha256",
        "size_bytes",
    ):
        if ea[k] != eb[k]:
            diff.append({"field": k, "a": ea[k], "b": eb[k]})
    for k in sorted(set(ea["params"]) | set(eb["params"])):
        if ea["params"].get(k) != eb["params"].get(k):
            diff.append({"field": f"params.{k}", "a": ea["params"].get(k), "b": eb["params"].get(k)})
    for k in sorted(set(ea["row_counts"]) | set(eb["row_counts"])):
        if ea["row_counts"].get(k) != eb["row_counts"].get(k):
            diff.append({"field": f"rows.{k}", "a": ea["row_counts"].get(k), "b": eb["row_counts"].get(k)})
    both = one(
        ctx,
        "SELECT count(*) AS n FROM (SELECT transaction_id FROM xl_export_rows WHERE export_id = :a INTERSECT SELECT transaction_id FROM xl_export_rows WHERE export_id = :b) x",
        a=a,
        b=b,
    )
    return ser(
        {
            "a": ea["id"],
            "b": eb["id"],
            "differences": diff,
            "transactions_in_both": both["n"] if both else 0,
            "identical_configuration": not [
                d
                for d in diff
                if d["field"].startswith("params.")
                or d["field"] in ("template_version", "mapping_no", "include_unapproved")
            ],
        }
    )


# ================================================================== reports
def standard_reports() -> list[dict[str, Any]]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "category": d.category,
            "description": d.description,
            "filters": list(d.filters),
            "required_filters": [d.extras["requires"]] if "requires" in d.extras else [],
            "note": d.note,
            "statutory": False,
            "fields": [{"key": f.key, "label": f.label, "kind": f.kind} for f in d.fields],
        }
        for d in cat.DATASETS.values()
    ]


def _run_dataset(ctx: RequestCtx, key: str, params: dict[str, Any], *, perms: set[str]) -> data.DatasetResult:
    _, co = _co(ctx)
    if params.get("include_unapproved") and "excel.export_unapproved" not in perms:
        raise Forbidden("You do not have permission to include unapproved transactions.")
    f = data.parse_filters(key, _resolve_period(ctx, params))
    return data.fetch(ctx.conn, co, key, f, limit=get_settings().excel_max_export_rows)


def run_standard_report(ctx: RequestCtx, key: str, params: dict[str, Any], limit: int, offset: int) -> dict[str, Any]:
    if key not in cat.DATASETS:
        raise NotFound("Unknown report.")
    ds = cat.DATASETS[key]
    res = _run_dataset(ctx, key, params, perms=acc.user_perms(ctx))
    _record_run(ctx, None, key, params, {}, len(res.rows))
    return _report_page(
        ds, res, [{"key": f.key, "label": f.label, "kind": f.kind} for f in ds.fields], res.rows, limit, offset
    )


def _report_page(
    ds: cat.Dataset,
    res: data.DatasetResult,
    columns: list[dict[str, str]],
    rws: list[dict[str, Any]],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return ser(
        {
            "key": ds.key,
            "label": ds.label,
            "note": ds.note,
            "statutory": False,
            "columns": columns,
            "rows": [{c["key"]: r.get(c["key"]) for c in columns} for r in rws[offset : offset + limit]],
            "total": len(rws),
            "limit": limit,
            "offset": offset,
            "truncated": res.truncated,
            "meta": {k: v for k, v in res.meta.items() if k in {h.key for h in cat.HEADER_FIELDS}},
        }
    )


def _record_run(
    ctx: RequestCtx,
    report_id: uuid.UUID | None,
    key: str,
    params: dict[str, Any],
    definition: dict[str, Any],
    count: int,
) -> None:
    org, co = _co(ctx)
    ctx.conn.execute(
        text(
            "INSERT INTO xl_report_runs (id, organization_id, company_id, report_id, report_key, params, definition, row_count, created_by) VALUES (:i, :o, :c, :r, :k, CAST(:p AS jsonb), CAST(:d AS jsonb), :n, :u)"
        ),
        {
            "i": new_id(),
            "o": org,
            "c": co,
            "r": report_id,
            "k": key,
            "p": json.dumps(ser({k: (v.isoformat() if isinstance(v, date) else v) for k, v in params.items()})),
            "d": json.dumps(definition),
            "n": count,
            "u": ctx.user_id,
        },
    )


REPORT_COLS = "r.id, r.name, r.description, r.category, r.dataset, r.definition, r.template_id, r.status, r.created_at, r.updated_at, r.row_version, (SELECT u.display_name FROM users u WHERE u.id = r.created_by) AS created_by_name"


def _report(ctx: RequestCtx, rid: uuid.UUID) -> dict[str, Any]:
    _, co = _co(ctx)
    r = one(ctx, f"SELECT {REPORT_COLS} FROM xl_reports r WHERE r.company_id = :c AND r.id = :i", c=co, i=rid)
    if r is None:
        raise NotFound("Report configuration not found.")
    return r


def list_reports(ctx: RequestCtx, include_archived: bool = False) -> list[dict[str, Any]]:
    _, co = _co(ctx)
    return ser(
        rows(
            ctx,
            f"SELECT {REPORT_COLS} FROM xl_reports r WHERE r.company_id = :c"
            + ("" if include_archived else " AND r.status = 'active'")
            + " ORDER BY lower(r.name)",
            c=co,
        )
    )


def _validate_report(ctx: RequestCtx, definition: dict[str, Any], template_id: Any) -> list[dict[str, str]]:
    issues = mp.validate_report_definition(definition)
    if not [i for i in issues if i["severity"] == "error"]:
        try:
            data.parse_filters(definition["dataset"], definition.get("filters") or {})
        except Unprocessable as exc:
            issues.append(mp.issue("invalid_filter", str(exc.detail if hasattr(exc, "detail") else exc), "filters"))
    if template_id:
        t = _template(ctx, U(template_id))
        m = one(ctx, "SELECT definition FROM xl_mappings WHERE template_id = :t AND status = 'active'", t=U(t["id"]))
        if m is None or not any(s["dataset"] == definition.get("dataset") for s in m["definition"]["sheets"]):
            issues.append(
                mp.issue(
                    "template_dataset_mismatch",
                    "The associated template has no active mapping for this report's data source.",
                    "template_id",
                )
            )
    return issues


def create_report(ctx: RequestCtx, body: dict[str, Any]) -> dict[str, Any]:
    org, co = _co(ctx)
    if not entitlements.has_feature(ctx.conn, org, "feature.report_builder"):
        raise Conflict("The custom report builder is not included in the current plan.")
    issues = _validate_report(ctx, body["definition"], body.get("template_id"))
    if any(i["severity"] == "error" for i in issues):
        raise Unprocessable(issues[0]["message"], extra={"issues": issues})
    if one(
        ctx,
        "SELECT 1 AS x FROM xl_reports WHERE company_id = :c AND lower(name) = lower(:n) AND status = 'active'",
        c=co,
        n=body["name"].strip(),
    ):
        raise Conflict("A report with that name already exists.")
    before = entitlements.company_usage(ctx.conn, org, co)
    rid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO xl_reports (id, organization_id, company_id, name, description, category, dataset, definition, template_id, created_by, updated_by)"
            " VALUES (:i, :o, :c, :n, :d, :cat, :ds, CAST(:def AS jsonb), :t, :u, :u)"
        ),
        {
            "i": rid,
            "o": org,
            "c": co,
            "n": body["name"].strip(),
            "d": body.get("description"),
            "cat": _category(body.get("category") or "custom_report"),
            "ds": body["definition"]["dataset"],
            "def": json.dumps(body["definition"], default=str),
            "t": U(body["template_id"]) if body.get("template_id") else None,
            "u": ctx.user_id,
        },
    )
    entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.custom_reports_per_company"])
    _audit(ctx, "excel.report_created", "xl_report", rid, {"dataset": body["definition"]["dataset"]})
    return ser(_report(ctx, rid))


def update_report(ctx: RequestCtx, rid: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    r = _report(ctx, rid)
    if body.get("base_row_version") is not None and body["base_row_version"] != r["row_version"]:
        raise Conflict("The report changed since you opened it. Reload and try again.")
    definition = body.get("definition", r["definition"])
    template_id = body["template_id"] if "template_id" in body else r["template_id"]
    issues = _validate_report(ctx, definition, template_id)
    if any(i["severity"] == "error" for i in issues):
        raise Unprocessable(issues[0]["message"], extra={"issues": issues})
    ctx.conn.execute(
        text(
            "UPDATE xl_reports SET name = :n, description = :d, category = :cat, dataset = :ds, definition = CAST(:def AS jsonb), template_id = :t, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {
            "n": (body.get("name") or r["name"]).strip(),
            "d": body.get("description", r["description"]),
            "cat": _category(body.get("category") or r["category"]),
            "ds": definition["dataset"],
            "def": json.dumps(definition, default=str),
            "t": U(template_id) if template_id else None,
            "u": ctx.user_id,
            "i": rid,
        },
    )
    _audit(ctx, "excel.report_updated", "xl_report", rid, {})
    return ser(_report(ctx, rid))


def archive_report(ctx: RequestCtx, rid: uuid.UUID, restore: bool = False) -> dict[str, Any]:
    org, co = _co(ctx)
    _report(ctx, rid)
    before = entitlements.company_usage(ctx.conn, org, co)
    ctx.conn.execute(
        text(
            "UPDATE xl_reports SET status = :s, updated_by = :u, updated_at = now(), row_version = row_version + 1 WHERE id = :i"
        ),
        {"s": "active" if restore else "archived", "u": ctx.user_id, "i": rid},
    )
    if restore:
        entitlements.enforce_company_limits(ctx.conn, org, co, before, keys=["limit.custom_reports_per_company"])
    _audit(ctx, "excel.report_restored" if restore else "excel.report_archived", "xl_report", rid, {})
    return ser(_report(ctx, rid))


def validate_report_definition(ctx: RequestCtx, definition: dict[str, Any], template_id: Any = None) -> dict[str, Any]:
    issues = _validate_report(ctx, definition, template_id)
    return {"issues": issues, "ready": mp.is_ready(issues)}


def run_custom_report(
    ctx: RequestCtx, rid: uuid.UUID, params: dict[str, Any], limit: int, offset: int
) -> dict[str, Any]:
    r = _report(ctx, rid)
    if r["status"] != "active":
        raise Conflict("The report is archived.")
    definition = {**r["definition"]}
    merged = {**(definition.get("filters") or {}), **{k: v for k, v in params.items() if v not in (None, "")}}
    res = _run_dataset(ctx, definition["dataset"], merged, perms=acc.user_perms(ctx))
    cols, rws = mp.apply_report(definition, res.rows)
    _record_run(ctx, rid, definition["dataset"], merged, definition, len(rws))
    ds = cat.DATASETS[definition["dataset"]]
    out = _report_page(ds, res, cols, rws, limit, offset)
    out["label"] = r["name"]
    out["report_id"] = str(rid)
    return out


def export_report(ctx: RequestCtx, rid: uuid.UUID, body: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    r = _report(ctx, rid)
    if not r["template_id"]:
        raise Conflict("Associate a template with this report before exporting it.")
    params = {**(r["definition"].get("filters") or {}), **(body.get("params") or {})}
    return create_export(
        ctx,
        {
            "template_id": str(r["template_id"]),
            "params": params,
            "idempotency_key": body["idempotency_key"],
            "acknowledge_repeat": body.get("acknowledge_repeat", False),
            "reason": body.get("reason"),
        },
    )


# ================================================================== Control Center overview (organization level)
def overview(ctx: RequestCtx) -> dict[str, Any]:
    assert ctx.org_id is not None
    c = ctx.conn
    templates = rows(
        c,
        "SELECT t.id, t.company_id, co.name AS company_name, t.name, t.category, t.status, t.file_kind, t.current_version_no, v.risk_level,"
        " (SELECT count(*) FROM xl_exports e WHERE e.template_id = t.id AND e.status = 'completed') AS exports,"
        " (SELECT max(e.created_at) FROM xl_exports e WHERE e.template_id = t.id) AS last_export_at"
        " FROM xl_templates t JOIN companies co ON co.id = t.company_id JOIN xl_template_versions v ON v.template_id = t.id AND v.version_no = t.current_version_no WHERE t.status <> 'archived' ORDER BY co.name, lower(t.name) LIMIT 500",
    )
    recent = rows(
        c,
        "SELECT e.id, e.company_id, co.name AS company_name, t.name AS template_name, e.status, e.created_at, e.filename, e.row_counts, e.include_unapproved, e.error_message, u.display_name AS created_by_name"
        " FROM xl_exports e JOIN companies co ON co.id = e.company_id JOIN xl_templates t ON t.id = e.template_id LEFT JOIN users u ON u.id = e.created_by ORDER BY e.created_at DESC LIMIT 50",
    )
    stats = one(
        c,
        "SELECT count(*) FILTER (WHERE status = 'completed') AS completed, count(*) FILTER (WHERE status = 'failed') AS failed, count(*) FILTER (WHERE status IN ('queued', 'running')) AS running,"
        " count(*) FILTER (WHERE created_at >= date_trunc('month', now())) AS this_month FROM xl_exports",
    )
    store = one(
        c,
        "SELECT COALESCE(sum(size_bytes) FILTER (WHERE purpose = 'template'), 0) AS template_bytes, COALESCE(sum(size_bytes) FILTER (WHERE purpose = 'export'), 0) AS export_bytes FROM storage_objects WHERE state IN ('stored', 'verified')",
    )
    usage = [
        {
            "key": k,
            "used": entitlements.measure(c, ctx.org_id, k),
            "limit": (lim := entitlements.effective_limit(c, ctx.org_id, k)).value,
            "unlimited": lim.unlimited,
        }
        for k in ("quota.excel_exports_per_month",)
    ]
    reports = one(c, "SELECT count(*) FILTER (WHERE status = 'active') AS active FROM xl_reports")
    return ser(
        {
            "templates": templates,
            "recent_exports": recent,
            "stats": stats,
            "storage": store,
            "usage": usage,
            "custom_reports": reports["active"] if reports else 0,
        }
    )
