"""Synchronisation foundation API (Phase 12): a permission-aware change feed and a preflight check. Read-only; the writes a
client queues still go through the normal endpoints with their own concurrency and idempotency checks."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from artify_api import sync as S
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
