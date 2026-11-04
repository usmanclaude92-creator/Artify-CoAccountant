"""Document management API (Phase 6): import batches, upload sessions, documents, versions, activity, search,
storage usage and reconciliation.

Every route requires a `documents.*` permission on the company (organization role + company scope; out-of-scope
companies answer 404). Each operation is then checked against the document's workspace node by the Phase 4 allow/deny
engine, so workspace grants and restricted mode govern documents exactly as they govern folders.

Uploads are a three-step, restart-safe protocol: register (`POST uploads`), stream the bytes (`PUT .../content`),
confirm (`POST .../complete`). A document exists only after `complete` returned success."""

from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from artify_api import documents as D
from artify_api import storage
from artify_api.context import RequestCtx, Requires
from artify_api.errors import Forbidden, Unprocessable
from artify_api.settings import get_settings

router = APIRouter(tags=["documents"])
Read = Annotated[RequestCtx, Depends(Requires("documents.read", company=True))]
Upload = Annotated[RequestCtx, Depends(Requires("documents.upload", company=True))]
Manage = Annotated[RequestCtx, Depends(Requires("documents.manage", company=True))]
Delete = Annotated[RequestCtx, Depends(Requires("documents.delete", company=True))]
Purge = Annotated[RequestCtx, Depends(Requires("documents.purge", company=True))]
StorageAdmin = Annotated[RequestCtx, Depends(Requires("storage.manage", org_source="header"))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
_INLINE = {"application/pdf", "image/png", "image/jpeg", "image/bmp"}


# ---------------------------------------------------------------- import batches and upload sessions
class BatchIn(BaseModel):
    total_files: int = Field(ge=1, le=1000)
    source: Literal["desktop", "web", "mobile", "email", "api"] = "desktop"
    client_batch_key: str | None = Field(default=None, max_length=128)


@router.post("/companies/{company_id}/documents/import-batches", status_code=201, operation_id="documents_batch_create")
def create_batch(company_id: uuid.UUID, body: BatchIn, ctx: Upload) -> Any:
    return D.create_batch(ctx, body.total_files, body.source, body.client_batch_key)


@router.get("/companies/{company_id}/documents/import-batches/{batch_id}", operation_id="documents_batch_status")
def batch_status(company_id: uuid.UUID, batch_id: uuid.UUID, ctx: Upload) -> Any:
    return D.batch_status(ctx, batch_id)


class UploadIn(BaseModel):
    idempotency_key: str = Field(
        min_length=8, max_length=128, description="Client-generated; repeats return the same session"
    )
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, description="Exact size in bytes")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    mime: str | None = Field(default=None, max_length=200)
    batch_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = Field(
        default=None, description="Destination workspace node (required for a new document)"
    )
    category_id: uuid.UUID | None = None
    display_name: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=200)
    filing_rule_id: uuid.UUID | None = Field(
        default=None, description="Rule that suggested the destination (traceability)"
    )
    duplicate_policy: Literal["block", "allow_copy"] = "block"
    target_document_id: uuid.UUID | None = Field(
        default=None, description="Set to upload a NEW VERSION of this document"
    )
    change_reason: str | None = Field(default=None, max_length=500)


@router.post("/companies/{company_id}/documents/uploads", operation_id="documents_upload_init")
def init_upload(company_id: uuid.UUID, body: UploadIn, ctx: Upload) -> Any:
    """Register an upload. Nothing is stored yet; the response says where to send the bytes. Replaying the same
    `idempotency_key` returns the existing session (HTTP 200); a new session answers 201."""
    status, op = D.init_upload(ctx, D.doc_access(ctx), body.model_dump())
    return JSONResponse(jsonable_encoder(op), status_code=status)


