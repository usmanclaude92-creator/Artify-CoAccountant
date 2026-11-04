"""Operations CLI:  artify-api seed | create-platform-admin | dispatch-outbox | reconcile"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import Engine, create_engine, text

from artify_api.ids import new_id
from artify_api.security import passwords
from artify_api.settings import get_settings

PLATFORM_ROLES = ("platform_owner", "platform_admin", "platform_support", "platform_billing", "platform_auditor")


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
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
