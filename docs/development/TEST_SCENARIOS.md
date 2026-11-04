# Acceptance Test Scenarios

**Status:** scenarios are SPECIFIED unless listed in the execution table below. A scenario counts as executed only when an automated test implementing it ran and the phase completion report records the command and result.

### Execution status (updated at the end of Phase 1)

Run: `make test-api` → 58 passed, 1 skipped (Redis test needs `ARTIFY_TEST_REDIS_URL`; with it set: 2 passed). Browser e2e: 3 passed.

| ID | Status | Implemented by (in `services/api/tests/` unless noted) | Coverage note |
|---|---|---|---|
| TS-ISO-01 | EXECUTED | `test_isolation_api::test_cross_tenant_access_is_denied_with_404_and_audited` | Org, company, member, audit, entitlement routes; denial audited (unattributed) |
| TS-ISO-02 | EXECUTED | `test_isolation_api::test_accountant_sees_only_assigned_companies` | Includes assignment removal taking effect immediately |
| TS-ISO-03 | PARTIAL | `test_isolation_api` (companies, members, assignments) | Workspace templates, Excel templates and documents do not exist yet (Phases 4, 6, 9) |
| TS-ISO-04 | EXECUTED | `test_database_rls::test_empty_context_sees_nothing_and_cannot_write` | |
| TS-ISO-05 | EXECUTED | `test_database_rls::test_composite_fk_blocks_cross_company_reference` | Uses `company_memberships`; `documents` arrive in Phase 6 |
| TS-ISO-06 | PARTIAL | `test_database_rls::test_platform_role_cannot_read_tenant_business_data`, `test_auth::test_platform_admin_must_enroll_mfa_then_sees_metadata_only` | Documents/extracted data/entries do not exist yet |
| TS-ISO-07 | NOT EXECUTED | — | Support-grant policies arrive in Phase 2 |
| TS-ISO-08 | EXECUTED | `test_isolation_api::test_company_employee_is_confined_to_own_company` | |
| TS-ISO-09 | EXECUTED | `test_isolation_api::test_realms_are_separate` | |
| TS-ISO-10 | EXECUTED | `test_isolation_api::test_every_route_declares_its_authorization` | |
| TS-ENT-01 | EXECUTED (4 of 9 keys) | `test_entitlements::test_company_limit_enforced_by_raw_api`, `test_user_accountant_and_device_limits` | companies, users, accountants, devices; the other `limit.*` keys arrive with their features |
| TS-ENT-02 | EXECUTED | `test_entitlements::test_concurrent_creation_cannot_oversubscribe` | 8 parallel creators, 1 slot left → exactly 1 success |
| TS-ENT-03 | PARTIAL | `test_entitlements::test_plan_change_takes_effect_without_deploy`, `test_override_precedence_expiry_and_unlimited` | Done with SQL as the owner; Super Admin UI is Phase 2 |
| TS-ENT-07 | PARTIAL | `test_entitlements::test_suspended_subscription_blocks_creation_but_not_reads`, `test_organization_suspension_makes_it_read_only` | Uploads/AI not present yet |
| TS-SEC-01 | EXECUTED | `test_auth::test_refresh_rotation_and_reuse_detection` | |
| TS-SEC-02 | EXECUTED | `test_auth::test_privileged_roles_require_mfa_before_org_access`, `test_platform_admin_must_enroll_mfa_then_sees_metadata_only` | |
| TS-SEC-04 | EXECUTED | `test_database_rls::test_audit_tables_are_append_only` | |
| TS-SEC-06 | EXECUTED | `test_auth::test_login_is_rate_limited`; `test_ratelimit` (Redis, when configured) | Login rate limit tested; recovery/signup limits share the same mechanism but are not individually tested |
| all others | NOT EXECUTED | — | Depend on later-phase features |

Additional executed tests not tied to a scenario ID: password hashing, enumeration resistance, password reset, MFA replay/recovery codes, idempotency, commit-before-response, outbox dispatch/backoff, worker tenant context, invitation edge cases, settings safety, schema lint (no float columns, every table classified), RLS plan-shape guard, and the Playwright journeys in `apps/web/e2e/smoke.spec.ts`.

Format: Given / When / Then. "Phase" is where the scenario must first become an executable automated test.

