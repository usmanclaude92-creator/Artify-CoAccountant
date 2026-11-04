# Team, Accountant and Assignment Management

## Team members (`/team`)

`GET /orgs/{org}/team` returns members with role, status, professional profile, last activity (from the audit log) and number of currently assigned companies (only for viewers who see all companies; `null` otherwise). Supports search, status/role filter, sort and pagination.

Actions (all server-enforced):

- **Change role** (`PATCH /orgs/{org}/members/{user}`, `members.manage`): only roles at or below the caller’s own rank; never `firm_owner` (use ownership transfer); never on yourself; never on someone ranked above you. Privileged roles require MFA before they work. Moving a person to a role that reaches all companies clears their assignments; moving to a restricted role re-checks assignability.
- **Suspend / reactivate / remove**: immediate effect on the next request (sessions are checked per request). The last owner cannot be suspended or removed. Reactivation re-checks the user limit.
- **Professional profile** (`PATCH …/profile`, `member_profiles.manage`): job title, phone, licence number. Descriptive only; it never grants or limits access.
- **Ownership transfer**: see [`ORGANIZATION_MANAGEMENT.md`](ORGANIZATION_MANAGEMENT.md).

## Invitations (`/invitations`)

- Create (`POST /orgs/{org}/invitations`, `invitations.manage`) with role (rank rule) and optional `company_ids` to be assigned on acceptance. Duplicate active membership and duplicate pending invitation are rejected.
- Tokens are random, stored only as a hash, single use, with expiry. The token is the credential for accept/decline; it is shown only in the e-mail (or, in local development, the API console).
- **Resend** renews the token and expiry (old link stops working) and increments `resent_count`. **Cancel** revokes immediately.
- **Accept** (`POST /auth/invitations/accept`): creates or links the user, re-checks limits and the active-membership rule at acceptance time, creates assignments for the listed companies, writes audit. An invitation addressed to one e-mail cannot be accepted as another account. **Decline** (`POST /auth/invitations/decline`) marks it declined.
- **E-mail honesty**: the response carries `email_delivery`. Only the SMTP backend counts as configured. Otherwise the UI states that nothing was sent.

## Accountants (`/accountants`)

Professional staff = roles `accountant`, `assistant_accountant`, `staff` (these are the roles counted by `limit.accountants`). The list shows professional title and licence **separately from** the authorization role, active company count and assigned companies (limited to those the viewer may see). Detail: `GET /orgs/{org}/accountants/{user}`.

## Assignments (`/assignments` and the company page)

Many-to-many between companies and assignable members (`accountant_assignments`). Each assignment has `assignment_type` (primary/secondary/reviewer), `responsibility`, `starts_on`, `ends_on`, active/inactive and history.

- Only members whose role does **not** already reach all companies can be assigned (DB trigger `check_assignment_member` and API check).
- An assignment grants visibility only while `starts_on <= today` and (`ends_on` is null or `>= today`) **and** the member’s organization membership is active. Ending or removing an assignment removes access on the next request.
- Create/change/remove need `assignments.manage` and the company must be active. Every change is audited and appears in the assignment history (`GET /companies/{id}/assignments/history`).
