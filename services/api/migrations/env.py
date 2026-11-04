"""Alembic environment. Migrations run as the schema owner (ARTIFY_DATABASE_URL_MIGRATE),
never as the application role."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    url = os.environ.get("ARTIFY_DATABASE_URL_MIGRATE")
    if not url:
        raise RuntimeError("ARTIFY_DATABASE_URL_MIGRATE is required to run migrations")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
