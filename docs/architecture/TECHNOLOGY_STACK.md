# Technology Stack

Exact versions were pinned at the start of Phase 1 (ADR-019): Python dependencies in `services/api/pyproject.toml` + `uv.lock`, JavaScript in `package.json` files + `pnpm-lock.yaml`. Key pins: FastAPI 0.162.0, Pydantic 2.14.2, SQLAlchemy 2.1.8, Alembic 1.20.1, psycopg 3.3.8, argon2-cffi 25.1.0, PyJWT 2.15.1, Celery 5.6.3, Next.js 16.4.5, React 19.3.1, TypeScript 5.9.3, Tailwind CSS 4.3.4, Playwright 1.64.1; PostgreSQL 16 (verified locally), Redis 7.0, Node 22, Python 3.13 locally (3.12+ supported). Versions were read from the package registries on 2026-11-04.

## 1. Stack by layer

| Layer | Choice | Baseline | Rationale / notes |
|---|---|---|---|
| Web | Next.js (App Router), React, TypeScript (strict) | current Node LTS | SSR for authenticated shells is optional; the console is a data-driven app. Two surfaces from one codebase (ADR-014). |
| Styling | Tailwind CSS + design tokens (CSS variables) | — | Light/dark through tokens (see design system). |
| Component base | Headless accessible primitives (e.g. Radix UI) wrapped in `packages/ui` | — | Accessibility without bespoke ARIA code. Library selection confirmed in Phase 3. |
| Workspace builder DnD | dnd-kit or equivalent with full keyboard alternative | — | Selected in Phase 4. |
| Data fetching (web) | Generated OpenAPI client + TanStack Query | — | |
| API | Python, FastAPI, Pydantic v2 | Python 3.12+ | Python is also the best fit for the OCR/ML/Excel libraries. |
| Data access / migrations | SQLAlchemy 2.x **Core** (bound-parameter SQL), Alembic with raw-SQL revisions | pinned in `uv.lock` | See ADR-020. Migrations mandatory for all schema change; reviewed SQL for RLS and constraints. |
| Database | PostgreSQL (central source of truth) | currently supported major | RLS, partitioning, `NUMERIC`, `ltree`/CTEs for trees, `pg_trgm` for fuzzy duplicate search. |
| Local DB (Desktop) | SQLite | — | Cache/queue only; never authoritative. |
| Queue / cache | Redis + Celery | — | Alternative considered: Dramatiq/Arq. Celery chosen for maturity, scheduling and monitoring. Redis is not a durable store of record; the outbox in PostgreSQL is. |
| Object storage | S3-compatible (e.g. AWS S3, MinIO locally) | — | Provider-neutral via `StorageBackend` interface; multipart pre-signed uploads. |
| Excel | OpenPyXL (+ template compatibility analysis) | — | See [`EXCEL_ENGINE_DESIGN.md`](EXCEL_ENGINE_DESIGN.md) for fidelity limits. |
| PDF / image | PyMuPDF or pypdf/pdfplumber, Pillow, OpenCV (as needed) | — | Final library licensing review (PyMuPDF is AGPL/commercial) happens in Phase 6; this is a flagged risk. |
| OCR / extraction | Provider-independent interfaces; adapters for local engines and cloud services | — | Providers chosen in Phase 7 under decision D-5. Arabic + English support is a selection criterion (D-9). |
| Desktop | Tauri, React, TypeScript, SQLite | — | Narrow Rust command surface; OS credential vault. |
| Mobile | Flutter (Dart) | — | Document-scanning plugin selected in Phase 11 after licensing/accuracy review. |
| Auth | In-house module behind `AuthProvider` (D-1) | — | Argon2id, rotating refresh tokens, TOTP; WebAuthn later. |
| Infra / dev | Git, Docker, Docker Compose, environment-based config | — | Compose stack: api, worker, postgres, redis, minio. |
| CI/CD | GitHub Actions (planned, Phase 1) | — | Lint, type-check, test, migration check, OpenAPI diff, dependency and secret scanning, container build. |
| Observability | Structured logging, OpenTelemetry, Prometheus-compatible metrics | — | No sensitive data in telemetry. |

## 2. Repository tooling (decided for Phase 1)

- Monorepo; JavaScript/TypeScript with pnpm workspaces; Python services with `uv` (workspace + lockfile); a root task runner (Makefile or `just`) so developers have one command vocabulary.
- Formatting/linting: Ruff + mypy (strict on domain modules) for Python; ESLint + Prettier + `tsc --noEmit` for TypeScript; `dart analyze`/`flutter test` for mobile.
- Pre-commit hooks: formatting, secret scanning, docs check (`tools/check_docs.py` exists from Phase 0).

## 3. Dependencies deliberately **not** installed in Phase 0

No application dependencies are installed. Phase 0 adds only one stdlib-only Python script (`tools/check_docs.py`) and documentation. Each dependency above is introduced in the phase that needs it, with a short entry in the phase completion report (name, version, license, why).

## 4. Dependency policy

- Licenses must be compatible with proprietary commercial distribution; copyleft (GPL/AGPL) dependencies require explicit written approval and are flagged in the phase report (PyMuPDF is the known example).
- Lockfiles committed; automated vulnerability scanning in CI.
- Prefer maintained libraries with a clear release cadence; avoid unvetted community skills/plugins in the repository's `.claude` configuration.

## 5. Technology risks to retire early

| Risk | Retire in |
|---|---|
| OpenPyXL cannot preserve some Excel features (charts, images, pivot tables, form controls, slicers in some cases) | Phase 9 spike with real, anonymized-by-construction templates; compatibility analyzer built before the mapping UI |
| Arabic OCR/extraction quality | Phase 7 evaluation harness on synthetic and consented documents |
| Tauri filesystem watcher edge cases on Windows (long paths, AV locks, OneDrive folders) | Phase 5/12 test matrix |
| Flutter scanning plugin quality/licensing | Phase 11 spike |
| RLS performance with large company sets | Phase 1 benchmark with synthetic data |
