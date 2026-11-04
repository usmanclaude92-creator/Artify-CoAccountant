"""Centralized entitlement and usage enforcement (ADR-012).

Only this module decides whether a limited action is allowed. Limits are data (plans and
per-subscription overrides) and are enforced in the same transaction as the mutation.

Count-type limits are serialized per (organization, metric) by locking the usage_counters row;
the authoritative count is re-measured from source tables after the lock is held (READ
COMMITTED), so concurrent creators cannot oversubscribe and counters cannot drift.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Connection, text

from artify_api.errors import EntitlementExceeded, Forbidden
from artify_api.ids import new_id


@dataclass(frozen=True)
class Definition:
    key: str
    scope: str  # organization | company
    value_type: str  # int | bigint | bool
    enforcement: str  # hard | metered | flag
    unit: str | None
    description: str


_D = Definition
DEFINITIONS: tuple[Definition, ...] = (
    _D("limit.companies", "organization", "int", "hard", "companies", "Active client companies"),
    _D("limit.users", "organization", "int", "hard", "users", "Active users (organization and company members)"),
    _D("limit.accountants", "organization", "int", "hard", "users", "Active accountant/staff members"),
    _D("limit.devices", "organization", "int", "hard", "devices", "Active registered devices"),
    _D("limit.workspace_sections_per_company", "company", "int", "hard", "sections", "Non-archived workspace sections"),
    _D("limit.folder_depth", "company", "int", "hard", "levels", "Maximum folder nesting depth"),
    _D("limit.folders_per_company", "company", "int", "hard", "folders", "Non-archived folders"),
    _D("limit.custom_fields_per_company", "company", "int", "hard", "fields", "Non-archived custom field definitions"),
    _D("limit.filing_rules_per_company", "company", "int", "hard", "rules", "Non-archived automatic filing rules"),
    _D(
        "limit.workspace_templates",
        "organization",
        "int",
        "hard",
        "templates",
        "Non-archived organization workspace templates",
    ),
    _D("limit.excel_templates_per_company", "company", "int", "hard", "templates", "Non-archived Excel templates"),
    _D(
        "quota.documents_per_month",
        "organization",
        "bigint",
        "metered",
        "documents",
        "Documents entering processing per period",
    ),
    _D("quota.storage_bytes", "organization", "bigint", "metered", "bytes", "Stored bytes"),
    _D("limit.file_size_bytes", "organization", "bigint", "hard", "bytes", "Largest single file accepted for upload"),
    _D("quota.ai_units_per_month", "organization", "bigint", "metered", "units", "Metered AI/OCR units per period"),
    _D("feature.custom_roles", "organization", "bool", "flag", None, "Organization-defined custom roles"),
    _D("feature.api_access", "organization", "bool", "flag", None, "Programmatic API access"),
    _D("feature.report_builder", "organization", "bool", "flag", None, "Configurable report builder"),
    _D("feature.macro_templates", "organization", "bool", "flag", None, "Macro-enabled Excel templates"),
    _D("feature.cloud_ai", "organization", "bool", "flag", None, "Cloud AI providers (subject to tenant AI policy)"),
)
BY_KEY = {d.key: d for d in DEFINITIONS}
# Keys the database can measure today (app.measure_entitlement). Others arrive with their feature.
COUNT_LIMITS = frozenset(
    {"limit.companies", "limit.users", "limit.accountants", "limit.devices", "limit.workspace_templates"}
)
# Metered quotas (Phase 6): measured by the same database function and enforced by `guard_quota`.
QUOTAS = frozenset({"quota.storage_bytes", "quota.documents_per_month"})
MEASURABLE = COUNT_LIMITS | QUOTAS
# Company-scope workspace limits are measured per company through RLS-protected counts (see workspace_usage below).
COMPANY_MEASURES: dict[str, str] = {
    "limit.workspace_sections_per_company": "SELECT count(*) FROM workspace_nodes WHERE company_id = :c AND node_type = 'section' AND archived_at IS NULL",
    "limit.folders_per_company": "SELECT count(*) FROM workspace_nodes WHERE company_id = :c AND node_type = 'folder' AND archived_at IS NULL",
    "limit.custom_fields_per_company": "SELECT count(*) FROM custom_field_definitions WHERE company_id = :c AND archived_at IS NULL",
    "limit.filing_rules_per_company": "SELECT count(*) FROM filing_rules WHERE company_id = :c AND archived_at IS NULL",
    "limit.folder_depth": "SELECT COALESCE(max(depth), 0) FROM workspace_nodes WHERE company_id = :c AND archived_at IS NULL",
}
_CURRENT_STATUSES = ("trialing", "active", "past_due", "suspended")


class SubscriptionRestricted(Forbidden):
    code, title = "subscription_restricted", "Subscription does not permit this action"


@dataclass(frozen=True)
class Limit:
    key: str
    unlimited: bool
    value: int | None  # None when unlimited or for a boolean flag
    flag: bool | None
    source: str  # override | plan | default


# Everything the backend enforces today: organization counts measured by the database function, plus the per-company
# workspace limits above. (`MEASURABLE` is only the organization-level subset.)
ENFORCED = MEASURABLE | frozenset(COMPANY_MEASURES) | {"limit.file_size_bytes"}


def effective_limit(conn: Connection, org_id: uuid.UUID, key: str) -> Limit:
    """Override (unexpired) > plan value (pinned plan version) > default deny."""
    row = conn.execute(
        text(
            "SELECT int_value, bool_value, unlimited FROM entitlement_overrides"
            " WHERE organization_id = :o AND entitlement_key = :k"
            " AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC LIMIT 1"
        ),
        {"o": org_id, "k": key},
    ).first()
    source = "override"
    if row is None:
        row = conn.execute(
            text(
                "SELECT pe.int_value, pe.bool_value, pe.unlimited FROM subscriptions s"
                " JOIN plan_entitlements pe ON pe.plan_version_id = s.plan_version_id"
                " WHERE s.organization_id = :o AND s.status = ANY(:st) AND pe.entitlement_key = :k"
            ),
            {"o": org_id, "k": key, "st": list(_CURRENT_STATUSES)},
        ).first()
        source = "plan"
    if row is None:
        return Limit(key, False, 0, False, "default")
    return Limit(key, bool(row.unlimited), None if row.unlimited else row.int_value, row.bool_value, source)


def subscription_status(conn: Connection, org_id: uuid.UUID) -> str | None:
    return conn.execute(
        text("SELECT status FROM subscriptions WHERE organization_id = :o AND status = ANY(:st)"),
        {"o": org_id, "st": list(_CURRENT_STATUSES)},
    ).scalar()


def measure(conn: Connection, org_id: uuid.UUID, key: str) -> int:
    return int(conn.execute(text("SELECT app.measure_entitlement(:o, :k)"), {"o": org_id, "k": key}).scalar_one())


def _lock_counter(conn: Connection, org_id: uuid.UUID, key: str) -> None:
    conn.execute(
        text(
            "INSERT INTO usage_counters (id, organization_id, metric) VALUES (:id, :o, :k)"
            " ON CONFLICT (organization_id, metric, scope_key, period_key) DO NOTHING"
        ),
        {"id": new_id(), "o": org_id, "k": key},
    )
    conn.execute(
        text(
            "SELECT value FROM usage_counters WHERE organization_id = :o AND metric = :k"
            " AND scope_key = 'org' AND period_key = 'all' FOR UPDATE"
        ),
        {"o": org_id, "k": key},
    )


def guard(conn: Connection, org_id: uuid.UUID, key: str, delta: int = 1) -> None:
    """Raise unless `delta` more units of `key` fit. Call BEFORE the mutation, in its transaction;
    call `refresh_counter` after it."""
    if BY_KEY[key].enforcement != "hard" or key not in COUNT_LIMITS:
        raise ValueError(f"{key} is not an enforceable count limit yet")
    _lock_counter(conn, org_id, key)
    status = subscription_status(conn, org_id)
    if status in (None, "suspended"):
        raise SubscriptionRestricted(
            "The subscription is suspended or missing; this action is not available.",
            extra={"subscription_status": status},
        )
    limit = effective_limit(conn, org_id, key)
    current = measure(conn, org_id, key)
    if not limit.unlimited and current + delta > (limit.value or 0):
        raise EntitlementExceeded(key=key, limit=limit.value or 0, current=current, requested=delta)


def guard_quota(conn: Connection, org_id: uuid.UUID, key: str, delta: int) -> None:
    """Refuse a NEW upload that would push a metered quota past its limit (storage bytes, documents per month).

    Only additions are refused: existing documents are never deleted or hidden when a quota is exceeded, and
    reading, downloading, archiving and purging stay available. Serialised per (organization, key) like `guard`."""
    if key not in QUOTAS:
        raise ValueError(f"{key} is not an enforceable quota")
    _lock_counter(conn, org_id, key)
    status = subscription_status(conn, org_id)
    if status in (None, "suspended"):
        raise SubscriptionRestricted(
            "The subscription is suspended or missing; this action is not available.",
            extra={"subscription_status": status},
        )
    limit = effective_limit(conn, org_id, key)
    current = measure(conn, org_id, key)
    if not limit.unlimited and current + delta > (limit.value or 0):
        raise EntitlementExceeded(key=key, limit=limit.value or 0, current=current, requested=delta)


def max_file_size(conn: Connection, org_id: uuid.UUID, system_ceiling: int) -> int:
    """The per-file size cap for an organization: its plan/override value, never above the system ceiling."""
    lim = effective_limit(conn, org_id, "limit.file_size_bytes")
    if lim.unlimited:
        return system_ceiling
    return min(lim.value or 0, system_ceiling)


def refresh_counter(conn: Connection, org_id: uuid.UUID, key: str) -> int:
    current = measure(conn, org_id, key)
    conn.execute(
        text(
            "UPDATE usage_counters SET value = :v, updated_at = now() WHERE organization_id = :o"
            " AND metric = :k AND scope_key = 'org' AND period_key = 'all'"
        ),
        {"v": current, "o": org_id, "k": key},
    )
    return current


def refresh_counter_locked(conn: Connection, org_id: uuid.UUID, key: str) -> int:
    """Create-if-missing, lock and refresh a counter (used where no limit check applies, e.g. signup)."""
    _lock_counter(conn, org_id, key)
    return refresh_counter(conn, org_id, key)


def has_feature(conn: Connection, org_id: uuid.UUID, key: str) -> bool:
    lim = effective_limit(conn, org_id, key)
    return bool(lim.flag)


def summarize(conn: Connection, org_id: uuid.UUID) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for d in DEFINITIONS:
        lim = effective_limit(conn, org_id, d.key)
        current = measure(conn, org_id, d.key) if d.key in MEASURABLE else None
        out.append(
            {
                "key": d.key,
                "enforcement": d.enforcement,
                "unit": d.unit,
                "source": lim.source,
                "unlimited": lim.unlimited,
                "limit": lim.value,
                "enabled": lim.flag,
                "current": current,
            }
        )
    return out


def reconcile(conn: Connection, org_id: uuid.UUID) -> dict[str, tuple[int, int]]:
    """Recompute counters from source tables; return {key: (stored, actual)} for drifted keys."""
    drift: dict[str, tuple[int, int]] = {}
    for key in sorted(MEASURABLE):
        _lock_counter(conn, org_id, key)
        stored = int(
            conn.execute(
                text(
                    "SELECT value FROM usage_counters WHERE organization_id = :o AND metric = :k"
                    " AND scope_key = 'org' AND period_key = 'all'"
                ),
                {"o": org_id, "k": key},
            ).scalar_one()
        )
        actual = refresh_counter(conn, org_id, key)
        if stored != actual:
            drift[key] = (stored, actual)
    return drift


# ---------------------------------------------------------------- per-company workspace limits
def company_usage(conn: Connection, org_id: uuid.UUID, company_id: uuid.UUID) -> dict[str, int]:
    """Current per-company counts (depth is a maximum, not a count). Runs under the caller's RLS context."""
    return {k: int(conn.execute(text(q), {"c": company_id}).scalar_one()) for k, q in COMPANY_MEASURES.items()}