@router.get("/companies/{company_id}/documents/uploads", operation_id="documents_upload_list")
def list_uploads(
    company_id: uuid.UUID,
    ctx: Upload,
    status: str | None = None,
    batch_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return D.list_uploads(ctx, status=status, batch_id=batch_id, limit=limit, offset=offset)


@router.get("/companies/{company_id}/documents/uploads/{op_id}", operation_id="documents_upload_get")
def get_upload(company_id: uuid.UUID, op_id: uuid.UUID, ctx: Upload) -> Any:
    return D.get_upload(ctx, op_id)


@router.put("/companies/{company_id}/documents/uploads/{op_id}/content", operation_id="documents_upload_content")
async def upload_content(company_id: uuid.UUID, op_id: uuid.UUID, request: Request, ctx: Upload) -> Any:
    """Stream the file bytes (raw body, `application/octet-stream`). The server hashes and size-checks as it reads,
    validates the real content, scans (when a scanner is configured) and stores the object. The answer carries the
    session; a rejected file answers 4xx with `operation.status` = failed | quarantined and the reason in `code`."""
    acc = await run_in_threadpool(D.doc_access, ctx)
    op, already = await run_in_threadpool(D.begin_content, ctx, acc, op_id)
    if already:
        return op
    declared = int(op["declared_size"])
    h = hashlib.sha256()
    n = 0
    settings = get_settings()
    with tempfile.TemporaryDirectory(dir=settings.storage_tmp_dir) as td:
        tmp = Path(td) / "upload"
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                n += len(chunk)
                if n > declared:  # more bytes than announced: stop reading and let the size check record the failure
                    break
                h.update(chunk)
                f.write(chunk)
        outcome = await run_in_threadpool(D.finish_content, ctx, acc, op_id, tmp, n, h.hexdigest())
    return JSONResponse(jsonable_encoder(outcome.body), status_code=outcome.status)


@router.post("/companies/{company_id}/documents/uploads/{op_id}/complete", operation_id="documents_upload_complete")
def complete_upload(company_id: uuid.UUID, op_id: uuid.UUID, ctx: Upload) -> Any:
    """Verify the stored object and create the document (or the new version). Idempotent: completing twice returns the
    same result. The upload is a success only when this call returns 200."""
    out = D.complete_upload(ctx, D.doc_access(ctx), op_id)
    return JSONResponse(jsonable_encoder(out.body), status_code=out.status)


@router.post("/companies/{company_id}/documents/uploads/{op_id}/cancel", operation_id="documents_upload_cancel")
def cancel_upload(company_id: uuid.UUID, op_id: uuid.UUID, ctx: Upload) -> Any:
    return D.cancel_upload(ctx, op_id)


@router.post("/companies/{company_id}/documents/uploads/{op_id}/retry", operation_id="documents_upload_retry")
def retry_upload(company_id: uuid.UUID, op_id: uuid.UUID, ctx: Upload) -> Any:
    """Re-open a transiently failed upload (storage or scanner unavailable). The file is sent again from the start."""
    return D.retry_upload(ctx, op_id)


# ---------------------------------------------------------------- search, counts, suggestions
@router.get("/companies/{company_id}/documents", operation_id="documents_list")
def list_documents(
    company_id: uuid.UUID,
    ctx: Read,
    node_id: uuid.UUID | None = None,
    include_subfolders: bool = False,
    category_id: uuid.UUID | None = None,
    status: Literal["active", "archived", "deleted"] = "active",
    q: Annotated[str | None, Query(max_length=200)] = None,
    extension: Annotated[str | None, Query(max_length=10)] = None,
    source: str | None = None,
    created_by: uuid.UUID | None = None,
    min_size: Annotated[int | None, Query(ge=0)] = None,
    max_size: Annotated[int | None, Query(ge=0)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sha256: Annotated[str | None, Query(pattern=r"^[0-9a-fA-F]{64}$")] = None,
    import_batch_id: uuid.UUID | None = None,
    field_key: Annotated[str | None, Query(max_length=48)] = None,
    field_value: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["name", "created", "updated", "size", "type"] = "created",
    order: Literal["asc", "desc"] = "desc",
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    """Search/filter/sort/paginate the documents the caller may see. Archived and deleted documents are listed only
    when asked for. Results never include documents in locations the caller cannot view."""
    filters = {k: v for k, v in locals().items() if k not in ("company_id", "ctx")}
    return D.list_documents(ctx, D.doc_access(ctx), filters)


@router.get("/companies/{company_id}/documents/counts", operation_id="documents_counts")
def counts(company_id: uuid.UUID, ctx: Read) -> Any:
    """Document counts per workspace node (own and including subfolders) for tree badges."""
    return D.node_counts(ctx, D.doc_access(ctx))


class DupCheckIn(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@router.post("/companies/{company_id}/documents/duplicates/check", operation_id="documents_duplicate_check")
def duplicate_check(company_id: uuid.UUID, body: DupCheckIn, ctx: Read) -> Any:
    """Pre-check before uploading: identical content already in THIS company (never other companies)."""
    return {"duplicates": D.find_duplicates(ctx, D.doc_access(ctx), body.sha256.lower())}


class SuggestIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    category_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    source: str | None = Field(default=None, max_length=20)
    attributes: dict[str, Any] = Field(default_factory=dict, max_length=50)
    values: dict[str, Any] = Field(default_factory=dict, max_length=100)


@router.post("/companies/{company_id}/documents/suggest", operation_id="documents_suggest")
def suggest(company_id: uuid.UUID, body: SuggestIn, ctx: Upload) -> Any:
    """Advisory filing and naming suggestion from the company's own rules. Nothing is filed or renamed."""
    return D.suggest(ctx, D.doc_access(ctx), body.model_dump())


@router.get("/companies/{company_id}/documents/events", operation_id="documents_activity_company")
def company_events(company_id: uuid.UUID, ctx: Read, limit: Limit = 50, offset: Offset = 0) -> Any:
    return D.list_events(ctx, D.doc_access(ctx), document_id=None, limit=limit, offset=offset)


# ---------------------------------------------------------------- one document
@router.get("/companies/{company_id}/documents/{doc_id}", operation_id="documents_get")
def get_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Read) -> Any:
    acc = D.doc_access(ctx)
    doc = D.get_document(ctx, acc, doc_id, allow_deleted=True)
    return D.document_detail(ctx, acc, doc)


class DocPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: uuid.UUID | None = None
    metadata: dict[str, Any] | None = Field(default=None, max_length=200)
    base_row_version: int | None = Field(default=None, description="Optimistic concurrency token from the last read")


@router.patch("/companies/{company_id}/documents/{doc_id}", operation_id="documents_update")
def update_document(company_id: uuid.UUID, doc_id: uuid.UUID, body: DocPatch, ctx: Manage) -> Any:
    patch = body.model_dump(exclude_unset=True)
    return D.update_document(ctx, D.doc_access(ctx), doc_id, patch)


class MoveIn(BaseModel):
    node_id: uuid.UUID
    category_id: uuid.UUID | None = None
    base_row_version: int | None = None


@router.post("/companies/{company_id}/documents/{doc_id}/move", operation_id="documents_move")
def move_document(company_id: uuid.UUID, doc_id: uuid.UUID, body: MoveIn, ctx: Manage) -> Any:
    """Re-file a document. Only the database changes; the stored bytes are never moved."""
    return D.move_document(
        ctx,
        D.doc_access(ctx),
        doc_id,
        body.node_id,
        body.category_id,
        body.base_row_version,
        "category_id" in body.model_fields_set,
    )


class VersionToken(BaseModel):
    base_row_version: int | None = None


@router.post("/companies/{company_id}/documents/{doc_id}/archive", operation_id="documents_archive")
def archive_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Manage, body: VersionToken | None = None) -> Any:
    return D.set_status(ctx, D.doc_access(ctx), doc_id, "archive", body.base_row_version if body else None)


@router.post("/companies/{company_id}/documents/{doc_id}/restore", operation_id="documents_restore")
def restore_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Manage, body: VersionToken | None = None) -> Any:
    return D.set_status(ctx, D.doc_access(ctx), doc_id, "restore", body.base_row_version if body else None)


@router.post("/companies/{company_id}/documents/{doc_id}/delete", operation_id="documents_delete")
def delete_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Delete, body: VersionToken | None = None) -> Any:
    """Soft delete: the document moves to the deleted items and can be restored. Bytes stay in storage."""
    return D.set_status(ctx, D.doc_access(ctx), doc_id, "delete", body.base_row_version if body else None)


