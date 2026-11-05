# Project-Specific Claude Code Skills — Plan

## 1. Current state

**Updated at the end of Phase 1:** six skills now exist in `.claude/skills/` (`co-accountant-architecture`, `multi-tenant-saas`, `api-development`, `security-audit`, `automated-testing`, `code-review`), written against the implemented code. The remaining eleven are created in the phases listed below. The Phase 0 state follows for history.

### Phase 0 state (verified then)

- The repository contained **no** `.claude/` directory. There were no existing skills to inspect or preserve.
- **No skill files are created in Phase 0.** Skills must be written against real code and conventions; skills written before code exists would be speculative and misleading. This document is the plan; `.claude/skills/README.md` points here.
- No community skills are installed. The `/plugin marketplace add anthropics/skills` command attempted at session start was not available in the environment, so nothing was installed from it.

## 2. Rules for creating skills

1. A skill is created in the phase where the code it describes exists (or is being created) and is reviewed like code.
2. Each skill = a directory `.claude/skills/<name>/SKILL.md` with frontmatter `name` and a precise `description` (when to use / when not), concise body, and references to canonical docs rather than copies of them (to prevent drift).
3. Content must be verifiable: commands that run, paths that exist, invariants tied to tests. Remove or fix a skill the moment it becomes wrong.
4. Only first-party or reviewed-in-full third-party skills; any external skill is read completely, license-checked, and committed only after approval.
5. Skills encode **invariants** (e.g. "never reference sections by display name") and **procedures** (e.g. "adding a new document kind"), not generic tutorials.
6. Each skill lists the test scenario IDs that guard its invariants.

## 3. Planned skills

| Skill | Created in | Purpose / key invariants | Guarded by |
|---|---|---|---|
| `co-accountant-architecture` | Phase 1 | Entry point: glossary, ecosystem, decision log pointers, "where does X live", how to propose an ADR | docs check |
| `multi-tenant-saas` | Phase 1 | Tenant/company scoping rules, context setting, RLS migration template, composite-FK rule, how to add a new table (classification P/R/T/C/U/D) | TS-ISO-* |
| `api-development` | Phase 1 | Router layout (`/platform` vs tenant), permission declaration, problem+json errors, idempotency, OpenAPI/client generation | TS-ISO-10 |
| `security-audit` | Phase 1 | Checklist per PR and per phase: authz, RLS, file handling, secrets, audit events | TS-SEC-*, TS-ISO-* |
| `automated-testing` | Phase 1 | Test pyramid, synthetic data policy, fixtures, how to write isolation tests, what to report | all |
| `code-review` | Phase 1 | Review checklist (invariants, money/Decimal, authorization, migrations) | — |
| `frontend-design` | Phase 2/3 | Design tokens, component rules, accessibility, i18n/RTL, banned patterns | a11y CI |
| `workspace-customization-engine` | Phase 4 (**created**) | Versioned atomic edits (change sets deferred), stable ids, archive-not-delete, template copy semantics, category→kind mapping | TS-WS-* |
| `multi-company-configuration` | Phase 4 (**created**) | Never share config between companies; provenance vs dependency; fixtures for two-company tests | TS-WS-01/05/06, TS-ISO-03 |
| `tauri-desktop-development` | Phase 5 (**created**) | Narrow command surface, credential vault, SQLite cache, watcher patterns, Windows path rules | TS-SYNC-03/06 |
| `document-storage-and-sync` | Phase 6 (**created**) | Opaque keys, versions, change feed, idempotent uploads, conflict handling | TS-SYNC-* |
| `financial-document-intelligence` | Phase 7 (**created**) | Provider interfaces, extraction schemas by kind, provenance, never-invent-data, AI safety controls | TS-ACC-07, TS-SEC-05 |
| `accounting-engine` | Phase 8 (**created**) | Decimal rules, entry lifecycle, balance, immutability, approval actor rules, traceability | TS-ACC-* |
| `vat-compliance` | Phase 8 | Jurisdiction-pack data model, effective dating, rounding policies, verification process for packs | TS-ACC-09 |
| `financial-data-validation` | Phase 8 | Validator catalog, severity semantics, override rules | TS-ACC-* |
| `excel-template-engine` | Phase 9 | Mapping language, compatibility analysis, formula preservation, injection guards, export idempotency | TS-XLS-* |
| `flutter-mobile-development` | Phase 11 | Config-driven categories (no hard-coding), upload queue, scanning, secure storage | TS-SYNC-05 |

## 4. Maintenance

- Owners: the phase lead creates and maintains the skills created in that phase.
- Skill changes accompany code changes in the same PR; the code-review skill checks this.
- Quarterly (or per release) skills audit: each skill's commands executed in CI where possible (a smoke script that runs documented commands), stale ones fixed or deleted.
- The phase completion report lists skills created/changed.

## 5. Usage

Claude Code loads skills by description match; descriptions must be specific ("Use when adding or modifying database tables, RLS policies, or tenant-scoped queries") to avoid noisy triggering. Project-level guidance that applies to every task lives in the root `CLAUDE.md` (created in Phase 0), kept short and linking to docs.
