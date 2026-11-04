"""Document management API (Phase 6). Thin: authorization, validation and response shaping; logic is in documents*.py.

Every route declares `workspace.read` (company reach) and then decides per FOLDER (workspace node) what the caller may do.
There are no public or signed object URLs: bytes leave only through `GET …/content`, authenticated and audited."""

from __future__ import annotations

import re
import urllib.parse
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import documents as docs
from artify_api import documents_ops as ops
from artify_api import workspace as ws
from artify_api.context import RequestCtx, Requires
from artify_api.errors import PROBLEM_JSON, Unprocessable
from artify_api.ids import new_id
from artify_api.policy import authorize
from artify_api.routers.workspace import _base
from artify_api.settings import get_settings

router = APIRouter(tags=["documents"])
Read = Annotated[RequestCtx, Depends(Requires("workspace.read", company=True))]
OrgStorage = Annotated[RequestCtx, Depends(Requires("entitlements.read"))]
OrgPurge = Annotated[RequestCtx, Depends(Requires("documents.purge"))]
OrgList = Annotated[RequestCtx, Depends(Requires("companies.list"))]
INLINE_OK = {"pdf", "jpg", "png", "tif", "bmp"}  # types a browser/webview can show safely without running content


def env_of(ctx: RequestCtx) -> docs.Env:
    assert ctx.principal is not None and ctx.org_id is not None and ctx.company_id is not None
    acc = ws.load_access(ctx.conn, ctx.company_id, ctx.user_id, ctx.principal.org_role, _base(ctx))
    return docs.Env(
        ctx.conn,
        ctx.org_id,
        ctx.company_id,
        ctx.user_id,
        ctx.meta,
        acc,
        ctx.principal.org_role,
        can_purge=authorize(ctx.principal, "documents.purge").allowed,
    )


def _problem(request: Request, status: int, code: str, detail: str, extra: dict[str, Any]) -> JSONResponse:
    body = {
        "type": f"https://api.artify.example/problems/{code}",
        "title": detail,
        "status": status,
        "code": code,
        "detail": detail,
        "instance": request.url.path,
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


# ---------------------------------------------------------------- schemas
class BatchIn(BaseModel):
    source: Literal["desktop", "web", "api", "mobile"] = "desktop"
    label: str | None = Field(default=None, max_length=200)
    file_count: int = Field(ge=1, le=10_000)
    device_id: uuid.UUID | None = None


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern="^[0-9a-fA-F]{64}$")
    node_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None  # set to add a NEW VERSION to an existing document
    batch_id: uuid.UUID | None = None
    source: Literal["desktop", "web", "api", "mobile", "inbox"] = "desktop"
    display_name: str | None = Field(default=None, max_length=255)
    field_values: dict[str, Any] = Field(default_factory=dict)
    change_reason: str | None = Field(default=None, max_length=500)
    duplicate_policy: Literal["block", "allow_copy"] = "block"
    filing_rule_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None


class UploadPatch(BaseModel):
    node_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    display_name: str | None = Field(default=None, max_length=255)
    field_values: dict[str, Any] | None = None
    duplicate_policy: Literal["block", "allow_copy"] | None = None


class SuggestIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    category_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    source: Literal["desktop", "web", "api", "mobile", "inbox"] = "desktop"
    field_values: dict[str, Any] = Field(default_factory=dict)
    document_date: str | None = None


class DuplicateCheckIn(BaseModel):
    sha256: str = Field(pattern="^[0-9a-fA-F]{64}$")


class DocumentPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    field_values: dict[str, Any] | None = None
    row_version: int | None = None


class MoveIn(BaseModel):
    node_id: uuid.UUID
    category_id: uuid.UUID | None = None
    row_version: int | None = None


class DeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PurgeIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=255)


class HoldIn(BaseModel):
    hold: bool


class RestoreVersionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ReconcileIn(BaseModel):
    dry_run: bool = True


# ---------------------------------------------------------------- limits, batches, suggestions
@router.get("/companies/{company_id}/documents/limits", operation_id="documents_limits")
def limits(company_id: uuid.UUID, ctx: Read) -> Any:
    lim = docs.effective_limits(ctx.conn, ctx.org_id)  # type: ignore[arg-type]
    return {
        "max_file_size_bytes": lim.max_file_size,
        "import_batch_max_files": lim.batch_max_files,
        "chunk_max_bytes": lim.chunk_max_bytes,
        "max_concurrent_uploads": lim.max_concurrent,
        "allowed_extensions": lim.allowed_extensions,
    }


