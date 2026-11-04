# Phase 2 Implementation Overview — Artify Super Admin

Status of every requirement is in [`PHASE_2_COMPLETION_REPORT.md`](PHASE_2_COMPLETION_REPORT.md); evidence in [`PHASE_2_TEST_REPORT.md`](PHASE_2_TEST_REPORT.md). Related: [`../security/PLATFORM_ADMIN_SECURITY.md`](../security/PLATFORM_ADMIN_SECURITY.md), [`../security/SUPPORT_ACCESS_POLICY.md`](../security/SUPPORT_ACCESS_POLICY.md), [`SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md`](SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md), [`PLATFORM_MONITORING.md`](PLATFORM_MONITORING.md).

## 1. What was reused from Phase 1 (nothing was duplicated)

The platform realm (separate token audience/secret, `artify_platform` DB role, `platform_admins`, sessions, TOTP, refresh rotation), `platform_audit_events`, the subscription/plan/override/usage-counter tables, the `EntitlementService`, `support_access_grants`, the BFF with HttpOnly cookies, the design tokens and `packages/ui`, the OpenAPI → TypeScript pipeline, and the e2e harness. Independent accountants are **organizations of type `independent_accountant`** — the same tenant model; the Super Admin lists them through a type filter.

## 2. Repository map (new or changed)

| Path | Content |
|---|---|
| `services/api/migrations/sql/0004_super_admin.sql` (+ revision `0004`) | schema, grants, RLS, views, functions — see §3 |
| `services/api/src/artify_api/permissions.py` | 24 platform permissions, six platform roles, rank table, `support_grants.manage` |
| `.../routers/platform_common.py` | pagination envelope, typed-confirmation and reason bodies, ordering whitelist, audit helpers (incl. isolated-transaction audit) |
| `.../routers/platform_orgs.py` | organizations, provisioning, suspend/reactivate, history, companies overview, global search |
| `.../routers/platform_plans.py` | plans, immutable versions, catalog, comparison |
| `.../routers/platform_subs.py` | subscriptions, plan change, extend, status machine, overrides |
| `.../routers/platform_billing.py` | invoices, payments, refunds, adjustments, summary |
| `.../routers/platform_admins.py` | administrators, invitations, role/suspension, activity, invitation acceptance |
| `.../routers/platform_support.py`, `support_grants.py` | support access: platform side and customer approval |
| `.../routers/platform_ops.py` | dashboard, usage, health, audit viewer, activity feed, settings |
| `.../platform_usage.py`, `metrics.py` | usage read model; in-process counters |
| `.../context.py`, `audit.py`, `mailer.py` | platform denial auditing; audit outcome/reason; two new mail templates |
| `packages/ui/src` | `StatusChip`, `Dialog`, shell/table/dialog/KPI styles (reused by Phase 3) |
| `apps/web/src/components/platform/` | `shell.tsx` (navigation, search, alerts, profile), `kit.tsx` (table, dialogs, toasts, tabs, KPI) |
| `apps/web/src/app/(platform)/platform/(app)/…` | the 15 modules; `accept-invitation/` (public); `apps/web/src/app/(console)/support-access/` (customer) |
| `services/api/tests/test_p2_*.py`, `apps/web/e2e/superadmin.spec.ts` | tests |

## 3. Database changes (migration 0004, forward-only)

- **New tables:** `platform_admin_invitations`, `organization_status_events`, `subscription_events`, `billing_invoices`, `billing_payments`, `billing_adjustments`; sequence `billing_invoice_seq`.
- **Changed:** `platform_admins` (role `platform_readonly`, `invited_by`); `subscription_plans` (status `inactive`, `description`); `subscription_plan_versions` (price + currency, support level, trial days, notes, creator); `subscriptions` (status `expired`, cancellation fields); `support_access_grants` (requester name, requested hours, approved level, decision/revocation fields); `platform_audit_events` (`outcome`, `reason` + indexes); `invitations` (`invited_by_platform_admin`).
- **Integrity:** triggers make `subscription_events`, `organization_status_events`, `billing_payments`, `billing_adjustments` append-only and make a **plan version in use immutable**; `NUMERIC(18,3)` money (schema lint still forbids floats); RLS enabled **and forced** on every new table.
- **Platform realm access:** narrow, column-limited grants (organizations, subscriptions, overrides, administrators — never `password_hash`); **aggregate views owned by `artify_definer`** (`platform_org_overview`, `platform_company_overview`, `platform_user_totals`, `platform_outbox_overview`) instead of policies on tenant tables; **grant-scoped policies** for members, users and the customer audit log via `app.platform_granted_org_ids()`; policies letting the platform read/revoke platform sessions only.
- **Functions (SECURITY DEFINER, pinned search_path):** `platform_provision_organization` (organization + trial subscription + owner invitation, callable only by an active owner/administrator), `platform_accept_invitation`, `guard_pinned_plan_version`; `create_trial_subscription` now writes the history event.
- Documentation: [`../database/INITIAL_DATABASE_IMPLEMENTATION.md`](../database/INITIAL_DATABASE_IMPLEMENTATION.md) §8.

