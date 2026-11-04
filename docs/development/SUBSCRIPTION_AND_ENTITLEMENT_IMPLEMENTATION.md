# Subscription and Entitlement Implementation (Phase 1 service + Phase 2 administration)

Design intent: [`../architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md`](../architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md). This document records what exists, how it behaves and what is not built. Code: `entitlements.py` (enforcement, Phase 1), `platform_usage.py` and `routers/platform_{plans,subs,billing}.py` (administration, Phase 2). Tests: `test_entitlements.py`, `test_p2_plans_subs.py`, `test_p2_billing.py`, `test_p2_ops.py`.

## 1. Model

| Table | Purpose |
|---|---|
| `entitlement_definitions` | catalog of keys (type, scope, enforcement, unit); read-only reference data |
| `subscription_plans` | identity + status: `active`, `inactive`, `retired` (= archived) |
| `subscription_plan_versions` | **immutable** versions: billing interval, price (`NUMERIC(18,3)` + currency), support level, trial days, notes |
| `plan_entitlements` | `(version, key) → number | unlimited | flag` |
| `subscriptions` | one row per organization, pinned to a version; status; period/trial dates; `cancel_at_period_end`, `cancelled_at` |
| `subscription_events` | **append-only** history of creation, plan changes, status changes, extensions, overrides |
| `entitlement_overrides` | per-organization exceptions with reason and optional expiry |
| `usage_counters` | maintained counts the enforcement service locks and reconciles |

Effective limit = unexpired **override** → **plan version** value → **deny** (0). A plan that omits a key therefore denies it; the plan editor lists every catalog key so omissions are deliberate (e2e test sets `limit.users` explicitly for this reason).

## 2. Plans and versions

- A plan is created **inactive**. Activating makes it assignable; deactivating blocks new assignments only; archiving is final. The default sign-up plan (`platform_settings.default_plan_key`) cannot be deactivated or archived.
- **Editing publishes a new version.** A database trigger rejects any insert/update/delete on a plan version or its entitlements once any subscription uses it. Existing customers keep the version they have (grandfathering); moving a customer is an explicit subscription action. Bulk "migrate everyone to the new version" is **not implemented**.
- Entitlement values are validated against the catalog: unknown key, wrong type (flag vs number), negative, more than one of value/unlimited/flag are rejected. Prices are exact decimals serialized as strings; amount and currency come together.
- Compare: up to five plans side by side (latest versions).
- The catalog marks `enforced_now`. Only `limit.companies`, `limit.users`, `limit.accountants`, `limit.devices` are enforced today; the rest (workspace sections, folders, custom fields, templates, documents/month, AI units, storage, feature flags) can be configured now and **start being enforced when their feature ships**. The UI says so next to each key.

## 3. Subscription statuses

| From → To | Via | Notes |
|---|---|---|
| (any live) → `suspended` | Suspend (reason + typed slug) | remembers the previous status |
| `suspended` → previous | Reactivate | a trial that ended while suspended becomes `expired`, it does not resume |
| `trialing` → `active`, `expired`, `cancelled` | Set status | |
| `active` → `past_due`, `cancelled`, `expired` | Set status | |
| `past_due` → `active`, `cancelled`, `expired` | Set status | |
| `cancelled` / `expired` → `active` | Set status | reinstatement; clears `cancelled_at` |

Customer-visible effect (enforced by the API, `entitlements.guard`): while `suspended`, `cancelled`, `expired` or without a subscription the customer **cannot create** companies, invite users or register devices, but can read and export what exists (403 `subscription_restricted`). `past_due` is allowed (grace) and flagged on the dashboard. Organization suspension is separate (Phase 1: organization read-only).

**No scheduler.** Nothing flips `trialing → expired` when `trial_ends_at` passes, or advances `current_period_end`; those dates are informational and an administrator changes the status. A renewal/expiry job belongs with the billing provider integration (D-2) and Phase 12.

## 4. Changing a plan safely

`POST /platform/subscriptions/{id}/change-plan`:

1. target must be a version of an **active** plan, different from the current one; cancelled/expired subscriptions must be reinstated first;
2. the service compares each enforceable count limit with the **current counters** under the target version (an unexpired override for the same key is respected);
3. if usage exceeds a new limit the request is refused with `409 over_limit` and the list of violations unless `acknowledge_over_limit` is sent;
4. when applied, **nothing is deleted**: the organization keeps all companies/users/devices and is simply blocked from creating more until usage falls below the limit ("over limit" state, shown on Usage & Limits);
5. the event records direction (`upgrade`/`downgrade`/`change`, by comparing prices in the same currency), violations and the reason; the platform audit records it too.

## 5. Other administrative actions

- **Extend**: adds days to `trial_ends_at` (trialing) or `current_period_end` (active, past due).
- **Schedule cancellation at period end** (flag; no job acts on it yet — see §3).
- **Overrides**: numeric, unlimited or flag, with a mandatory reason, optional future expiry, ending early at any time (`platform.entitlements.override`, separate from subscription management).
- **Provisioning**: an administrator can create an organization (trial subscription on a chosen active plan + a pending owner invitation) in one database function call; sign-up uses the same default plan.

## 6. Usage monitoring

Current values come from the maintained counters (same source the enforcement service uses; `artify-api reconcile` repairs drift). Per organization: current, limit, remaining, percent, status (`ok`, `approaching` ≥ 80 %, `exceeded`, `not_metered`, `not_configured`, `flag`), and source (override/plan/default). **A metric without a counter is `not metered`, never zero.** Documents processed, AI units and storage are not metered yet. `usage-history` aggregates real `usage_records` by month (empty today, with an explanatory note); count limits have current values only.

## 7. Billing (manual, provider-independent)

`billing_invoices`, `billing_payments` (payment/refund), `billing_adjustments` — all amounts `NUMERIC(18,3)`, strings in JSON, never summed across currencies. Payments and adjustments are append-only; an invoice moves `draft → open → paid`/`void`, and `paid ⇄ open` follows the balance after refunds and adjustments. Overpayment and negative totals are refused; a paid or partly paid invoice cannot be voided. **Nothing is created automatically; no payment provider, card data or credential exists anywhere.** `provider` and `provider_reference` columns are reserved and unused.

Integration requirements for a future provider (decision D-2): a webhook endpoint that is idempotent and signature-verified; a provider customer id per organization (not a card); mapping provider events to `billing_payments` rows with `method = 'provider'`; a reconciliation job; failure/dunning rules that move subscriptions to `past_due`; and the renewal/expiry scheduler of §3. None of this exists.

## 8. Known limitations

- No scheduler for trial expiry, renewals, cancellation at period end, or invoice generation.
- Bulk version migration, proration, add-ons, coupons and usage-based pricing are not implemented (the schema leaves room: overrides + new keys).
- Customers cannot see their own invoices or limits history in a billing page yet (the Control Center in Phase 3 will read `GET /orgs/{id}/entitlements`).
- Limits other than the four count limits are stored but not enforced until their features exist.
