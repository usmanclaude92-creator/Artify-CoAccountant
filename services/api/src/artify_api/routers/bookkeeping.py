"""Daily bookkeeping API (Phase 10): workbench, tasks, checklists, validation, closing, snapshots, daily reports, readiness and the
organization overview.

Every route declares its permission (company scope: an out-of-scope company answers 404). The server decides every step from the
signed-in user's roles and the company's own closing policy; nothing the client sends (company, actor, status) is trusted.
Closing, approving, reopening and overriding are human decisions and are refused to any non-user actor."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from pydantic import BaseModel, Field

from artify_api import bookkeeping as B
from artify_api import bookkeeping_closing as K
from artify_api import documents as docs
from artify_api import excel as X
from artify_api.accounting import one, ser
from artify_api.context import RequestCtx, Requires

router = APIRouter(tags=["bookkeeping"])
C = "/companies/{company_id}/bookkeeping"


def _dep(perm: str) -> Any:
    return Depends(Requires(perm, company=True))


Read = Annotated[RequestCtx, _dep("bookkeeping.read")]
Work = Annotated[RequestCtx, _dep("bookkeeping.work")]
Tasks = Annotated[RequestCtx, _dep("bookkeeping.tasks.manage")]
Lists = Annotated[RequestCtx, _dep("bookkeeping.checklists.manage")]
PolicyManage = Annotated[RequestCtx, _dep("bookkeeping.policy.manage")]
Prepare = Annotated[RequestCtx, _dep("bookkeeping.close.prepare")]
Review = Annotated[RequestCtx, _dep("bookkeeping.close.review")]
Approve = Annotated[RequestCtx, _dep("bookkeeping.close.approve")]
Reopen = Annotated[RequestCtx, _dep("bookkeeping.close.reopen")]
Override = Annotated[RequestCtx, _dep("bookkeeping.close.override")]
Export = Annotated[RequestCtx, Depends(Requires("excel.export", company=True))]
Monitor = Annotated[RequestCtx, Depends(Requires("bookkeeping.monitor", org_source="header"))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _vis(ctx: RequestCtx) -> list[str]:
    """Folders the caller can see, as strings. Used only to limit document ids and the queue, never the closing's totals."""
    return [str(n) for n in docs.visible_node_ids(docs.doc_access(ctx))]


# ------------------------------------------------------------------ policy
class PolicyIn(BaseModel):
    require_independent_approval: bool | None = None
    require_independent_task_review: bool | None = None
    action_roles: dict[str, list[str]] | None = None
    severity_overrides: dict[str, str] | None = None
    override_min_chars: int | None = Field(default=None, ge=5, le=500)
    timezone: str | None = Field(default=None, max_length=64)  # IANA name; empty text = use the organization's zone
    base_row_version: int | None = None


@router.get(C + "/policy", operation_id="bookkeeping_policy")
def policy(company_id: uuid.UUID, ctx: Read) -> Any:
    return B.policy_view(ctx)


@router.put(C + "/policy", operation_id="bookkeeping_policy_update")
def policy_update(company_id: uuid.UUID, body: PolicyIn, ctx: PolicyManage) -> Any:
    return B.update_policy(ctx, body.model_dump(exclude_none=True))


# ------------------------------------------------------------------ checklists
class ItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    mandatory: bool = False
    assigned_role: str | None = Field(default=None, max_length=40)
    auto_rule: str | None = Field(default=None, max_length=40)
    position: int | None = Field(default=None, ge=0)


class ItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    mandatory: bool | None = None
    assigned_role: str | None = Field(default=None, max_length=40)
    auto_rule: str | None = Field(default=None, max_length=40)
    position: int | None = Field(default=None, ge=0)
    archived: bool | None = None


class ChecklistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    items: list[ItemIn] = Field(default_factory=list, max_length=100)


class ChecklistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    position: int | None = Field(default=None, ge=0)
    archived: bool | None = None
    base_row_version: int | None = None


class OrderIn(BaseModel):
    item_ids: list[uuid.UUID]


class TickIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get(C + "/checklist-templates", operation_id="bookkeeping_checklist_templates")
def checklist_templates(company_id: uuid.UUID, ctx: Read) -> Any:
    return B.list_templates()


@router.get(C + "/checklists", operation_id="bookkeeping_checklists")
def checklists(company_id: uuid.UUID, ctx: Read, include_archived: bool = False) -> Any:
    return ser(B.list_checklists(ctx, include_archived))


