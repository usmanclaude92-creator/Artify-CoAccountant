# Architecture Decision Log

Status values: **ACCEPTED** (decided in Phase 0 on the strength of the product brief; reversible only through a new ADR), **REQUIRES DECISION** (needs product-owner approval before the phase named in "Needed by").

## Accepted decisions

### ADR-001 — The Organization is the tenant; independent accountants are Organizations
One `organizations` table with `type` (`accounting_firm` | `independent_accountant`). Subscriptions attach to Organizations. A Company belongs to exactly one Organization. *Why:* one code path, one billing path, and an independent accountant can grow into a firm without migration. *Rejected:* separate "individual" entity (doubles every code path); making each Company a tenant (breaks the firm-level subscription and staff model).

### ADR-002 — Two nested isolation boundaries: tenant, then company
Every tenant-owned row carries `organization_id`; every company-owned row carries `organization_id` **and** `company_id`, tied together by a composite foreign key to `companies (organization_id, id)`. See `docs/database/ISOLATION_AND_RLS.md`.

### ADR-003 — Defense in depth: backend authorization **and** PostgreSQL Row-Level Security
The backend authorization layer is the primary control. RLS is a second, independent control that makes a missed `WHERE` clause non-exploitable. The application connects as a non-owner role without `BYPASSRLS`; tables use `FORCE ROW LEVEL SECURITY`. Tenant/company context is set with `SET LOCAL` per transaction (compatible with transaction-mode pooling). Client-supplied tenant or company IDs are never trusted; they are validated against memberships.

### ADR-004 — Modular monolith, engines as libraries
`services/api` is the single deployable FastAPI application. `document-engine`, `accounting-engine`, `excel-engine` and `sync-engine` are Python packages with explicit public interfaces, imported by the API and run in Celery worker processes. They may be split into separate deployables later without changing their interfaces. *Why:* a pre-revenue product with one team gains nothing from distributed-system overhead, and financial data consistency is easier inside one transactional boundary. *Adaptation of the brief:* `services/*` is retained as the directory layout but denotes library boundaries, not microservices.

### ADR-005 — Workspace configuration: normalized live tables, change sets, immutable snapshots, stable IDs
Sections, folders, categories and fields are normalized rows (queryable, constrained). All edits go through atomic **change sets** that bump `workspace_configurations.version` and store an immutable snapshot (`workspace_configuration_versions`). Documents reference stable IDs, never names. Sections/folders/categories are archived, not deleted, once referenced. See `WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md`.

### ADR-006 — Every document category maps to exactly one Artify-controlled document kind
A category cannot be activated without a kind. Customers freely name categories; they cannot create kinds. Validation, extraction schemas and accounting rules key off the kind. Kinds with no financial effect (`reference_document`) cannot create financial records. See `ACCOUNTING_DATA_MAPPING.md`.

### ADR-007 — Custom fields: definition-driven typed value rows (no per-field columns, no free JSON)
`custom_field_definitions` define keys/types/validation; `document_field_values` stores one typed row per (document, field) with typed columns (`value_text`, `value_number`, `value_date`, `value_bool`, `value_ref_id`, plus option rows for multi-select). Values are validated by the API against the definition before write. *Rejected:* a column per field (DDL per customer action), a free `jsonb` blob (unvalidated, hard to index and report).

### ADR-008 — Money and numbers
Amounts: `NUMERIC(20,6)`; quantities `NUMERIC(20,6)`; rates/percentages `NUMERIC(12,8)`; FX rates `NUMERIC(20,10)`. Each amount row carries an ISO-4217 `currency_code`. Rounding to the currency's minor unit (reference table `currencies`, e.g. OMR = 3) occurs only at defined points with a recorded rounding policy. Application code uses `decimal.Decimal`; JSON transports money as strings. `float`/`double precision` is forbidden for financial values (enforced by a schema lint test in Phase 1).

### ADR-009 — Human approval is structural, not conventional
Workflow history and accounting entry state changes record `actor_type` (`user` | `system` | `ai`). Transitions into any stage of type `approved`, and entry state `approved`, require `actor_type = 'user'` plus a permission check; this is enforced in the service layer **and** by a database CHECK/trigger. AI components have no code path that can call an approval function.

