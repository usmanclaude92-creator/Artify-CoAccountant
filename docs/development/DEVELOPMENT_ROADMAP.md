# Development Roadmap

Phases 0–12 as specified in the product brief. Each phase has its own document (`PHASE_N.md`) with objectives, scope, technical implementation, database changes, API requirements, dependencies, security requirements, testing requirements, acceptance criteria, completion checklist and known risks.

**Status: Phase 0 approved. Phase 1 delivered for review (partially complete — see `PHASE_1_COMPLETION_REPORT.md`). Phase 2 (Super Admin) delivered for review — see `PHASE_2_COMPLETION_REPORT.md`. Phase 3 must not start until Phase 2 is approved.**

Sizing is relative (S/M/L/XL), not a schedule; calendar estimates need team size and are intentionally not invented here.

## 1. Phase overview

| Phase | Name | Size | Primary deliverable |
|---|---|---|---|
| 0 | Architecture and project foundation | M | Architecture, database, security, roadmap documents; repo foundation |
| 1 | SaaS foundation, database and authentication | XL | Monorepo scaffolding, API skeleton, PostgreSQL + RLS, identity, tenancy, entitlement service, CI, isolation test suite, web shell + auth screens |
| 2 | Artify Super Admin | L | Platform realm UI: tenants, plans/entitlements, subscriptions, usage, health, support access, platform audit |
| 3 | Firm Control Center and client management | L | Companies, staff, assignments, roles, subscription usage view, notifications base, audit viewer, `packages/ui` hardening |
| 4 | Workspace Structure & Customization engine | XL | Workspace model, change sets, builder UI, templates, custom fields, workflows, filing rules, section permissions, upload-config API |
| 5 | Windows Desktop foundation | L | Tauri shell, login, device registration, workspace sync, local mirror, queue skeleton |
| 6 | Document management and storage | XL | Uploads, versions, storage, duplicate detection, change-feed documents, Desktop document workspace, basic processing monitor |
| 7 | OCR and AI document intelligence | XL | Provider-independent pipeline, extraction per document kind, review UI, evaluation harness, Processing Monitor |
| 8 | Accounting intelligence engine | XL | Chart of accounts, VAT config, validators, records, entries, approvals, reconciliation, accounting configuration UI |
| 9 | Excel template and reporting engine | XL | Template compatibility analysis, mapping builder, exports, report configurations |
| 10 | Daily bookkeeping and closing | M | Daily workflow, summaries, closing, period locks in the UI |
| 11 | Mobile application | L | Flutter capture/upload app, config-driven categories, queue |
| 12 | Cloud sync hardening, Windows integration, security testing, commercial release | XL | Production-grade sync, pen test, DR drills, observability, release engineering |

## 2. Dependency graph

```
0 ─▶ 1 ─▶ 2 ─────────────────────────────────────────────┐
      └─▶ 3 ─▶ 4 ─▶ 5 ─▶ 6 ─▶ 7 ─▶ 8 ─▶ 9 ─▶ 10 ─▶ 12 ◀─ 11 (needs 4, 6)
                 └──────────────▶ 6 (workspace before documents)
```

Hard dependencies: 2 and 3 need 1; 4 needs 3 (Companies, roles, entitlements UI); 5 needs 4 (workspace to mirror); 6 needs 4 and 5; 7 needs 6; 8 needs 7 (extracted data, kinds) and the Company configuration from 3/4; 9 needs 8 (approved entries); 10 needs 8 and 9; 11 needs 4 and 6 (upload-config, upload API); 12 needs everything.

## 3. Sequencing rationale (minimizing rework)

1. **Tenancy, RLS and entitlements before features (Phase 1).** Retrofitting isolation or limits is the most expensive rework in a multi-tenant product.
2. **Workspace before documents and AI (Phase 4 before 6–7).** Documents reference workspace node ids; categories carry document kinds; processing, Mobile and Windows folders all consume the configurable model. Nothing in Phases 5–11 may hard-code categories.
3. **Document kinds are defined with the workspace (Phase 4)** as reference data, even though extraction schemas arrive in Phase 7 and accounting rules in Phase 8, so category mapping exists from day one.
4. **Excel templates (Phase 9) come after accounting entries (Phase 8)** so the source-field catalog is real, but the **Company-scoped data model** for templates is already reserved in Phase 1's conventions.
5. **Desktop foundation (5) before Mobile (11)** because Desktop validates sync and workspace distribution first; Mobile reuses the proven APIs.
6. **Risk spikes pulled forward** (timeboxed, throwaway code, results in an ADR): Flutter scanning plugin (during Phase 5), OpenPyXL fidelity (during Phase 6), Arabic OCR evaluation set creation (during Phase 6).
7. **Contract-first API from Phase 1** so Desktop and Mobile can generate clients without waiting.

## Control Center module delivery matrix

| # | Module | Delivered in | Notes |
|---|---|---|---|
| 1 | Overview Dashboard | 3 (shell, real counts) | Enriched in 7 and 10 with processing and closing data |
| 2 | Client Company Management | 3 | |
| 3 | Accountant and Staff Management | 3 | |
| 4 | Client-to-Accountant Assignments | 3 | |
| 5 | User Roles and Permissions | 3 | Custom roles behind `feature.custom_roles` |
| 6 | Workspace Structure & Customization | 4 | |
| 7 | Document Processing Monitor | 6 (basic list), 7 (full) | |
| 8 | Company Accounting Configuration | 8 | Chart of accounts, role mappings, periods |
| 9 | Excel Template Management | 9 | |
| 10 | Report Configuration | 9 | |
| 11 | VAT and Tax Configuration | 8 | |
| 12 | Subscription and Usage Management | 3 | Read-only for tenants; management is in Super Admin (2) |
| 13 | Notifications | 3 (framework), extended 4–10 | |
| 14 | Audit Logs and Security | 3 (viewer), 12 (hardening) | |
| 15 | Device and Application Management | 5 (Desktop), 11 (Mobile) | |

## 4. Cross-phase rules

- A phase may not start until the previous phase's acceptance criteria are met by executed evidence and the owner approves.
- Every phase ends with a completion report in `docs/development/PHASE_N_COMPLETION_REPORT.md` using the same classification as Phase 0: DOCUMENTED / IMPLEMENTED / NOT IMPLEMENTED / REQUIRES DECISION / DEFERRED, and lists commands actually run.
- Every phase updates the affected architecture documents and `DECISIONS.md` in the same change set as the code.
- No phase may contradict `GLOSSARY.md`; terminology changes go through the glossary first.
- Project skills (see `PROJECT_SKILLS_PLAN.md`) are created in the phase listed there.
- Security: each phase starts with a short threat-model note for its new surface and ends with the isolation suite green.

## 5. Decisions needed before each phase

| Before | Decisions (from `DECISIONS.md`) |
|---|---|
| Phase 1 | D-1 auth approach, D-3 dev infra choices, D-9 languages/RTL, D-8 retention policy schema |
| Phase 2 | D-2 billing provider (or confirm manual invoicing first) |
| Phase 5 | D-7 local-folder edit behavior |
| Phase 7 | D-5 AI/OCR provider policy |
| Phase 8 | D-6 ledger scope |
| Phase 11 | D-10 mobile offline scope |
| Phase 12 | D-4 per-tenant keys, D-3 production hosting, D-8 retention values; D-11 legal items before any external pilot |
