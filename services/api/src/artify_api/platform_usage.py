"""Platform-side usage and subscription read models.

Reads only what the platform realm may see: plans, overrides, subscriptions and the maintained
usage counters. Current values come from `usage_counters` (the same counters the EntitlementService
locks and reconciles); a metric that has no counter yet is reported as NOT METERED, never as zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Connection, text

from artify_api.entitlements import MEASURABLE, QUOTAS

APPROACHING_PERCENT = 80.0


class UsageRow(BaseModel):
    key: str
    description: str
    unit: str | None
    enforcement: str
    enforced_now: bool
    source: str  # override | plan | default
    unlimited: bool
    limit: int | None
    enabled: bool | None
    current: int | None
    remaining: int | None
    percent: float | None
    status: str  # ok | approaching | exceeded | not_metered | not_configured | flag


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    plan_id: uuid.UUID
    plan_key: str
    plan_name: str
    plan_version_id: uuid.UUID
    plan_version: int
    status: str
    current_period_start: datetime
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    cancelled_at: datetime | None
    created_at: datetime


SUBSCRIPTION_SELECT = (
    "SELECT s.id, s.organization_id, o.display_name AS organization_name, p.id AS plan_id, p.key AS plan_key,"
    " p.name AS plan_name, pv.id AS plan_version_id, pv.version AS plan_version, s.status,"
    " s.current_period_start, s.current_period_end, s.trial_ends_at, s.cancel_at_period_end, s.cancelled_at,"
    " s.created_at FROM subscriptions s JOIN organizations o ON o.id = s.organization_id"
    " JOIN subscription_plan_versions pv ON pv.id = s.plan_version_id JOIN subscription_plans p ON p.id = pv.plan_id"
)


def fetch_subscription(conn: Connection, *, org_id: uuid.UUID | None = None, sub_id: uuid.UUID | None = None) -> Any:
    where, params = ("s.organization_id = :i", {"i": org_id}) if org_id else ("s.id = :i", {"i": sub_id})
    return conn.execute(text(f"{SUBSCRIPTION_SELECT} WHERE {where}"), params).mappings().first()


_USAGE_SQL = """
SELECT o.id AS org_id, d.key, d.description, d.unit, d.enforcement,
       ov.id AS ov_id, ov.int_value AS ov_int, ov.bool_value AS ov_bool, ov.unlimited AS ov_unl,
       pe.entitlement_key AS pe_key, pe.int_value AS pe_int, pe.bool_value AS pe_bool,
       COALESCE(pe.unlimited, false) AS pe_unl, uc.value AS current
  FROM unnest(CAST(:ids AS uuid[])) AS o(id)
 CROSS JOIN entitlement_definitions d
  LEFT JOIN LATERAL (
        SELECT * FROM entitlement_overrides x
         WHERE x.organization_id = o.id AND x.entitlement_key = d.key AND (x.expires_at IS NULL OR x.expires_at > now())
         ORDER BY x.created_at DESC LIMIT 1) ov ON true
  LEFT JOIN subscriptions s ON s.organization_id = o.id AND s.status IN ('trialing', 'active', 'past_due', 'suspended')
  LEFT JOIN plan_entitlements pe ON pe.plan_version_id = s.plan_version_id AND pe.entitlement_key = d.key
  LEFT JOIN usage_counters uc ON uc.organization_id = o.id AND uc.metric = d.key AND uc.scope_key = 'org'
       AND uc.period_key IN ('all', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM'))
 ORDER BY d.key
"""


def _row(r: Any, measured: dict[str, int] | None = None) -> UsageRow:
    if r.ov_id is not None:
        source, unl, lim, flag = "override", bool(r.ov_unl), r.ov_int, r.ov_bool
    elif r.pe_key is not None:
        source, unl, lim, flag = "plan", bool(r.pe_unl), r.pe_int, r.pe_bool
    else:
        source, unl, lim, flag = "default", False, None, None
    if measured is not None and r.key in measured:
        current: int | None = measured[r.key]  # live measurement beats a possibly missing counter
    else:
        current = int(r.current) if r.current is not None else None
    if current is None and r.key in QUOTAS:
        current = 0  # quota counters are created by the first upload; no counter yet means nothing has been stored
    remaining = percent = None
    if r.enforcement == "flag":
        status = "flag"
    elif source == "default":
        status = "not_configured" if not current else "exceeded"
    elif current is None:
        status = "not_metered"
    elif unl:
        status = "ok"
    else:
        cap = lim or 0
        remaining = cap - current
        percent = round(100.0 * current / cap, 1) if cap > 0 else (100.0 if current > 0 else 0.0)
        status = "exceeded" if current > cap else ("approaching" if percent >= APPROACHING_PERCENT else "ok")
    return UsageRow(
        key=r.key,
        description=r.description,
        unit=r.unit,
        enforcement=r.enforcement,
        enforced_now=r.key in MEASURABLE,
        source=source,
        unlimited=unl,
        limit=None if unl else lim,
        enabled=flag,
        current=current,
        remaining=remaining,
        percent=percent,
        status=status,
    )


def usage_for_orgs(
    conn: Connection, org_ids: list[uuid.UUID], measured: dict[uuid.UUID, dict[str, int]] | None = None
) -> dict[uuid.UUID, list[UsageRow]]:
    out: dict[uuid.UUID, list[UsageRow]] = {i: [] for i in org_ids}
    if not org_ids:
        return out
    for r in conn.execute(text(_USAGE_SQL), {"ids": org_ids}):
        out[r.org_id].append(_row(r, (measured or {}).get(r.org_id)))
    return out
