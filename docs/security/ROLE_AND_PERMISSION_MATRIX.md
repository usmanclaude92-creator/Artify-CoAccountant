# Role and Permission Matrix (as implemented in Phase 1)

Source of truth: `services/api/src/artify_api/permissions.py` (code), synchronized into the `roles`, `permissions` and `role_permissions` tables by `artify-api seed`. Evaluated by the pure policy engine `policy.py`; every route declares its permission (`Requires(...)`), and a test fails if a route declares none.

## 1. How a decision is made

`decision = role permissions ∩ scope ∩ permission scope`, evaluated for the **requested organization and company**, using memberships loaded on the server (never client-supplied roles).

| Permission scope | Satisfied by |
|---|---|
| `organization` | a permission of the caller's **organization role** in the claimed organization |
| `company` | the caller can *reach* the company (firm owner / practice admin reach all; accountants and staff only their **assigned** companies; client users only their own) **and** holds the permission via an organization role or the company role |
| `member` | any active role the caller holds in the organization |
| `platform` | only in the platform realm (never satisfiable from the tenant realm) |

Other gates: roles `firm_owner` and `practice_admin` require an MFA-verified session (`mfa_required` otherwise); a suspended organization allows reads only; out-of-scope companies and other tenants' objects return **404**, missing permission returns **403**.

## 2. Roles

| Role key | Scope | Reaches | Notes |
|---|---|---|---|
| `firm_owner` | organization | all companies | every tenant permission; created only at sign-up; cannot be invited; the last one cannot be demoted |
| `practice_admin` | organization | all companies | everything except `organization.update` |
| `accountant` | organization | **assigned** companies only | read-only on organization/members; no company creation, no member management |
| `staff` | organization | **assigned** companies only | read companies; no member visibility |
| `company_owner` | company | its own company | manages that company's client-side members (any company role) |
| `company_admin` | company | its own company | manages `company_employee` members only; cannot grant, change or remove owners/admins |
| `company_employee` | company | its own company | read the company |

Platform roles (separate realm, issuer, secrets and DB role): `platform_owner`, `platform_admin`, `platform_support`, `platform_billing`, `platform_auditor`. They hold only `platform.*` permissions and have **no** policy or grant on tenant business tables (test `test_platform_role_cannot_read_tenant_business_data`).

## 3. Permission × role (tenant realm)

`✓` granted · `—` not granted. Company roles apply only inside their own company.

| Permission | firm_owner | practice_admin | accountant | staff | company_owner | company_admin | company_employee |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `organization.read` | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `organization.update` | ✓ | — | — | — | — | — | — |
| `members.read` | ✓ | ✓ | ✓ | — | — | — | — |
| `members.manage` | ✓ | ✓ | — | — | — | — | — |
| `invitations.manage` | ✓ | ✓ | — | — | — | — | — |
| `audit.read` | ✓ | ✓ | — | — | — | — | — |
| `entitlements.read` | ✓ | ✓ | — | — | — | — | — |
| `companies.create` | ✓ | ✓ | — | — | — | — | — |
| `companies.list` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `companies.read` | ✓ | ✓ | ✓ (assigned) | ✓ (assigned) | ✓ | ✓ | ✓ |
| `companies.update` (incl. archive) | ✓ | ✓ | — | — | — | — | — |
| `company_members.read` | ✓ | ✓ | ✓ (assigned) | — | ✓ | ✓ | — |
| `company_members.manage` | ✓ | ✓ | — | — | ✓ | ✓ (employees only) | — |
| `assignments.read` | ✓ | ✓ | ✓ (assigned) | — | — | — | — |
| `assignments.manage` | ✓ | ✓ | — | — | — | — | — |
| `devices.manage_own` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## 4. Privilege-escalation rules (all tested)

- Nobody changes their **own** organization role or company membership.
- Only a `firm_owner` can grant, change or remove the `firm_owner` role; the last firm owner cannot be demoted or deactivated.
- `firm_owner` cannot be granted by invitation (invitable organization roles: `practice_admin`, `accountant`, `staff`).
- A `company_admin` cannot invite or change anyone above `company_employee`; `company_owner` and organization-level roles can.
- A role's scope must match the table (database trigger `check_role_scope`): an organization role cannot be stored as a company membership and vice versa.
- Assignments only point to **active** `accountant`/`staff` members (database trigger) and are deleted when a member's role changes to one that is not `accountant`/`staff`. (Suspending a member keeps the assignment row, but a suspended member has no active membership and therefore no access.)
- Reactivating members and companies is checked against entitlements (`limit.users`, `limit.accountants`, `limit.companies`).

## 5. Mapping from the Phase 1 brief's example permissions

| Brief | Implemented as | Status |
|---|---|---|
| manage_organization | `organization.update` | implemented |
| manage_organization_users | `members.manage`, `invitations.manage` | implemented |
| manage_companies | `companies.create`, `companies.update` | implemented |
| assign_accountants | `assignments.manage` | implemented |
| view_company | `companies.read` | implemented |
| manage_company_users | `company_members.manage` | implemented |
| view_audit_logs | `audit.read` | implemented |
| manage_company_settings, manage_workspace | — | **deferred** (workspace configuration is Phase 4) |
| view_documents, upload_documents, review_documents | — | **deferred** (documents: Phases 5–7) |
| approve_accounting_records, export_accounting_data | — | **deferred** (accounting/exports: Phases 8–9; approval will require `actor_type = user`) |
| manage_excel_templates | — | **deferred** (templates: Phase 4/9) |
| manage_subscription | — | **deferred** (Super Admin / billing: Phase 2, D-2) |

A permission is added to the catalog only together with the resource it protects, so the catalog never lists powers that nothing enforces.

## 6. Gaps

- **Custom (organization-defined) roles** are supported by the schema (`roles.organization_id`) but have no API or UI; entitlement `feature.custom_roles` gates them later.
- A user holding *both* an organization role and a company role in the same organization receives the union of permissions; this is intended but only partly covered by tests.
- The matrix above is hand-maintained; the executable source is `permissions.py` and `test_isolation_api.py` / `test_phase1_gap_pass.py`. A generated matrix check is not implemented.
