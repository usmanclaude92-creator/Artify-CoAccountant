# Firm Control Center — Overview (Phase 3)

The Firm Control Center is the customer-facing web console of an accounting firm (an **Organization**). It is where the firm manages its client companies, its people, who works on which company, and sees its own subscription and audit trail. It is not the Artify Super Admin console (platform operators, separate realm and URL space) and it is not the workspace builder (Phase 4).

Status: implemented and tested as listed in [`../development/PHASE_3_COMPLETION_REPORT.md`](../development/PHASE_3_COMPLETION_REPORT.md). Where this document and the code differ, the code and the report win.

## What it contains

| Navigation item | Route | Needs permission | Purpose |
|---|---|---|---|
| Dashboard | `/dashboard` | `organization.read` | Real counts: companies by status, team, accountants, pending invitations, my assigned companies, plan and usage meters, recent activity, shortcuts |
| My Companies | `/companies`, `/companies/new`, `/companies/{id}` | `companies.list` | Searchable list, 4-step creation wizard, company detail (overview, accounting settings, assigned accountants, client users, configuration, history), activate / deactivate / archive / restore |
| Team Members | `/team` | `members.read` | Directory, role change, suspend/reactivate, professional profile, roles-and-permissions panel, ownership transfer |
| Accountants | `/accountants` | `members.read` | Professional staff with workload and assigned companies |
| Client Assignments | `/assignments` | `members.read` | Company ↔ accountant assignments (type, responsibility, period, state) |
| Invitations | `/invitations` | `invitations.manage` | Invite, resend, cancel; honest e-mail delivery notice |
| Subscription & Usage | `/subscription` | `entitlements.read` | Read-only plan, status, limits, live usage |
| Activity & Audit History | `/activity` | `audit.read` | The organization’s own append-only audit trail with filters |
| Organization Settings | `/settings` | `organization.read` (edit: `organization.settings.update`) | Contact, regional defaults, notification preferences (stored only) |
| Support Access | `/support-access` | `support_grants.manage` | Approve/deny/end Artify support requests (moved here from Phase 2) |
| My Profile | `/profile` | any signed-in user | Display name, password change, two-factor status, active sessions |

Navigation items are **hidden** when the caller lacks the permission, but hiding is only convenience: every request is authorized again by the API (see [`ROLE_AND_PERMISSION_MODEL.md`](ROLE_AND_PERMISSION_MODEL.md)).

## What it deliberately does not contain

Workspace builder, folder engine, metadata/category builder, auto-filing, OCR/AI, accounting or VAT engine, Excel engine, bookkeeping, desktop and mobile apps, payments/billing for customers, a notification sender, custom roles. See §Limitations in the completion report.

## Reuse

No parallel identity, organization, company, subscription or permission model was created. Phase 3 extends Phase 1/2 tables (migration `0005_control_center`) and the existing `EntitlementService`, policy engine, audit writer and design kit. Details: [`ORGANIZATION_MANAGEMENT.md`](ORGANIZATION_MANAGEMENT.md), [`COMPANY_MANAGEMENT.md`](COMPANY_MANAGEMENT.md), [`TEAM_AND_ACCOUNTANT_MANAGEMENT.md`](TEAM_AND_ACCOUNTANT_MANAGEMENT.md), [`API_REFERENCE.md`](API_REFERENCE.md), [`PHASE_4_INTEGRATION.md`](PHASE_4_INTEGRATION.md), [`SECURITY_TEST_RESULTS.md`](SECURITY_TEST_RESULTS.md).

## Language

The Control Center UI is English-only in Phase 3. The Phase 1 dashboard was bilingual (English/Arabic); the new screens have no Arabic strings, so that capability regressed for signed-in pages. Public pages (sign-in, sign-up, invitation) remain bilingual. See ADR-040 in `DECISIONS.md`.
