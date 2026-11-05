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
| `practice_admin` | organization | all companies | everything except `organization.update` and `support_grants.manage` |
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
| `support_grants.manage` (approve/deny/revoke Artify support access) | ✓ | — | — | — | — | — | — |

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

## 7. Platform realm (Super Admin, Phase 2)

Separate realm: platform roles never appear in the tenant tables above, hold no tenant permission, and are not satisfiable from a tenant token. Source of truth: `permissions.py` (`PLATFORM_ROLES`); the matrix below is generated from it. "Finance" is the `platform_billing` role.

| Permission | Owner | Administrator | Support | Finance | Read-only | Auditor |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `platform.self.read` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `platform.dashboard.read` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `platform.organizations.read` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `platform.organizations.manage` | ✓ | ✓ | — | — | — | — |
| `platform.organizations.suspend` | ✓ | ✓ | — | — | — | — |
| `platform.companies.read` | ✓ | ✓ | ✓ | — | ✓ | — |
| `platform.plans.read` | ✓ | ✓ | — | ✓ | ✓ | — |
| `platform.plans.manage` | ✓ | ✓ | — | — | — | — |
| `platform.subscriptions.read` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `platform.subscriptions.manage` | ✓ | ✓ | — | — | — | — |
| `platform.entitlements.override` | ✓ | ✓ | — | — | — | — |
| `platform.billing.read` | ✓ | ✓ | — | ✓ | ✓ | — |
| `platform.billing.manage` | ✓ | — | — | ✓ | — | — |
| `platform.usage.read` | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `platform.admins.read` | ✓ | ✓ | — | — | ✓ | — |
| `platform.admins.manage` | ✓ | — | — | — | — | — |
| `platform.support.read` | ✓ | ✓ | ✓ | — | ✓ | — |
| `platform.support.request` | ✓ | ✓ | ✓ | — | — | — |
| `platform.support.view_data` | ✓ | — | ✓ | — | — | — |
| `platform.support.revoke` | ✓ | ✓ | — | — | — | — |
| `platform.audit.read` | ✓ | ✓ | — | — | ✓ | ✓ |
| `platform.health.read` | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| `platform.settings.read` | ✓ | ✓ | — | — | ✓ | — |
| `platform.settings.manage` | ✓ | — | — | — | — | — |

Rules (tested): only `platform.admins.manage` (owner) manages administrators; nobody changes their own role or status; nobody grants a role above their own (rank owner 100 > administrator 80 > support/finance 40 > read-only/auditor 20); every route declares exactly one permission and its type (read vs write) must match its HTTP verb. Support grants are the only route to tenant members and audit logs and need customer approval (`SUPPORT_ACCESS_POLICY.md`). Tenant side: `support_grants.manage` is held by `firm_owner` only (not `practice_admin`).

Gaps: no per-administrator custom permission sets; no dual control; no IP allow-list. Details and threats: `PLATFORM_ADMIN_SECURITY.md`.

## 8. Phase 3 additions (Firm Control Center)

Phase 3 adds roles `practice_manager`, `assistant_accountant`, `document_operator` and `read_only`, and the permissions `organization.settings.update`, `member_profiles.manage`, `ownership.transfer`, `companies.archive` and `company_settings.manage`. The rank rule (invite or grant only at or below your own rank), ownership transfer and assignment windows are described, with generated role and permission tables, in [`../firm-control-center/ROLE_AND_PERMISSION_MODEL.md`](../firm-control-center/ROLE_AND_PERMISSION_MODEL.md). Where the two documents differ, the code in `permissions.py` wins. Custom roles are not implemented.

Phase 4 adds `workspace.read`, `workspace.manage`, `workspace.permissions.manage` (company scope) and `workspace_templates.manage` (organization scope); see [`../workspace/WORKSPACE_PERMISSIONS.md`](../workspace/WORKSPACE_PERMISSIONS.md). Platform admins have no workspace permission (workspace configuration is tenant business data).
## 9. Phase 10 additions (daily bookkeeping and closing)

Eleven permissions: `bookkeeping.read`, `.work`, `.tasks.manage`, `.checklists.manage`, `.policy.manage`, `.close.prepare`, `.close.review`, `.close.approve`, `.close.reopen`, `.close.override` (company scope) and `bookkeeping.monitor` (organization scope). The full role table, the per-company role restrictions and the independence rules are in [`../bookkeeping/PERMISSIONS_AND_POLICY.md`](../bookkeeping/PERMISSIONS_AND_POLICY.md). Invariant (test-enforced): every role holding `bookkeeping.read` also holds `accounting.read`; `staff` and `document_operator` hold no bookkeeping permission. Platform roles hold none.

## Phase 12 additions

| Permission | Scope | Meaning | Roles (system) |
|---|---|---|---|
| `sync.resolve` | company | Decide synchronization conflicts found by other people's devices (the finder can always decide their own) | firm owner, practice administrator, practice manager, accountant |
| `notifications.read_own` | member | Read the caller's own notifications and choose how they are delivered | every role |