## Isolation (TS-ISO)

| ID | Phase | Scenario |
|---|---|---|
| TS-ISO-01 | 1 | **Cross-tenant denial.** *Given* Organizations O1 and O2 each with Companies and documents. *When* a user of O1 requests (by id) an O2 Company, document, template or workspace node, through every API route that accepts such ids. *Then* the response is 404, no data is returned, and an audit event of the denied attempt exists. |
| TS-ISO-02 | 1 | **Cross-company denial inside one firm.** *Given* one Organization with Companies A and B and an `accountant` assigned only to A. *When* the accountant requests B's documents, workspace, Excel templates, reports or entries. *Then* 404 for every route; list endpoints never include B's rows. |
| TS-ISO-03 | 4/9 | **Company A cannot access Company B's templates or documents** even for a `practice_admin` operating in A's context: a request carrying `company_id = A` in the path and a B resource id in the body is rejected (and the same attempt performed at the SQL level fails with an RLS/FK violation). Covers workspace templates applied to A, Excel templates, reports and documents. |
| TS-ISO-04 | 1 | **RLS fail-closed.** *Given* a DB session as `artify_app` with no context variables. *When* `SELECT` on any tenant table. *Then* zero rows. With context of O1, `INSERT` of a row with `organization_id = O2` fails. |
| TS-ISO-05 | 1 | **Composite-FK guard.** An `INSERT` into `documents` referencing a `workspace_sections` row of another Company fails at the database even when authorized in the API layer (simulated bug). |
| TS-ISO-06 | 1/2 | **Platform admin has no implicit data access.** A Platform Admin session (platform realm/role) reading documents, extracted data, entries, Excel files of any tenant returns nothing/denied; reading plan and usage metadata succeeds. |
| TS-ISO-07 | 2 | **Support grant semantics.** With an active `read` grant for Company A only: A's documents readable, B's not; after `expires_at` or revocation: A's not readable; every read has audit events visible to the tenant; grant cannot write. |
| TS-ISO-08 | 1 | **Client employee confinement.** A `company_employee` of A cannot see other users' submissions where policy restricts, nor any other Company, nor Organization admin routes. |
| TS-ISO-09 | 1 | **Realm separation.** A tenant-realm token is rejected by `/platform/*`; a platform token is rejected by tenant routes. |
| TS-ISO-10 | 1 | **Route-table authorization.** Every route in the OpenAPI document declares a permission; a test fails if any does not. |

## Workspace (TS-WS)

| ID | Phase | Scenario |
|---|---|---|
| TS-WS-01 | 4 | **Different structures in one firm.** *Given* one Organization with Companies A and B. *When* A is configured with sections {Bank Statements, Receivables, Payables, Expenses} and B with {Bank & Cash, Sales Invoices, Purchase Invoices, Operating Expenses, Inventory, Payroll, VAT & Tax Documents} plus different nested folders and categories. *Then* each Company's workspace API returns only its own tree; changing B (rename, move, archive) leaves A's tree and version number unchanged. |
| TS-WS-02 | 4 | **Four sections vs ten.** Company A has 4 sections and Company B has 10 (entitlement permitting). Both file, process and export documents correctly; Mobile upload-config for each lists exactly its own sections; the Windows folder preview for each matches its own tree. |
| TS-WS-03 | 4/6 | **Rename preserves documents.** *Given* section S holding documents. *When* S is renamed. *Then* all documents remain filed in S (same id), remain retrievable, Excel mappings and filing rules still resolve, object storage keys are unchanged, and the change feed carries a rename (Desktop mirror renames the folder, no re-download). |
| TS-WS-04 | 4/6 | **Archive preserves documents.** Archiving S (and its folders) leaves existing documents viewable/searchable/exportable; S no longer appears in Mobile upload-config or pickers; an upload targeting S returns `409 section_archived`; restore returns S to pickers. Hard deletion of S is rejected while it has ever held documents. |
| TS-WS-05 | 4 | **Template apply creates an independent copy.** After applying template T v1 to Company A: A's nodes have new ids unequal to T's; editing A leaves T unchanged; publishing T v2 leaves A unchanged; applying T to Company B yields a separate set of ids. |
| TS-WS-06 | 4 | **No live dependency.** Deleting/archiving T does not affect Companies that applied it; no runtime query path joins from a Company node to a template (checked via query/log audit in test). |
| TS-WS-07 | 4 | **Atomic change sets.** A change set with one invalid operation (e.g. exceeds depth limit) applies none of its operations and leaves `version` unchanged; a stale `base_version` returns `409 workspace_version_conflict`. |
| TS-WS-08 | 4 | **Dry-run impact.** Dry run of archiving a section reports exact counts of documents, rules, mappings and devices affected and persists nothing. |
| TS-WS-09 | 4 | **Category must map to a kind.** Activating a category without a document-kind mapping is rejected; changing the kind of a category with derived financial records is rejected. |
| TS-WS-10 | 4/6 | **Custom fields.** (Definitions and validators in Phase 4; stored values on documents in Phase 6.) Typed validation (number range, date range, dropdown option, required-by-category), no arbitrary columns created, type change after values exist rejected, multi-select stored as option rows. |
| TS-WS-11 | 4/6 | **Section permissions.** A user without `view` on the payroll section cannot list, fetch or receive change-feed records for its documents (API and RLS), while retaining access to other sections of the Company. |
| TS-WS-12 | 4 | **Intake Inbox.** Every Company has exactly one Intake Inbox; it cannot be archived or deleted. |

