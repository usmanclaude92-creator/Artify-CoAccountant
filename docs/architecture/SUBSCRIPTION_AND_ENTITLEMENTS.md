# Subscription and Entitlements

Limits are **data configured in Super Admin**, never constants in frontend or backend code (ADR-012).

## 1. Data model (summary; detail in `ENTITY_CATALOG.md`)

| Table | Role |
|---|---|
| `subscription_plans` | Plan identity: key, name, status, billing interval |
| `subscription_plan_versions` | Immutable versions of a plan (price display data; billing provider is D-2). Editing a plan creates a new version |
| `plan_entitlements` | `(plan_version_id, entitlement_key, value)`; value typed (`int`, `bigint`, `bool`, `decimal`) with `NULL`/`-1` semantics for "unlimited" defined by the catalog |
| `subscriptions` | Organization ↔ pinned `plan_version_id`, status (`trialing`, `active`, `past_due`, `suspended`, `cancelled`), period dates |
| `entitlement_overrides` | Per-subscription override of any key, with reason, author, expiry (support-negotiated exceptions) |
| `usage_counters` | Current measured values (e.g. companies in use) per Organization and, where relevant, per Company, per period |
| `usage_records` | Append-only metering events (AI units, documents processed, storage deltas) feeding counters and Super Admin dashboards |
| `entitlement_definitions` (reference) | Catalog of keys: type, scope (`organization` | `company`), unit, enforcement mode, user-facing message key |

Effective limit = override (if unexpired) else plan value else catalog default (deny).

## 2. Entitlement catalog

| Key | Scope | Meaning |
|---|---|---|
| `limit.companies` | organization | Active client Companies |
| `limit.users` | organization | Active users (org members + company members) |
| `limit.accountants` | organization | Active `accountant`/`staff` members |
| `limit.devices` | organization (optionally per user) | Active registered devices |
| `limit.workspace_sections_per_company` | company | Non-archived sections |
| `limit.folder_depth` | company | Maximum nesting depth |
| `limit.folders_per_company` | company | Non-archived folders |
| `limit.custom_fields_per_company` | company | Non-archived field definitions |
| `limit.excel_templates_per_company` | company | Non-archived templates |
| `quota.documents_per_month` | organization | Documents entering processing per billing period |
| `quota.storage_bytes` | organization | Stored bytes (originals + derivatives) |
| `quota.ai_units_per_month` | organization | Metered AI/OCR units per period |
| `feature.*` | organization | Boolean features (e.g. `feature.api_access`, `feature.custom_roles`, `feature.report_builder`, `feature.macro_templates`, `feature.cloud_ai`) |

New keys are added by migration to the catalog without schema change to subscriptions.

## 3. Enforcement architecture

```
 API handler / worker
      │  intent: "create section in company C"
      ▼
 EntitlementService.check(organization_id, key, scope_id, delta, mode)
      │  1. load effective limit (cached per request; cache invalidated on plan/override change)
      │  2. read/lock usage counter (SELECT … FOR UPDATE on the counter row for count-type limits)
      │  3. compare current + delta against limit
      ▼
 allow → mutation + counter update in the SAME transaction
 deny  → EntitlementExceeded(code, key, limit, current, requested) → 402/403 problem+json
```

Rules:

1. **The backend is the only enforcement point.** Frontends may display limits and disable buttons for UX but are never trusted.
2. **Same-transaction accounting for counts.** Count-type limits (companies, sections…) use locked counter rows so concurrent requests cannot oversubscribe. Counters are reconcilable: `artify-api reconcile` recomputes every organization's counters from the source tables, repairs drift and prints what it changed (tested). *Phase 1 ships the command; running it on a schedule (cron / Kubernetes CronJob) and alerting on drift is a deployment concern for Phase 12.*
3. **Quotas** (storage, AI units, documents/month) use *reserve-then-commit*: reserve before expensive work, commit actual usage after; reservations expire. Overage policy per key: `hard` (block), `soft` (allow, notify, bill), `grace` (allow until date).
4. **Change sets** (workspace, templates) check the **resulting** totals for the whole set atomically.
5. **State-based gating.** Subscription status `suspended` or `past_due` beyond grace restricts creation/upload/AI while keeping read/export of existing data.
6. **Downgrades never delete data.** If a new plan is below current usage, the Organization enters "over limit": existing items stay accessible; creating more is blocked until usage falls below the limit.
7. **Auditability.** Plan edits, overrides and status changes are platform-audited; denied actions are logged with key/limit/current.
8. **Errors are structured**: `{type, code: "entitlement_exceeded", key, limit, current, requested, upgrade_hint}` so clients show actionable messages and localize them.

## 4. Super Admin management (Phase 2)

Create/clone/retire plans; edit entitlement values; assign plans; apply/expire overrides; view usage vs limit per tenant; suspend/reactivate; see projected overages. Plan changes take effect without a deploy and apply to current and future subscriptions as configured (grandfathering = keep the subscription on the plan version it started with: plans are versioned; a subscription pins `plan_version_id`).

## 5. Tests (see `TEST_SCENARIOS.md`)

Backend enforcement independent of the UI (TS-ENT-*); concurrent creation races; downgrade-over-limit behavior; override expiry; suspended-state restrictions.