## 4. Platform API (71 operations under `/api/v1/platform`)

Every route is guarded by exactly one `PlatformRequires(permission)` or is a public authentication endpoint; two tests enforce this (`test_every_route_declares_its_authorization`, `test_every_platform_route_declares_a_permission_that_matches_its_verb`). List endpoints return `{items, total, limit, offset}` and accept `q`, filters, `sort`/`order`, `limit` (≤ 200) and `offset`.

| Method | Path | Guard |
|---|---|---|
| `GET` | `/platform/activity` | `platform.audit.read` |
| `GET` | `/platform/admins` | `platform.admins.read` |
| `POST` | `/platform/admins/invitations` | `platform.admins.manage` |
| `DELETE` | `/platform/admins/invitations/{invitation_id}` | `platform.admins.manage` |
| `PATCH` | `/platform/admins/{admin_id}` | `platform.admins.manage` |
| `GET` | `/platform/admins/{admin_id}/activity` | `platform.admins.read` |
| `POST` | `/platform/admins/{admin_id}/reactivate` | `platform.admins.manage` |
| `POST` | `/platform/admins/{admin_id}/revoke-sessions` | `platform.admins.manage` |
| `POST` | `/platform/admins/{admin_id}/suspend` | `platform.admins.manage` |
| `GET` | `/platform/audit-events` | `platform.audit.read` |
| `GET` | `/platform/audit-events/{event_id}` | `platform.audit.read` |
| `POST` | `/platform/auth/invitations/accept` | public |
| `POST` | `/platform/auth/login` | public |
| `POST` | `/platform/auth/logout` | session |
| `POST` | `/platform/auth/mfa/confirm` | session |
| `POST` | `/platform/auth/mfa/enroll` | session |
| `POST` | `/platform/auth/mfa/verify` | public |
| `POST` | `/platform/auth/refresh` | public |
| `GET` | `/platform/billing/invoices` | `platform.billing.read` |
| `POST` | `/platform/billing/invoices` | `platform.billing.manage` |
| `GET` | `/platform/billing/invoices/{invoice_id}` | `platform.billing.read` |
| `POST` | `/platform/billing/invoices/{invoice_id}/adjustments` | `platform.billing.manage` |
| `POST` | `/platform/billing/invoices/{invoice_id}/issue` | `platform.billing.manage` |
| `POST` | `/platform/billing/invoices/{invoice_id}/payments` | `platform.billing.manage` |
| `POST` | `/platform/billing/invoices/{invoice_id}/refunds` | `platform.billing.manage` |
| `POST` | `/platform/billing/invoices/{invoice_id}/void` | `platform.billing.manage` |
| `GET` | `/platform/billing/payments` | `platform.billing.read` |
| `GET` | `/platform/billing/summary` | `platform.billing.read` |
| `GET` | `/platform/companies` | `platform.companies.read` |
| `GET` | `/platform/dashboard` | `platform.dashboard.read` |
| `GET` | `/platform/entitlements` | `platform.plans.read` |
| `GET` | `/platform/health` | `platform.health.read` |
| `GET` | `/platform/me` | `platform.self.read` |
| `GET` | `/platform/organizations` | `platform.organizations.read` |
| `POST` | `/platform/organizations` | `platform.organizations.manage` |
| `GET` | `/platform/organizations/{org_id}` | `platform.organizations.read` |
| `PATCH` | `/platform/organizations/{org_id}` | `platform.organizations.manage` |
| `GET` | `/platform/organizations/{org_id}/history` | `platform.organizations.read` |
| `GET` | `/platform/organizations/{org_id}/overrides` | `platform.subscriptions.read` |
| `POST` | `/platform/organizations/{org_id}/overrides` | `platform.entitlements.override` |
| `POST` | `/platform/organizations/{org_id}/overrides/{override_id}/expire` | `platform.entitlements.override` |
| `POST` | `/platform/organizations/{org_id}/reactivate` | `platform.organizations.suspend` |
| `POST` | `/platform/organizations/{org_id}/suspend` | `platform.organizations.suspend` |
| `GET` | `/platform/organizations/{org_id}/usage-history` | `platform.usage.read` |
| `GET` | `/platform/plans` | `platform.plans.read` |
| `POST` | `/platform/plans` | `platform.plans.manage` |
| `GET` | `/platform/plans/compare` | `platform.plans.read` |
| `GET` | `/platform/plans/{plan_id}` | `platform.plans.read` |
| `PATCH` | `/platform/plans/{plan_id}` | `platform.plans.manage` |
| `POST` | `/platform/plans/{plan_id}/activate` | `platform.plans.manage` |
| `POST` | `/platform/plans/{plan_id}/archive` | `platform.plans.manage` |
| `POST` | `/platform/plans/{plan_id}/deactivate` | `platform.plans.manage` |
| `POST` | `/platform/plans/{plan_id}/versions` | `platform.plans.manage` |
| `GET` | `/platform/search` | `platform.dashboard.read` |
| `GET` | `/platform/settings` | `platform.settings.read` |
| `PUT` | `/platform/settings/{key}` | `platform.settings.manage` |
| `GET` | `/platform/subscriptions` | `platform.subscriptions.read` |
| `GET` | `/platform/subscriptions/{sub_id}` | `platform.subscriptions.read` |
| `POST` | `/platform/subscriptions/{sub_id}/cancel-at-period-end` | `platform.subscriptions.manage` |
| `POST` | `/platform/subscriptions/{sub_id}/change-plan` | `platform.subscriptions.manage` |
| `POST` | `/platform/subscriptions/{sub_id}/extend` | `platform.subscriptions.manage` |
| `POST` | `/platform/subscriptions/{sub_id}/reactivate` | `platform.subscriptions.manage` |
| `POST` | `/platform/subscriptions/{sub_id}/status` | `platform.subscriptions.manage` |
| `POST` | `/platform/subscriptions/{sub_id}/suspend` | `platform.subscriptions.manage` |
| `GET` | `/platform/support/grants` | `platform.support.read` |
| `POST` | `/platform/support/grants` | `platform.support.request` |
| `GET` | `/platform/support/grants/{grant_id}` | `platform.support.read` |
| `GET` | `/platform/support/grants/{grant_id}/audit-events` | `platform.support.view_data` |
| `GET` | `/platform/support/grants/{grant_id}/members` | `platform.support.view_data` |
| `POST` | `/platform/support/grants/{grant_id}/revoke` | `platform.support.read` |
| `GET` | `/platform/usage` | `platform.usage.read` |