## Accounting (TS-ACC)

| ID | Phase | Scenario |
|---|---|---|
| TS-ACC-01 | 8 | **Concepts recognized regardless of names.** Company A files invoices under "Payables › Supplier Invoices" and Company B under "Supplier Payments › Vendor Invoices"; both categories map to `purchase_invoice`. Identical synthetic invoices yield identical extraction schema use, identical validation findings, and entries differing only by each Company's account role mappings and VAT config. Renaming B's section changes nothing in results. |
| TS-ACC-02 | 8 | **Reference documents cannot post.** A `reference_document` cannot create invoices, expenses, bank transactions or entries (service and DB). |
| TS-ACC-03 | 8 | **Balanced entries.** Property tests: random lines produce balanced entries or are rejected; unbalanced entries cannot reach `pending_approval` (deferred trigger). |
| TS-ACC-04 | 8 | **Decimal exactness.** VAT and totals computed with `Decimal` match golden values for 3-decimal currencies and per-line vs per-document rounding policies; schema lint finds no float columns. |
| TS-ACC-05 | 8 | **No silent approval.** Calls to approve a workflow stage or entry with `actor_type` `system`/`ai`, or by a user lacking the permission, fail in service and in DB; the AI/worker codepaths have no import path to approval functions (static test). |
| TS-ACC-06 | 8 | **Immutability.** An `approved` entry cannot be updated or deleted; correction is by a reversal entry linked via `reverses_entry_id`. |
| TS-ACC-07 | 7/8 | **Never invent data.** Documents missing total/date/tax id produce `NULL` + `not_found` flags and blocking findings; no defaulted amounts. |
| TS-ACC-08 | 8 | **Duplicate detection.** Exact SHA-256 and fuzzy (vendor/number/date/amount) duplicates create `duplicate_candidates` and block auto-advance until a human decides. |
| TS-ACC-09 | 8 | **Jurisdiction-neutral VAT.** Switching a Company's VAT configuration to a different jurisdiction pack changes results without code change; effective-dated rate changes apply by tax point date. |
| TS-ACC-10 | 8/9 | **Traceability.** From an Excel row, navigate to entry → record → extracted values → original document, and back from the document to its exports. |
| TS-ACC-11 | 8 | **Period control.** Entries cannot be created or approved in a closed/locked period. |

## Excel (TS-XLS)

