"""Super Admin: subscription plans, immutable plan versions and the entitlement catalog.

Limits are data, never constants. Editing a plan publishes a NEW immutable version; subscriptions stay
pinned to the version they use until an administrator moves them (grandfathering). A database trigger
rejects any change to a version that a subscription uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from artify_api.context import PlatformCtx
from artify_api.entitlements import ENFORCED
from artify_api.errors import Conflict, NotFound, Unprocessable
from artify_api.ids import new_id
from artify_api.routers.platform_common import Sensitive, padmin, paudit, require_confirmation

router = APIRouter(prefix="/platform", tags=["platform-plans"])


class EntitlementValue(BaseModel):
    """Exactly one of: a number (`value`), `unlimited`, or a boolean (`enabled`) for feature flags."""

    value: int | None = Field(default=None, ge=0)
    unlimited: bool = False
    enabled: bool | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> EntitlementValue:
        given = int(self.value is not None) + int(self.unlimited) + int(self.enabled is not None)
        if given != 1:
            raise ValueError("provide exactly one of value, unlimited or enabled")
        return self


class VersionSpec(BaseModel):
    billing_interval: Literal["monthly", "annual", "none"] = "monthly"
    price_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=3)
    price_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    support_level: Literal["community", "standard", "priority", "dedicated"] = "standard"
    trial_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=1000)
    entitlements: dict[str, EntitlementValue]

    @model_validator(mode="after")
    def _price_pair(self) -> VersionSpec:
        if (self.price_amount is None) != (self.price_currency is None):
            raise ValueError("price_amount and price_currency must be given together")
        return self


class PlanCreate(BaseModel):
    key: str = Field(pattern="^[a-z0-9_]{2,40}$")
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    version: VersionSpec


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class VersionOut(BaseModel):
    id: uuid.UUID
    version: int
    billing_interval: str
    price_amount: Decimal | None
    price_currency: str | None
    support_level: str
    trial_days: int | None
    notes: str | None
    created_at: datetime
    subscriptions: int
    entitlements: dict[str, EntitlementValue]


class PlanRow(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    status: Literal["active", "inactive", "retired"]
    created_at: datetime
    latest_version: int
    latest_version_id: uuid.UUID
    versions: int
    subscriptions: int
    price_amount: Decimal | None
    price_currency: str | None
    billing_interval: str
    support_level: str
    trial_days: int | None
    is_default: bool


class PlanDetail(BaseModel):
    plan: PlanRow
    versions: list[VersionOut]


class CatalogEntry(BaseModel):
    key: str
    scope: str
    value_type: str
    enforcement: str
    unit: str | None
    description: str
    enforced_now: bool


class CompareColumn(BaseModel):
    plan_id: uuid.UUID
    key: str
    name: str
    version: int
    price_amount: Decimal | None
    price_currency: str | None
    billing_interval: str
    support_level: str
    trial_days: int | None


class CompareRow(BaseModel):
    key: str
    description: str
    unit: str | None
    enforced_now: bool
    values: list[EntitlementValue | None]


class Comparison(BaseModel):
    plans: list[CompareColumn]
    rows: list[CompareRow]


_PLAN_SELECT = """
SELECT p.id, p.key, p.name, p.description, p.status, p.created_at,
       lv.version AS latest_version, lv.id AS latest_version_id,
       (SELECT count(*) FROM subscription_plan_versions x WHERE x.plan_id = p.id) AS versions,
       (SELECT count(*) FROM subscriptions s JOIN subscription_plan_versions x ON x.id = s.plan_version_id
         WHERE x.plan_id = p.id) AS subscriptions,
       lv.price_amount, lv.price_currency, lv.billing_interval, lv.support_level, lv.trial_days,
       (p.key = (SELECT value #>> '{}' FROM platform_settings WHERE key = 'default_plan_key')) AS is_default
  FROM subscription_plans p
  JOIN LATERAL (SELECT * FROM subscription_plan_versions v WHERE v.plan_id = p.id ORDER BY v.version DESC LIMIT 1) lv ON true
"""


def _definitions(ctx: PlatformCtx) -> dict[str, str]:
    return {r.key: r.value_type for r in ctx.conn.execute(text("SELECT key, value_type FROM entitlement_definitions"))}


def _validate_entitlements(ctx: PlatformCtx, spec: VersionSpec) -> None:
    defs = _definitions(ctx)
    for key, v in spec.entitlements.items():
        kind = defs.get(key)
        if kind is None:
            raise Unprocessable(f"Unknown entitlement “{key}”.")
        if kind == "bool" and v.enabled is None:
            raise Unprocessable(f"“{key}” is a feature flag: provide enabled true/false.")
        if kind != "bool" and v.enabled is not None:
            raise Unprocessable(f"“{key}” is numeric: provide value or unlimited.")
    if (
        spec.price_currency
        and not ctx.conn.execute(text("SELECT 1 FROM currencies WHERE code = :c"), {"c": spec.price_currency}).first()
    ):
        raise Unprocessable("Unknown currency code.")


def _insert_version(ctx: PlatformCtx, plan_id: uuid.UUID, version: int, spec: VersionSpec) -> uuid.UUID:
    _validate_entitlements(ctx, spec)
    vid = new_id()
    ctx.conn.execute(
        text(
            "INSERT INTO subscription_plan_versions (id, plan_id, version, billing_interval, price_amount, price_currency,"
            " support_level, trial_days, notes, created_by) VALUES (:id, :p, :v, :bi, :pa, :pc, :sl, :td, :n, :by)"
        ),
        {
            "id": vid,
            "p": plan_id,
            "v": version,
            "bi": spec.billing_interval,
            "pa": spec.price_amount,
            "pc": spec.price_currency,
            "sl": spec.support_level,
            "td": spec.trial_days,
            "n": spec.notes,
            "by": ctx.admin_id,
        },
    )
    for key, v in spec.entitlements.items():
        ctx.conn.execute(
            text(
                "INSERT INTO plan_entitlements (plan_version_id, entitlement_key, int_value, bool_value, unlimited)"
                " VALUES (:v, :k, :i, :b, :u)"
            ),
            {"v": vid, "k": key, "i": v.value, "b": v.enabled, "u": v.unlimited},
        )
    return vid


def _plan(ctx: PlatformCtx, plan_id: uuid.UUID) -> PlanRow:
    r = ctx.conn.execute(text(f"{_PLAN_SELECT} WHERE p.id = :i"), {"i": plan_id}).mappings().first()
    if r is None:
        raise NotFound("Plan not found.")
    return PlanRow(**r)


def _versions(ctx: PlatformCtx, plan_id: uuid.UUID) -> list[VersionOut]:
    out: list[VersionOut] = []
    for v in ctx.conn.execute(
        text(
            "SELECT v.*, (SELECT count(*) FROM subscriptions s WHERE s.plan_version_id = v.id) AS subs"
            " FROM subscription_plan_versions v WHERE v.plan_id = :p ORDER BY v.version DESC"
        ),
        {"p": plan_id},
    ).mappings():
        ents = {
            e.entitlement_key: EntitlementValue(value=e.int_value, unlimited=bool(e.unlimited), enabled=e.bool_value)
            for e in ctx.conn.execute(
                text("SELECT * FROM plan_entitlements WHERE plan_version_id = :v ORDER BY entitlement_key"),
                {"v": v["id"]},
            )
        }
        out.append(
            VersionOut(
                id=v["id"],
                version=v["version"],
                billing_interval=v["billing_interval"],
                price_amount=v["price_amount"],
                price_currency=v["price_currency"],
                support_level=v["support_level"],
                trial_days=v["trial_days"],
                notes=v["notes"],
                created_at=v["created_at"],
                subscriptions=v["subs"],
                entitlements=ents,
            )
        )
    return out


@router.get("/entitlements", response_model=list[CatalogEntry], operation_id="platform_entitlement_catalog")
def entitlement_catalog(ctx: Annotated[PlatformCtx, padmin("platform.plans.read")]) -> list[CatalogEntry]:
    """Every key plans can configure. `enforced_now` is false for keys whose feature does not exist yet:
    the value can be set today and starts being enforced when the feature ships."""
    rows = ctx.conn.execute(text("SELECT * FROM entitlement_definitions ORDER BY key")).mappings()
    return [CatalogEntry(**r, enforced_now=r["key"] in ENFORCED) for r in rows]


@router.get("/plans", response_model=list[PlanRow], operation_id="platform_plans")
def list_plans(
    ctx: Annotated[PlatformCtx, padmin("platform.plans.read")],
    status: Literal["active", "inactive", "retired"] | None = None,
) -> list[PlanRow]:
    clause = " WHERE p.status = :s" if status else ""
    rows = ctx.conn.execute(text(f"{_PLAN_SELECT}{clause} ORDER BY p.created_at"), {"s": status}).mappings()
    return [PlanRow(**r) for r in rows]


@router.get("/plans/compare", response_model=Comparison, operation_id="platform_plans_compare")
def compare_plans(
    ctx: Annotated[PlatformCtx, padmin("platform.plans.read")],
    plan_id: Annotated[list[uuid.UUID], Query(min_length=2, max_length=5)],
) -> Comparison:
    cols: list[CompareColumn] = []
    values: list[dict[str, EntitlementValue]] = []
    for pid in plan_id:
        plan = _plan(ctx, pid)
        latest = _versions(ctx, pid)[0]
        cols.append(
            CompareColumn(
                plan_id=pid,
                key=plan.key,
                name=plan.name,
                version=latest.version,
                price_amount=latest.price_amount,
                price_currency=latest.price_currency,
                billing_interval=latest.billing_interval,
                support_level=latest.support_level,
                trial_days=latest.trial_days,
            )
        )
        values.append(latest.entitlements)
    rows = [
        CompareRow(
            key=d.key,
            description=d.description,
            unit=d.unit,
            enforced_now=d.key in ENFORCED,
            values=[v.get(d.key) for v in values],
        )
        for d in ctx.conn.execute(text("SELECT key, description, unit FROM entitlement_definitions ORDER BY key"))
    ]
    return Comparison(plans=cols, rows=rows)


@router.get("/plans/{plan_id}", response_model=PlanDetail, operation_id="platform_plan_get")
def get_plan(plan_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.plans.read")]) -> PlanDetail:
    return PlanDetail(plan=_plan(ctx, plan_id), versions=_versions(ctx, plan_id))


@router.post("/plans", response_model=PlanDetail, status_code=201, operation_id="platform_plan_create")
def create_plan(body: PlanCreate, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]) -> PlanDetail:
    plan_id = new_id()
    try:
        ctx.conn.execute(
            text(
                "INSERT INTO subscription_plans (id, key, name, description, status) VALUES (:i, :k, :n, :d, 'inactive')"
            ),
            {"i": plan_id, "k": body.key, "n": body.name, "d": body.description},
        )
    except IntegrityError as exc:
        raise Conflict("A plan with this key already exists.") from exc
    _insert_version(ctx, plan_id, 1, body.version)
    paudit(ctx, "plan.created", target_type="plan", target_id=plan_id, summary={"key": body.key})
    return get_plan(plan_id, ctx)


@router.patch("/plans/{plan_id}", response_model=PlanRow, operation_id="platform_plan_update")
def update_plan(
    plan_id: uuid.UUID, body: PlanUpdate, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]
) -> PlanRow:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise Unprocessable("Provide name and/or description.")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    res = ctx.conn.execute(text(f"UPDATE subscription_plans SET {sets} WHERE id = :id"), {**changes, "id": plan_id})
    if not res.rowcount:
        raise NotFound("Plan not found.")
    paudit(ctx, "plan.updated", target_type="plan", target_id=plan_id, summary={"fields": sorted(changes)})
    return _plan(ctx, plan_id)


@router.post(
    "/plans/{plan_id}/versions", response_model=PlanDetail, status_code=201, operation_id="platform_plan_new_version"
)
def new_version(
    plan_id: uuid.UUID, body: VersionSpec, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]
) -> PlanDetail:
    """Publish a new immutable version. Existing subscriptions keep the version they use."""
    plan = _plan(ctx, plan_id)
    if plan.status == "retired":
        raise Conflict("An archived plan cannot receive new versions.")
    _insert_version(ctx, plan_id, plan.latest_version + 1, body)
    paudit(
        ctx,
        "plan.version_published",
        target_type="plan",
        target_id=plan_id,
        summary={"key": plan.key, "version": plan.latest_version + 1},
    )
    return get_plan(plan_id, ctx)


def _set_status(
    ctx: PlatformCtx, plan_id: uuid.UUID, to: str, allowed: tuple[str, ...], action: str, reason: str | None = None
) -> PlanRow:
    plan = _plan(ctx, plan_id)
    if plan.status not in allowed:
        raise Conflict(f"A {plan.status} plan cannot be changed this way.")
    if to != "active" and plan.is_default:
        raise Conflict("This is the default plan for new sign-ups; choose another default in Platform settings first.")
    ctx.conn.execute(text("UPDATE subscription_plans SET status = :s WHERE id = :i"), {"s": to, "i": plan_id})
    paudit(
        ctx,
        action,
        target_type="plan",
        target_id=plan_id,
        summary={"key": plan.key, "from": plan.status, "to": to},
        reason=reason,
    )
    return _plan(ctx, plan_id)


@router.post("/plans/{plan_id}/activate", response_model=PlanRow, operation_id="platform_plan_activate")
def activate_plan(plan_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]) -> PlanRow:
    return _set_status(ctx, plan_id, "active", ("inactive",), "plan.activated")


@router.post("/plans/{plan_id}/deactivate", response_model=PlanRow, operation_id="platform_plan_deactivate")
def deactivate_plan(plan_id: uuid.UUID, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]) -> PlanRow:
    """A deactivated plan cannot be assigned to new subscriptions; existing ones are unaffected."""
    return _set_status(ctx, plan_id, "inactive", ("active",), "plan.deactivated")


@router.post("/plans/{plan_id}/archive", response_model=PlanRow, operation_id="platform_plan_archive")
def archive_plan(
    plan_id: uuid.UUID, body: Sensitive, ctx: Annotated[PlatformCtx, padmin("platform.plans.manage")]
) -> PlanRow:
    plan = _plan(ctx, plan_id)
    require_confirmation(body.confirm, plan.key)
    return _set_status(ctx, plan_id, "retired", ("active", "inactive"), "plan.archived", body.reason)
