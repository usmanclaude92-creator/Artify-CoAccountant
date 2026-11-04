# Role and Permission Model

Permissions are fixed keys defined by Artify in `services/api/src/artify_api/permissions.py`; roles map to permissions in that same catalog, and `artify-api seed` synchronizes the database copy (`roles`, `permissions`, `role_permissions`). The tables below are **generated from that catalog** (`GET /api/v1/roles` serves the same data). Where this document and the code differ, the code wins.

## Two scopes

- **Organization roles** — held through `organization_memberships`. Some reach every company of the organization; the rest reach only companies they are assigned to.
- **Company roles** — held through `company_memberships` (the client’s own people). They never see another company or any organization-level data.

A request is authorized by the pure policy engine (`policy.py`): permission in role **and** (for company-scoped permissions) the company inside the caller’s scope **and** MFA satisfied for roles that require it. Row-level security enforces company scope again in the database. Out-of-scope objects return **404**.

## Suggested role mapping

| Brief’s role | System role key | Notes |
|---|---|---|
| Organization Owner | `firm_owner` | created at sign-up / ownership transfer only; MFA required |
| Administrator | `practice_admin` | everything except platform-only and ownership/support-grant powers; MFA required |
| Practice Manager | `practice_manager` | reaches all companies; manages companies, assignments, invitations; no member role changes; MFA required |
| Accountant | `accountant` | assigned companies only; can read the team and assignments |
| Assistant Accountant | `assistant_accountant` | assigned companies only; read-only on company data and assignments |
| Document Operator | `document_operator` | assigned companies only; foundation role for Phase 5+ document work |
| Read-Only User | `read_only` | assigned companies only; reading only |
| (legacy) Staff | `staff` | Phase 1 role, kept; same permissions as before |
| Company Owner / Administrator / Employee | `company_owner`, `company_admin`, `company_employee` | company scope |

Custom (organization-defined) roles and per-user permission overrides are **not implemented**; the schema allows custom roles per organization but no UI or API creates them.

## Role attributes

| Role key | Scope | Name | Rank | All companies | MFA required | Invitable | Assignable | Counts as accountant |
|---|---|---|---|---|---|---|---|---|
| `firm_owner` | organization | Firm owner | 100 | yes | yes | no | no | no |
| `practice_admin` | organization | Practice administrator | 80 | yes | yes | yes | no | no |
| `practice_manager` | organization | Practice manager | 60 | yes | yes | yes | no | no |
| `accountant` | organization | Accountant | 40 | no | no | yes | yes | yes |
| `assistant_accountant` | organization | Assistant accountant | 30 | no | no | yes | yes | yes |
| `staff` | organization | Staff | 30 | no | no | yes | yes | yes |
| `document_operator` | organization | Document operator | 20 | no | no | yes | yes | no |
| `read_only` | organization | Read-only user | 10 | no | no | yes | yes | no |
| `company_owner` | company | Company owner | — | no | no | no | no | no |
| `company_admin` | company | Company administrator | — | no | no | no | no | no |
| `company_employee` | company | Company employee | — | no | no | no | no | no |

## Permission matrix (organization and company scope)

| Permission | `firm_owner` | `practice_admin` | `practice_manager` | `accountant` | `assistant_accountant` | `staff` | `document_operator` | `read_only` | `company_owner` | `company_admin` | `company_employee` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `organization.read` | ● | ● | ● | ● | ● | ● | ● | ● |  |  |  |
| `organization.update` | ● |  |  |  |  |  |  |  |  |  |  |
| `members.read` | ● | ● | ● | ● |  |  |  |  |  |  |  |
| `members.manage` | ● | ● |  |  |  |  |  |  |  |  |  |
| `invitations.manage` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `audit.read` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `entitlements.read` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `companies.create` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `companies.list` | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `companies.read` | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `companies.update` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `company_members.read` | ● | ● | ● | ● | ● |  |  |  | ● | ● |  |
| `company_members.manage` | ● | ● | ● |  |  |  |  |  | ● | ● |  |
| `assignments.read` | ● | ● | ● | ● | ● |  |  |  |  |  |  |
| `assignments.manage` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `devices.manage_own` | ● | ● | ● | ● | ● | ● | ● |  | ● | ● | ● |
| `support_grants.manage` | ● |  |  |  |  |  |  |  |  |  |  |
| `organization.settings.update` | ● | ● |  |  |  |  |  |  |  |  |  |
| `member_profiles.manage` | ● | ● |  |  |  |  |  |  |  |  |  |
| `ownership.transfer` | ● |  |  |  |  |  |  |  |  |  |  |
| `companies.archive` | ● | ● |  |  |  |  |  |  |  |  |  |
| `company_settings.manage` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `workspace.read` | ● | ● | ● | ● | ● | ● | ● | ● |  |  |  |
| `workspace.manage` | ● | ● | ● |  |  |  |  |  |  |  |  |
| `workspace.permissions.manage` | ● | ● |  |  |  |  |  |  |  |  |  |
| `workspace_templates.manage` | ● | ● | ● |  |  |  |  |  |  |  |  |

## Rules enforced by the server

1. **Rank rule** — a caller may invite or grant only roles ranked at or below their own, and may change or suspend only members at or below their own rank. `firm_owner` is never grantable through invitation or role change.
2. **No self-elevation** — changing your own role is rejected (422); a lower role cannot invite a higher one; tested with real tokens.
3. **Last owner** — the final active owner cannot be demoted, suspended or removed. The approved process is **ownership transfer**: owner-only, MFA-verified session, confirmation by typing the organization identifier, active target; the target becomes owner and the caller becomes administrator in one transaction.
4. **MFA** — owner, administrator and practice manager are refused (403 `mfa_required`) until the session completed two-factor authentication.
5. **Assignment-based visibility** — restricted roles see a company only through a currently open assignment window and an active organization membership.
6. **Workspace keys (Phase 4)** gate the route; what a member may do to a particular section or folder is then decided by the allow/deny grant engine described in [`../workspace/WORKSPACE_PERMISSIONS.md`](../workspace/WORKSPACE_PERMISSIONS.md).
7. **Company lifecycle powers are separate keys** — `companies.archive` (activate/deactivate/archive/restore) and `company_settings.manage` are distinct from `companies.update`.
7. **Audit** — role changes, invitations, assignment changes, ownership transfers, status changes and settings changes write append-only audit events with actor, target and before/after values.
8. **Platform admins** hold no tenant permissions; they reach customer data only through approved, time-limited support grants.

## UI behaviour

Navigation, buttons and forms are hidden when the caller lacks the permission (from `GET /me` with `X-Organization-Id`). This is convenience only; the end-to-end test *accountant sees a reduced menu and the API still refuses what the menu hides* drives the URL directly.
