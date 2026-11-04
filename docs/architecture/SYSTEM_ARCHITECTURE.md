# System Architecture

## 1. Logical view

```
 ┌──────────────┐ ┌────────────────┐ ┌───────────────┐ ┌──────────────┐
 │ Super Admin  │ │ Control Center │ │ Desktop       │ │ Mobile       │
 │ (web surface)│ │ (web surface)  │ │ Tauri+React   │ │ Flutter      │
 └──────┬───────┘ └───────┬────────┘ └───────┬───────┘ └──────┬───────┘
        │ platform realm  │ tenant realm     │ tenant realm   │ tenant realm
        └────────────┬────┴──────────────────┴────────────────┘
                     ▼   HTTPS, REST /api/v1, OpenAPI contract
        ┌──────────────────────────────────────────────────────────┐
        │ API (FastAPI, Pydantic)                                   │
        │  routers: /platform/*  (platform realm)                   │
        │           /orgs/*, /companies/{id}/*  (tenant realm)      │
        │  cross-cutting: authn → context resolution → policy engine│
        │    → entitlement service → audit → problem+json errors    │
        │  domain modules (library boundaries, ADR-004):            │
        │   tenancy · identity · workspace · documents · workflow   │
        │   accounting-engine · document-engine · excel-engine      │
        │   sync-engine · billing/entitlements · reporting · notify │
        └───────┬───────────────┬───────────────┬──────────────────┘
                │               │               │
        ┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼───────────┐
        │ PostgreSQL   │ │ Redis       │ │ S3-compatible     │
        │ source of    │ │ broker,     │ │ object storage    │
        │ truth + RLS  │ │ cache,      │ │ (originals, XLSX, │
        │              │ │ rate limits │ │ exports)          │
        └───────▲──────┘ └──────┬──────┘ └──────▲───────────┘
                │               │               │
        ┌───────┴───────────────▼───────────────┴───────────┐
        │ Celery workers (same code, same RLS context rules) │
        │  OCR/extraction · validation · exports · sync jobs │
        │  notifications · usage metering · retention        │
        └──────────────┬─────────────────────────────────────┘
                       ▼
        Provider adapters: OCR / LLM (local or cloud, tenant policy), email/push, payment (later)
```

## 2. Architectural style

- **Modular monolith** (ADR-004): one deployable API, library-style engines with explicit interfaces, one transactional boundary around PostgreSQL. Workers run the same codebase with a different entrypoint.
- **Contract-first REST** (ADR-017): `/api/v1`, OpenAPI generated from Pydantic models, generated TypeScript and Dart clients.
- **Outbox pattern** (ADR-018): state change and job enqueue commit atomically; a dispatcher moves `outbox_events` to Celery. Jobs are idempotent and keyed.
- **Event/audit log is first-class**: domain events feed audit, notifications and the sync change feed.

## 3. Request lifecycle (tenant realm)

1. TLS terminates at the edge; rate limiting applies per IP, user and tenant.
2. Authenticate (access token → session → user). Reject tokens from the wrong realm.
3. Resolve context: active Organization (header/path) and, where the route has `{company_id}`, the Company. Load memberships, assignments and role permissions (cached briefly, invalidated on change). **Reject if the user has no membership for the claimed IDs** (404 for objects outside scope, to avoid existence leaks).
4. Begin the DB transaction; `SET LOCAL app.*` context variables for RLS (user, organization, company scope, realm, optional support grant).
5. Policy engine: permission(s) + scope + section permission + (if mutating a limited resource) `EntitlementService.check`.
6. Execute domain logic; write business rows, `audit_events`, `outbox_events`, and `company_change_log` rows in the same transaction.
7. Commit; return problem+json on failure with a stable error `code`.

Platform realm requests follow the same shape with the platform role, which has no policy access to tenant business tables except under a support grant (ADR-013).

## 4. Environments and configuration

- Environments: `local` (Docker Compose), `ci`, `staging`, `production`. Configuration via environment variables validated at startup (Pydantic Settings); no secrets in the repository; `.env.example` is committed, `.env` is ignored.
- Secrets in a secret manager in staging/production; development uses generated throwaway secrets.
- **Never** use production credentials or real customer financial documents in development or CI. Test data is synthetic (see `docs/development/TESTING_STRATEGY.md`).

## 5. Background processing

| Queue | Work | Notes |
|---|---|---|
| `ingest` | virus scan, type validation, thumbnail/page split | before any extraction |
| `extract` | OCR + extraction pipeline | metered against `quota.ai_units_per_month`; provider-adapter |
| `validate` | deterministic validation, duplicate detection | pure functions where possible |
| `export` | Excel and report generation | idempotent per export record |
| `sync` | storage reconciliation, change-feed compaction | |
| `notify` | notifications fan-out | |
| `maintenance` | retention, usage rollups, outbox cleanup | scheduled |

Every task payload contains `organization_id`, `company_id` (when applicable) and a correlation id; the worker re-establishes RLS context from the payload and verifies the target row's tenant before any write.

## 6. Observability and operations

- Structured JSON logs with correlation, organization and company ids; **no document content or financial amounts in logs**.
- Metrics (request latency, queue depth, job failures, OCR latency/cost, storage per tenant) and tracing hooks (OpenTelemetry) designed in Phase 1; dashboards in Phase 2 (platform health).
- Backups and DR: see [`SECURITY_ARCHITECTURE.md`](../security/SECURITY_ARCHITECTURE.md#12-backup-and-disaster-recovery).

## 7. Scalability posture

- Stateless API and workers scale horizontally.
- PostgreSQL: composite indexes begin with `(organization_id, company_id, …)`; large append-heavy tables (`audit_events`, `company_change_log`, `usage_records`) are range-partitioned by time; read replicas and pgbouncer (transaction pooling, compatible with `SET LOCAL`) when needed.
- Large files bypass the API via pre-signed multipart upload/download, issued only after authorization.
- Per-tenant rate and concurrency limits protect noisy-neighbour risks.

## 8. Failure modes designed for

| Failure | Behavior |
|---|---|
| OCR/LLM provider down | Jobs retry with backoff, then park in `needs_attention`; manual entry always possible |
| Worker crash mid-job | Outbox + idempotency keys → safe retry |
| Client offline | Queue locally, sync later; server revalidates permissions and workspace version at sync time |
| Workspace section archived while a mobile upload is queued | Server returns `409 section_archived`; client re-prompts or files to Intake Inbox on user confirmation; nothing is lost |
| Subscription limit hit mid-flow | Server rejects the specific action with an entitlement error code; existing data unaffected |
