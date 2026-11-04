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

## Decisions requiring product-owner approval

| ID | Question | Recommendation | Needed by |
|---|---|---|---|
| **D-1** | Authentication: build in-house on FastAPI (Argon2id, rotating refresh tokens, TOTP/WebAuthn) or adopt a managed/self-hosted IdP (Keycloak, Auth0, Cognito…)? | In-house behind an `AuthProvider` interface: device-bound sessions, offline desktop sessions, and per-request membership resolution are central to the design. Revisit if compliance requires an IdP. | Start of Phase 1 |
| **D-2** | Payment/billing provider and invoicing/tax treatment for Artify's own subscriptions. | Defer provider choice; Phase 2 records plans/subscriptions/payments behind a provider interface with manual invoicing first. Provider availability for the target markets must be verified. | Phase 2 |
| **D-3** | Hosting provider, regions and data residency (e.g. in-country or GCC hosting for Oman customers). | Cloud-neutral Docker/S3-compatible design; pick provider and region after confirming customer residency expectations. | Phase 1 (dev infra), Phase 12 (production) |
| **D-4** | Per-tenant encryption keys (envelope keys per Organization) vs platform-managed server-side encryption only. | Platform-managed KMS encryption at launch; schema keeps `encryption_key_ref` hooks so per-tenant keys can be added. | Phase 12 |
| **D-5** | AI/OCR provider policy: which providers, whether tenant documents may leave the platform's infrastructure, and per-tenant consent/opt-out. | Provider-independent interface; per-Organization `ai_processing_policy` (local-only / approved cloud providers); cloud use off by default until a data-processing agreement exists. | Phase 7 |
| **D-6** | Is Artify ever a general ledger of record (balances, trial balance, period close), or strictly an entry-preparation/export system? | Preparation and export only (ADR-010). Adding a ledger later is additive but large. | Phase 8 |
| **D-7** | Behavior when a user edits the local Windows mirror directly (moves/renames/deletes in Explorer). | Local structure is derived from cloud; Explorer moves are detected and surfaced as "refile requests" needing permission; deletes never propagate to the cloud. A watched `_Inbox` subfolder is the supported drop target. | Phase 5 |
| **D-8** | Data retention periods per jurisdiction and Artify's own policy on contract end. | Retention is configuration (policy table); defaults to be set with legal counsel, not by engineering. | Phase 1 (policy schema), Phase 12 (values) |
| **D-9** | Languages: Arabic/RTL UI and Arabic/English bilingual OCR for the Oman market. | Treat i18n and RTL as a Phase 1 platform requirement (cheap now, expensive later); prioritize Arabic + English. Confirm launch languages. | Phase 1 |
| **D-10** | Offline scope of Mobile (queue uploads offline only, or also browse cached history). | Offline capture and upload queue; history is online-only plus a small cache. | Phase 11 |
| **D-11** | Legal/commercial: product licensing, terms, data-processing agreement, and trademark of the "Artify" name. | Outside engineering scope; needed before any external pilot. | Before pilot |
