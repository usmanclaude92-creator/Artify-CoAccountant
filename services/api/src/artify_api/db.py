"""Database engines and the request transaction context.

Every request runs in ONE transaction. Context variables are set with set_config(..., true)
(transaction-local, safe with transaction-mode connection pooling). Nothing here trusts
client input: callers pass values derived from validated memberships.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, create_engine, text

from artify_api.settings import get_settings

_engines: dict[str, Engine] = {}


def _engine(name: str, url: str) -> Engine:
    if name not in _engines:
        _engines[name] = create_engine(url, pool_size=10, max_overflow=10, pool_pre_ping=True)
    return _engines[name]


def app_engine() -> Engine:
    return _engine("app", get_settings().database_url_app)


def platform_engine() -> Engine:
    return _engine("platform", get_settings().database_url_platform)


def dispatcher_engine() -> Engine:
    return _engine("dispatcher", get_settings().database_url_dispatcher)


def dispose_engines() -> None:
    for e in _engines.values():
        e.dispose()
    _engines.clear()


@dataclass
class DbContext:
    """What RLS sees. Missing values => policies match nothing (fail closed)."""

    user_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    company_scope: str | None = None  # 'all' | 'list'
    company_ids: Sequence[uuid.UUID] = ()


def apply_context(conn: Connection, ctx: DbContext) -> None:
    conn.execute(
        text(
            "SELECT set_config('app.user_id', :u, true), set_config('app.org_id', :o, true),"
            " set_config('app.company_scope', :s, true), set_config('app.company_ids', :c, true)"
        ),
        {
            "u": str(ctx.user_id) if ctx.user_id else "",
            "o": str(ctx.org_id) if ctx.org_id else "",
            "s": ctx.company_scope or "",
            "c": ",".join(str(c) for c in ctx.company_ids),
        },
    )


@contextmanager
def transaction(engine: Engine, ctx: DbContext | None = None) -> Iterator[Connection]:
    """Open a transaction on `engine`, apply `ctx`, commit on success, roll back on error."""
    with engine.connect() as conn, conn.begin():
        apply_context(conn, ctx or DbContext())
        yield conn
