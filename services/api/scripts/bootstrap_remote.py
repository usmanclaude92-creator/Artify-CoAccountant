"""Idempotent preparation of a hosted database (demo/preview deployments only).

Runs as the schema owner (ARTIFY_DATABASE_URL_MIGRATE): applies migrations, creates the three login roles with passwords from
the environment, seeds reference data and (optionally) creates one verified demo owner. Safe to run on every cold start; a
Postgres advisory lock serialises concurrent instances. No credential is stored in the repository."""

from __future__ import annotations

import os
import re
import sys

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool, text

LOCK_KEY = 7_770_011
ROLES = (
    ("artify_app_login", "artify_app", "ARTIFY_ROLE_PASSWORD_APP"),
    ("artify_platform_login", "artify_platform", "ARTIFY_ROLE_PASSWORD_PLATFORM"),
    ("artify_dispatcher_login", "artify_dispatcher", "ARTIFY_ROLE_PASSWORD_DISPATCHER"),
)


def _log(msg: str) -> None:
    print(f"[bootstrap] {msg}", file=sys.stderr, flush=True)


def _head() -> str:
    from artify_api.cli import _alembic_config

    return str(ScriptDirectory.from_config(_alembic_config()).get_current_head())


def _owner_engine():  # type: ignore[no-untyped-def]
    return create_engine(os.environ["ARTIFY_DATABASE_URL_MIGRATE"], poolclass=pool.NullPool)


def _ready(conn) -> bool:  # type: ignore[no-untyped-def]
    exists = conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar()
    if not exists:
        return False
    version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    roles = conn.execute(
        text(
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('artify_app_login','artify_platform_login','artify_dispatcher_login')"
        )
    ).scalar()
    return version == _head() and roles == 3


def _prepare_hosted_owner(conn) -> None:  # type: ignore[no-untyped-def]
    """Managed Postgres (e.g. Supabase) does not give the schema owner SUPERUSER, but the migrations hand objects to the
    definer role (`ALTER ... OWNER TO artify_definer`), and PostgreSQL only allows that when the new owner may create objects
    in the schema. Create the NOLOGIN group roles first (the migrations accept existing roles) and allow that one grant."""
    for role in ("artify_app", "artify_platform", "artify_dispatcher", "artify_definer"):
        if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is None:
            conn.exec_driver_sql(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
    conn.exec_driver_sql("GRANT CREATE ON SCHEMA public TO artify_definer")


def _create_roles(conn) -> None:  # type: ignore[no-untyped-def]
    for login, group, env in ROLES:
        pw = os.environ.get(env, "")
        if not re.fullmatch(r"[A-Za-z0-9]{24,128}", pw):
            raise RuntimeError(f"{env} must be 24-128 letters/digits")
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": login}).first()
        if exists is None:
            conn.exec_driver_sql(f"CREATE ROLE {login} LOGIN PASSWORD '{pw}' IN ROLE {group}")
        else:
            conn.exec_driver_sql(f"ALTER ROLE {login} PASSWORD '{pw}'")


def _demo_owner() -> None:
    email = os.environ.get("ARTIFY_BOOTSTRAP_DEMO_EMAIL")
    password = os.environ.get("ARTIFY_BOOTSTRAP_DEMO_PASSWORD")
    if not email or not password:
        return
    with _owner_engine().connect() as conn:
        if conn.execute(text("SELECT 1 FROM users WHERE lower(email::text) = lower(:e)"), {"e": email}).first():
            return
    from artify_api import mailer as mailer_mod
    from artify_api.main import app
    from fastapi.testclient import TestClient

    mem = mailer_mod.MemoryMailer()
    mailer_mod.set_mailer(mem)
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/auth/signup",
                json={
                    "email": email,
                    "password": password,
                    "display_name": "Demo Owner",
                    "organization_name": os.environ.get("ARTIFY_BOOTSTRAP_DEMO_ORG", "Artify Demo Accounting"),
                    "organization_type": "accounting_firm",
                    "country": "OM",
                    "default_currency": "OMR",
                },
            )
            if r.status_code != 202:
                raise RuntimeError(f"demo signup failed: {r.status_code} {r.text[:200]}")
            token = mem.last("verify_email", email).context["token"]
            v = client.post("/api/v1/auth/verify-email", json={"token": token})
            if v.status_code != 204:
                raise RuntimeError(f"demo verification failed: {v.status_code}")
    finally:
        mailer_mod.set_mailer(None)
    _log("demo owner created")


def ensure_bootstrapped() -> None:
    from artify_api import cli

    eng = _owner_engine()
    with eng.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            if not _ready(lock_conn):
                lock_conn.commit()
                _log("applying migrations")
                import argparse

                with eng.begin() as c:
                    _prepare_hosted_owner(c)

                cli.cmd_migrate(argparse.Namespace())
                with eng.begin() as c:
                    _create_roles(c)
                cli.cmd_seed(argparse.Namespace())
                _log("database ready")
            _demo_owner()
        finally:
            lock_conn.rollback()
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()


if __name__ == "__main__":
    ensure_bootstrapped()
