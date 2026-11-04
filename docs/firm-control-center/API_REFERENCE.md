# Firm Control Center — API Reference

Base path `/api/v1`. All tenant routes require a bearer token from the tenant realm (the web app’s BFF attaches it from an HttpOnly cookie) and, for organization routes, the organization in the path or the `X-Organization-Id` header. Errors are RFC 9457 `application/problem+json` with a stable `code`. The authoritative contract is `packages/api-client/openapi.json`; the table below is generated from the route declarations (`Requires(...)`).

**Conventions**

- Lists use `Page[T] = {items, total, limit, offset}`; `limit` 1–200 (defaults 25/50), `offset` ≥ 0, `sort` from a server-side allow-list, `order=asc|desc`. Filters are explicit query parameters (no free-form SQL).
- Mutations that create resources accept `Idempotency-Key`.
- Money is a decimal string; dates are ISO 8601; ids are UUIDs.
- `402 entitlement_exceeded` (limit reached), `403 subscription_restricted`, `403 mfa_required`, `403 forbidden`, `404` (also for out-of-scope objects), `409` (state conflict), `422` (validation).

## Endpoints

| Method | Path | Permission | operationId |
|---|---|---|---|
| `POST` | `/auth/invitations/accept` | (public or token) | `accept_invitation` |
| `POST` | `/auth/invitations/decline` | (public or token) | `decline_invitation` |
| `POST` | `/auth/login` | (public or token) | `login` |
| `POST` | `/auth/logout` | (authenticated user) | `logout` |
| `POST` | `/auth/mfa/confirm` | (authenticated user) | `mfa_confirm` |
| `POST` | `/auth/mfa/enroll` | (authenticated user) | `mfa_enroll` |
| `POST` | `/auth/mfa/verify` | (public or token) | `mfa_verify` |
| `POST` | `/auth/password-reset/confirm` | (public or token) | `password_reset_confirm` |
| `POST` | `/auth/password-reset/request` | (public or token) | `password_reset_request` |
| `POST` | `/auth/refresh` | (public or token) | `refresh` |
| `GET` | `/auth/sessions` | (authenticated user) | `list_sessions` |
| `DELETE` | `/auth/sessions/{session_id}` | (authenticated user) | `revoke_session` |
| `POST` | `/auth/signup` | (public or token) | `signup` |
| `POST` | `/auth/verify-email` | (public or token) | `verify_email` |
| `GET` | `/companies/{company_id}` | `companies.read` (company-scoped) | `companies_get` |
| `PATCH` | `/companies/{company_id}` | `companies.update` (company-scoped) | `companies_update` |
| `GET` | `/companies/{company_id}/assignments` | `assignments.read` (company-scoped) | `companies_assignments` |
| `POST` | `/companies/{company_id}/assignments` | `assignments.manage` (company-scoped) | `companies_assign` |
| `GET` | `/companies/{company_id}/assignments/history` | `assignments.read` (company-scoped) | `companies_assignment_history` |
| `DELETE` | `/companies/{company_id}/assignments/{user_id}` | `assignments.manage` (company-scoped) | `companies_unassign` |
| `PATCH` | `/companies/{company_id}/assignments/{user_id}` | `assignments.manage` (company-scoped) | `companies_update_assignment` |
| `GET` | `/companies/{company_id}/configuration` | `companies.read` (company-scoped) | `companies_configuration` |
| `GET` | `/companies/{company_id}/history` | `companies.read` (company-scoped) | `companies_history` |
| `POST` | `/companies/{company_id}/invitations` | `company_members.manage` (company-scoped) | `companies_invite_member` |
| `GET` | `/companies/{company_id}/members` | `company_members.read` (company-scoped) | `companies_members` |
| `PATCH` | `/companies/{company_id}/members/{user_id}` | `company_members.manage` (company-scoped) | `companies_update_member` |
| `PATCH` | `/companies/{company_id}/settings` | `company_settings.manage` (company-scoped) | `companies_update_settings` |
| `POST` | `/companies/{company_id}/status` | `companies.archive` (company-scoped) | `companies_status` |
| `GET` | `/devices` | `devices.manage_own` | `devices_list` |
| `POST` | `/devices` | `devices.manage_own` | `devices_register` |
| `DELETE` | `/devices/{device_id}` | `devices.manage_own` | `devices_revoke` |
| `GET` | `/health` | (public or token) | `liveness` |
| `GET` | `/health/ready` | (public or token) | `readiness` |
| `GET` | `/me` | (authenticated user) | `me_get` |
| `PATCH` | `/me` | (authenticated user) | `me_update` |
| `POST` | `/me/password` | (authenticated user) | `me_change_password` |
| `GET` | `/orgs` | (authenticated user) | `organizations_list` |
| `GET` | `/orgs/{org_id}` | `organization.read` | `organizations_get` |
| `PATCH` | `/orgs/{org_id}` | `organization.update` | `organizations_update` |
| `GET` | `/orgs/{org_id}/accountants` | `members.read` | `control_center_accountants` |
| `GET` | `/orgs/{org_id}/accountants/{user_id}` | `members.read` | `control_center_accountant` |
| `GET` | `/orgs/{org_id}/activity` | `audit.read` | `control_center_activity` |
| `GET` | `/orgs/{org_id}/assignments` | `members.read` | `control_center_assignments` |
| `GET` | `/orgs/{org_id}/audit-events` | `audit.read` | `organizations_audit_events` |
| `GET` | `/orgs/{org_id}/companies` | `companies.list` | `companies_list` |
| `POST` | `/orgs/{org_id}/companies` | `companies.create` | `companies_create` |
| `GET` | `/orgs/{org_id}/companies/overview` | `companies.list` | `companies_overview` |
| `GET` | `/orgs/{org_id}/dashboard` | `organization.read` | `control_center_dashboard` |
| `GET` | `/orgs/{org_id}/entitlements` | `entitlements.read` | `organizations_entitlements` |
| `GET` | `/orgs/{org_id}/invitations` | `invitations.manage` | `organizations_list_invitations` |
| `POST` | `/orgs/{org_id}/invitations` | `invitations.manage` | `organizations_invite_member` |
| `DELETE` | `/orgs/{org_id}/invitations/{invitation_id}` | `invitations.manage` | `organizations_revoke_invitation` |
| `POST` | `/orgs/{org_id}/invitations/{invitation_id}/resend` | `invitations.manage` | `organizations_resend_invitation` |
| `GET` | `/orgs/{org_id}/members` | `members.read` | `organizations_list_members` |
| `PATCH` | `/orgs/{org_id}/members/{user_id}` | `members.manage` | `organizations_update_member` |
| `PATCH` | `/orgs/{org_id}/members/{user_id}/profile` | `organization.read` | `control_center_member_profile` |
| `POST` | `/orgs/{org_id}/ownership-transfer` | `ownership.transfer` | `control_center_transfer_ownership` |
| `GET` | `/orgs/{org_id}/settings` | `organization.read` | `control_center_settings` |
| `PATCH` | `/orgs/{org_id}/settings` | `organization.settings.update` | `control_center_update_settings` |
| `GET` | `/orgs/{org_id}/subscription` | `entitlements.read` | `control_center_subscription` |
| `GET` | `/orgs/{org_id}/support-grants` | `support_grants.manage` | `support_grants_list` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/approve` | `support_grants.manage` | `support_grants_approve` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/deny` | `support_grants.manage` | `support_grants_deny` |
| `POST` | `/orgs/{org_id}/support-grants/{grant_id}/revoke` | `support_grants.manage` | `support_grants_revoke` |
| `GET` | `/orgs/{org_id}/team` | `members.read` | `control_center_team` |
| `GET` | `/permissions` | (authenticated user) | `permissions_list` |
| `GET` | `/roles` | (authenticated user) | `roles_list` |

