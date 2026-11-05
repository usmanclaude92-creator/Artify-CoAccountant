"""Synchronisation foundation API (Phase 12): a permission-aware change feed and a preflight check. Read-only; the writes a
client queues still go through the normal endpoints with their own concurrency and idempotency checks."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from artify_api import sync as S
from artify_api import sync_conflicts as SC
from artify_api.context import RequestCtx, Requires

router = APIRouter(tags=["sync"])
C = "/companies/{company_id}/sync"
Read = Annotated[RequestCtx, Depends(Requires("workspace.read", company=True))]


class ItemIn(BaseModel):
    kind: Literal["workspace", "document", "transaction", "closing", "export"]
    id: str = Field(min_length=1, max_length=64)
    base_revision: int | None = Field(default=None, ge=0)


class CheckIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=200)


@router.get(C + "/changes", operation_id="sync_changes")
def changes(
    company_id: uuid.UUID,
    ctx: Read,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    kind: Annotated[list[str] | None, Query()] = None,
    overlap_seconds: Annotated[int, Query(ge=0, le=600)] = 0,
) -> Any:
    """What changed in this company after `cursor` (oldest first), limited to what the caller may see. Time comes from the
    database, never from a device. Re-reading with `overlap_seconds` is safe: items are idempotent by (kind, id, revision)."""
    return S.changes(ctx, cursor=cursor, limit=limit, kinds=kind, overlap_seconds=overlap_seconds)


@router.post(C + "/check", operation_id="sync_check")
def check(company_id: uuid.UUID, body: CheckIn, ctx: Read) -> Any:
    """For items a client holds with the revision it last saw: current, stale, locked or missing, and why."""
    return {"items": S.check(ctx, [i.model_dump() for i in body.items])}


class ConflictIn(BaseModel):
    kind: Literal["workspace", "document", "transaction", "closing", "export"]
    id: str = Field(min_length=1, max_length=64)
    client_id: str = Field(min_length=1, max_length=80)
    base_revision: int | None = Field(default=None, ge=0)
    local_summary: dict[str, Any] = Field(default_factory=dict)


class ResolveIn(BaseModel):
    resolution: Literal["keep_server", "apply_local", "merged", "discarded"]
    note: str | None = Field(default=None, max_length=500)


@router.get(C + "/status", operation_id="sync_status")
def status(company_id: uuid.UUID, ctx: Read) -> Any:
    """Open conflicts the caller can see, counted by kind. Server-derived (unlike the numbers a device reports about itself)."""
    return SC.status(ctx)


@router.post(C + "/conflicts", status_code=201, operation_id="sync_conflict_record")
def record_conflict(company_id: uuid.UUID, body: ConflictIn, ctx: Read, response: Response) -> Any:
    """A client reports that it could not write an item. The server verifies the claim; `no_conflict` if the item is current."""
    code, out = SC.record(ctx, body.model_dump())
    response.status_code = code
    return out


@router.get(C + "/conflicts", operation_id="sync_conflicts")
def list_conflicts(
    company_id: uuid.UUID,
    ctx: Read,
    state: Literal["open", "resolved"] | None = None,
    kind: Literal["workspace", "document", "transaction", "closing", "export"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return SC.list_(ctx, state=state, kind=kind, limit=limit)


@router.get(C + "/conflicts/{conflict_id}", operation_id="sync_conflict")
def get_conflict(company_id: uuid.UUID, conflict_id: uuid.UUID, ctx: Read) -> Any:
    return SC.get(ctx, conflict_id)


@router.post(C + "/conflicts/{conflict_id}/resolve", operation_id="sync_conflict_resolve")
def resolve_conflict(company_id: uuid.UUID, conflict_id: uuid.UUID, body: ResolveIn, ctx: Read) -> Any:
    """Record the decision. It does not write the record: apply_local/merged return `next_base_revision` to send with the write."""
    return SC.resolve(ctx, conflict_id, body.model_dump())
