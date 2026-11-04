"""Shared building blocks for the Super Admin (platform realm) routers."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api import audit
from artify_api.context import PlatformCtx, PlatformRequires
from artify_api.errors import ApiError, Unprocessable


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Sensitive(BaseModel):
    """Body for destructive / sensitive actions: a recorded reason and a typed confirmation of the
    target (e.g. the organization slug), so a stray click or replayed request cannot trigger them."""

    reason: str = Field(min_length=10, max_length=500)
    confirm: str = Field(min_length=1, max_length=200)


class Reasoned(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class ConfirmationMismatch(ApiError):
    status, code, title = 422, "confirmation_mismatch", "Confirmation does not match the target"


def require_confirmation(given: str, expected: str) -> None:
    if given.strip().lower() != expected.strip().lower():
        raise ConfirmationMismatch(f"Type “{expected}” to confirm this action.")


Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def like(q: str | None) -> str | None:
    """ILIKE pattern with wildcards in the user input escaped."""
    if not q or not q.strip():
        return None
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def order_clause(sort: str | None, order: str, allowed: dict[str, str], default: str) -> str:
    """Whitelisted ORDER BY (never interpolates user input)."""
    column = allowed.get(sort or "", allowed[default])
    direction = "ASC" if order.lower() == "asc" else "DESC"
    return f"{column} {direction}, 1 ASC"


def require_uuid_list(values: list[str]) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(v) for v in values]
    except ValueError as exc:
        raise Unprocessable("Invalid identifier.") from exc


def padmin(permission: str) -> Any:
    """Route dependency: platform-realm authentication + the named permission, enforced per route."""
    return Depends(PlatformRequires(permission))


def paudit(
    ctx: PlatformCtx,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object | None = None,
    org: uuid.UUID | None = None,
    summary: dict[str, Any] | None = None,
    reason: str | None = None,
    outcome: str = "success",
) -> None:
    audit.write_platform_audit(
        ctx.conn,
        action=action,
        actor_id=ctx.admin_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        target_organization=org,
        summary=summary,
        meta=ctx.meta,
        reason=reason,
        outcome=outcome,
    )


def paudit_isolated(
    ctx: PlatformCtx,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object | None = None,
    org: uuid.UUID | None = None,
    summary: dict[str, Any] | None = None,
    outcome: str = "denied",
) -> None:
    """Record an event in its OWN transaction, so it survives the rollback of a request that then fails."""
    from artify_api.db import DbContext, platform_engine, transaction

    with transaction(platform_engine(), DbContext(user_id=ctx.admin_id)) as conn:
        audit.write_platform_audit(
            conn,
            action=action,
            actor_id=ctx.admin_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            target_organization=org,
            summary=summary,
            meta=ctx.meta,
            outcome=outcome,
        )


def total_of(conn: Any, base_sql: str, params: dict[str, Any]) -> int:
    return int(conn.execute(text(f"SELECT count(*) FROM ({base_sql}) t"), params).scalar_one())