`/auth/*` routes are public or token-based by design (credentials, one-time tokens). Platform (`/platform/*`) routes are documented in the Super Admin documentation.

## Notable request/response shapes

- `POST /orgs/{org}/companies` — `CompanyCreate`: `name`, `code`, `country`, `base_currency` required; optional profile, accounting settings (`financial_year_start_month/day`, `accounting_basis`, `vat_registered`, `vat_number`, `tax_jurisdiction`) and `assignments[]` (`user_id`, `assignment_type`, `responsibility`, `starts_on`, `ends_on`). Atomic.
- `POST /companies/{id}/status` — `{action: activate|deactivate|archive|restore, reason?}`.
- `POST /orgs/{org}/invitations` — `{email, role, company_ids[]}`; the response includes `email_delivery` (`configured`, `note`).
- `POST /orgs/{org}/ownership-transfer` — `{new_owner_user_id, confirm}` (`confirm` = organization slug).
- `GET /orgs/{org}/dashboard` — only the sections the caller may see are non-null (`team`, `pending_invitations`, `subscription`/`usage`, `recent_activity`).
- `GET /orgs/{org}/subscription` — read-only; `entitlements[]` rows carry `current` (null = not metered), `limit`, `unlimited`, `percent`, `status`.

## Compatibility with Phases 1–2

`make openapi-breaking BASE=<phase 2 commit>` reports no breaking change by the detector’s rules. One behavioural change the detector cannot see: `PATCH /companies/{id}` no longer accepts `status`; lifecycle moved to `POST /companies/{id}/status`. See the completion report, known issues.
