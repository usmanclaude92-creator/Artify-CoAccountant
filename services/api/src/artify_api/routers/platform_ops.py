"""Super Admin: overview dashboard, usage monitoring, platform health, audit viewer, activity feed, settings.

Honesty rule: every figure comes from a real query. Anything the product cannot measure yet (documents,
storage, AI units, revenue without payment records, worker heartbeats, backups) is reported as
`available: false` with a reason - never as zero and never as a placeholder.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from importlib import metadata
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, StrictInt, StrictStr
from sqlalchemy import text

from artify_api import metrics
from artify_api.context import PlatformCtx
from artify_api.errors import NotFound, Unprocessable
from artify_api.platform_usage import UsageRow, usage_for_orgs
from artify_api.routers.platform_common import Limit, Offset, Page, like, padmin, paudit
from artify_api.settings import get_settings

router = APIRouter(prefix="/platform", tags=["platform-operations"])

SUB_STATUSES = ("trialing", "active", "past_due", "suspended", "cancelled", "expired")
USAGE_SCAN_CAP = 500


# ------------------------------------------------------------------ models
class Unavailable(BaseModel):
    available: bool = False
    note: str


class CountBy(BaseModel):
    total: int
    breakdown: dict[str, int]


class MonthPoint(BaseModel):
    month: str
    count: int


class SubTrendPoint(BaseModel):
    month: str
    created: int
    status_changes: int
    plan_changes: int


class Alert(BaseModel):
    kind: str
    severity: Literal["info", "warning", "critical"]
    count: int
    message: str
    href: str


class RecentOrg(BaseModel):
    id: uuid.UUID
    display_name: str
    type: str
    created_at: datetime
    status: str


class RecentSubEvent(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    event_type: str
    from_status: str | None
    to_status: str | None
    created_at: datetime


class RecentAdminEvent(BaseModel):
    id: uuid.UUID
    action: str
    outcome: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime


class JobStats(BaseModel):
    pending: int
    failed: int
    dispatched: int
    oldest_pending_seconds: int | None


class RevenueStats(BaseModel):
    available: bool
    collected_last_30_days: list[dict[str, str]]
    note: str


class Dashboard(BaseModel):
    generated_at: datetime
    environment: str
    organizations: CountBy
    organization_types: dict[str, int]
    subscriptions: CountBy
    companies: CountBy
    users: CountBy
    documents: Unavailable
    storage: Unavailable
    ai_usage: Unavailable
    revenue: RevenueStats | None
    jobs: JobStats
    organization_growth: list[MonthPoint]
    subscription_trend: list[SubTrendPoint]
    usage_trend: Unavailable
    recent_registrations: list[RecentOrg]
    recent_subscription_changes: list[RecentSubEvent] | None
    recent_admin_activity: list[RecentAdminEvent] | None
    alerts: list[Alert]


class OrgUsage(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_type: str
    organization_status: str
    plan_name: str | None
    subscription_status: str | None
    worst: Literal["ok", "approaching", "exceeded"]
    rows: list[UsageRow]


class UsageHistoryPoint(BaseModel):
    month: str
    metric: str
    quantity: int


class UsageHistory(BaseModel):
    organization_id: uuid.UUID
    points: list[UsageHistoryPoint]
    note: str


class HealthCheck(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down", "not_configured", "unknown"]
    detail: str
    latency_ms: float | None = None


class Health(BaseModel):
    generated_at: datetime
    environment: str
    environment_note: str
    version: str
    overall: Literal["ok", "degraded", "down"]
    uptime_seconds: int
    checks: list[HealthCheck]
    response_counts_since_start: dict[str, int]
    monitoring_note: str


class AuditOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_name: str | None
    action: str
    target_type: str | None
    target_id: str | None
    target_organization: uuid.UUID | None
    organization_name: str | None
    outcome: str
    reason: str | None
    summary: dict[str, Any]
    ip: str | None
    correlation_id: str | None


class ActivityItem(BaseModel):
    at: datetime
    kind: str
    title: str
    organization_id: uuid.UUID | None
    organization_name: str | None


class SettingOut(BaseModel):
    key: str
    description: str
    kind: Literal["integer", "string"]
    value: int | str | None
    default: int | str | None
    minimum: int | None = None
    maximum: int | None = None
    updated_at: datetime | None
    updated_by: uuid.UUID | None


class SettingUpdate(BaseModel):
    value: StrictInt | StrictStr  # strict: JSON true must not become 1
    reason: str = Field(min_length=10, max_length=500)


# ------------------------------------------------------------------ dashboard
def _month_series(ctx: PlatformCtx, sql: str) -> dict[str, Any]:
    return {r.m: r for r in ctx.conn.execute(text(sql))}


def _last_12_months() -> list[str]:
    now = datetime.now(UTC)
    y, m = now.year, now.month
    out = []
    for _ in range(12):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _job_stats(ctx: PlatformCtx) -> JobStats:
    by = {r.state: r for r in ctx.conn.execute(text("SELECT state, n, oldest FROM platform_outbox_overview"))}
    oldest = None
    if "pending" in by and by["pending"].oldest is not None:
        oldest = int(
            ctx.conn.execute(text("SELECT extract(epoch FROM now() - :o)"), {"o": by["pending"].oldest}).scalar_one()
        )
    return JobStats(
        pending=by["pending"].n if "pending" in by else 0,
        failed=by["failed"].n if "failed" in by else 0,
        dispatched=by["dispatched"].n if "dispatched" in by else 0,
        oldest_pending_seconds=oldest,
    )


@router.get("/dashboard", response_model=Dashboard, operation_id="platform_dashboard")
def dashboard(ctx: Annotated[PlatformCtx, padmin("platform.dashboard.read")]) -> Dashboard:
    c = ctx.conn
    org_types = {r.type: r.n for r in c.execute(text("SELECT type, count(*) AS n FROM organizations GROUP BY type"))}
    org_status = {
        r.status: r.n for r in c.execute(text("SELECT status, count(*) AS n FROM organizations GROUP BY status"))
    }
    sub_status = dict.fromkeys(SUB_STATUSES, 0) | {
        r.status: r.n for r in c.execute(text("SELECT status, count(*) AS n FROM subscriptions GROUP BY status"))
    }
    comp = {
        r.status: r.n
        for r in c.execute(text("SELECT status, count(*) AS n FROM platform_company_overview GROUP BY status"))
    }
    users = c.execute(text("SELECT total_users, active_users FROM platform_user_totals")).one()
    jobs = _job_stats(ctx)

    months = _last_12_months()
    growth_raw = {
        r.m: r.n
        for r in c.execute(
            text(
                "SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS m, count(*) AS n FROM organizations GROUP BY 1"
            )
        )
    }
    trend_raw = {
        r.m: r
        for r in c.execute(
            text(
                "SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS m,"
                " count(*) FILTER (WHERE event_type = 'created') AS created,"
                " count(*) FILTER (WHERE event_type = 'status_changed') AS status_changes,"
                " count(*) FILTER (WHERE event_type = 'plan_changed') AS plan_changes FROM subscription_events GROUP BY 1"
            )
        )
    }
    usage_rows = c.execute(text("SELECT count(*) FROM usage_records")).scalar_one()

    revenue: RevenueStats | None = None
    if ctx.can("platform.billing.read"):
        rev = [
            {"currency": r.currency, "amount": str(r.amount)}
            for r in c.execute(
                text(
                    "SELECT currency, sum(CASE kind WHEN 'payment' THEN amount ELSE -amount END) AS amount FROM billing_payments"
                    " WHERE received_at > now() - interval '30 days' GROUP BY currency ORDER BY currency"
                )
            )
        ]
        revenue = RevenueStats(
            available=bool(rev),
            collected_last_30_days=rev,
            note="Manual payment records entered by administrators."
            if rev
            else "No payment has been recorded. No billing provider is integrated.",
        )

    recent_sub = None
    if ctx.can("platform.subscriptions.read"):
        recent_sub = [
            RecentSubEvent(**r)
            for r in c.execute(
                text(
                    "SELECT e.organization_id, o.display_name AS organization_name, e.event_type, e.from_status, e.to_status, e.created_at"
                    " FROM subscription_events e JOIN organizations o ON o.id = e.organization_id ORDER BY e.created_at DESC LIMIT 8"
                )
            ).mappings()
        ]
    recent_admin = None
    if ctx.can("platform.audit.read"):
        recent_admin = [
            RecentAdminEvent(**r)
            for r in c.execute(
                text(
                    "SELECT e.id, e.action, e.outcome, e.actor_id, a.display_name AS actor_name, e.created_at FROM platform_audit_events e"
                    " LEFT JOIN platform_admins a ON a.id = e.actor_id WHERE e.action NOT LIKE 'auth.%' ORDER BY e.created_at DESC LIMIT 8"
                )
            ).mappings()
        ]

    alerts: list[Alert] = []
    if jobs.failed:
        alerts.append(
            Alert(
                kind="failed_jobs",
                severity="critical",
                count=jobs.failed,
                message="Background events failed to dispatch.",
                href="/platform/health",
            )
        )
    if sub_status["past_due"]:
        alerts.append(
            Alert(
                kind="past_due",
                severity="warning",
                count=sub_status["past_due"],
                message="Subscriptions are past due.",
                href="/platform/subscriptions?status=past_due",
            )
        )
    if ctx.can("platform.support.read"):
        n = c.execute(
            text(
                "SELECT count(*) FROM support_access_grants g WHERE g.status = 'requested' AND g.created_at >= now() - interval '14 days'"
            )
        ).scalar_one()
        if n:
            alerts.append(
                Alert(
                    kind="support_requests",
                    severity="info",
                    count=n,
                    message="Support-access requests await customer approval.",
                    href="/platform/support",
                )
            )
    if ctx.can("platform.usage.read"):
        ids = [
            r.id
            for r in c.execute(
                text("SELECT id FROM organizations WHERE status <> 'closed' LIMIT :n"), {"n": USAGE_SCAN_CAP}
            )
        ]
        exceeded = approaching = 0
        for rows in usage_for_orgs(c, ids).values():
            states = {r.status for r in rows if r.enforced_now}
            exceeded += "exceeded" in states
            approaching += ("approaching" in states) and "exceeded" not in states
        if exceeded:
            alerts.append(
                Alert(
                    kind="limits_exceeded",
                    severity="warning",
                    count=exceeded,
                    message="Organizations are over a plan limit.",
                    href="/platform/usage?attention=exceeded",
                )
            )
        if approaching:
            alerts.append(
                Alert(
                    kind="limits_approaching",
                    severity="info",
                    count=approaching,
                    message="Organizations are approaching a plan limit.",
                    href="/platform/usage?attention=approaching",
                )
            )

    return Dashboard(
        generated_at=datetime.now(UTC),
        environment=get_settings().environment,
        organizations=CountBy(total=sum(org_status.values()), breakdown=org_status),
        organization_types=org_types,
        subscriptions=CountBy(total=sum(sub_status.values()), breakdown=sub_status),
        companies=CountBy(total=sum(comp.values()), breakdown=comp),
        users=CountBy(total=users.total_users, breakdown={"active": users.active_users}),
        documents=Unavailable(note="Document processing is not built yet (Phase 5+); nothing is metered."),
        storage=Unavailable(note="Object storage is not integrated yet (Phase 6); storage is not metered."),
        ai_usage=Unavailable(note="AI processing is not built yet; no units are metered."),
        revenue=revenue,
        jobs=jobs,
        organization_growth=[MonthPoint(month=m, count=growth_raw.get(m, 0)) for m in months],
        subscription_trend=[
            SubTrendPoint(
                month=m,
                created=trend_raw[m].created if m in trend_raw else 0,
                status_changes=trend_raw[m].status_changes if m in trend_raw else 0,
                plan_changes=trend_raw[m].plan_changes if m in trend_raw else 0,
            )
            for m in months
        ],
        usage_trend=Unavailable(
            note="Metered usage records do not exist yet."
            if not usage_rows
            else "Usage records exist; see Usage & limits."
        ),
        recent_registrations=[
            RecentOrg(**r)
            for r in c.execute(
                text(
                    "SELECT id, display_name, type, created_at, status FROM organizations ORDER BY created_at DESC LIMIT 8"
                )
            ).mappings()
        ],
        recent_subscription_changes=recent_sub,
        recent_admin_activity=recent_admin,
        alerts=alerts,
    )


# ------------------------------------------------------------------ usage
@router.get("/usage", response_model=Page[OrgUsage], operation_id="platform_usage")
def usage(
    ctx: Annotated[PlatformCtx, padmin("platform.usage.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    attention: Literal["approaching", "exceeded"] | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[OrgUsage]:
    """Usage against limits per organization. Current values are the maintained usage counters; metrics that
    are not metered yet show as such. Scans at most 500 organizations per request."""
    params: dict[str, Any] = {"cap": USAGE_SCAN_CAP}
    where = "WHERE o.status <> 'closed'"
    if pattern := like(q):
        where += " AND (o.display_name ILIKE :q ESCAPE '\\' OR o.slug::text ILIKE :q ESCAPE '\\')"
        params["q"] = pattern
    orgs = ctx.conn.execute(
        text(
            "SELECT o.id, o.display_name, o.type, o.status, p.name AS plan_name, s.status AS sub_status FROM organizations o"
            " LEFT JOIN subscriptions s ON s.organization_id = o.id LEFT JOIN subscription_plan_versions v ON v.id = s.plan_version_id"
            f" LEFT JOIN subscription_plans p ON p.id = v.plan_id {where} ORDER BY o.display_name LIMIT :cap"
        ),
        params,
    ).all()
    by_org = usage_for_orgs(ctx.conn, [o.id for o in orgs])
    items: list[OrgUsage] = []
    for o in orgs:
        rows = by_org[o.id]
        states = {r.status for r in rows if r.enforced_now}
        worst = "exceeded" if "exceeded" in states else ("approaching" if "approaching" in states else "ok")
        if attention and worst != attention:
            continue
        items.append(
            OrgUsage(
                organization_id=o.id,
                organization_name=o.display_name,
                organization_type=o.type,
                organization_status=o.status,
                plan_name=o.plan_name,
                subscription_status=o.sub_status,
                worst=worst,
                rows=rows,
            )
        )
    return Page[OrgUsage](items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


@router.get("/organizations/{org_id}/usage-history", response_model=UsageHistory, operation_id="platform_usage_history")
def usage_history(org_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.usage.read")]) -> UsageHistory:
    if not ctx.conn.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": org_id}).first():
        raise NotFound("Organization not found.")
    pts = ctx.conn.execute(
        text(
            "SELECT to_char(date_trunc('month', occurred_at), 'YYYY-MM') AS month, metric, sum(quantity)::bigint AS quantity"
            " FROM usage_records WHERE organization_id = :o GROUP BY 1, 2 ORDER BY 1 DESC, 2"
        ),
        {"o": org_id},
    ).mappings()
    points = [UsageHistoryPoint(**p) for p in pts]
    return UsageHistory(
        organization_id=org_id,
        points=points,
        note=""
        if points
        else "No metered usage has been recorded for this organization. Count limits (companies, users, devices) show current values only.",
    )


# ------------------------------------------------------------------ health
def _check_db(ctx: PlatformCtx) -> HealthCheck:
    t = time.perf_counter()
    ctx.conn.execute(text("SELECT 1"))
    ms = round((time.perf_counter() - t) * 1000, 1)
    return HealthCheck(
        name="database",
        status="ok" if ms < 500 else "degraded",
        detail="PostgreSQL answered a query as the platform role.",
        latency_ms=ms,
    )


def _check_redis() -> HealthCheck:
    s = get_settings()
    try:
        import redis

        t = time.perf_counter()
        redis.Redis.from_url(s.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
        return HealthCheck(
            name="redis",
            status="ok",
            detail="Redis answered PING (rate limiting and task broker).",
            latency_ms=round((time.perf_counter() - t) * 1000, 1),
        )
    except Exception as exc:  # health checks must never raise
        used = "required by the rate limiter" if s.rate_limit_backend == "redis" else "used by the task broker"
        return HealthCheck(name="redis", status="down", detail=f"Redis is unreachable ({type(exc).__name__}); {used}.")


@router.get("/health", response_model=Health, operation_id="platform_health")
def health(ctx: Annotated[PlatformCtx, padmin("platform.health.read")]) -> Health:
    s = get_settings()
    jobs = _job_stats(ctx)
    queue = HealthCheck(
        name="outbox_queue",
        status="ok",
        detail=f"{jobs.pending} pending, {jobs.failed} failed, {jobs.dispatched} dispatched.",
    )
    if jobs.failed:
        queue.status = "degraded"
    elif jobs.oldest_pending_seconds is not None and jobs.oldest_pending_seconds > 300:
        queue.status, queue.detail = (
            "degraded",
            queue.detail + f" Oldest pending event is {jobs.oldest_pending_seconds}s old; is the dispatcher running?",
        )
    checks = [
        HealthCheck(name="api", status="ok", detail="This API process is serving requests."),
        _check_db(ctx),
        _check_redis(),
        queue,
        HealthCheck(
            name="workers",
            status="unknown",
            detail="Worker heartbeats are not instrumented yet; only the queue backlog above is observable.",
        ),
        HealthCheck(
            name="object_storage", status="not_configured", detail="No object storage is integrated yet (Phase 6)."
        ),
        HealthCheck(
            name="backups",
            status="not_configured",
            detail="Backup status is not integrated; no backup system is reported on here.",
        ),
    ]
    live = [c.status for c in checks if c.status in ("ok", "degraded", "down")]
    overall = (
        "down"
        if "down" in live and any(c.name == "database" and c.status == "down" for c in checks)
        else ("degraded" if set(live) - {"ok"} else "ok")
    )
    try:
        version = metadata.version("artify-api")
    except metadata.PackageNotFoundError:
        version = "unknown"
    notes = {
        "local": "Local development environment: services run on the developer's machine.",
        "ci": "Automated test environment.",
        "staging": "Staging environment.",
        "production": "Production environment.",
    }
    return Health(
        generated_at=datetime.now(UTC),
        environment=s.environment,
        environment_note=notes[s.environment],
        version=version,
        overall=overall,
        uptime_seconds=int(time.time() - metrics.STARTED_AT),
        checks=checks,
        response_counts_since_start=metrics.snapshot(),
        monitoring_note="These checks run from this API process only. There is no external monitoring, alerting or on-call integration in Phase 2.",
    )


# ------------------------------------------------------------------ audit and activity
_AUDIT_SELECT = (
    "SELECT e.id, e.created_at, e.actor_id, a.display_name AS actor_name, e.action, e.target_type, e.target_id,"
    " e.target_organization, o.display_name AS organization_name, e.outcome, e.reason, e.summary, host(e.ip) AS ip,"
    " e.correlation_id FROM platform_audit_events e LEFT JOIN platform_admins a ON a.id = e.actor_id"
    " LEFT JOIN organizations o ON o.id = e.target_organization"
)


@router.get("/audit-events", response_model=Page[AuditOut], operation_id="platform_audit_events")
def audit_events(
    ctx: Annotated[PlatformCtx, padmin("platform.audit.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    actor_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    target_type: Annotated[str | None, Query(max_length=60)] = None,
    outcome: Literal["success", "failure", "denied"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AuditOut]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if pattern := like(q):
        where.append(
            "(e.action ILIKE :q ESCAPE '\\' OR e.target_id ILIKE :q ESCAPE '\\' OR e.reason ILIKE :q ESCAPE '\\')"
        )
        params["q"] = pattern
    if action:
        where.append("e.action LIKE :action ESCAPE '\\'")
        params["action"] = (like(action) or "").strip("%") + "%"
    for column, value, key in (
        ("e.actor_id", actor_id, "actor"),
        ("e.target_organization", organization_id, "org"),
        ("e.target_type", target_type, "tt"),
        ("e.outcome", outcome, "outcome"),
    ):
        if value:
            where.append(f"{column} = :{key}")
            params[key] = value
    if since:
        where.append("e.created_at >= :since")
        params["since"] = since
    if until:
        where.append("e.created_at < :until")
        params["until"] = until
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    total = ctx.conn.execute(text(f"SELECT count(*) FROM platform_audit_events e{clause}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(f"{_AUDIT_SELECT}{clause} ORDER BY e.created_at DESC, e.id DESC LIMIT :l OFFSET :o"),
        {**params, "l": limit, "o": offset},
    ).mappings()
    return Page[AuditOut](items=[AuditOut(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/audit-events/{event_id}", response_model=AuditOut, operation_id="platform_audit_event_get")
def audit_event(event_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.audit.read")]) -> AuditOut:
    r = ctx.conn.execute(text(f"{_AUDIT_SELECT} WHERE e.id = :i"), {"i": event_id}).mappings().first()
    if r is None:
        raise NotFound("Audit event not found.")
    return AuditOut(**r)


@router.get("/activity", response_model=list[ActivityItem], operation_id="platform_activity")
def activity(
    ctx: Annotated[PlatformCtx, padmin("platform.audit.read")], limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ActivityItem]:
    """One time-ordered feed of platform-level events: registrations, subscription changes, administrative
    actions and support requests."""
    rows = ctx.conn.execute(
        text(
            "SELECT * FROM ("
            " SELECT o.created_at AS at, 'registration' AS kind, o.display_name || ' registered (' || replace(o.type, '_', ' ') || ')' AS title,"
            "        o.id AS organization_id, o.display_name AS organization_name FROM organizations o"
            " UNION ALL SELECT e.created_at, 'subscription', replace(e.event_type, '_', ' ') || COALESCE(': ' || e.from_status || ' → ' || e.to_status, ''),"
            "        e.organization_id, o.display_name FROM subscription_events e JOIN organizations o ON o.id = e.organization_id"
            " UNION ALL SELECT p.created_at, 'admin', p.action || CASE WHEN p.outcome <> 'success' THEN ' (' || p.outcome || ')' ELSE '' END,"
            "        p.target_organization, o.display_name FROM platform_audit_events p LEFT JOIN organizations o ON o.id = p.target_organization"
            "        WHERE p.action NOT LIKE 'auth.%'"
            " UNION ALL SELECT g.created_at, 'support', 'Support access requested by ' || g.requester_name, g.organization_id, o.display_name"
            "        FROM support_access_grants g JOIN organizations o ON o.id = g.organization_id"
            ") t ORDER BY at DESC LIMIT :n"
        ),
        {"n": limit},
    ).mappings()
    return [ActivityItem(**r) for r in rows]


# ------------------------------------------------------------------ settings
_SPECS: dict[str, dict[str, Any]] = {
    "default_plan_key": {
        "description": "Plan assigned to new self-service sign-ups (must be an active plan).",
        "kind": "string",
        "default": "development_trial",
    },
    "trial_days": {
        "description": "Length of the trial given at sign-up, in days.",
        "kind": "integer",
        "default": 30,
        "min": 0,
        "max": 365,
    },
    "support.grant_max_hours": {
        "description": "Longest support-access grant an administrator may request, in hours.",
        "kind": "integer",
        "default": 72,
        "min": 1,
        "max": 720,
    },
    "billing.invoice_prefix": {
        "description": "Prefix of invoice numbers (2-6 capital letters).",
        "kind": "string",
        "default": "INV",
    },
}


@router.get("/settings", response_model=list[SettingOut], operation_id="platform_settings")
def list_settings(ctx: Annotated[PlatformCtx, padmin("platform.settings.read")]) -> list[SettingOut]:
    stored = {
        r.key: r
        for r in ctx.conn.execute(
            text("SELECT key, value #>> '{}' AS value, updated_at, updated_by FROM platform_settings")
        )
    }
    out: list[SettingOut] = []
    for key, spec in _SPECS.items():
        row = stored.get(key)
        raw = row.value if row else None
        value: int | str | None = int(raw) if (raw is not None and spec["kind"] == "integer") else raw
        out.append(
            SettingOut(
                key=key,
                description=spec["description"],
                kind=spec["kind"],
                value=value,
                default=spec["default"],
                minimum=spec.get("min"),
                maximum=spec.get("max"),
                updated_at=row.updated_at if row else None,
                updated_by=row.updated_by if row else None,
            )
        )
    return out


@router.put("/settings/{key}", response_model=SettingOut, operation_id="platform_setting_update")
def update_setting(
    key: str, body: SettingUpdate, ctx: Annotated[PlatformCtx, padmin("platform.settings.manage")]
) -> SettingOut:
    import json
    import re

    spec = _SPECS.get(key)
    if spec is None:
        raise NotFound("Unknown setting.")
    value: int | str = body.value
    if spec["kind"] == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or not (spec["min"] <= value <= spec["max"]):
            raise Unprocessable(f"{key} must be an integer between {spec['min']} and {spec['max']}.")
    else:
        if not isinstance(value, str):
            raise Unprocessable(f"{key} must be text.")
        if (
            key == "default_plan_key"
            and not ctx.conn.execute(
                text("SELECT 1 FROM subscription_plans WHERE key = :k AND status = 'active'"), {"k": value}
            ).first()
        ):
            raise Unprocessable("The default plan must be an existing, active plan.")
        if key == "billing.invoice_prefix" and not re.fullmatch(r"[A-Z]{2,6}", value):
            raise Unprocessable("The invoice prefix must be 2-6 capital letters.")
    old = ctx.conn.execute(text("SELECT value #>> '{}' FROM platform_settings WHERE key = :k"), {"k": key}).scalar()
    ctx.conn.execute(
        text(
            "INSERT INTO platform_settings (key, value, updated_at, updated_by) VALUES (:k, CAST(:v AS jsonb), now(), :a)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = :a"
        ),
        {"k": key, "v": json.dumps(value), "a": ctx.admin_id},
    )
    paudit(
        ctx,
        "settings.updated",
        target_type="setting",
        target_id=key,
        reason=body.reason,
        summary={"from": old, "to": str(value)},
    )
    return next(s for s in list_settings(ctx) if s.key == key)
