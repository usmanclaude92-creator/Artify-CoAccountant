# Artify Co-Accountant

A multi-tenant accounting-operations and document-intelligence SaaS for accounting firms, independent accountants and their client companies.

**Current status: Phase 0 (architecture and foundation) — documentation only. No application code exists yet.** Phase 1 has not started and begins only after Phase 0 is reviewed and approved.

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

## Verify the documentation

```bash
python3 tools/check_docs.py
```

Standard-library Python only. It checks required files, links/anchors, phase-document structure, scenario and decision references, and numeric types in SQL snippets. It does not judge architectural correctness.

## Guiding principles

One shared backend · tenant (Organization) and company isolation enforced in the API and in PostgreSQL · fully configurable per-company workspaces and Excel templates · accounting meaning via Artify-controlled document kinds · AI suggests, humans approve · decimal arithmetic only · end-to-end traceability from original document to Excel export.
