# Phase 3 — Firm Control Center and Client Management

**Status: DELIVERED FOR REVIEW (2026-11-04) — see [`PHASE_3_COMPLETION_REPORT.md`](PHASE_3_COMPLETION_REPORT.md) for what is implemented, tested and still open (notably: notifications framework, custom roles, audit export, Arabic UI and the roles-configuration screen are not delivered).** Phase 4 must not start until this phase is approved.

## Objectives

Deliver the commercial web application shell and the tenant administration modules: Companies, staff, assignments, roles, subscription/usage view, notifications framework, audit viewer.

## Functional scope

- Control Center modules 1–5, 12, 13 (framework), 14 (viewer) per the delivery matrix.
- Company lifecycle: create, edit, suspend, archive; company settings; company owner invitation; company members.
- Staff management: invite, deactivate, role assignment; accountant assignments (`primary|secondary|reviewer`) with effect on access (verified).
- Roles: system roles; custom roles behind `feature.custom_roles`; permission matrix UI.
- Subscription and usage page (read-only): limits, current usage, over-limit states, upgrade contact flow.
- Notification framework (in-app + email; preferences) and an Overview Dashboard built only from real data (companies, members, assignments; document metrics appear in later phases).
- Audit log viewer for tenant events with filters and export.

## Technical implementation

`packages/ui` hardening (tokens, data table, forms, app shell, org/company switcher, entitlement banner, i18n + RTL), `(console)` route group deployed as its own surface, TanStack Query + generated client, server-side permission-aware navigation (UI hides, API enforces), notification service + templates.

## Database changes

Adds `financial_periods`, `company_settings`, `notifications`, notification preference tables, company-status fields, custom role support in `roles`/`role_permissions`, indexes for list/search; invitations for company targets.

## API requirements

`/orgs/{id}/companies`, `/orgs/{id}/members`, `/orgs/{id}/roles`, `/companies/{id}/members|assignments|settings`, `/orgs/{id}/usage`, `/orgs/{id}/audit-events`, `/me/notifications`.

## Dependencies

Phase 1 (identity, tenancy), Phase 2 initial UI primitives (can proceed in parallel after Phase 1 once tokens exist).

## Security requirements

Permission checks per route (route-table test); assignment changes take effect on the next request and are audited; invitations single-use; audit viewer limited to tenant data; export of audit data is itself audited.

## Testing requirements

Executable: TS-ISO-02, TS-ISO-08 (UI + API), TS-ENT-01 (users/accountants), a11y (axe) on all pages, keyboard-only walkthroughs, RTL snapshot tests, E2E: firm owner onboards staff → assigns → accountant sees only assigned Companies.

## Acceptance criteria

1. A firm owner can create Companies, invite staff and client users, assign accountants; accountants see only assigned Companies in UI and API.
2. Entitlement limits produce actionable, localized messages in the UI and structured errors in the API.
3. No placeholder statistics; empty states are honest.
4. WCAG 2.2 AA checks pass for delivered pages.

## Completion checklist

- [ ] Delivery-matrix modules for Phase 3 complete
- [ ] `frontend-design` skill finalized
- [ ] Usability notes captured
- [ ] `PHASE_3_COMPLETION_REPORT.md`

## Known risks

Over-building the dashboard before data exists; RBAC UI complexity; i18n/RTL retrofit cost if not applied from the first component.