@router.post("/companies/{company_id}/documents/{doc_id}/undelete", operation_id="documents_undelete")
def undelete_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Delete) -> Any:
    return D.set_status(ctx, D.doc_access(ctx), doc_id, "undelete")


class HoldIn(BaseModel):
    hold: bool
    reason: str | None = Field(default=None, max_length=500)


@router.post("/companies/{company_id}/documents/{doc_id}/hold", operation_id="documents_hold")
def hold_document(company_id: uuid.UUID, doc_id: uuid.UUID, body: HoldIn, ctx: Purge) -> Any:
    """Retention / legal hold: a held document cannot be permanently deleted."""
    return D.set_hold(ctx, D.doc_access(ctx), doc_id, body.hold, body.reason)


@router.delete("/companies/{company_id}/documents/{doc_id}", operation_id="documents_purge")
def purge_document(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Purge, confirm: bool = False) -> Any:
    """Permanent deletion (irreversible). Only from the deleted items, never under hold; `confirm=true` is required."""
    if not confirm:
        raise Unprocessable("Permanent deletion must be confirmed with confirm=true.")
    return D.purge_document(ctx, D.doc_access(ctx), doc_id)


class DuplicateIn(BaseModel):
    node_id: uuid.UUID | None = None
    display_name: str | None = Field(default=None, max_length=200)


