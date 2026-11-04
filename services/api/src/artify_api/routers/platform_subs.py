"""Super Admin: subscriptions, plan changes, status transitions, history and limit overrides.

Safety rules (SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md):
- a plan change never deletes anything; moving below current usage needs explicit acknowledgement and
  leaves the organization "over limit" (existing data stays, further creation is blocked by the backend);
- every transition is recorded in `subscription_events` (append-only) and the platform audit;
- cancelled / expired / suspended subscriptions block creation in the tenant API (EntitlementService).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from artify_api.context import PlatformCtx
from artify_api.entitlements import MEASURABLE
from artify_api.errors import ApiError, Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.platform_usage import SUBSCRIPTION_SELECT, SubscriptionOut, fetch_subscription
from artify_api.routers.platform_common import (
    Limit,
    Offset,
    Page,
    Reasoned,
    Sensitive,
    like,
    order_clause,
    padmin,
    paudit,
    require_confirmation,
)
from artify_api.routers.platform_plans import EntitlementValue

router = APIRouter(prefix="/platform", tags=["platform-subscriptions"])


class OverLimit(ApiError):
    status, code, title = 409, "over_limit", "The new plan is below current usage"


class SubRow(SubscriptionOut):
    organization_slug: str
    latest_invoice_status: str | None = None


class EventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    from_plan_version_id: uuid.UUID | None
    to_plan_version_id: uuid.UUID | None
    reason: str | None
    actor_type: str
    actor_id: uuid.UUID | None
    details: dict[str, Any]
    created_at: datetime


class OverrideOut(BaseModel):
    id: uuid.UUID
    entitlement_key: str
    value: EntitlementValue
    reason: str
    expires_at: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime
    active: bool


class SubDetail(BaseModel):
    subscription: SubRow
    events: list[EventOut]
    overrides: list[OverrideOut]


class ChangePlan(BaseModel):
    plan_version_id: uuid.UUID
    reason: str = Field(min_length=10, max_length=500)
    acknowledge_over_limit: bool = False


class Extend(BaseModel):
    days: int = Field(ge=1, le=365)
    reason: str = Field(min_length=10, max_length=500)


class SetStatus(Sensitive):
    status: Literal["active", "past_due", "cancelled", "expired"]


class CancelAtPeriodEnd(BaseModel):
    enabled: bool
    reason: str = Field(min_length=10, max_length=500)


class OverrideCreate(BaseModel):
    entitlement_key: str
    value: EntitlementValue
    reason: str = Field(min_length=10, max_length=500)
    expires_at: datetime | None = None


_TRANSITIONS: dict[str, set[str]] = {
    "trialing": {"active", "expired", "cancelled"},
    "active": {"past_due", "cancelled", "expired"},
    "past_due": {"active", "cancelled", "expired"},
    "cancelled": {"active"},
    "expired": {"active"},
}
_SORT = {
    "organization": "organization_name",
    "status": "status",
    "created": "created_at",
    "renewal": "current_period_end",
    "plan": "plan_name",
}


def _row(ctx: PlatformCtx, sub_id: uuid.UUID) -> SubRow:
    r = (
        ctx.conn.execute(
            text(
                f"SELECT t.*, o.slug::text AS organization_slug, (SELECT i.status FROM billing_invoices i"
                f" WHERE i.organization_id = t.organization_id ORDER BY i.created_at DESC LIMIT 1) AS latest_invoice_status"
                f" FROM ({SUBSCRIPTION_SELECT} WHERE s.id = :i) t JOIN organizations o ON o.id = t.organization_id"
            ),
            {"i": sub_id},
        )
        .mappings()
        .first()
    )
    if r is None:
        raise NotFound("Subscription not found.")
    return SubRow(**r)


def _event(
    ctx: PlatformCtx,
    sub: SubRow,
    event_type: str,
    *,
    reason: str,
    to_status: str | None = None,
    to_version: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    import json

    ctx.conn.execute(
        text(
            "INSERT INTO subscription_events (id, subscription_id, organization_id, event_type, from_status, to_status,"
            " from_plan_version_id, to_plan_version_id, reason, actor_type, actor_id, details)"
            " VALUES (:id, :s, :o, :t, :fs, :ts, :fv, :tv, :r, 'platform_admin', :a, CAST(:d AS jsonb))"
        ),
        {
            "id": new_id(),
            "s": sub.id,
            "o": sub.organization_id,
            "t": event_type,
            "fs": sub.status,
            "ts": to_status,
            "fv": sub.plan_version_id,
            "tv": to_version,
            "r": reason,
            "a": ctx.admin_id,
            "d": json.dumps(details or {}),
        },
    )


@router.get("/subscriptions", response_model=Page[SubRow], operation_id="platform_subscriptions")
def list_subscriptions(
    ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.read")],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["trialing", "active", "past_due", "suspended", "cancelled", "expired"] | None = None,
    plan: Annotated[str | None, Query(max_length=60)] = None,
    sort: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[SubRow]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if pattern := like(q):
        where.append("(t.organization_name ILIKE :q ESCAPE '\\' OR t.organization_slug ILIKE :q ESCAPE '\\')")
        params["q"] = pattern
    if status:
        where.append("t.status = :status")
        params["status"] = status
    if plan:
        where.append("t.plan_key = :plan")
        params["plan"] = plan
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    base = (
        f"SELECT x.*, o.slug::text AS organization_slug, (SELECT i.status FROM billing_invoices i"
        f" WHERE i.organization_id = x.organization_id ORDER BY i.created_at DESC LIMIT 1) AS latest_invoice_status"
        f" FROM ({SUBSCRIPTION_SELECT}) x JOIN organizations o ON o.id = x.organization_id"
    )
    total = ctx.conn.execute(text(f"SELECT count(*) FROM ({base}) t{clause}"), params).scalar_one()
    rows = ctx.conn.execute(
        text(
            f"SELECT * FROM ({base}) t{clause} ORDER BY {order_clause(sort, order, _SORT, 'created')} LIMIT :l OFFSET :o"
        ),
        {**params, "l": limit, "o": offset},
    ).mappings()
    return Page[SubRow](items=[SubRow(**r) for r in rows], total=total, limit=limit, offset=offset)


def _overrides(ctx: PlatformCtx, org_id: uuid.UUID) -> list[OverrideOut]:
    rows = ctx.conn.execute(
        text(
            "SELECT *, (expires_at IS NULL OR expires_at > now()) AS active FROM entitlement_overrides"
            " WHERE organization_id = :o ORDER BY created_at DESC"
        ),
        {"o": org_id},
    ).mappings()
    return [
        OverrideOut(
            id=r["id"],
            entitlement_key=r["entitlement_key"],
            reason=r["reason"],
            expires_at=r["expires_at"],
            created_by=r["created_by"],
            created_at=r["created_at"],
            active=r["active"],
            value=EntitlementValue(value=r["int_value"], unlimited=r["unlimited"], enabled=r["bool_value"]),
        )
        for r in rows
    ]


@router.get("/subscriptions/{sub_id}", response_model=SubDetail, operation_id="platform_subscription_get")
def get_subscription(
    sub_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.read")]
) -> SubDetail:
    sub = _row(ctx, sub_id)
    events = ctx.conn.execute(
        text("SELECT * FROM subscription_events WHERE subscription_id = :s ORDER BY created_at DESC, id DESC"),
        {"s": sub_id},
    ).mappings()
    return SubDetail(
        subscription=sub, events=[EventOut(**e) for e in events], overrides=_overrides(ctx, sub.organization_id)
    )


def _violations(ctx: PlatformCtx, org_id: uuid.UUID, version_id: uuid.UUID) -> list[dict[str, Any]]:
    """Enforceable count limits the organization currently exceeds under the target version
    (an unexpired override for the same key takes precedence and is respected)."""
    out: list[dict[str, Any]] = []
    for key in sorted(MEASURABLE):
        cur = (
            ctx.conn.execute(
                text(
                    "SELECT value FROM usage_counters WHERE organization_id = :o AND metric = :k AND scope_key = 'org' AND period_key = 'all'"
                ),
                {"o": org_id, "k": key},
            ).scalar()
            or 0
        )
        override = ctx.conn.execute(
            text(
                "SELECT 1 FROM entitlement_overrides WHERE organization_id = :o AND entitlement_key = :k AND (expires_at IS NULL OR expires_at > now())"
            ),
            {"o": org_id, "k": key},
        ).first()
        pe = ctx.conn.execute(
            text(
                "SELECT int_value, unlimited FROM plan_entitlements WHERE plan_version_id = :v AND entitlement_key = :k"
            ),
            {"v": version_id, "k": key},
        ).first()
        limit = 0 if pe is None else (None if pe.unlimited else pe.int_value)
        if override is None and limit is not None and cur > limit:
            out.append({"key": key, "limit": limit, "current": int(cur)})
    return out


@router.post(
    "/subscriptions/{sub_id}/change-plan", response_model=SubRow, operation_id="platform_subscription_change_plan"
)
def change_plan(
    sub_id: uuid.UUID, body: ChangePlan, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    if sub.status in ("cancelled", "expired"):
        raise Conflict(f"A {sub.status} subscription must be reinstated before its plan is changed.")
    target = ctx.conn.execute(
        text(
            "SELECT v.id, v.price_amount, v.price_currency, p.status, p.key FROM subscription_plan_versions v"
            " JOIN subscription_plans p ON p.id = v.plan_id WHERE v.id = :v"
        ),
        {"v": body.plan_version_id},
    ).first()
    if target is None:
        raise NotFound("Plan version not found.")
    if target.status != "active":
        raise Unprocessable("Only versions of an active plan can be assigned.")
    if target.id == sub.plan_version_id:
        raise Conflict("The subscription already uses this plan version.")
    violations = _violations(ctx, sub.organization_id, body.plan_version_id)
    if violations and not body.acknowledge_over_limit:
        raise OverLimit(
            "The organization currently uses more than this plan allows. Existing data is kept and further "
            "creation is blocked until usage falls below the limit. Re-send with acknowledge_over_limit to proceed.",
            extra={"violations": violations},
        )
    current = ctx.conn.execute(
        text("SELECT price_amount, price_currency FROM subscription_plan_versions WHERE id = :v"),
        {"v": sub.plan_version_id},
    ).first()
    direction = "change"
    if (
        current
        and current.price_currency == target.price_currency
        and current.price_amount is not None
        and target.price_amount is not None
    ):
        direction = (
            "upgrade"
            if target.price_amount > current.price_amount
            else ("downgrade" if target.price_amount < current.price_amount else "change")
        )
    ctx.conn.execute(
        text("UPDATE subscriptions SET plan_version_id = :v, updated_at = now() WHERE id = :s"),
        {"v": body.plan_version_id, "s": sub_id},
    )
    _event(
        ctx,
        sub,
        "plan_changed",
        reason=body.reason,
        to_version=body.plan_version_id,
        details={"direction": direction, "over_limit": violations, "to_plan": target.key},
    )
    paudit(
        ctx,
        "subscription.plan_changed",
        target_type="subscription",
        target_id=sub_id,
        org=sub.organization_id,
        reason=body.reason,
        summary={"to_plan": target.key, "direction": direction, "over_limit_keys": [v["key"] for v in violations]},
    )
    return _row(ctx, sub_id)


@router.post("/subscriptions/{sub_id}/extend", response_model=SubRow, operation_id="platform_subscription_extend")
def extend(
    sub_id: uuid.UUID, body: Extend, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    if sub.status not in ("trialing", "active", "past_due"):
        raise Conflict(f"A {sub.status} subscription cannot be extended; change its status first.")
    column = "trial_ends_at" if sub.status == "trialing" else "current_period_end"
    ctx.conn.execute(
        text(
            f"UPDATE subscriptions SET {column} = GREATEST(COALESCE({column}, now()), now()) + make_interval(days => :d),"
            " updated_at = now() WHERE id = :s"
        ),
        {"d": body.days, "s": sub_id},
    )
    after = _row(ctx, sub_id)
    _event(
        ctx,
        sub,
        "extended",
        reason=body.reason,
        details={
            "days": body.days,
            "field": column,
            "from": str(getattr(sub, column)),
            "to": str(getattr(after, column)),
        },
    )
    paudit(
        ctx,
        "subscription.extended",
        target_type="subscription",
        target_id=sub_id,
        org=sub.organization_id,
        reason=body.reason,
        summary={"days": body.days, "field": column},
    )
    return after


def _move(ctx: PlatformCtx, sub: SubRow, to: str, event: str, action: str, reason: str, extra: str = "") -> SubRow:
    cancelled = ", cancelled_at = now()" if to == "cancelled" else (", cancelled_at = NULL" if to == "active" else "")
    ctx.conn.execute(
        text(f"UPDATE subscriptions SET status = :t, updated_at = now(){cancelled} WHERE id = :s"),
        {"t": to, "s": sub.id},
    )
    _event(ctx, sub, event, reason=reason, to_status=to)
    paudit(
        ctx,
        action,
        target_type="subscription",
        target_id=sub.id,
        org=sub.organization_id,
        reason=reason,
        summary={"from": sub.status, "to": to},
    )
    return _row(ctx, sub.id)


@router.post("/subscriptions/{sub_id}/suspend", response_model=SubRow, operation_id="platform_subscription_suspend")
def suspend(
    sub_id: uuid.UUID, body: Sensitive, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    require_confirmation(body.confirm, sub.organization_slug)
    if sub.status not in ("trialing", "active", "past_due"):
        raise Conflict(f"A {sub.status} subscription cannot be suspended.")
    return _move(ctx, sub, "suspended", "status_changed", "subscription.suspended", body.reason)


@router.post(
    "/subscriptions/{sub_id}/reactivate", response_model=SubRow, operation_id="platform_subscription_reactivate"
)
def reactivate(
    sub_id: uuid.UUID, body: Reasoned, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    if sub.status != "suspended":
        raise Conflict("Only a suspended subscription can be reactivated.")
    previous = (
        ctx.conn.execute(
            text(
                "SELECT from_status FROM subscription_events WHERE subscription_id = :s AND to_status = 'suspended'"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"s": sub_id},
        ).scalar()
        or "active"
    )
    target = previous
    if (
        previous == "trialing"
        and sub.trial_ends_at is not None
        and sub.trial_ends_at <= datetime.now(sub.trial_ends_at.tzinfo)
    ):
        target = "expired"  # the trial ended while suspended; it does not silently resume
    return _move(ctx, sub, target, "status_changed", "subscription.reactivated", body.reason)


@router.post("/subscriptions/{sub_id}/status", response_model=SubRow, operation_id="platform_subscription_set_status")
def set_status(
    sub_id: uuid.UUID, body: SetStatus, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    require_confirmation(body.confirm, sub.organization_slug)
    if body.status not in _TRANSITIONS.get(sub.status, set()):
        raise Conflict(f"A {sub.status} subscription cannot move to {body.status}.")
    return _move(ctx, sub, body.status, "status_changed", f"subscription.{body.status}", body.reason)


@router.post(
    "/subscriptions/{sub_id}/cancel-at-period-end",
    response_model=SubRow,
    operation_id="platform_subscription_cancel_at_period_end",
)
def cancel_at_period_end(
    sub_id: uuid.UUID, body: CancelAtPeriodEnd, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.manage")]
) -> SubRow:
    sub = _row(ctx, sub_id)
    if sub.status not in ("trialing", "active", "past_due"):
        raise Conflict(f"A {sub.status} subscription cannot be scheduled for cancellation.")
    ctx.conn.execute(
        text("UPDATE subscriptions SET cancel_at_period_end = :e, updated_at = now() WHERE id = :s"),
        {"e": body.enabled, "s": sub_id},
    )
    _event(ctx, sub, "cancel_at_period_end_set" if body.enabled else "cancel_at_period_end_cleared", reason=body.reason)
    paudit(
        ctx,
        "subscription.cancel_at_period_end",
        target_type="subscription",
        target_id=sub_id,
        org=sub.organization_id,
        reason=body.reason,
        summary={"enabled": body.enabled},
    )
    return _row(ctx, sub_id)


# ------------------------------------------------------------------ limit overrides (support-negotiated exceptions)
@router.get("/organizations/{org_id}/overrides", response_model=list[OverrideOut], operation_id="platform_overrides")
def list_overrides(
    org_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.subscriptions.read")]
) -> list[OverrideOut]:
    return _overrides(ctx, org_id)


@router.post(
    "/organizations/{org_id}/overrides",
    response_model=OverrideOut,
    status_code=201,
    operation_id="platform_override_create",
)
def create_override(
    org_id: uuid.UUID, body: OverrideCreate, ctx: Annotated[PlatformCtx, padmin("platform.entitlements.override")]
) -> OverrideOut:
    sub = fetch_subscription(ctx.conn, org_id=org_id)
    if sub is None:
        raise NotFound("The organization has no subscription.")
    kind = ctx.conn.execute(
        text("SELECT value_type FROM entitlement_definitions WHERE key = :k"), {"k": body.entitlement_key}
    ).scalar()
    if kind is None:
        raise Unprocessable(f"Unknown entitlement “{body.entitlement_key}”.")
    if (kind == "bool") != (body.value.enabled is not None):
        raise Unprocessable("Use enabled for feature flags and value/unlimited for numeric limits.")
    if body.expires_at is not None and body.expires_at <= datetime.now(body.expires_at.tzinfo):
        raise Unprocessable("expires_at must be in the future.")
    oid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO entitlement_overrides (id, organization_id, subscription_id, entitlement_key, int_value, bool_value,"
            " unlimited, reason, expires_at, created_by) VALUES (:id, :o, :s, :k, :i, :b, :u, :r, :e, :a)"
        ),
        {
            "id": oid,
            "o": org_id,
            "s": sub["id"],
            "k": body.entitlement_key,
            "i": body.value.value,
            "b": body.value.enabled,
            "u": body.value.unlimited,
            "r": body.reason,
            "e": body.expires_at,
            "a": ctx.admin_id,
        },
    )
    row = _row(ctx, sub["id"])
    _event(
        ctx,
        row,
        "override_added",
        reason=body.reason,
        details={"key": body.entitlement_key, "expires_at": str(body.expires_at)},
    )
    paudit(
        ctx,
        "entitlement.override_added",
        target_type="organization",
        target_id=org_id,
        org=org_id,
        reason=body.reason,
        summary={
            "key": body.entitlement_key,
            "unlimited": body.value.unlimited,
            "value": body.value.value,
            "enabled": body.value.enabled,
            "expires_at": str(body.expires_at),
        },
    )
    return next(o for o in _overrides(ctx, org_id) if o.id == oid)


@router.post(
    "/organizations/{org_id}/overrides/{override_id}/expire",
    response_model=OverrideOut,
    operation_id="platform_override_expire",
)
def expire_override(
    org_id: uuid.UUID,
    override_id: uuid.UUID,
    body: Reasoned,
    ctx: Annotated[PlatformCtx, padmin("platform.entitlements.override")],
) -> OverrideOut:
    res = ctx.conn.execute(
        text(
            "UPDATE entitlement_overrides SET expires_at = now() WHERE id = :i AND organization_id = :o AND (expires_at IS NULL OR expires_at > now()) RETURNING subscription_id, entitlement_key"
        ),
        {"i": override_id, "o": org_id},
    ).first()
    if res is None:
        raise NotFound("Active override not found.")
    row = _row(ctx, res.subscription_id)
    _event(ctx, row, "override_expired", reason=body.reason, details={"key": res.entitlement_key})
    paudit(
        ctx,
        "entitlement.override_expired",
        target_type="organization",
        target_id=org_id,
        org=org_id,
        reason=body.reason,
        summary={"key": res.entitlement_key},
    )
    return next(o for o in _overrides(ctx, org_id) if o.id == override_id)