@router.post(C + "/checklists", status_code=201, operation_id="bookkeeping_checklist_create")
def checklist_create(company_id: uuid.UUID, body: ChecklistIn, ctx: Lists) -> Any:
    return ser(B.create_checklist(ctx, body.model_dump()))


@router.post(C + "/checklists/from-template/{key}", status_code=201, operation_id="bookkeeping_checklist_from_template")
def checklist_from_template(company_id: uuid.UUID, key: str, ctx: Lists) -> Any:
    """COPY a default template into this company. The copy is the company's own; it never follows the template."""
    return ser(B.create_from_template(ctx, key))


@router.patch(C + "/checklists/{checklist_id}", operation_id="bookkeeping_checklist_update")
def checklist_update(company_id: uuid.UUID, checklist_id: uuid.UUID, body: ChecklistPatch, ctx: Lists) -> Any:
    return ser(B.update_checklist(ctx, checklist_id, body.model_dump(exclude_unset=True)))


@router.post(C + "/checklists/{checklist_id}/items", status_code=201, operation_id="bookkeeping_checklist_item_add")
def item_add(company_id: uuid.UUID, checklist_id: uuid.UUID, body: ItemIn, ctx: Lists) -> Any:
    return ser(B.add_item(ctx, checklist_id, body.model_dump()))


@router.put(C + "/checklists/{checklist_id}/order", operation_id="bookkeeping_checklist_order")
def item_order(company_id: uuid.UUID, checklist_id: uuid.UUID, body: OrderIn, ctx: Lists) -> Any:
    return ser(B.reorder_items(ctx, checklist_id, body.item_ids))


@router.patch(C + "/checklist-items/{item_id}", operation_id="bookkeeping_checklist_item_update")
def item_update(company_id: uuid.UUID, item_id: uuid.UUID, body: ItemPatch, ctx: Lists) -> Any:
    return ser(B.update_item(ctx, item_id, body.model_dump(exclude_unset=True)))


@router.post(C + "/checklist-items/{item_id}/complete", operation_id="bookkeeping_checklist_item_complete")
def item_complete(company_id: uuid.UUID, item_id: uuid.UUID, business_date: date, body: TickIn, ctx: Work) -> Any:
    return B.complete_item(ctx, item_id, business_date, body.note)


@router.delete(C + "/checklist-items/{item_id}/complete", operation_id="bookkeeping_checklist_item_uncomplete")
def item_uncomplete(company_id: uuid.UUID, item_id: uuid.UUID, business_date: date, ctx: Work) -> Any:
    return B.uncomplete_item(ctx, item_id, business_date)


# ------------------------------------------------------------------ tasks
TaskStatus = Literal[
    "new",
    "assigned",
    "in_progress",
    "awaiting_information",
    "awaiting_review",
    "approved",
    "rejected",
    "completed",
    "cancelled",
]
Priority = Literal["low", "normal", "high", "urgent"]


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority = "normal"
    category: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,59}$")
    business_date: date | None = None
    due_date: date | None = None
    requires_review: bool = False
    blocks_closing: bool = False
    assignee_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    subject_type: Literal["document", "transaction", "exception", "closing"] | None = None
    subject_id: uuid.UUID | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    category: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,59}$")
    reviewer_id: uuid.UUID | None = None
    business_date: date | None = None
    due_date: date | None = None
    requires_review: bool | None = None
    blocks_closing: bool | None = None
    base_row_version: int | None = None


class AssignIn(BaseModel):
    assignee_id: uuid.UUID | None = None


class TransitionIn(BaseModel):
    status: TaskStatus
    reason: str | None = Field(default=None, max_length=500)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.get(C + "/tasks", operation_id="bookkeeping_tasks")