@router.post(
    "/companies/{company_id}/documents/{doc_id}/duplicate", status_code=201, operation_id="documents_duplicate"
)
def duplicate_document(company_id: uuid.UUID, doc_id: uuid.UUID, body: DuplicateIn, ctx: Upload) -> Any:
    return D.duplicate_document(ctx, D.doc_access(ctx), doc_id, body.node_id, body.display_name)


@router.get("/companies/{company_id}/documents/{doc_id}/events", operation_id="documents_activity")
def document_events(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Read, limit: Limit = 50, offset: Offset = 0) -> Any:
    return D.list_events(ctx, D.doc_access(ctx), document_id=doc_id, limit=limit, offset=offset)


@router.get("/companies/{company_id}/documents/{doc_id}/versions", operation_id="documents_versions")
def versions(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Read) -> Any:
    acc = D.doc_access(ctx)
    return {"items": D.document_detail(ctx, acc, D.get_document(ctx, acc, doc_id))["versions"]}


class RestoreVersionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post(
    "/companies/{company_id}/documents/{doc_id}/versions/{version_no}/restore", operation_id="documents_version_restore"
)
def restore_version(
    company_id: uuid.UUID, doc_id: uuid.UUID, version_no: int, body: RestoreVersionIn, ctx: Upload
) -> Any:
    return D.restore_version(ctx, D.doc_access(ctx), doc_id, version_no, body.reason)