def company_limits(conn: Connection, org_id: uuid.UUID, company_id: uuid.UUID) -> list[dict[str, object]]:
    usage = company_usage(conn, org_id, company_id)
    out: list[dict[str, object]] = []
    for key, current in usage.items():
        lim = effective_limit(conn, org_id, key)
        out.append(
            {
                "key": key,
                "unit": BY_KEY[key].unit,
                "description": BY_KEY[key].description,
                "limit": lim.value,
                "unlimited": lim.unlimited,
                "current": current,
                "source": lim.source,
                "at_limit": (not lim.unlimited) and current >= (lim.value or 0),
            }
        )
    return out


def enforce_company_limits(
    conn: Connection,
    org_id: uuid.UUID,
    company_id: uuid.UUID,
    before: dict[str, int],
    *,
    keys: Iterable[str] | None = None,
) -> None:
    """Raise EntitlementExceeded when a change INCREASED a company count beyond its limit.

    Called inside the mutating transaction after the change (so one check covers bulk operations such as template
    apply, duplicate and restore). A company already above a reduced limit keeps everything it has and can still edit,
    archive and restore within the limit: only increases are refused. Callers hold the company configuration lock, so
    concurrent changes to one company are serialized."""
    after = company_usage(conn, org_id, company_id)
    status = subscription_status(conn, org_id)
    for key in keys or COMPANY_MEASURES:
        if after[key] <= before.get(key, 0):
            continue
        if status in (None, "suspended"):
            raise SubscriptionRestricted(
                "The subscription is suspended or missing; this action is not available.",
                extra={"subscription_status": status},
            )
        lim = effective_limit(conn, org_id, key)
        if not lim.unlimited and after[key] > (lim.value or 0):
            raise EntitlementExceeded(
                key=key, limit=lim.value or 0, current=before.get(key, 0), requested=after[key] - before.get(key, 0)
            )