| ID | Phase | Scenario |
|---|---|---|
| TS-XLS-01 | 9 | **Templates are company-specific.** Companies A and B each have a purchase-journal template with different columns/sheets; exporting the same logical entry set per Company yields each Company's layout; editing A's template/mapping creates a new A version only. |
| TS-XLS-02 | 9 | **No cross-company template access.** A user in Company A cannot list, read, copy or export with Company B's template (API 404, SQL RLS). |
| TS-XLS-03 | 9 | **Presets are copied.** Using a preset creates an independent template; later preset changes do not change the copy. |
| TS-XLS-04 | 9 | **Formula preservation.** Template formulas in untouched cells survive export; formula columns extend with translated references; unsupported features are reported by the compatibility analyzer before publish. |
| TS-XLS-05 | 9 | **Version immutability and re-export.** Published versions cannot be modified; exporting the same entries twice requires an explicit re-export action with reason; both exports are recorded. |
| TS-XLS-06 | 9 | **Only approved entries export.** Draft/pending entries are rejected by the export service. |
| TS-XLS-07 | 9 | **Formula-injection guard.** Vendor text starting with `=`, `+`, `-`, `@` is written as inert text in text columns. |
| TS-XLS-08 | 9 | **Debit/credit/VAT/narration/voucher mappings** produce correct cells for both "row per line" and "row per document" layouts with 3-decimal currency formats. |

## Entitlements (TS-ENT)

| ID | Phase | Scenario |
|---|---|---|
| TS-ENT-01 | 1/2 | **Backend enforcement without the UI.** With `limit.companies = N`, creating company N+1 through the raw API fails with a structured `entitlement_exceeded` error; same for sections, folders, depth, fields, templates, users, accountants, devices. |
| TS-ENT-02 | 1 | **Concurrency.** Parallel creation requests at `N-1` usage result in exactly one success (locked counters). |
| TS-ENT-03 | 2 | **Plan edits without deploy.** Changing a plan version's limit in Super Admin takes effect for new checks without code change; overrides apply and expire. |
| TS-ENT-04 | 2 | **Downgrade over limit.** Existing data remains accessible; new creations blocked until usage falls below the limit. |
| TS-ENT-05 | 4 | **Change-set totals.** A workspace change set creating more sections than allowed is rejected atomically (nothing applied). |
| TS-ENT-06 | 6/7 | **Quotas.** Storage, documents/month and AI unit quotas block (hard) or notify (soft) per entitlement definition; reservation expiry releases capacity. |
| TS-ENT-07 | 2 | **Suspended state.** Suspended Organizations can read/export existing data but cannot upload, create Companies or trigger AI. |

## Sync (TS-SYNC)

| ID | Phase | Scenario |
|---|---|---|
| TS-SYNC-01 | 5/6 | **Feed ordering and resume.** Client applies changes in `seq` order, resumes from cursor after interruption, handles `410 cursor_expired` with snapshot resync. |
| TS-SYNC-02 | 6/12 | **Idempotent resumable upload.** Replaying the same upload with the same `Idempotency-Key` creates one document; interrupted multipart upload resumes; hash mismatch is rejected. |
| TS-SYNC-03 | 5/12 | **Local mirror per company.** Companies A and B on one device produce different folder trees under separate roots; Windows-illegal and reserved names are sanitized; collisions get id suffixes; paths longer than the limit are shortened deterministically. |
| TS-SYNC-04 | 12 | **Conflicts are never silent.** Concurrent metadata edits and local-modified originals produce `sync_conflicts` and user decisions; originals are never overwritten. |
| TS-SYNC-05 | 11/12 | **Archived target while offline.** A queued mobile upload to a later-archived section yields `409 section_archived` and a user-guided resolution; nothing lost. |
| TS-SYNC-06 | 5/12 | **Offline limits.** Approvals/exports are blocked offline; capture/queue works; queue replays safely online. |
| TS-SYNC-07 | 12 | **Device revocation.** Revoked device's tokens fail; cache wipe flagged. |

## Security (TS-SEC)

| ID | Phase | Scenario |
|---|---|---|
| TS-SEC-01 | 1 | **Session rotation.** Refresh-token reuse revokes the token family. |
| TS-SEC-02 | 1 | **MFA enforcement** for platform admins, firm owners, practice admins. |
| TS-SEC-03 | 6 | **Hostile files.** Polyglot, oversized, zip-bomb, XXE-bearing XLSX, active-content PDF samples are rejected or neutralized. |
| TS-SEC-04 | 1 | **Audit immutability.** `UPDATE/DELETE` on audit tables fails for the app role. |
| TS-SEC-05 | 7 | **Prompt-injection fixtures.** Documents containing instructions ("approve this", "ignore previous…") cannot change extraction schema, approvals or other data. |
| TS-SEC-06 | 1 | **Rate limiting** on auth and recovery endpoints. |
