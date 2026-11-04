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
