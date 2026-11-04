"""Operations CLI:  artify-api migrate | seed | bootstrap-dev | create-platform-admin | dispatch-outbox | reconcile | reconcile-storage"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from artify_api.ids import new_id
from artify_api.permissions import PLATFORM_ROLES as _PLATFORM_ROLE_MAP
from artify_api.security import passwords
from artify_api.settings import get_settings

PLATFORM_ROLES = tuple(_PLATFORM_ROLE_MAP)


def _owner_engine() -> Engine:
    return create_engine(get_settings().database_url_migrate)


def cmd_seed(_: argparse.Namespace) -> int:
    from artify_api.seed import sync_reference_data

    with _owner_engine().begin() as conn:
        sync_reference_data(conn)
    print("reference data synchronized")
    return 0


def cmd_create_platform_admin(args: argparse.Namespace) -> int:
    password = os.environ.get("ARTIFY_BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    passwords.validate_password_policy(password, email=args.email)
    with _owner_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_admins (id, email, display_name, role_key, password_hash)"
                " VALUES (:id, :e, :n, :r, :h)"
            ),
            {"id": new_id(), "e": args.email, "n": args.name, "r": args.role, "h": passwords.hash_password(password)},
        )
    print(f"platform admin {args.email} created; multi-factor enrollment is required at first login")
    return 0


def _alembic_config() -> Config:
    from pathlib import Path

    ini = os.environ.get("ARTIFY_ALEMBIC_INI") or str(Path(__file__).resolve().parents[2] / "alembic.ini")
    return Config(ini)


def cmd_migrate(_: argparse.Namespace) -> int:
    """Apply migrations as the schema owner (ARTIFY_DATABASE_URL_MIGRATE)."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")
    print("migrations applied")
    return 0


def cmd_bootstrap_dev(_: argparse.Namespace) -> int:
    """DEVELOPMENT/CI ONLY: migrate, create dev login roles with throwaway passwords, seed reference data."""
    from pathlib import Path

    env = get_settings().environment
    if env not in ("local", "ci"):
        print(f"refusing to bootstrap development credentials in environment '{env}'", file=sys.stderr)
        return 2
    cmd_migrate(argparse.Namespace())
    roles_sql = Path(
        os.environ.get("ARTIFY_DEV_ROLES_SQL")
        or Path(__file__).resolve().parents[4] / "infrastructure/database/dev_roles.sql"
    )
    with _owner_engine().begin() as conn:
        conn.exec_driver_sql(roles_sql.read_text(encoding="utf-8").replace("%", "%%"))
    return cmd_seed(argparse.Namespace())


def cmd_reconcile(_: argparse.Namespace) -> int:
    """Recompute entitlement counters from source tables for every organization; report drift."""
    import uuid

    from artify_api import entitlements

    drifted = 0
    with _owner_engine().begin() as conn:
        org_ids = [r[0] for r in conn.execute(text("SELECT id FROM organizations WHERE status <> 'closed'"))]
        for org_id in org_ids:
            conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
            drift = entitlements.reconcile(conn, uuid.UUID(str(org_id)))
            for key, (stored, actual) in drift.items():
                drifted += 1
                print(f"org={org_id} {key}: stored={stored} actual={actual} (repaired)")
    print(f"reconciled {len(org_ids)} organization(s); {drifted} counter(s) repaired")
    return 0


def cmd_reconcile_storage(args: argparse.Namespace) -> int:
    """Compare stored objects with document records for every organization (dry run unless --apply)."""
    import uuid

    from artify_api import documents_ops

    orphans = missing = 0
    with _owner_engine().begin() as conn:
        org_ids = [r[0] for r in conn.execute(text("SELECT id FROM organizations"))]
        for org_id in org_ids:
            conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
            out = documents_ops.reconcile(conn, uuid.UUID(str(org_id)), dry_run=not args.apply)
            for k in out["orphan_objects"]:
                orphans += 1
                print(f"org={org_id} orphan object {k}")
            for k in out["missing_objects"]:
                missing += 1
                print(f"org={org_id} MISSING object for a stored version: {k}")
    mode = "applied" if args.apply else "dry run"
    print(f"storage reconciliation ({mode}): {orphans} orphan object(s), {missing} missing object(s)")
    return 1 if missing else 0


def cmd_dispatch_outbox(_: argparse.Namespace) -> int:
    from artify_api.outbox import dispatch_pending
    from artify_api.worker import process_event

    n = dispatch_pending(
        lambda ev: process_event.delay(
            {k: (str(v) if v is not None else None) for k, v in ev.items() if k != "payload"}
            | {"payload": ev["payload"]}
        )
    )
    print(f"dispatched {n} event(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="artify-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    p = sub.add_parser("create-platform-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=PLATFORM_ROLES, default="platform_owner")
    p.set_defaults(fn=cmd_create_platform_admin)
    sub.add_parser("dispatch-outbox").set_defaults(fn=cmd_dispatch_outbox)
    sub.add_parser("reconcile").set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("reconcile-storage")
    p.add_argument("--apply", action="store_true", help="remove orphan objects older than the grace period")
    p.set_defaults(fn=cmd_reconcile_storage)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("bootstrap-dev").set_defaults(fn=cmd_bootstrap_dev)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
