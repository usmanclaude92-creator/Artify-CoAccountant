"""Document intelligence API (Phase 7): processing jobs, OCR results, classification, extraction, bank statements,
suggestions, review and approval. Authorization is the same two stages as documents: a `documents.*` permission on the
company, then the workspace engine on the document's node. Extraction content is as sensitive as the document itself."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response
from pydantic import BaseModel, Field

from artify_api import documents as D
from artify_api import processing as P
from artify_api.context import RequestCtx, Requires

router = APIRouter(tags=["processing"])
Read = Annotated[RequestCtx, Depends(Requires("documents.read", company=True))]
Process = Annotated[RequestCtx, Depends(Requires("documents.process", company=True))]
Review = Annotated[RequestCtx, Depends(Requires("documents.review", company=True))]
Approve = Annotated[RequestCtx, Depends(Requires("documents.approve_extraction", company=True))]
Monitor = Annotated[RequestCtx, Depends(Requires("processing.monitor", org_source="header"))]
Providers = Annotated[RequestCtx, Depends(Requires("ai_providers.manage", org_source="header"))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
C = "/companies/{company_id}"


# ---------------------------------------------------------------- jobs
class ProcessIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    kind: Literal["full", "ocr", "classify", "extract"] = "full"
    languages: list[str] = Field(default_factory=list, max_length=4)
    reason: str | None = Field(default=None, max_length=500)


@router.post(C + "/documents/{doc_id}/process", status_code=202, operation_id="processing_start")
def start(
    company_id: uuid.UUID, doc_id: uuid.UUID, body: ProcessIn, ctx: Process, bg: BackgroundTasks, response: Response
) -> Any:
    """Queue OCR + classification + extraction for the document's CURRENT version. Repeating the request (same key, or a
    job already running for this version) returns the existing job (HTTP 200) instead of creating a duplicate."""
    created, job = P.create_job(
        ctx,
        D.doc_access(ctx),
        doc_id,
        kind=body.kind,
        languages=body.languages,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    if created:
        assert ctx.org_id is not None
        bg.add_task(P.dispatch, ctx.org_id, company_id, job["id"])  # runs after the request transaction has committed
    else:
        response.status_code = 200
    return job


@router.get(C + "/processing/jobs", operation_id="processing_jobs")
def jobs(
    company_id: uuid.UUID,
    ctx: Read,
    document_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return P.list_jobs(ctx, D.doc_access(ctx), document_id=document_id, status=status, limit=limit, offset=offset)


@router.get(C + "/processing/jobs/{job_id}", operation_id="processing_job")
def job(company_id: uuid.UUID, job_id: uuid.UUID, ctx: Read) -> Any:
    P._job_doc_access(ctx, D.doc_access(ctx), job_id)
    return P.get_job(ctx, job_id)


@router.get(C + "/processing/jobs/{job_id}/history", operation_id="processing_job_history")
def history(company_id: uuid.UUID, job_id: uuid.UUID, ctx: Read) -> Any:
    return P.job_history(ctx, D.doc_access(ctx), job_id)


@router.post(C + "/processing/jobs/{job_id}/cancel", operation_id="processing_cancel")
def cancel(company_id: uuid.UUID, job_id: uuid.UUID, ctx: Process) -> Any:
    return P.cancel_job(ctx, D.doc_access(ctx), job_id)


@router.post(C + "/processing/jobs/{job_id}/retry", operation_id="processing_retry")
def retry(company_id: uuid.UUID, job_id: uuid.UUID, ctx: Process, bg: BackgroundTasks) -> Any:
    out = P.retry_job(ctx, D.doc_access(ctx), job_id)
    assert ctx.org_id is not None
    bg.add_task(P.dispatch, ctx.org_id, company_id, str(job_id))
    return out


@router.get(C + "/processing/review-queue", operation_id="processing_review_queue")
def review_queue(company_id: uuid.UUID, ctx: Review, limit: Limit = 50, offset: Offset = 0) -> Any:
    return P.review_queue(ctx, D.doc_access(ctx), limit=limit, offset=offset)


# ---------------------------------------------------------------- results
@router.get(C + "/documents/{doc_id}/intelligence", operation_id="processing_latest")
def latest(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Read) -> Any:
    """Latest job, classification and extraction of a document in one call (what the review screen opens with)."""
    return P.latest_view(ctx, D.doc_access(ctx), doc_id)


@router.get(C + "/documents/{doc_id}/ocr", operation_id="processing_ocr")
def ocr(
    company_id: uuid.UUID,
    doc_id: uuid.UUID,
    ctx: Read,
    page_from: Annotated[int, Query(ge=1)] = 1,
    page_to: Annotated[int, Query(ge=1)] = 5,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Any:
    """Recognised text with line boxes and confidences, a few pages at a time."""
    return P.ocr_view(
        ctx, D.doc_access(ctx), doc_id, page_from=page_from, page_to=min(page_to, page_from + 19), version=version
    )


@router.get(
    C + "/documents/{doc_id}/pages/{page_no}/image", operation_id="processing_page_image", response_class=Response
)
def page_image(company_id: uuid.UUID, doc_id: uuid.UUID, page_no: Annotated[int, Path(ge=1)], ctx: Read) -> Response:
    """The page as it was read (rotation/deskew applied): the surface source boxes refer to. Streamed, never a public URL."""
    data = P.page_image(ctx, D.doc_access(ctx), doc_id, page_no)
    return Response(
        data,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(C + "/documents/{doc_id}/extractions", operation_id="processing_extractions")
def extractions(company_id: uuid.UUID, doc_id: uuid.UUID, ctx: Read) -> Any:
    """Every extraction version of the document (earlier ones are kept when it is reprocessed)."""
    return {"items": P.list_extractions(ctx, D.doc_access(ctx), doc_id)}


@router.get(C + "/extractions/{extraction_id}", operation_id="processing_extraction")
def extraction(company_id: uuid.UUID, extraction_id: uuid.UUID, ctx: Read) -> Any:
    return P.extraction_view(ctx, D.doc_access(ctx), extraction_id)


# ---------------------------------------------------------------- review
class FieldReview(BaseModel):
    action: Literal["correct", "confirm", "reject", "missing", "unreadable"]
    value: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=500)


@router.patch(C + "/extractions/{extraction_id}/fields/{field_key}", operation_id="processing_review_field")
def review_field(
    company_id: uuid.UUID, extraction_id: uuid.UUID, field_key: str, body: FieldReview, ctx: Review
) -> Any:
    return P.review_field(
        ctx, D.doc_access(ctx), extraction_id, field_key, action=body.action, value=body.value, reason=body.reason
    )


class ItemReview(BaseModel):
    action: Literal["correct", "confirm", "reject", "missing", "unreadable"]
    values: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.patch(C + "/extractions/{extraction_id}/line-items/{item_id}", operation_id="processing_review_item")
def review_item(
    company_id: uuid.UUID, extraction_id: uuid.UUID, item_id: uuid.UUID, body: ItemReview, ctx: Review
) -> Any:
    return P.review_item(
        ctx, D.doc_access(ctx), extraction_id, item_id, action=body.action, values=body.values, reason=body.reason
    )


class TxReview(BaseModel):
    action: Literal["correct", "confirm", "reject", "unreadable"]
    patch: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.patch(C + "/extractions/{extraction_id}/transactions/{tx_id}", operation_id="processing_review_transaction")
def review_tx(company_id: uuid.UUID, extraction_id: uuid.UUID, tx_id: uuid.UUID, body: TxReview, ctx: Review) -> Any:
    return P.review_transaction(
        ctx, D.doc_access(ctx), extraction_id, tx_id, action=body.action, patch=body.patch, reason=body.reason
    )


class TxAdd(BaseModel):
    tx_date: str | None = None
    description: str = Field(default="", max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    debit: str | None = None
    credit: str | None = None
    balance: str | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.post(
    C + "/extractions/{extraction_id}/transactions", status_code=201, operation_id="processing_add_transaction"
)
def add_tx(company_id: uuid.UUID, extraction_id: uuid.UUID, body: TxAdd, ctx: Review) -> Any:
    """Add a transaction the reader missed. It is marked as manually added and counts in the balance checks."""
    return P.add_transaction(
        ctx,
        D.doc_access(ctx),
        extraction_id,
        after_row=None,
        data=body.model_dump(exclude={"reason"}),
        reason=body.reason,
    )


class StatementPatch(BaseModel):
    opening_balance: str | None = None
    closing_balance: str | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.patch(C + "/extractions/{extraction_id}/statement", operation_id="processing_review_statement")
def review_statement(company_id: uuid.UUID, extraction_id: uuid.UUID, body: StatementPatch, ctx: Review) -> Any:
    return P.review_statement_header(
        ctx, D.doc_access(ctx), extraction_id, body.model_dump(exclude_none=True, exclude={"reason"}), body.reason
    )


class ClsReview(BaseModel):
    action: Literal["confirm", "correct", "reject"]
    type_key: str | None = Field(default=None, max_length=48)
    reason: str | None = Field(default=None, max_length=500)


@router.post(C + "/documents/{doc_id}/classification", operation_id="processing_review_classification")
def review_cls(company_id: uuid.UUID, doc_id: uuid.UUID, body: ClsReview, ctx: Review) -> Any:
    return P.review_classification(
        ctx, D.doc_access(ctx), doc_id, type_key=body.type_key, action=body.action, reason=body.reason
    )


class ApproveIn(BaseModel):
    acknowledge_validation_errors: bool = False
    note: str | None = Field(default=None, max_length=1000)


@router.post(C + "/extractions/{extraction_id}/approve", operation_id="processing_approve")
def approve(company_id: uuid.UUID, extraction_id: uuid.UUID, body: ApproveIn, ctx: Approve) -> Any:
    """Approval is a human act: it needs the approval permission, `edit` on the document's folder, and every flagged item
    resolved. Nothing here creates accounting entries."""
    return P.approve_extraction(
        ctx, D.doc_access(ctx), extraction_id, acknowledge=body.acknowledge_validation_errors, note=body.note
    )


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post(C + "/extractions/{extraction_id}/reject", operation_id="processing_reject")
def reject(company_id: uuid.UUID, extraction_id: uuid.UUID, body: RejectIn, ctx: Review) -> Any:
    return P.reject_extraction(ctx, D.doc_access(ctx), extraction_id, body.reason)


class AcceptWorkspace(BaseModel):
    move: bool = True


@router.post(
    C + "/extractions/{extraction_id}/workspace-suggestions/{suggestion_id}/accept",
    operation_id="processing_accept_workspace",
)
def accept_ws(
    company_id: uuid.UUID, extraction_id: uuid.UUID, suggestion_id: uuid.UUID, body: AcceptWorkspace, ctx: Review
) -> Any:
    """File the document where the suggestion says — through the normal move operation, so every Phase 6 rule applies."""
    return P.accept_workspace(ctx, D.doc_access(ctx), extraction_id, suggestion_id, body.move)


class AcceptMeta(BaseModel):
    value: Any | None = None


@router.post(
    C + "/extractions/{extraction_id}/metadata-suggestions/{suggestion_id}/accept",
    operation_id="processing_accept_metadata",
)
def accept_meta(
    company_id: uuid.UUID, extraction_id: uuid.UUID, suggestion_id: uuid.UUID, body: AcceptMeta, ctx: Review
) -> Any:
    return P.accept_metadata(ctx, D.doc_access(ctx), extraction_id, suggestion_id, body.value)


class SplitDecision(BaseModel):
    approve: bool


@router.post(C + "/extractions/{extraction_id}/split", operation_id="processing_split_decision")
def split(company_id: uuid.UUID, extraction_id: uuid.UUID, body: SplitDecision, ctx: Review) -> Any:
    return P.decide_split(ctx, D.doc_access(ctx), extraction_id, body.approve)


# ---------------------------------------------------------------- organization: monitoring and providers
class ProcessingUsageRow(BaseModel):
    key: str
    label: str
    used: int
    limit: int | None
    unlimited: bool


class ProcessingProviderState(BaseModel):
    kind: str
    key: str
    external: bool
    configured: bool
    active_default: bool
    version: str | None
    languages: list[str]
    note: str | None


class ProcessingQueueRow(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    display_name: str
    company_name: str
    company_id: uuid.UUID


class ProcessingFailedRow(ProcessingQueueRow):
    error_code: str | None
    error_message: str | None
    attempt_count: int


class ProcessingCompanyRow(BaseModel):
    company_id: uuid.UUID
    name: str
    awaiting_review: int
    failed: int
    jobs_30d: int


class ProcessingOverviewOut(BaseModel):
    jobs_by_status: dict[str, int]
    awaiting_review: list[ProcessingQueueRow]
    failed: list[ProcessingFailedRow]
    companies: list[ProcessingCompanyRow]
    usage: list[ProcessingUsageRow]
    providers: list[ProcessingProviderState]
    window_days: int


@router.get("/processing/overview", response_model=ProcessingOverviewOut, operation_id="processing_overview")
def overview(ctx: Monitor) -> Any:
    """Counts and lists from real job records. Shows names and statuses only, never document content."""
    assert ctx.principal is not None
    if ctx.principal.company_scope != "all":
        from artify_api.errors import Forbidden

        raise Forbidden("Processing monitoring needs access to every company of the organization.")
    return P.overview(ctx)


class ProviderConfigIn(BaseModel):
    kind: Literal["ocr", "understanding"]
    provider_key: str = Field(max_length=48)
    enabled: bool = True
    allow_external_content: bool = False
    company_id: uuid.UUID | None = None


@router.get("/processing/providers", operation_id="processing_providers")
def providers(ctx: Monitor) -> Any:
    """Provider state and the organization's choices. Secrets are never returned: only whether a provider is configured."""
    return {"providers": P.provider_status(), "configuration": P.provider_configs(ctx)}


@router.put("/processing/providers", operation_id="processing_set_provider")
def set_provider(body: ProviderConfigIn, ctx: Providers) -> Any:
    return P.set_provider_config(
        ctx,
        company_id=body.company_id,
        kind=body.kind,
        provider_key=body.provider_key,
        enabled=body.enabled,
        allow_external=body.allow_external_content,
    )