### Customer-side (firm owner only)

| Method | Path | Guard |
|---|---|---|
| `GET` | `/orgs/{org_id}/support-grants` | `support_grants.manage` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/approve` | `support_grants.manage` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/deny` | `support_grants.manage` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/revoke` | `support_grants.manage` |

## 5. Super Admin interface

15 modules in the sidebar, shown only when the administrator holds the read permission: Overview, Accounting Firms, Independent Accountants, All Organizations, Client Companies, Subscription Plans, Subscriptions, Billing & Payments, Usage & Limits, Platform Administrators, Support Access, Platform Activity, Audit Logs, System Health, Platform Settings. Plus: organization creation and detail (tabs: summary, subscription, usage, companies, overrides, history), plan creation/versioning/comparison, subscription and invoice detail, global search, alerts, profile menu with sign-out, breadcrumbs, sortable/filterable/paginated tables with URL-held state, loading/empty/error states, toasts, and typed-confirmation dialogs for destructive actions. Dark mode and responsive layout (drawer navigation under 52 rem). Public pages: `/platform/login`, `/platform/accept-invitation`. Customer console: `/support-access`.

Every page calls the real API through the BFF; none contains sample data. The interface is **English only** (deviation, below).

## 6. Deviations from the Phase 0/1 plans and from the brief

| Deviation | Reason |
|---|---|
| Cross-tenant overviews use definer-owned **views**, not policies on tenant tables | keeps Phase 0 invariant 10 (no default platform access to business data) and the Phase 1 isolation test intact; ADR-028 |
| `GET /platform/organizations` changed from a list to a paginated envelope | one organization list instead of two; the allow-listed breaking change replaced the only consumer (the Phase 1 placeholder page) |
| Support access is approved by the **customer** only; no alternative internal-approval policy | the safest reading of "customer authorization or another explicitly approved policy"; ADR-029 |
| **Dual control not implemented**; sensitive actions use reason + typed confirmation + audit | scope; recorded as open in `PLATFORM_ADMIN_SECURITY.md` |
| Per-administrator custom permissions not implemented (roles only) | scope |
| Billing is manual records only; no provider, no scheduler | D-2 undecided; ADR-031 |
| Super Admin UI is English only (the customer console stays English + Arabic) | internal staff tool; the Arabic review in Phase 1 is still outstanding for the customer surface |
| Brief entity names (`platform_roles`, `organization_subscriptions`, `usage_events`, `support_access_sessions`…) not created | Phase 1 tables cover them (`roles` in code, `subscriptions`, `usage_records`, `support_access_grants`); no duplicates |
| Brief's "Independent accountants" module has no separate backend | same organization model, `type` filter |
| Company-level contact information not shown | the registration flow does not collect any beyond the owner's e-mail |