def tasks(
    company_id: uuid.UUID,
    ctx: Read,
    status: TaskStatus | None = None,
    mine: bool = False,
    business_date: date | None = None,
    open_only: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return ser(
        B.list_tasks(
            ctx, status=status, mine=mine, business_date=business_date, open_only=open_only, limit=limit, offset=offset
        )
    )


@router.post(C + "/tasks", status_code=201, operation_id="bookkeeping_task_create")
def task_create(company_id: uuid.UUID, body: TaskIn, ctx: Tasks) -> Any:
    return ser(B.create_task(ctx, body.model_dump()))


@router.get(C + "/tasks/{task_id}", operation_id="bookkeeping_task")
def task(company_id: uuid.UUID, task_id: uuid.UUID, ctx: Read) -> Any:
    return ser(B.get_task(ctx, task_id, detail=True))


@router.patch(C + "/tasks/{task_id}", operation_id="bookkeeping_task_update")
def task_update(company_id: uuid.UUID, task_id: uuid.UUID, body: TaskPatch, ctx: Tasks) -> Any:
    return ser(B.update_task(ctx, task_id, body.model_dump(exclude_unset=True)))


@router.post(C + "/tasks/{task_id}/assign", operation_id="bookkeeping_task_assign")
def task_assign(company_id: uuid.UUID, task_id: uuid.UUID, body: AssignIn, ctx: Tasks) -> Any:
    return ser(B.assign_task(ctx, task_id, body.assignee_id))


@router.post(C + "/tasks/{task_id}/transition", operation_id="bookkeeping_task_transition")
def task_transition(company_id: uuid.UUID, task_id: uuid.UUID, body: TransitionIn, ctx: Work) -> Any:
    """Move a task along its status graph. Who may do which move (assignee, manager, an independent reviewer) is decided on the server."""
    return ser(B.transition_task(ctx, task_id, body.status, reason=body.reason))


@router.post(C + "/tasks/{task_id}/comments", status_code=201, operation_id="bookkeeping_task_comment")
def task_comment(company_id: uuid.UUID, task_id: uuid.UUID, body: CommentIn, ctx: Work) -> Any:
    return ser(B.add_comment(ctx, task_id, body.body))


# ------------------------------------------------------------------ the day
@router.get(C + "/days/{business_date}", operation_id="bookkeeping_day")
def day(company_id: uuid.UUID, business_date: date, ctx: Read) -> Any:
    """The workbench overview for one business day (a calendar day in the company's own time zone). Real data only."""
    return K.day_overview(ctx, business_date)


@router.get(C + "/days/{business_date}/validation", operation_id="bookkeeping_day_validation")
def day_validation(company_id: uuid.UUID, business_date: date, ctx: Read) -> Any:
    """What a validation would find right now. Nothing is stored."""
    return K.preview_validation(ctx, business_date)


@router.get(C + "/days/{business_date}/documents", operation_id="bookkeeping_day_documents")
def day_documents(company_id: uuid.UUID, business_date: date, ctx: Read, limit: Limit = 50, offset: Offset = 0) -> Any:
    vis = _vis(ctx)
    return K.document_queue(ctx, business_date, vis, limit, offset) if vis else []


@router.get(C + "/days/{business_date}/transactions", operation_id="bookkeeping_day_transactions")
def day_transactions(
    company_id: uuid.UUID, business_date: date, ctx: Read, limit: Limit = 100, offset: Offset = 0
) -> Any:
    return K.transaction_list(ctx, business_date, limit, offset)


@router.get(C + "/days/{business_date}/exceptions", operation_id="bookkeeping_day_exceptions")
def day_exceptions(
    company_id: uuid.UUID,
    business_date: date,
    ctx: Read,
    status: Literal["open", "resolved", "overridden", "acknowledged"] | None = None,
) -> Any:
    return ser(B.list_exceptions(ctx, business_date, status=status))


@router.get(C + "/days/{business_date}/report", operation_id="bookkeeping_day_report")
def day_report(company_id: uuid.UUID, business_date: date, ctx: Read) -> Any:
    return K.daily_report(ctx, business_date)


@router.get(C + "/days/{business_date}/cash-bank", operation_id="bookkeeping_day_cash_bank")
def day_cash_bank(company_id: uuid.UUID, business_date: date, ctx: Read) -> Any:
    return K.cash_bank(ctx, business_date)


@router.get(C + "/days/{business_date}/snapshots", operation_id="bookkeeping_day_snapshots")
def day_snapshots(company_id: uuid.UUID, business_date: date, ctx: Read) -> Any:
    """Every closing snapshot the day ever had, oldest first. A reopened day keeps its original."""
    return K.snapshot_history(ctx, business_date)


class SyncIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    business_date: date
    pending_uploads: int = Field(default=0, ge=0, le=1_000_000)
    failed_uploads: int = Field(default=0, ge=0, le=1_000_000)
    pending_changes: int = Field(default=0, ge=0, le=1_000_000)
    conflicts: int = Field(default=0, ge=0, le=1_000_000)
    last_sync_at: datetime | None = None


@router.post(C + "/sync-reports", operation_id="bookkeeping_sync_report")
def sync_report(company_id: uuid.UUID, body: SyncIn, ctx: Work) -> Any:
    """A device DECLARES its local synchronisation state for a day. It is shown as declared, never as proof."""
    return B.report_sync(ctx, body.model_dump())


# ------------------------------------------------------------------ exceptions
class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post(C + "/exceptions/{exception_id}/acknowledge", operation_id="bookkeeping_exception_acknowledge")
def exception_ack(company_id: uuid.UUID, exception_id: uuid.UUID, body: DecisionIn, ctx: Review) -> Any:
    out = B.decide_exception(ctx, exception_id, "acknowledge", body.note)
    _refresh_for(ctx, exception_id)
    return ser(out)


@router.post(C + "/exceptions/{exception_id}/override", operation_id="bookkeeping_exception_override")
def exception_override(company_id: uuid.UUID, exception_id: uuid.UUID, body: DecisionIn, ctx: Override) -> Any:
    out = B.decide_exception(ctx, exception_id, "override", body.note)
    _refresh_for(ctx, exception_id)
    return ser(out)


def _refresh_for(ctx: RequestCtx, exception_id: uuid.UUID) -> None:
    r = one(
        ctx,
        "SELECT s.id FROM bk_exceptions e JOIN bk_closing_sessions s ON s.company_id = e.company_id AND s.business_date = e.business_date"
        " AND s.status IN ('draft', 'in_progress', 'ready_for_review') WHERE e.id = :i",
        i=exception_id,
    )
    if r:
        K.refresh_result(ctx, uuid.UUID(r["id"]))


# ------------------------------------------------------------------ closings
class ClosingIn(BaseModel):
    business_date: date


class StageIn(BaseModel):
    stage: int = Field(ge=1, le=6)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class ClosingExportIn(BaseModel):
    template_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=128)
    include_unapproved: bool = False
    acknowledge_repeat: bool = False
    reason: str | None = Field(default=None, max_length=500)


class RegenerateIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.get(C + "/closings", operation_id="bookkeeping_closings")
def closings(
    company_id: uuid.UUID,
    ctx: Read,
    date_from: date | None = None,
    date_to: date | None = None,
    status: Literal["draft", "in_progress", "ready_for_review", "closed", "reopened", "cancelled"] | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    return K.list_sessions(ctx, date_from=date_from, date_to=date_to, status=status, limit=limit, offset=offset)


@router.post(C + "/closings", status_code=201, operation_id="bookkeeping_closing_create")
def closing_create(company_id: uuid.UUID, body: ClosingIn, ctx: Prepare) -> Any:
    return K.create_session(ctx, body.business_date)


@router.get(C + "/closings/{closing_id}", operation_id="bookkeeping_closing")
def closing(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Read) -> Any:
    return K.session_view(ctx, closing_id)


@router.post(C + "/closings/{closing_id}/validate", operation_id="bookkeeping_closing_validate")
def closing_validate(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Prepare) -> Any:
    return K.validate(ctx, closing_id)


@router.post(C + "/closings/{closing_id}/stage", operation_id="bookkeeping_closing_stage")
def closing_stage(company_id: uuid.UUID, closing_id: uuid.UUID, body: StageIn, ctx: Prepare) -> Any:
    return K.set_stage(ctx, closing_id, body.stage)


@router.get(C + "/closings/{closing_id}/summary", operation_id="bookkeeping_closing_summary")
def closing_summary(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Read) -> Any:
    return K.summary(ctx, closing_id)


@router.post(C + "/closings/{closing_id}/submit", operation_id="bookkeeping_closing_submit")
def closing_submit(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Prepare) -> Any:
    return K.submit(ctx, closing_id)


@router.post(C + "/closings/{closing_id}/send-back", operation_id="bookkeeping_closing_send_back")
def closing_send_back(company_id: uuid.UUID, closing_id: uuid.UUID, body: NoteIn, ctx: Review) -> Any:
    return K.send_back(ctx, closing_id, body.note)


@router.post(C + "/closings/{closing_id}/close", operation_id="bookkeeping_closing_close")
def closing_close(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Approve) -> Any:
    """Approve and close. The day is validated again at this moment and an immutable snapshot is stored."""
    return K.close(ctx, closing_id)


@router.post(C + "/closings/{closing_id}/reopen", operation_id="bookkeeping_closing_reopen")
def closing_reopen(company_id: uuid.UUID, closing_id: uuid.UUID, body: ReasonIn, ctx: Reopen) -> Any:
    """Keeps the original closing and snapshot and returns a NEW, linked closing for the same day."""
    return K.reopen(ctx, closing_id, body.reason)


@router.post(C + "/closings/{closing_id}/cancel", operation_id="bookkeeping_closing_cancel")
def closing_cancel(company_id: uuid.UUID, closing_id: uuid.UUID, body: ReasonIn, ctx: Prepare) -> Any:
    return K.cancel(ctx, closing_id, body.reason)


@router.get(C + "/closings/{closing_id}/snapshot", operation_id="bookkeeping_closing_snapshot")
def closing_snapshot(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Read) -> Any:
    """The stored snapshot with a live re-check of its hash. Snapshots can only be read, never changed."""
    return K.get_snapshot(ctx, closing_id)


@router.get(C + "/closings/{closing_id}/exports", operation_id="bookkeeping_closing_exports")
def closing_exports(company_id: uuid.UUID, closing_id: uuid.UUID, ctx: Read) -> Any:
    K.load_session(ctx, closing_id)
    return ser(K.list_closing_exports(ctx, closing_id))


@router.post(C + "/closings/{closing_id}/exports", status_code=202, operation_id="bookkeeping_closing_export_create")
def closing_export_create(
    company_id: uuid.UUID,
    closing_id: uuid.UUID,
    body: ClosingExportIn,
    ctx: Export,
    bg: BackgroundTasks,
    response: Response,
) -> Any:
    """A daily report for the day through the Excel engine (approved data unless explicitly allowed otherwise)."""
    created, exp = K.create_closing_export(ctx, closing_id, body.model_dump())
    if created:
        assert ctx.org_id is not None
        bg.add_task(X.dispatch, ctx.org_id, company_id, exp["id"])
    else:
        response.status_code = 200
    return exp


@router.post(
    C + "/closings/{closing_id}/exports/{export_id}/regenerate",
    status_code=202,
    operation_id="bookkeeping_closing_export_regenerate",
)
def closing_export_regenerate(
    company_id: uuid.UUID,
    closing_id: uuid.UUID,
    export_id: uuid.UUID,
    body: RegenerateIn,
    ctx: Export,
    bg: BackgroundTasks,
    response: Response,
) -> Any:
    payload = {"reason": body.reason, "idempotency_key": body.idempotency_key}
    created, exp = K.regenerate_closing_export(ctx, closing_id, export_id, payload)
    if created:
        assert ctx.org_id is not None
        bg.add_task(X.dispatch, ctx.org_id, company_id, exp["id"])
    else:
        response.status_code = 200
    return exp


# ------------------------------------------------------------------ period readiness
@router.get(C + "/readiness", operation_id="bookkeeping_readiness")
def readiness(
    company_id: uuid.UUID,
    ctx: Read,
    period_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Any:
    """Whether every day that needs a closing has one. An overview only: it locks no period and prepares no VAT return."""
    return K.readiness(ctx, period_id=period_id, date_from=date_from, date_to=date_to)


# ------------------------------------------------------------------ Firm Control Center (organization level, read-only)
class OverviewCompany(BaseModel):
    company_id: str
    company_name: str
    timezone: str
    day: date
    closing_status: str | None
    open_exceptions: int
    open_tasks: int
    overdue_tasks: int
    unclosed_recent_days: int


class OverviewSummary(BaseModel):
    companies: int
    closed: int
    ready_for_review: int
    not_started: int
    open_exceptions: int
    overdue_tasks: int


class BookkeepingOverviewOut(BaseModel):
    companies: list[OverviewCompany]
    summary: OverviewSummary


@router.get("/bookkeeping/overview", operation_id="bookkeeping_overview", response_model=BookkeepingOverviewOut)
def overview(ctx: Monitor, business_date: date | None = None) -> Any:
    """Closing status of every visible company for one day (default: each company's own today). Status and counts only:
    no transactions, documents or amounts."""
    return K.org_overview(ctx, business_date)
