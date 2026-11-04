"""Append-only audit writer. Sensitive values are redacted before they reach the database."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from artify_api.ids import new_id

_SENSITIVE = ("password", "token", "secret", "code", "hash", "authorization")


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    correlation_id: str | None = None


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in _SENSITIVE) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def write_audit(
    conn: Connection,
    *,
    action: str,
    actor_type: str = "user",
    actor_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    summary: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    m = meta or RequestMeta()
    conn.execute(
        text(
            "INSERT INTO audit_events (id, organization_id, company_id, actor_type, actor_id, action,"
            " entity_type, entity_id, summary, ip, user_agent, correlation_id)"
            " VALUES (:id, :org, :co, :at, :aid, :action, :et, :eid, CAST(:summary AS jsonb),"
            " CAST(:ip AS inet), :ua, :cid)"
        ),
        {
            "id": new_id(),
            "org": org_id,
            "co": company_id,
            "at": actor_type,
            "aid": actor_id,
            "action": action,
            "et": entity_type,
            "eid": str(entity_id) if entity_id else None,
            "summary": json.dumps(redact(summary or {})),
            "ip": m.ip,
            "ua": (m.user_agent or "")[:300],
            "cid": m.correlation_id,
        },
    )


def write_platform_audit(
    conn: Connection,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_organization: uuid.UUID | None = None,
    summary: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    m = meta or RequestMeta()
    conn.execute(
        text(
            "INSERT INTO platform_audit_events (id, actor_type, actor_id, action, target_type, target_id,"
            " target_organization, summary, ip, user_agent, correlation_id)"
            " VALUES (:id, 'platform_admin', :aid, :action, :tt, :tid, :torg, CAST(:summary AS jsonb),"
            " CAST(:ip AS inet), :ua, :cid)"
        ),
        {
            "id": new_id(),
            "aid": actor_id,
            "action": action,
            "tt": target_type,
            "tid": target_id,
            "torg": target_organization,
            "summary": json.dumps(redact(summary or {})),
            "ip": m.ip,
            "ua": (m.user_agent or "")[:300],
            "cid": m.correlation_id,
        },
    )


def write_user_event(
    conn: Connection,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    summary: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    """Record a user-level security event (login, MFA, session, password reset).

    Stored once in the platform security stream (organization_id NULL) and once per organization the
    user belongs to, so tenant admins with `audit.read` can see their members' security events.
    The connection's context must already be the acting user (enforced in the database)."""
    m = meta or RequestMeta()
    conn.execute(
        text("SELECT app.audit_user_event(:actor, :action, CAST(:summary AS jsonb), CAST(:ip AS inet), :ua, :cid)"),
        {
            "actor": actor_id,
            "action": action,
            "summary": json.dumps(redact(summary or {})),
            "ip": m.ip,
            "ua": (m.user_agent or "")[:300],
            "cid": m.correlation_id,
        },
    )