# ---------------------------------------------------------------- content (backend-streamed; no public URLs)
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _disposition(kind: str, filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)[:150] or "document"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/companies/{company_id}/documents/{doc_id}/content", operation_id="documents_content")
def content(
    company_id: uuid.UUID,
    doc_id: uuid.UUID,
    request: Request,
    ctx: Read,
    version: Annotated[int | None, Query(ge=1)] = None,
    disposition: Literal["inline", "attachment"] = "attachment",
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Any:
    """Stream the original bytes. Authorization is checked on every request and the access is recorded. Only formats
    that render without executing anything (PDF, PNG, JPEG, BMP) may be shown inline; everything else downloads."""
    acc = D.doc_access(ctx)
    doc, v = D.version_content_source(ctx, acc, doc_id, version)
    inline = disposition == "inline" and v["mime_type"] in _INLINE
    size = int(v["size_bytes"])
    start, end, status = 0, size - 1, 200
    if range_header:
        m = _RANGE.match(range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return JSONResponse(
                {"title": "Invalid range", "status": 416, "code": "range_not_satisfiable"},
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:
            start = max(size - int(m.group(2)), 0)
        if start >= size or start > end:
            return JSONResponse(
                {"title": "Invalid range", "status": 416, "code": "range_not_satisfiable"},
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        status = 206
    if start == 0:  # one record per access, not per ranged chunk
        D.log_event(
            ctx, doc["id"], "document.viewed" if inline else "document.downloaded", version_no=v["version_no"],
            also_audit=not inline, summary={"name": doc["display_name"], "size": size},
        )  # fmt: skip
    try:
        stream: Iterator[bytes] = storage.get_storage().open_range(v["storage_key"], start, end)
    except storage.StorageError:
        raise D.StorageUnavailable("The stored file could not be read.") from None
    headers = {
        "Content-Disposition": _disposition("inline" if inline else "attachment", doc["display_name"]),
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "X-Document-Sha256": v["sha256"],
        "X-Document-Version": str(v["version_no"]),
        "Cache-Control": "private, no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    media = v["mime_type"] if inline else "application/octet-stream"
    return StreamingResponse(stream, status_code=status, media_type=media, headers=headers)


# ---------------------------------------------------------------- organization storage
class StorageUsage(BaseModel):
    used_bytes: int
    limit_bytes: int | None
    unlimited: bool
    source: str


class MonthlyDocuments(BaseModel):
    used: int
    limit: int | None
    unlimited: bool


class CompanyStorage(BaseModel):
    company_id: uuid.UUID
    name: str
    stored_bytes: int
    active_documents: int
    archived_documents: int
    deleted_documents: int


class UploadFailure(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    original_filename: str
    status: str
    error_code: str | None
    error_message: str | None
    updated_at: datetime


class ScannerState(BaseModel):
    name: str
    active: bool


class StorageOverviewOut(BaseModel):
    storage: StorageUsage
    documents_this_month: MonthlyDocuments
    max_file_size_bytes: int
    accounting: str
    backend: str
    scanner: ScannerState
    companies: list[CompanyStorage]
    uploads_last_7_days: dict[str, int]
    recent_failures: list[UploadFailure]


@router.get("/storage/overview", response_model=StorageOverviewOut, operation_id="storage_overview")
def storage_overview(ctx: StorageAdmin) -> Any:
    """Storage used against the plan, per-company breakdown, scanner/backend status and recent failures."""
    _require_all_companies(ctx)
    return D.storage_overview(ctx)


class ReconcileIn(BaseModel):
    repair: bool = False
    min_age_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)


class ReconcileOut(BaseModel):
    repair: bool
    objects_in_database: int
    missing_objects: int
    size_mismatches: int
    orphan_objects: int
    expired_uploads: int
    missing_sample: list[str]
    size_mismatch_sample: list[str]
    orphan_sample: list[str]


@router.post("/storage/reconcile", response_model=ReconcileOut, operation_id="storage_reconcile")
def storage_reconcile(body: ReconcileIn, ctx: StorageAdmin) -> Any:
    """Compare database and object store. Read-only unless `repair` is true (marks lost objects `missing`, removes
    orphan objects older than `min_age_seconds`, expires abandoned upload sessions)."""
    _require_all_companies(ctx)
    return D.reconcile_storage(ctx, repair=body.repair, min_age_seconds=body.min_age_seconds)


def _require_all_companies(ctx: RequestCtx) -> None:
    assert ctx.principal is not None
    if ctx.principal.company_scope != "all":
        raise Forbidden("Storage administration needs access to every company of the organization.")
