# Platform Administration Security (Super Admin, Phase 2)

The Super Admin is the most sensitive surface of the product: it can suspend customers, change what they may do and see who they are. This document states what protects it, as implemented and tested. Companion documents: [`AUTHENTICATION_AND_SESSION_DESIGN.md`](AUTHENTICATION_AND_SESSION_DESIGN.md) (realm, MFA, sessions), [`ROLE_AND_PERMISSION_MATRIX.md`](ROLE_AND_PERMISSION_MATRIX.md) §7 (platform roles), [`SUPPORT_ACCESS_POLICY.md`](SUPPORT_ACCESS_POLICY.md), [`THREAT_MODEL_AUTH.md`](THREAT_MODEL_AUTH.md).

## 1. Principles

1. **A separate world.** Platform administrators are not users of any organization. They have their own table, signing secret, token audience, DB role, cookies, and (in deployment) their own surface: `APP_SURFACE=console` makes `/platform/*` return 404 on the customer site.
2. **No authority from the browser.** The UI hides what a role cannot do; the API re-checks on every call. A hidden menu item is a convenience, never a control (e2e test: a support administrator who deep-links to `/platform/billing` or calls the BFF directly gets 403).
3. **Least privilege.** No role except the owner holds every permission. Sensitive powers are separate keys (`platform.billing.manage`, `platform.entitlements.override`, `platform.support.view_data`, `platform.admins.manage`, `platform.settings.manage`).
4. **No default access to customer business data.** The platform database role has no policy on companies, memberships, documents or ledgers. Cross-tenant overviews come from aggregate views; deeper access exists only through a customer-approved support grant.
5. **Everything sensitive is attributable.** Every mutation writes an append-only platform audit event with actor, target, organization, outcome and reason. Denied attempts are audited too.

## 2. Authentication of administrators

Phase 1 realm, unchanged: Argon2id passwords, mandatory TOTP for **every** platform administrator, access token 10 minutes, refresh 24 hours, rotating refresh tokens with reuse detection, session checked on every request. New in Phase 2:

- **Invitation only.** Administrators are created by an owner's invitation (`platform_admin_invitations`: single-use, hashed token, 7-day expiry). The invitee sets their own password; nobody else ever knows or sets it. The first administrator is created by the operations CLI (`artify-api create-platform-admin`).
- **Suspension is immediate.** Suspending an administrator marks the account disabled **and** revokes all their sessions (test: the session rows are revoked, not just the account flag).
- **No password hash exposure.** The platform DB role cannot `SELECT password_hash` (column-level grant); no API response contains it (tests).

## 3. Roles and permissions

| Role | Intent | Notable powers | Notably absent |
|---|---|---|---|
| `platform_owner` | operate the platform | every permission | — (still cannot change their own role or status) |
| `platform_admin` | run customers and plans | organizations (create/edit/suspend), plans, subscriptions, overrides, support request/revoke, audit, settings (read) | administrator management, billing writes, settings writes, using support grants |
| `platform_support` | help customers | read organizations/companies/subscriptions/usage, request support access, **use** an approved grant | any write to organizations, plans, billing; audit log; administrators |
| `platform_billing` (finance) | invoicing | invoices, payments, refunds, adjustments; read plans/subscriptions/usage | suspending organizations, support access, administrators |
| `platform_readonly` | oversight | read everything operational (incl. audit, administrators, settings) | every write; support grant use |
| `platform_auditor` | evidence review | audit log, health | everything else |

Permission keys and the role table live in `services/api/src/artify_api/permissions.py`; they are synchronized to the database by `artify-api seed`. Custom per-administrator permission sets are **not** implemented (deferred); roles are the unit of assignment.

### Escalation rules (all tested in `test_p2_admins.py`)

- Only `platform.admins.manage` (owner) can invite, change a role, suspend, reactivate or revoke sessions.
- **Nobody changes their own role or status.**
- Nobody grants or invites a role above their own (rank table), and cannot modify an administrator ranked above them.
- The **last active owner** cannot be demoted or suspended (defense in depth: because only an owner can act and nobody can act on themselves, the guard is unreachable through the API today; it protects against future changes).
- Role changes take effect on the next request (permissions are read from the database, not carried in the token).

## 4. Controls on sensitive actions