@router.post("/companies/{company_id}/document-batches", status_code=201, operation_id="documents_batch_create")
def create_batch(company_id: uuid.UUID, body: BatchIn, ctx: Read) -> Any:
    env = env_of(ctx)
    if not env.acc.can("upload") and not any(env.acc.can("upload", n) for n in env.acc.nodes):
        raise docs.DocumentError(403, "forbidden", "You cannot import documents into this company.")
    lim = docs.effective_limits(env.conn, env.org_id)
    if body.file_count > lim.batch_max_files:
        raise docs.DocumentError(
            413, "batch_too_large", "That is more files than one import may contain.", {"limit": lim.batch_max_files}
        )
    bid = new_id()
    env.conn.execute(
        text(
            "INSERT INTO document_import_batches (id, organization_id, company_id, created_by, device_id, source, label)"
            " VALUES (:i, :o, :c, :u, :d, :s, :l)"
        ),
        {
            "i": bid,
            "o": env.org_id,
            "c": env.company_id,
            "u": env.user_id,
            "d": body.device_id,
            "s": body.source,
            "l": body.label,
        },
    )
    return {
        "id": bid,
        "file_count": body.file_count,
        "max_file_size_bytes": lim.max_file_size,
        "chunk_max_bytes": lim.chunk_max_bytes,
    }


@router.post("/companies/{company_id}/documents/suggest", operation_id="documents_suggest")
def suggest(company_id: uuid.UUID, body: SuggestIn, ctx: Read) -> Any:
    return docs.suggest(
        env_of(ctx),
        filename=docs.clean_filename(body.filename),
        category_id=body.category_id,
        node_id=body.node_id,
        source=body.source,
        field_values=body.field_values,
        document_date=body.document_date,
    )


@router.post("/companies/{company_id}/documents/check-duplicate", operation_id="documents_check_duplicate")
def check_duplicate(company_id: uuid.UUID, body: DuplicateCheckIn, ctx: Read) -> Any:
    return docs.find_duplicate(env_of(ctx), body.sha256.lower()) or {"exists": False, "document": None}


