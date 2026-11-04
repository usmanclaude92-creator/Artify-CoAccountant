# Security Test Results — Phase 3

Everything marked *executed* was run in this repository’s sandbox (PostgreSQL 16, Redis 7, Chromium) on 2026-11-04 against a database rebuilt from migrations 0001→0005. Data is synthetic (`@example.com`). “Evidence” names the test; read the test for the exact assertions.

## The ten required checks

| # | Requirement | Evidence (executed) | Result |
|---|---|---|---|
| 1 | Organization A cannot access Organization B’s companies | `test_other_organizations_cannot_touch_these_endpoints` (every company endpoint with a foreign company/org id → 404); `test_cross_tenant_access_is_denied_with_404_and_audited` (Phase 1); e2e `switcher` (foreign company id → “no access”) | PASS |
| 2 | Organization A cannot access Organization B’s team members | `test_other_tenants_get_404_on_every_organization_endpoint` (team, members, invitations, accountants, assignments, activity, subscription, settings, dashboard); DB: `test_phase3_tables_are_isolated_between_organizations_and_companies` | PASS |
| 3 | An accountant cannot access an unauthorized company | `test_assignment_windows_control_access`, `test_accountant_sees_only_assigned_companies` (Phase 1), `test_new_roles_have_exactly_their_documented_powers`; e2e `controlcenter` (accountant sees only the assigned company) | PASS |
| 4 | Changing a company id in a request does not reach another company’s data | company endpoints answer 404 for ids outside the caller’s scope; `test_assignment_authorization_and_eligibility` (assigning with foreign ids); wizard ignores client-supplied organization ids (taken from the path and checked against memberships) | PASS |
| 5 | A user cannot assign themselves higher permissions | `test_invitation_rules_rank_duplicates_and_company_access` (rank rule), `test_users_cannot_escalate_their_own_privileges` (Phase 1), `test_ownership_transfer_is_the_only_way_to_hand_over` | PASS |
| 6 | A user cannot modify another organization’s subscription | `test_subscription_view_matches_the_platform_configuration_and_cannot_be_changed` (no write route exists: PATCH/PUT/POST/DELETE on the subscription URL answer 404/405, and the platform override endpoint rejects a customer token with 401), `test_subscription_visibility_is_permissioned_and_tenant_scoped` | PASS |
| 7 | Invitations cannot be accepted by unauthorized users | `test_only_the_invited_person_can_accept_and_never_twice`, `test_resend_cancel_decline_and_expiry` (revoked, declined, expired, replaced tokens refused) | PASS |
| 8 | Organization-level and company-level permissions are independently enforced | `test_profile_and_settings_have_separate_permissions`, `test_archive_permission_and_restore_respects_the_subscription_limit`, `test_new_roles_have_exactly_their_documented_powers`, `test_privileged_company_routes_declare_their_own_permission` | PASS |
| 9 | Archived or inactive memberships cannot be used to gain access | `test_membership_changes_remove_access_and_assignments_do_not_leak` (suspended/removed members refused on the next request; assignments of an inactive member grant nothing), `test_membership_status_changes_take_effect_immediately` (Phase 1) | PASS |
| 10 | Customer users cannot reach Super Admin | `test_customers_still_cannot_reach_super_admin_from_the_control_center`, `test_realms_are_separate` (Phase 1), e2e `superadmin` (customers cannot enter `/platform`) | PASS |

## Other Phase 3 controls tested

- Last-owner protection and ownership transfer (confirmation, MFA, target must be active).
- Practice manager MFA gate; new-role permission matrix; assistant accountants count against `limit.accountants`.
- Audit trail is not modifiable by the application role (`test_audit_log_is_not_modifiable_by_the_application_role`); every privileged change writes an event.
- Honest e-mail reporting (`email_delivery.configured` false without SMTP); notifications `notifications_sent: false`.
- Subscription numbers match the Phase 2 platform configuration and the live counts; provisioned organizations show true zero usage.
- Every route declares its authorization (`test_every_route_declares_its_authorization`, inspects the real route set) and privileged routes declare the specific key (`test_privileged_company_routes_declare_their_own_permission`).

## Mutation checks (do the tests fail when the control is broken?)

| Mutation | Outcome |
|---|---|
| Assignment window ignored in `resolve_principal` | caught — `test_assignment_windows_control_access` |
| `companies.archive` downgraded to `companies.update` | caught — `test_archive_permission_and_restore_respects_the_subscription_limit` |
| Invitation rank rule removed | caught — `test_invitation_rules_rank_duplicates_and_company_access` |
| Ownership-transfer confirmation ignored | caught — `test_ownership_transfer_is_the_only_way_to_hand_over` |
| `company_settings.manage` downgraded to `companies.update` | **survived the behavioural tests** — equivalent under the current catalog (no role holds one without the other). Added structural test `test_privileged_company_routes_declare_their_own_permission`; mutation now caught |
| Rank rule on **member role updates** removed | **survived** — equivalent under the current catalog (only roles ranked ≥ 80 hold `members.manage`, and the owner rules already cover them). Added `test_catalog_keeps_the_equivalence_the_behavioural_tests_rely_on`, which fails if the catalog changes so that the rule becomes reachable. The rule itself has no behavioural test |
| RLS policy of `member_profiles` opened to all organizations | **survived** (no test read that table across organizations). Added `test_phase3_tables_are_isolated_between_organizations_and_companies`; mutation now caught. A second mutation (`company_configurations` policy without the company scope) is also caught |

## Defects found by these checks

1. `test_outbox_dispatch_and_failure_backoff` began failing in the full suite: the dispatcher takes at most 100 events per call and the larger suite left more than 100 pending. The test now drains the queue first. This was a test-isolation fragility, not a product defect.
2. Disabling VAT while a VAT number existed raised an unhandled validation error (HTTP 500) — now cleared before validation and returned as 422 when inconsistent.
3. The provisioned-organization usage counters (Phase 2 defect): organizations created by Artify had no counters until their first mutation and showed “not metered” — fixed in migration 0005 and tested.

## Known security limitations (not hidden)

- `invitations.company_ids` is an array and cannot carry a foreign key; ids are validated against the organization at invitation **and** at acceptance, but the database does not enforce them.
- No second-person approval for ownership transfer or other sensitive actions; ownership transfer requires a fresh MFA-verified session only.
- Authorization is re-evaluated from the database on every request (membership status, role, assignment window), so role changes, suspensions and ended assignments take effect on the next call; the tests above exercise this. There is no cache to invalidate.
- No rate limit specific to invitation creation beyond the global limiter; per-organization quotas on pending invitations are not implemented.
- Automated accessibility checks do not replace a manual screen-reader review; no penetration test was performed.