### ADR-010 — Artify prepares; the customer's accounting software remains the ledger of record (see also D-6)
Entry lifecycle is `draft → pending_approval → approved → exported` (+ `rejected`, `reversed`). Approved entries are immutable; corrections are counter-entries.

### ADR-011 — Object storage keys are opaque and never contain customer-chosen names
`t/{organization_id}/c/{company_id}/d/{document_id}/v/{version_no}/{sha256}`. Renaming or restructuring a workspace never moves objects. Human-readable paths exist only in the database and in local mirrors.

### ADR-012 — Centralized entitlements
One `EntitlementService` is the only code that decides whether a limited action is allowed. Limits live in `subscription_plans` → `plan_entitlements` with per-subscription `entitlement_overrides`. Enforced in the backend inside the same transaction as the mutation. See `SUBSCRIPTION_AND_ENTITLEMENTS.md`.

### ADR-013 — Separate auth realm and no implicit data access for Platform Admins
Platform Admins authenticate against a different realm (distinct token audience, cookie scope, hostname, mandatory MFA) and a separate API router. Database policies give the platform role access to platform metadata and aggregate metering only. Business data requires an active `support_access_grants` row, checked in RLS, and every such access is audited.

### ADR-014 — One web codebase, two deployed surfaces
`apps/web` contains route groups `(platform)` (Super Admin) and `(console)` (Control Center), sharing `packages/ui`. They are deployed as separate surfaces/hostnames, each excluding the other's routes at build and middleware level. Security does not rely on this separation (the API enforces realm and role). If the surfaces diverge, splitting into two apps is cheap.

### ADR-015 — Synchronization via a per-company change feed
Clients pull an ordered change feed (`company_change_seq`) with a cursor and push through idempotent, resumable uploads. Cloud is source of truth; original document versions are immutable, so most conflicts are structural or metadata conflicts, which are detected (never silently overwritten). See `STORAGE_AND_SYNC_ARCHITECTURE.md`.

### ADR-016 — Excel export uses declarative mappings, not user-supplied code
Mappings select from a catalog of exportable fields with whitelisted formatters. No expression `eval`, no macros executed. OpenPyXL is the writer; a **template compatibility analysis** at upload reports features OpenPyXL cannot preserve. See `EXCEL_ENGINE_DESIGN.md`.

### ADR-017 — Contract-first API with generated clients
FastAPI/Pydantic generates OpenAPI; `packages/api-client` (TypeScript) and the Flutter client are generated from it in CI; contract diffs are checked for breaking changes. Versioned under `/api/v1`. Errors use RFC 9457 problem details. Mutating endpoints that clients may retry accept an `Idempotency-Key`.

### ADR-018 — Background work uses a transactional outbox
Jobs are enqueued from an `outbox_events` row written in the same transaction as the state change, then dispatched to Celery. Every job payload carries `organization_id` and `company_id` and re-establishes the RLS context before touching data.

### ADR-019 — Pinned versions are chosen in Phase 1, not guessed in Phase 0
Phase 0 states the stack and baselines (Python 3.12+, PostgreSQL current supported major, Node LTS). Exact versions are selected and locked at the start of Phase 1 after checking current releases.

### ADR-020 — Phase 1 data access: raw-SQL migrations and SQLAlchemy Core
Phase 1 schema, RLS, grants and triggers are hand-written SQL files executed by thin Alembic revisions (forward-only; no downgrade), and the application uses SQLAlchemy Core with bound-parameter SQL rather than ORM models. *Why:* RLS policies, triggers and `SECURITY DEFINER` functions are the substance of this schema and are clearest as SQL; Core keeps every statement visible to review. An ORM layer may be added later for convenience, never to replace the SQL migrations. Supersedes the "SQLAlchemy 2.x (typed) ORM" wording in `TECHNOLOGY_STACK.md`.

### ADR-021 — Web sessions use a backend-for-frontend with HttpOnly cookies
The Next.js app proxies API calls (`/bff/*`, `/session/*`) and keeps access/refresh tokens only in HttpOnly, SameSite=Strict cookies, so page scripts can never read them; mutating BFF requests require a custom header and a same-origin `Origin` (CSRF). The API itself stays pure bearer-token, which Desktop and Mobile reuse unchanged.