# ---------------------------------------------------------------- uploads (resumable)
@router.post("/companies/{company_id}/uploads", status_code=201, operation_id="documents_upload_init")
def upload_init(
    company_id: uuid.UUID,
    body: UploadIn,
    ctx: Read,
    response: Response,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise Unprocessable("An Idempotency-Key header of 8-128 characters is required.")
    session, created = docs.init_upload(
        env_of(ctx),
        idem_key=idempotency_key,
        filename=body.filename,
        size=body.size,
        sha256=body.sha256,
        node_id=body.node_id,
        category_id=body.category_id,
        document_id=body.document_id,
        batch_id=body.batch_id,
        source=body.source,
        display_name=body.display_name,
        field_values=body.field_values,
        change_reason=body.change_reason,
        duplicate_policy=body.duplicate_policy,
        filing_rule_id=body.filing_rule_id,
        device_id=body.device_id,
    )
    if not created:
        response.status_code = 200
    return docs.session_out(session)


@router.get("/companies/{company_id}/uploads", operation_id="documents_uploads_list")
def uploads_list(
    company_id: uuid.UUID,
    ctx: Read,
    status: str | None = None,
    batch_id: uuid.UUID | None = None,
    mine: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Any:
    return ops.list_sessions(env_of(ctx), status, batch_id, mine, limit, offset)


@router.get("/companies/{company_id}/uploads/{session_id}", operation_id="documents_upload_get")
def upload_get(company_id: uuid.UUID, session_id: uuid.UUID, ctx: Read) -> Any:
    return docs.session_out(docs._session(env_of(ctx), session_id))


@router.patch("/companies/{company_id}/uploads/{session_id}", operation_id="documents_upload_update")
def upload_update(company_id: uuid.UUID, session_id: uuid.UUID, body: UploadPatch, ctx: Read) -> Any:
    return docs.update_upload(env_of(ctx), session_id, body.model_dump(exclude_unset=True))


@router.put("/companies/{company_id}/uploads/{session_id}/content", operation_id="documents_upload_chunk")
async def upload_chunk(
    company_id: uuid.UUID, session_id: uuid.UUID, request: Request, ctx: Read, offset: int = Query(ge=0)
) -> Any:
    cap = get_settings().upload_chunk_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise docs.DocumentError(
            413, "bad_chunk", "The chunk is larger than the allowed chunk size.", {"chunk_max_bytes": cap}
        )
    data = b""
    async for part in request.stream():
        data += part
        if len(data) > cap:
            raise docs.DocumentError(
                413, "bad_chunk", "The chunk is larger than the allowed chunk size.", {"chunk_max_bytes": cap}
            )
    return await run_in_threadpool(docs.append_chunk, env_of(ctx), session_id, offset, data)


@router.post("/companies/{company_id}/uploads/{session_id}/complete", operation_id="documents_upload_complete")
def upload_complete(company_id: uuid.UUID, session_id: uuid.UUID, request: Request, ctx: Read) -> Any:
    env = env_of(ctx)
    session, failure = docs.complete_upload(env, session_id)
    if failure is not None:
        return _problem(
            request, failure.http, failure.code, failure.message, {"session": _jsonable(session), **failure.extra}
        )
    return {"session": session, "document": ops.detail(env, session["result_document_id"])}


@router.post("/companies/{company_id}/uploads/{session_id}/cancel", operation_id="documents_upload_cancel")
def upload_cancel(company_id: uuid.UUID, session_id: uuid.UUID, ctx: Read) -> Any:
    return docs.cancel_upload(env_of(ctx), session_id)


@router.post("/companies/{company_id}/uploads/{session_id}/retry", operation_id="documents_upload_retry")
def upload_retry(company_id: uuid.UUID, session_id: uuid.UUID, ctx: Read) -> Any:
    return docs.retry_upload(env_of(ctx), session_id)


def _jsonable(value: Any) -> Any:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(value)


# ---------------------------------------------------------------- documents
@router.get("/companies/{company_id}/documents", operation_id="documents_list")
def documents_list(
    company_id: uuid.UUID,
    ctx: Read,
    q: Annotated[str | None, Query(max_length=100)] = None,
    node_id: uuid.UUID | None = None,
    include_subfolders: bool = True,
    category_id: uuid.UUID | None = None,
    status: Literal["active", "archived", "deleted"] = "active",
    family: Literal["pdf", "image", "excel", "word", "text"] | None = None,
    ext: Annotated[str | None, Query(max_length=10)] = None,
    uploaded_by: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    field: Annotated[list[str] | None, Query(description="key:value, repeatable (max 5)")] = None,
    sort: Literal["name", "created", "updated", "size", "type"] = "created",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Any:
    pairs = [tuple(f.split(":", 1)) for f in (field or []) if ":" in f]
    return ops.list_documents(
        env_of(ctx),
        q=q,
        node_id=node_id,
        include_subfolders=include_subfolders,
        category_id=category_id,
        status=status,
        family=family,
        ext=ext,
        uploaded_by=uploaded_by,
        created_from=created_from,
        created_to=created_to,
        fields=[(k, v) for k, v in pairs],
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/companies/{company_id}/documents/counts", operation_id="documents_counts")
def documents_counts(company_id: uuid.UUID, ctx: Read) -> Any:
    return ops.counts(env_of(ctx))


@router.get("/companies/{company_id}/documents/storage", operation_id="documents_company_storage")
def documents_company_storage(company_id: uuid.UUID, ctx: Read) -> Any:
    return ops.company_storage(env_of(ctx))


@router.get("/companies/{company_id}/documents/{document_id}", operation_id="documents_get")
def documents_get(company_id: uuid.UUID, document_id: uuid.UUID, ctx: Read) -> Any:
    return ops.detail(env_of(ctx), document_id)


@router.patch("/companies/{company_id}/documents/{document_id}", operation_id="documents_update")
def documents_update(company_id: uuid.UUID, document_id: uuid.UUID, body: DocumentPatch, ctx: Read) -> Any:
    return ops.update_document(
        env_of(ctx),
        document_id,
        display_name=body.display_name,
        field_values=body.field_values,
        row_version=body.row_version,
    )


@router.post("/companies/{company_id}/documents/{document_id}/move", operation_id="documents_move")
def documents_move(company_id: uuid.UUID, document_id: uuid.UUID, body: MoveIn, ctx: Read) -> Any:
    return ops.move_document(
        env_of(ctx), document_id, node_id=body.node_id, category_id=body.category_id, row_version=body.row_version
    )


@router.post("/companies/{company_id}/documents/{document_id}/archive", operation_id="documents_archive")
def documents_archive(company_id: uuid.UUID, document_id: uuid.UUID, ctx: Read) -> Any:
    return ops.set_archived(env_of(ctx), document_id, True)


@router.post("/companies/{company_id}/documents/{document_id}/restore", operation_id="documents_restore")
def documents_restore(company_id: uuid.UUID, document_id: uuid.UUID, ctx: Read) -> Any:
    return ops.set_archived(env_of(ctx), document_id, False)


@router.post("/companies/{company_id}/documents/{document_id}/delete", operation_id="documents_delete")
def documents_delete(company_id: uuid.UUID, document_id: uuid.UUID, body: DeleteIn, ctx: Read) -> Any:
    return ops.soft_delete(env_of(ctx), document_id, body.reason)


@router.post("/companies/{company_id}/documents/{document_id}/undelete", operation_id="documents_undelete")
def documents_undelete(company_id: uuid.UUID, document_id: uuid.UUID, ctx: Read) -> Any:
    return ops.undelete(env_of(ctx), document_id)


@router.post("/companies/{company_id}/documents/{document_id}/purge", operation_id="documents_purge")
def documents_purge(company_id: uuid.UUID, document_id: uuid.UUID, body: PurgeIn, ctx: Read) -> Any:
    return ops.purge(env_of(ctx), document_id, body.confirm)


@router.post("/companies/{company_id}/documents/{document_id}/legal-hold", operation_id="documents_legal_hold")
def documents_legal_hold(company_id: uuid.UUID, document_id: uuid.UUID, body: HoldIn, ctx: Read) -> Any:
    return ops.set_legal_hold(env_of(ctx), document_id, body.hold)


@router.get("/companies/{company_id}/documents/{document_id}/versions", operation_id="documents_versions")
def documents_versions(company_id: uuid.UUID, document_id: uuid.UUID, ctx: Read) -> Any:
    return {"items": ops.versions(env_of(ctx), document_id)}


@router.post(
    "/companies/{company_id}/documents/{document_id}/versions/{number}/restore",
    operation_id="documents_version_restore",
)
def documents_version_restore(
    company_id: uuid.UUID, document_id: uuid.UUID, number: int, body: RestoreVersionIn, ctx: Read
) -> Any:
    return ops.restore_version(env_of(ctx), document_id, number, body.reason)


@router.get("/companies/{company_id}/documents/{document_id}/events", operation_id="documents_events")
def documents_events(
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Read,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Any:
    return ops.events(env_of(ctx), document_id, limit, offset)


@router.get("/companies/{company_id}/documents/{document_id}/content", operation_id="documents_content")
def documents_content(
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    ctx: Read,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    version: int | None = Query(default=None, ge=1),
    inline: bool = False,
) -> Any:
    """Authenticated, audited streaming of the stored file (Range supported). `inline` applies only to PDF and images; every
    other type is always an attachment. The file is never executed or transformed."""
    env = env_of(ctx)
    doc, v = ops.open_content(env, document_id, version, preview=inline)
    total = v["size_bytes"]
    start, end, status = 0, total - 1, 200
    if range_header:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), total - 1) if m.group(2) else total - 1
        else:
            start, end = max(0, total - int(m.group(2))), total - 1
        if start > end or start >= total:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        status = 206
    disp = "inline" if inline and v["extension"] in INLINE_OK else "attachment"
    headers = {
        "Content-Disposition": f"{disp}; filename*=UTF-8''{urllib.parse.quote(doc['display_name'])}",
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
        "X-Document-Version": str(v["version_number"]),
        "X-Content-SHA256": v["sha256"],
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return StreamingResponse(
        ops.stream(v["storage_key"], start, end), status_code=status, media_type=v["mime_type"], headers=headers
    )


# ---------------------------------------------------------------- organization level (Control Center)
@router.get("/orgs/{org_id}/storage", operation_id="documents_org_storage")
def org_storage(org_id: uuid.UUID, ctx: OrgStorage) -> Any:
    return ops.organization_storage(ctx.conn, org_id)


@router.post("/orgs/{org_id}/storage/reconcile", operation_id="documents_org_reconcile")
def org_reconcile(org_id: uuid.UUID, body: ReconcileIn, ctx: OrgPurge) -> Any:
    out = ops.reconcile(ctx.conn, org_id, dry_run=body.dry_run)
    from artify_api import audit

    audit.write_audit(
        ctx.conn,
        action="storage.reconciled",
        actor_id=ctx.user_id,
        org_id=org_id,
        summary={
            "dry_run": body.dry_run,
            "orphans": len(out["orphan_objects"]),
            "missing": len(out["missing_objects"]),
        },
        meta=ctx.meta,
    )
    return out
