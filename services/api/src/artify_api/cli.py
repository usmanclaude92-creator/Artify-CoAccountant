"""Operations CLI:  artify-api migrate | seed | bootstrap-dev | create-platform-admin | dispatch-outbox | seal-audit | audit-heads | audit-check-anchors | reconcile"""

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


def cmd_seal_audit(args: argparse.Namespace) -> int:
    """Extend every organization's tamper-evident audit chain with events written since the last run."""
    import time

    from artify_api.db import DbContext, dispatcher_engine, transaction

    while True:
        total = 0
        while True:
            with transaction(dispatcher_engine(), DbContext()) as conn:
                n = conn.execute(text("SELECT app.audit_seal(:n)"), {"n": args.batch}).scalar_one()
                n += conn.execute(text("SELECT app.platform_audit_seal(:n)"), {"n": args.batch}).scalar_one()
            total += n
            if n < args.batch:
                break
        print(f"sealed {total} audit event(s)", flush=True)
        if not args.loop:
            return 0
        time.sleep(args.loop)


def cmd_audit_heads(args: argparse.Namespace) -> int:
    """Print the newest seal of every audit chain as JSON lines (organization_id null = the platform chain). Copy the output to a
    store the database operator cannot rewrite (an append-only bucket in another account, a signed e-mail, a ticket)."""
    import json
    from datetime import UTC, datetime

    from artify_api.db import DbContext, dispatcher_engine, transaction

    now = datetime.now(UTC).isoformat()
    with transaction(dispatcher_engine(), DbContext()) as conn:
        rows = conn.execute(
            text("SELECT organization_id, head_seq, head_hash FROM app.audit_heads() ORDER BY 1 NULLS FIRST")
        ).all()
    out = open(args.out, "a", encoding="utf-8") if args.out else sys.stdout  # noqa: SIM115
    try:
        for r in rows:
            out.write(
                json.dumps({"at": now, "organization_id": str(r[0]) if r[0] else None, "seq": int(r[1]), "hash": r[2]})
                + "\n"
            )
    finally:
        if args.out:
            out.close()
    return 0


def cmd_audit_check_anchors(args: argparse.Namespace) -> int:
    """Compare previously exported heads with the database: every exported seal must still exist with the same hash. A missing seal
    means the newest history was cut off; a different hash means it was rewritten. Exit code 1 on any mismatch."""
    import json

    from artify_api.db import DbContext, dispatcher_engine, transaction

    bad = 0
    checked = 0
    with open(args.file, encoding="utf-8") as f, transaction(dispatcher_engine(), DbContext()) as conn:
        for line in f:
            if not line.strip():
                continue
            a = json.loads(line)
            got = conn.execute(
                text("SELECT app.audit_seal_at(CAST(:o AS uuid), :s)"), {"o": a["organization_id"], "s": a["seq"]}
            ).scalar_one()
            checked += 1
            if got != a["hash"]:
                bad += 1
                print(
                    f"MISMATCH org={a['organization_id']} seq={a['seq']} exported_at={a['at']}: {'seal missing' if got is None else 'hash differs'}"
                )
    print(f"checked {checked} anchor(s): {'ALL MATCH' if not bad else f'{bad} MISMATCH'}")
    return 1 if bad else 0


def cmd_dispatch_outbox(args: argparse.Namespace) -> int:
    """Move pending outbox events to the worker (Celery), or with --inline handle them in this process (no broker needed:
    notifications and e-mail are delivered here). --loop N repeats every N seconds until interrupted."""
    import time

    from artify_api.event_tasks import handle_event, process_event
    from artify_api.outbox import dispatch_pending

    def send(ev: dict) -> None:  # type: ignore[type-arg]
        event = {k: (str(v) if v is not None else None) for k, v in ev.items() if k != "payload"} | {
            "payload": ev["payload"]
        }
        if args.inline:
            handle_event(event)
        else:
            process_event.delay(event)

    while True:
        n = dispatch_pending(send)
        print(f"dispatched {n} event(s)", flush=True)
        if not args.loop:
            return 0
        time.sleep(args.loop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="artify-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    p = sub.add_parser("create-platform-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=PLATFORM_ROLES, default="platform_owner")
    p.set_defaults(fn=cmd_create_platform_admin)
    p = sub.add_parser("seal-audit")
    p.add_argument("--batch", type=int, default=1000, help="events per database call (1-10000)")
    p.add_argument("--loop", type=int, default=0, metavar="SECONDS", help="keep running, polling every SECONDS")
    p.set_defaults(fn=cmd_seal_audit)
    p = sub.add_parser("audit-heads")
    p.add_argument("--out", help="append JSON lines to this file instead of printing them")
    p.set_defaults(fn=cmd_audit_heads)
    p = sub.add_parser("audit-check-anchors")
    p.add_argument("file", help="a file written by audit-heads (possibly copied back from the external store)")
    p.set_defaults(fn=cmd_audit_check_anchors)
    p = sub.add_parser("dispatch-outbox")
    p.add_argument(
        "--inline", action="store_true", help="handle events in this process instead of queueing them to Celery"
    )
    p.add_argument("--loop", type=int, default=0, metavar="SECONDS", help="keep running, polling every SECONDS")
    p.set_defaults(fn=cmd_dispatch_outbox)
    sub.add_parser("reconcile").set_defaults(fn=cmd_reconcile)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("bootstrap-dev").set_defaults(fn=cmd_bootstrap_dev)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
