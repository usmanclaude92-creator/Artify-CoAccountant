# Artify Co-Accountant

A multi-tenant accounting-operations and document-intelligence SaaS for accounting firms, independent accountants and their client companies.

**Current status: Phase 1 (SaaS foundation) delivered for review.** Backend (identity, tenancy, row-level security, entitlements), generated API client, a Docker stack and a minimal web shell exist and are tested; Super Admin, Control Center modules, workspaces, documents, Desktop and Mobile do not. A few items remain open — see [`docs/development/PHASE_1_COMPLETION_REPORT.md`](docs/development/PHASE_1_COMPLETION_REPORT.md).

## Ecosystem (planned)

| Application | Audience | Phase |
|---|---|---|
| Artify Super Admin (web) | Artify staff | 2 |
| Artify Co-Accountant Control Center (web) | Firms / independent accountants | 3–4, extended 7–9 |
| Artify Co-Accountant Desktop (Windows, Tauri + React) | Accountants, client-side users | 5–10 |
| Artify Co-Accountant Mobile (Flutter) | Client employees, field staff | 11 |
| Shared cloud backend (FastAPI, PostgreSQL, Redis, S3-compatible storage) | all | 1 onward |

## Where to start reading

1. [`docs/README.md`](docs/README.md) — documentation index and reading order
2. [`docs/architecture/GLOSSARY.md`](docs/architecture/GLOSSARY.md) — canonical terms
3. [`docs/architecture/DECISIONS.md`](docs/architecture/DECISIONS.md) — accepted decisions and open questions needing owner approval
4. [`docs/development/PHASE_0_COMPLETION_REPORT.md`](docs/development/PHASE_0_COMPLETION_REPORT.md) — what Phase 0 did and did not do

## Develop

Requires `uv`, `pnpm` (Node 22+) and PostgreSQL 16 (see the `PG*` variables in `Makefile`).

```bash
make install          # dependencies from lockfiles
make db-reset         # recreate the dev database: migrate, dev login roles, seed
make check            # lint + import boundaries, strict types, OpenAPI drift, API tests (real PostgreSQL), docs check, web tests + build
make e2e              # browser tests: journeys, accessibility (axe), keyboard, org switcher
make audit            # dependency vulnerability scan (Python + JavaScript)
make hooks            # install git pre-commit hooks
make openapi-breaking BASE=<git ref>   # fail on breaking API changes vs a ref
```

### Docker

```bash
docker compose up --build            # PostgreSQL, Redis, API (:8000), Celery worker, outbox dispatcher
make compose-smoke                   # builds, starts, exercises the whole chain, tears down
docker compose --profile storage up  # adds MinIO (S3-compatible; needed from Phase 6) — NOT verified
```

The Compose stack (everything except MinIO) is verified by `infrastructure/dev/compose_smoke.py`, which also runs in CI. Image names can be overridden (`POSTGRES_IMAGE`, `REDIS_IMAGE`, `MINIO_IMAGE`, `PYTHON_IMAGE`) for registry mirrors.

## Verify the documentation

```bash
python3 tools/check_docs.py
```

Standard-library Python only. It checks required files, links/anchors, phase-document structure, scenario and decision references, and numeric types in SQL snippets. It does not judge architectural correctness.

## Guiding principles

One shared backend · tenant (Organization) and company isolation enforced in the API and in PostgreSQL · fully configurable per-company workspaces and Excel templates · accounting meaning via Artify-controlled document kinds · AI suggests, humans approve · decimal arithmetic only · end-to-end traceability from original document to Excel export.