| Action | Required in the request | Server checks |
|---|---|---|
| Suspend / reactivate an **organization** | reason ≥ 10 chars **and** the organization slug typed exactly | state transition valid; audited with reason; history row |
| Suspend a **subscription**, set status (cancel, expire…) | reason + slug | transition matrix; event row |
| Change plan | reason; explicit `acknowledge_over_limit` if usage exceeds the target | active plan only; over-limit refused without acknowledgement |
| Archive a plan | reason + plan key | not the default sign-up plan |
| Limit override | reason, optional expiry in the future | numeric/flag type matches the entitlement |
| Role change, suspend an **administrator** | reason + the administrator's e-mail typed | no self-change, rank rule |
| Invoice void, refund, adjustment | reason (≥ 10) | balance rules; append-only records |
| Change a platform setting | reason | per-setting validation (strict types) |
| Request support access | reason ≥ 20 chars | hours ≤ platform cap; one open request per organization |

The typed confirmation is a guard against slips and replayed requests, **not** a second person. **Dual control (a second administrator approving) is not implemented**; see §7.

## 5. What the platform database role can read

| Data | Access | Mechanism |
|---|---|---|
| Organizations, subscriptions, plans, overrides, usage counters, settings | read (some write) | direct policies, column-limited updates |
| Owner name/e-mail, member/accountant/company counts, company list (name, code, country, status) | read | **definer-owned views** (`platform_org_overview`, `platform_company_overview`, `platform_user_totals`, `platform_outbox_overview`) |
| Members (name, e-mail, role) and the customer's audit log | read **only inside an active, customer-approved, unexpired support grant held by this administrator** | RLS policy on `app.platform_granted_org_ids()`, evaluated once per query |
| Companies' tax registration, invitations, devices, documents, ledgers | **none** (permission denied) | no grant at all |
| Administrator password hashes | **none** | column-level grant excludes it |
| Audit records | read + insert only; update/delete/truncate refused for every role incl. the schema owner | privileges + `forbid_mutation` triggers |

Tests: `test_platform_role_cannot_read_tenant_business_data`, `test_grant_scoping_is_enforced_by_row_level_security_in_the_database`, `test_audit_viewer_filters_paginates_and_is_append_only`.

## 6. Audit

`platform_audit_events` (partitioned, append-only) now carries `outcome` (`success|failure|denied`) and `reason`, plus actor, action, target type/id, target organization, IP, correlation id and a redacted summary. Secrets and tokens are never written (test scans the table for the invitation token, password and hash). Viewer: filter by text, action prefix, outcome, organization, actor, date range; paginated; detail view. Permission failures (`authz.denied`) and misuse of a support grant (`support.access_denied`) are written in their **own** transaction so they survive the rollback of the failing request.

## 7. Threats, mitigations and what is still open

| Threat | Mitigation | Evidence / status |
|---|---|---|
| Customer reaches Super Admin | separate token audience and key; separate DB role; edge gate; `APP_SURFACE` | `test_customer_tokens_cannot_reach_super_admin`, `test_platform_token_is_useless_on_customer_routes`, e2e |
| Administrator exceeds their role | per-route permission, verified statically and dynamically | `test_every_platform_route_declares_a_permission_that_matches_its_verb`, role matrix tests |
| Privilege escalation through role management | rank rule, no self-change, owner-only | `test_no_self_escalation_and_no_privilege_above_your_own` |
| Compromised administrator session | 10-minute access tokens, revocation on suspend, "sign out everywhere", MFA | tests; **no IP allow-list, no device binding** |
| Silent data access by staff | grant-gated RLS, customer approval, double audit | `SUPPORT_ACCESS_POLICY.md` |
| Rogue single administrator | reasons + typed confirmation + audit | **dual control NOT implemented** (DEFERRED; recommended before production) |
| Tampering with evidence | append-only tables and triggers | tested for platform audit, subscription events, status history, payments, adjustments |
| Stale or forged requests (CSRF) | BFF custom header + same-origin check + SameSite=Strict | Phase 1 e2e; PUT added to the BFF in Phase 2 |
| Platform surface exposed on the public host | `APP_SURFACE=console` returns 404 for `/platform/*` | Phase 1 proxy; the production hostname split is Phase 12 |

**Open / not implemented:** dual control; optional IP allow-list; step-up re-authentication before the most sensitive actions; per-administrator custom permissions; notification e-mails to other owners when an owner is created or demoted; an external review of this design.
