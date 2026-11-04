# Phase 2 — Artify Super Admin

**Status: NOT STARTED.**

## Objectives

Deliver the internal platform administration interface that manages tenants, plans, entitlements, subscriptions, usage and support access — without any default access to customer financial data.

## Functional scope

- Organizations: list/search, view metadata, status, plan, usage vs limits, suspend/reactivate (reason + audit; dual-control for destructive actions).
- Plans: create/clone/retire; plan versions; edit entitlement values (`limit.*`, `quota.*`, `feature.*`); per-subscription overrides with expiry.
- Subscriptions and billing records: assign plan, trial, status transitions, manual invoicing and `payment_records` (provider integration per D-2).
- Usage: platform-wide and per-tenant metrics (companies, users, storage, documents, AI units) from real counters; "no data" shown honestly when empty.
- Platform health: API/worker/queue/DB indicators from real telemetry endpoints.
- Platform configuration (`platform_settings`), platform audit and security event viewer.
- Support access: request, approval flow integration, active grants list, revoke; view tenant metadata only.
- Platform admin management with roles `platform_owner|admin|support|billing|auditor`, mandatory MFA.

## Technical implementation

`apps/web` `(platform)` route group built as its own surface (`APP_SURFACE=platform`); platform API router `/api/v1/platform/*`; `artify_platform` DB role and policies (ISOLATION_AND_RLS §4); metering rollup jobs; `packages/ui` initial primitives (table, forms, dialogs, status chips) reused by Phase 3.

## Database changes

Adds usage rollup tables/views, `support_access_grants` workflow columns, plan version indexes, platform health snapshot table (if needed), views exposing aggregates only to `artify_platform`. No access policies on tenant business tables except the grant policy.

## API requirements

`/platform/organizations`, `/platform/plans`, `/platform/plans/{id}/versions`, `/platform/subscriptions`, `/platform/entitlement-overrides`, `/platform/usage`, `/platform/health`, `/platform/settings`, `/platform/audit-events`, `/platform/support-grants`, `/platform/admins`. Tenant-side endpoints for grant approval (`/orgs/{id}/support-grants`).

## Dependencies

Phase 1 complete; D-2 decided (or manual invoicing confirmed).

## Security requirements

Separate realm/hostname/cookies; MFA mandatory; short sessions; optional IP allow-list; every action platform-audited; no business-data access without active grant (RLS-enforced); destructive actions dual-control; rate limits.

## Testing requirements

Executable: TS-ISO-06, TS-ISO-07, TS-ENT-03, TS-ENT-04, TS-ENT-07, TS-SEC-02 (platform), authorization matrix for platform routes, UI a11y checks, E2E: create plan → assign → tenant limit enforced.

## Acceptance criteria

1. Plan edited in Super Admin changes a tenant's effective limit without deployment (verified by test).
2. A Platform Admin cannot read any document/extracted/accounting data without a grant; with a grant, only scoped, time-limited, audited.
3. Suspending an Organization produces the documented restricted behavior.
4. All displayed metrics derive from real data; no placeholders presented as data.

## Completion checklist

- [ ] Platform realm separation verified
- [ ] Support grant lifecycle complete with tenant visibility
- [ ] Skill `frontend-design` drafted (with Phase 3 refinement)
- [ ] `PHASE_2_COMPLETION_REPORT.md`

## Known risks

Metering accuracy before documents exist (use synthetic counters in tests only, never presented as real); billing-provider uncertainty (D-2); temptation to give support broad data access for convenience.
