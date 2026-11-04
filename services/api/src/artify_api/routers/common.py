"""Realm-neutral helpers shared by customer-facing and platform routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


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