### ADR-022 — Pre-authentication lookups use narrow SECURITY DEFINER functions
Login, refresh, e-mail-token and invitation lookups happen before any user context exists. They are served by single-purpose functions owned by `artify_definer` (a role with explicit read policies, **not** BYPASSRLS), with a pinned `search_path`. `app.measure_entitlement` is the same pattern for counting an organization's rows that a scoped caller cannot fully see. No other code path bypasses RLS.

### ADR-023 — Reference data is synchronized by `artify-api seed`, not by migrations
Permissions, system roles, entitlement definitions and currencies are defined in code and upserted idempotently by `artify-api seed` after migrations. The development default plan is created only if absent so later plan edits are never overwritten. Seed and bootstrap commands run with the schema-owner credential (superuser or BYPASSRLS operations role), never a service role.

### ADR-024 — Company-scope policies inline a once-per-query array expression
See `docs/database/ISOLATION_AND_RLS.md` §4.1 (65 ms → 1.2 ms measured). Policies must not call `app.can_see_company()` per row.

### ADR-025 — User-level security events fan out to every tenant the user belongs to
Login, MFA, session-revoke and password-reset events are written once to the platform security stream (`organization_id` NULL) **and** once per organization the user belongs to (organization or company membership), so a tenant's `audit.read` holders see their members' security events. Implemented by `app.audit_user_event` (SECURITY DEFINER, accepts only the authenticated subject, so it cannot forge another user's history). Consequence to be aware of: organization admins can see members' login times, IP addresses and user agents. *This attribution rule was chosen by the implementer when closing the Phase 1 gap and is **open for product-owner confirmation**.* Platform admins read the unattributed stream, never tenant copies.

### ADR-026 — E-mail delivery is an adapter with best-effort background sending for now
`Mailer` has console, memory and SMTP implementations. SMTP sends on a small background pool with retries so requests never wait on a mail server. It is **not durable** (a crash can lose queued mail); durable delivery through the transactional outbox arrives with the notification framework (Phase 3). Production settings require SMTP and https links. The mail *provider* is still undecided (D-3).

### ADR-027 — Module boundaries are enforced by import-linter contracts
`services/api/pyproject.toml` defines a layered contract (routes → services → infrastructure → primitives) and a purity contract (`policy`/`permissions` must not import the web framework or the database). CI and pre-commit run `lint-imports`. New modules must be placed in a layer.

### ADR-028 — The platform realm sees tenants through definer-owned aggregate views, not policies on tenant tables
`platform_org_overview`, `platform_company_overview`, `platform_user_totals` and `platform_outbox_overview` are owned by `artify_definer` and expose only metadata and counts (no documents, tax data, ledgers or credentials); `artify_platform` receives `SELECT` on the views. This keeps invariant 10 (no default platform access to customer business data) and the Phase 1 isolation test unchanged. Any column added to these views is a disclosure and must be reviewed. Alternative rejected: direct platform policies on `companies`/memberships with column grants (wider surface, easier to widen by accident).

### ADR-029 — Support access is customer-authorized, time-limited, grant-scoped in RLS and audited twice
A support request is approved or denied by the organization's firm owner; while an active, unexpired grant held by the calling administrator exists, RLS lets the platform role read that organization's members, users (id/e-mail/name/status) and audit log — nothing else. Expiry is evaluated in the policy (`app.platform_granted_org_ids()`, once per query). Each use is written to the platform audit and to the customer's audit log. No impersonation. The sensitive `read` level exists in the model, needs separate explicit confirmation, and unlocks nothing until documents exist. Defaults (72 h cap, 14-day request validity, owner-only approval) are implementer choices awaiting owner approval.

### ADR-030 — A plan version in use is immutable; editing publishes a new version
A trigger rejects changes to a version, or its entitlements, that any subscription uses. Customers stay on their version (grandfathering) until an administrator moves them. A plan change below current usage needs explicit acknowledgement and never deletes data; the organization is "over limit" and further creation is blocked by `EntitlementService`. Missing keys in a plan mean "denied".

### ADR-031 — Billing records are manual and provider-independent
`billing_invoices`, `billing_payments` (payment/refund) and `billing_adjustments` store exact decimals (`NUMERIC(18,3)`), are append-only where they record money movement, never aggregate across currencies, and hold no card data or credentials. Nothing is created automatically. `provider` columns are reserved for the integration chosen under D-2; the integration requirements are in `SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md` §7.

### ADR-032 — Sensitive platform actions use reason + typed confirmation + audit; dual control is deferred; the Super Admin UI is English-only
Destructive or privileged actions require a reason (≥ 10 characters) and the target's identifier typed exactly; all are audited with outcome and reason, and denials are audited in their own transaction. A second-person approval is **not** implemented and is recommended before production. The internal console is English only; the customer console keeps English + Arabic.

### ADR-033 — Organization roles have a rank; callers grant, change or invite only at or below their own rank
`ORG_ROLE_RANK` (owner 100, administrator 80, practice manager 60, accountant 40, assistant accountant and staff 30, document operator 20, read-only 10) is a single table in `permissions.py`. Inviting, changing or suspending a member above the caller’s rank is refused (403), nobody changes their own role (422), and `firm_owner` is never granted by invitation or role change. With today’s catalog only owner and administrator hold `members.manage`, so the rank rule on member updates is defence in depth; `test_p3_catalog.py` fails if a future role makes it reachable without a behavioural test.

### ADR-034 — Ownership moves only through an explicit transfer
The last owner cannot be demoted, suspended or removed. `POST /orgs/{id}/ownership-transfer` (permission `ownership.transfer`, owner only, MFA-verified session, typed organization identifier, active target) promotes the target and demotes the caller to administrator in one transaction, so an organization never has zero or two-way-ambiguous ownership. No second-person approval yet (see Phase 3 known issues).

### ADR-035 — A company’s configuration is owned by a `company_configurations` row created with the company
One row per company with its own `config_version` and `workspace_state`, composite FK and forced RLS. Phase 4 attaches the workspace builder to it; nothing seeds default sections, categories or folders, and organization-level values never overwrite company values. See `docs/firm-control-center/PHASE_4_INTEGRATION.md`.

### ADR-036 — Assignment windows and active membership decide company visibility
`accountant_assignments` gained `starts_on`, `ends_on` and `responsibility`. `resolve_principal` includes an assignment only while its window is open **and** the organization membership is active; reactivation restores exactly the assignments that existed. Assignable roles are those that do not already reach every company (`ASSIGNABLE_ROLES`, mirrored in the database trigger).

### ADR-037 — Company lifecycle and accounting settings have their own permissions and endpoints
`POST /companies/{id}/status` (`companies.archive`) replaces editing `status` through `PATCH /companies/{id}`; accounting settings are edited through `PATCH /companies/{id}/settings` (`company_settings.manage`). “Inactive” in the UI is the existing `suspended` status; archiving frees the subscription slot and restoring needs a free one. Known compatibility effect: clients that sent `status` to `PATCH /companies/{id}` must move to the new endpoint (the breaking-change detector does not see removed request fields).

### ADR-038 — New organization roles are added to the code catalog, not as editable data
Practice manager, assistant accountant, document operator and read-only user join the catalog; `artify-api seed` synchronizes them. Custom, organization-defined roles and per-user permission overrides are **not** implemented (the brief allowed configuration “where the architecture supports it”; supporting it safely needs a permission-editing audit model and `feature.custom_roles`, deferred to a later phase).

### ADR-039 — The Control Center never claims to have sent something that was not sent
E-mail delivery is reported from the mailer backend (`configured` only for SMTP). Notification preferences are stored but `notifications_sent` is `false` until a notification service exists. Usage rows that are not metered show “Not metered”, never zero. Upgrade and billing actions are text only (“Contact Artify support”) because no commercial workflow exists for customers.

### ADR-040 — The Control Center UI is English-only in Phase 3 (deviation from D-9 and ADR-032)
D-9 decided English + Arabic with RTL from the first screen and ADR-032 said the customer console keeps both. The Phase 3 screens were built in English only to finish within scope; the Phase 1 customer dashboard that had Arabic strings was replaced, so signed-in pages regressed to English. Public pages (sign-in, sign-up, invitation) remain bilingual. Restoring Arabic and RTL for the Control Center is open work and needs native-reviewed strings.

### ADR-041 — One adjacency table for sections and folders, with `path_ids`
Sections are roots and folders nest below in a single `workspace_nodes` table (`node_type`, `parent_id`, `depth`, `path_ids uuid[]`, GIN-indexed). Row-level CHECKs enforce type/parent, depth = path length, last path element = own id, second-last = parent and no repeated element (no cycles); cross-row path correctness is maintained by the application inside the per-company locked transaction. Supersedes the separate `workspace_sections`/`workspace_folders` tables and fractional `sort_key` of the Phase 0 design (integer `position`, renumbered per sibling list).

### ADR-042 — Workspace edits are individually atomic and versioned; change sets are deferred
Each mutation locks `company_configurations`, optionally checks `base_version`, bumps `config_version` and writes an immutable `workspace_revisions` row (changes + snapshot) and an audit event in the same transaction. Bulk operations (template apply, duplicate, restore) are atomic. The Phase 0 “change-set” request (many operations, dry run, pending summary) is **not built**; revisit if the Builder needs batch editing.

### ADR-043 — Workspace authorization is role keys plus an allow/deny grant engine
Role keys `workspace.read/manage/permissions.manage` gate the route; per-node actions are decided by `workspace_engine.decide`: administrators always pass; read-only is capped at view; nearest grant wins down the tree, deny beats allow on the same level; no grant → role default (`open`) or nothing for members below manager (`restricted`). Hidden nodes answer 404. Replaces the “grants to roles per section, deny implicit only” sketch of Phase 0 with explicit deny, because the brief requires deterministic allow/deny behaviour.

### ADR-044 — Per-company entitlement counts are measured under RLS and enforced after the change
`limit.workspace_sections_per_company`, `…folders…`, `…custom_fields…`, `limit.filing_rules_per_company` (new) and `limit.folder_depth` are measured by plain queries in the caller’s RLS context (`entitlements.COMPANY_MEASURES`) and checked once at `commit` against the counts before the change, so bulk operations are covered by one rule. Only **increases** beyond a limit are refused: lowering a limit never deletes or blocks edits/archive. `limit.workspace_templates` (new, organization scope) uses `app.measure_entitlement`. `limit.folder_depth` counts levels including the section (a section is level 1). The system ceiling `MAX_DEPTH_HARD = 12` in `workspace.py` is a technical safety limit, not a plan limit. Plans created before Phase 4 do not contain the two new keys and therefore deny those actions until Super Admin adds them (default-deny rule).

### ADR-045 — Custom field validation is declarative; no regular expressions
Phase 0 allowed length-bounded regex in a “ReDoS-safe engine”. Phase 4 supports lengths, numeric/date ranges, decimal places, currency lists, option sets and a linear-time character `mask` instead, so there is no ReDoS surface and no executable content. Revisit only with a concrete customer need and a linear-time engine.

### ADR-046 — Templates are deep-copied definitions; provenance only
A template version is an immutable JSON definition validated by the same engine that validates live configuration. Applying replays it with new ids; the company keeps `applied_template_id/_version` as provenance (no foreign key drives behaviour in code, queries never join back). `replace` archives, never deletes. System templates are seeded from code by version (changed definition → new version). The “review differences and merge” update flow is not built.

### ADR-047 — The desktop cache is encrypted field-by-field (AES-256-GCM, key in the OS vault), not with SQLCipher
Sensitive cached payloads (profile, organizations, companies, workspace, queued operation payloads) are sealed with AES-256-GCM; the key is generated on first run and stored only in the Windows Credential Manager; associated data binds each blob to `user|table|row`. Rejected: SQLCipher (adds OpenSSL/SQLCipher to the Windows build, still needs a key store) and OS-level disk encryption alone (does not protect a copied database file). Consequence: ids, statuses, timestamps, settings and notification text are not encrypted. Settles the cache-encryption ADR required by `PHASE_5.md`.

### ADR-048 — Rust owns HTTP and tokens; the webview has no network or file-system capability
All API calls are made by `artify-core` (reqwest); the access token lives in memory, the rotating refresh token in the OS vault; the UI receives data only, through 26 allow-listed commands, with a CSP that permits only `ipc:`. Rejected: calling the API with `fetch` from the webview (tokens would live in JavaScript and CORS/CSP would have to allow the API). Consequence: a typed bridge to maintain, and a mock bridge for UI tests.

### ADR-049 — Sessions are device-bound; device revocation ends the session (backend change in Phase 5)
`POST /devices` binds the registering session to the new device; `POST /devices/{id}/session` re-binds a later session to an active device of the same user and records activity; revocation already revoked bound sessions and now actually finds some. `DeviceOut` gained `registered_at`/`last_seen_at`. Additive, 0 breaking OpenAPI changes. The device id is server-issued; the client name is informational. Effect is at the next request (no push). Replaces the unmet Phase 0 promise "sessions are device-bound".

### ADR-050 — The desktop core is a separate crate from the Tauri shell
`artify-core` (API client, SQLite, sealing, paths, workspace parsing, orchestration) has no Tauri dependency, so it is tested without a window on any CI image and against a real API; the shell only wires commands, state, the sync loop and the updater hook. The UI talks to a `Bridge` interface (real Tauri implementation; in-memory mock for tests and a bannered browser preview that is excluded from production builds).

### ADR-051 — Updates stay disabled and installers stay unsigned until signing infrastructure exists
No updater key, update endpoint or code-signing certificate is in the repository (and none may be). `update_check` reports "not configured" truthfully; the Windows CI job builds an unsigned installer and is `continue-on-error` until a first verified run. Revisit when a certificate and an update host are available (`docs/desktop/RELEASE_AND_UPDATE_PROCESS.md`).

### ADR-052 — Workspace grants follow assignments, and exporting a configuration needs `configure` on its source
Found by reviewing Phase 4/5 against Phase 3: (1) a person's explicit workspace grants survived the removal of their assignment and silently returned on re-assignment; they are now deleted with the assignment (audited as `workspace.grants_revoked`). (2) `copy-from`, save-as-template and create-from-copy exported the source company's full configuration to anyone with `workspace.read` there, leaking nodes hidden from them; they now need `configure` on the source too. (3) Mutating workspace routes declare only `workspace.read`; an AST test now requires each to authorize inside its body or via a dependency.

### ADR-053 — Uploads go through the API in resumable chunks; there are no signed or public URLs
`POST /uploads` reserves a session, `PUT …/content?offset=N` appends raw chunks at a **server-authoritative offset**, `POST …/complete` verifies size and SHA-256, inspects the content, scans, checks duplicates and quotas, and only then commits the object and the rows. Downloads are authenticated, audited streams (Range, `X-Content-SHA256`, `nosniff`). Reason: validation must precede storage; a browser-to-bucket upload would keep unvalidated bytes first, and a signed URL is an unaudited bearer capability. Cost: the API carries the bytes (8 MiB per request by default). `Idempotency-Key` is required on reserve. Reviewed in `docs/documents/DOCUMENT_IMPORT_AND_UPLOAD.md`.

### ADR-054 — Phase 6 ships a filesystem object-store driver behind `ObjectStorage`; S3/MinIO is not implemented
The interface (`storage.py`) has staging append, no-overwrite commit, ranged read, delete and listing. Keys are opaque (`o/<org hex>/<company hex>/<version hex>`, strict regex, never derived from names). Only `FilesystemStorage` exists; settings reject other backends. This is an honest gap: a single API node with an encrypted, backed-up persistent volume works; multi-node or worker-on-other-host needs shared storage or a new driver. Encryption at rest, backups and restore tests are infrastructure obligations (open decision D-12). The compose `storage` profile (MinIO) is unchanged and unused by Phase 6.

### ADR-055 — File type comes from content; active content is refused, not stored; scanning is optional and fails closed
`inspect_file` ignores the client MIME type: the extension must be allow-listed and the content must match it (PDF `%%EOF`, JPEG/PNG markers, Office ZIP/OLE structure with bomb and unsafe-path limits, plain text without binary data); executables and web/script markup are rejected. PDFs with JavaScript/launch/embedded-file actions and Office macros end the upload as `quarantined` and **nothing is stored** (no review queue yet). `malware_scan = clamd` scans before availability and fails closed (`scan_unavailable`, retryable); the default is **off**, versions are then `not_scanned`. The clamd client is tested against a fake server only.

### ADR-056 — Versions are immutable; restoring a version moves the pointer
A trigger forbids changing a stored version's binary identity and any delete; only `scan_status` and `purged_at` may change. "Restore version n" sets `documents.current_version_*` and records an event; nothing is copied. The original is therefore always recoverable. Purge removes binaries and leaves a tombstone (rows without content, events, audit).

### ADR-057 — Storage is measured as the bytes of all non-purged versions; plans can only lower central ceilings; shrinking never deletes
`quota.storage_bytes` counts every retained version of every document including archived and removed ones; only permanent delete frees space. `quota.documents_per_month` counts new documents only. `limit.max_file_size_bytes` and `limit.import_batch_files` can only lower the central `max_upload_bytes` / `import_batch_max_files`. `guard_amount` refuses only an **increase** (402) — at reserve (before bytes move) and again under the counter lock at completion. Per-company storage quotas are not implemented; the Super Admin per-company storage column remains unmetered.

### ADR-058 — Document permissions: `documents.contribute` (= node action `upload`) and `documents.purge`; folder rules are enforced in handlers, not RLS
`documents.contribute` is a company-scope role key that lets the Phase 4 engine answer `upload`; node grants still decide per folder. `documents.purge` (organization scope, owner/administrator) gates permanent delete, legal hold and storage reconciliation. RLS isolates organizations and companies (forced, tested); the node-level allow/deny engine runs in the API on every document operation and in the search query itself. A database mirror of node grants is deferred. Platform roles hold no document permission.

### ADR-059 — Duplicates are exact SHA-256 matches inside one company; the user decides; the reference is shown only if the folder is visible
Policy `block` (default) fails the upload with `duplicate_detected` and keeps the staged data; `allow_copy` stores a separate document and records `duplicate_kept` plus an immutable `document_duplicate_candidates` row. Removed documents do not count. Other companies and organizations are never compared. Fuzzy duplicates are Phase 8.

### ADR-060 — The Desktop queue is persisted, sealed and resumable; the webview refers to files only by token; the command surface grows from 26 to 41
`imports` and `local_files` live in the sealed SQLite cache; an upload counts as stored only after the server confirms; interrupted uploads return to *waiting* at sign-in and resume at the server's offset; retries back off (30 s … hourly, six automatic attempts). Native file picker and window drag-and-drop stage paths inside Rust behind opaque tokens; no command accepts a path from the webview. The 15 added commands (picker, queue, document operations through the closed `DocOp::parse` set, preview/download/open, mirror) are listed in `docs/desktop/` and exercised by core tests. Local copies are placed by workspace **node id** (never by name), never overwrite, and are verified by SHA-256. The error code for an expired upload is `upload_expired` (not `session_expired`, which the desktop reserves for an ended sign-in).

### ADR-061 — The web Control Center monitors documents; uploading and viewing files is a Desktop capability in Phase 6
The Next.js BFF proxies text only. The Control Center therefore shows counts, storage usage, upload health and the company's documents list (names and metadata only, subject to folder permissions), and never serves file bytes. Reconcile/purge-level actions are API-level and audited.

## Outcomes of decisions taken at the start of Phase 1

The product owner replied "Accept the recommendations and start Phase 1" on 2026-11-04, which settles:

| ID | Outcome |
|---|---|
| D-1 | **Decided:** in-house authentication behind the `AuthProvider` protocol (`auth_provider.py`); routes depend only on the protocol. |
| D-3 (dev infra) | **Decided:** cloud-neutral development infrastructure (PostgreSQL, Redis, S3-compatible MinIO). Production hosting/region remains open for Phase 12. |
| D-8 (schema) | **Decided:** retention policy *schema* only (`retention_policies`, values NULL). Values remain open for Phase 12. |
| D-2 (Phase 2) | **Partly addressed:** manual, provider-independent billing records shipped (ADR-031). The billing **provider** is still undecided; a renewal/expiry scheduler and customer billing need it. |
| D-9 | **Decided:** English + Arabic with RTL from the first screen. Arabic strings are a first-pass translation needing native review. |

## Decisions requiring product-owner approval

| ID | Question | Recommendation | Needed by |
|---|---|---|---|
| **D-1** | Authentication: build in-house on FastAPI (Argon2id, rotating refresh tokens, TOTP/WebAuthn) or adopt a managed/self-hosted IdP (Keycloak, Auth0, Cognito…)? | In-house behind an `AuthProvider` interface: device-bound sessions, offline desktop sessions, and per-request membership resolution are central to the design. Revisit if compliance requires an IdP. | **Decided 2026-11-04** (see outcomes above) |
| **D-2** | Payment/billing provider and invoicing/tax treatment for Artify's own subscriptions. | Defer provider choice; Phase 2 records plans/subscriptions/payments behind a provider interface with manual invoicing first. Provider availability for the target markets must be verified. | Phase 2 |
| **D-3** | Hosting provider, regions and data residency (e.g. in-country or GCC hosting for Oman customers). | Cloud-neutral Docker/S3-compatible design; pick provider and region after confirming customer residency expectations. | Dev infra **decided 2026-11-04**; production hosting open until Phase 12 |
| **D-4** | Per-tenant encryption keys (envelope keys per Organization) vs platform-managed server-side encryption only. | Platform-managed KMS encryption at launch; schema keeps `encryption_key_ref` hooks so per-tenant keys can be added. | Phase 12 |
| **D-5** | AI/OCR provider policy: which providers, whether tenant documents may leave the platform's infrastructure, and per-tenant consent/opt-out. | Provider-independent interface; per-Organization `ai_processing_policy` (local-only / approved cloud providers); cloud use off by default until a data-processing agreement exists. | Phase 7 |
| **D-6** | Is Artify ever a general ledger of record (balances, trial balance, period close), or strictly an entry-preparation/export system? | Preparation and export only (ADR-010). Adding a ledger later is additive but large. | Phase 8 |
| **D-7** | Behavior when a user edits the local Windows mirror directly (moves/renames/deletes in Explorer). | Local structure is derived from cloud; Explorer moves are detected and surfaced as "refile requests" needing permission; deletes never propagate to the cloud. A watched `_Inbox` subfolder is the supported drop target. | Phase 5 |
| **D-8** | Data retention periods per jurisdiction and Artify's own policy on contract end. | Retention is configuration (policy table); defaults to be set with legal counsel, not by engineering. | Schema **decided 2026-11-04**; values open until Phase 12 |
| **D-9** | Languages: Arabic/RTL UI and Arabic/English bilingual OCR for the Oman market. | Treat i18n and RTL as a Phase 1 platform requirement (cheap now, expensive later); prioritize Arabic + English. Confirm launch languages. | **Decided 2026-11-04** (see outcomes above) |
| **D-10** | Offline scope of Mobile (queue uploads offline only, or also browse cached history). | Offline capture and upload queue; history is online-only plus a small cache. | Phase 11 |
| **D-11** | Legal/commercial: product licensing, terms, data-processing agreement, and trademark of the "Artify" name. | Outside engineering scope; needed before any external pilot. | Before pilot |
| **D-12** | Where do document binaries live in production, and how are they encrypted, backed up and restored? (Phase 6 ships a filesystem driver only.) | Choose an S3-compatible service in the chosen region (D-3) or an encrypted volume with tested backups; implement the S3 driver before multi-node. Decide whether per-tenant keys (D-4) apply to documents. | Before any pilot with real documents |
| **D-13** | Malware scanning: run ClamAV (or a vendor) in production and decide the review path for quarantined files. | Run clamd, keep fail-closed; keep rejected files out of storage until a reviewed quarantine area exists. | Before any pilot with real documents |
