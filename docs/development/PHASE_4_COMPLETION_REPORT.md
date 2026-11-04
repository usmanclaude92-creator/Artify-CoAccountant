# Phase 4 Completion Report — Workspace Structure & Customization Engine

**Status: delivered for review (2026-11-04). Phase 5 has not been started and will not be until the owner approves this phase.**

Evidence conventions: *Executed* means the command was run in this repository’s sandbox (PostgreSQL 16, Redis 7, Chromium) in this session. All data is synthetic. Per-requirement security evidence: [`../workspace/SECURITY_TEST_RESULTS.md`](../workspace/SECURITY_TEST_RESULTS.md). Feature documentation: [`../workspace/`](../workspace/WORKSPACE_ARCHITECTURE.md).

Phase 4 started on the owner’s Phase 4 brief; a separate recorded approval of Phase 3 is not in the repository.

## 1. Summary of implementation

A per-company, relational workspace engine and a Workspace Builder in the Firm Control Center:

- unlimited-by-code sections and nested folders (one adjacency table; depth limited by plan and a technical ceiling of 12), with create, rename, display name, description, active flag, move (valid destinations only), reorder, duplicate, archive/restore (cascade with exact restore), and safe delete;
- document categories mapped to **Artify-controlled document kinds** (mandatory), kept separate from customer folder names;
- custom metadata field definitions (10 types, options, scope, declarative validation), with a value-validation endpoint;
- naming-rule engine (validation, safe rendering, preview, overwrite protection, deterministic fallback);
- automatic filing-rule configuration (validation, priority resolution, conflict analysis, dry-run evaluator);
- allow/deny workspace permissions with inheritance, restricted mode, hidden-node semantics;
- templates (8 Artify starters, organization templates, save-as, versions, apply-as-deep-copy, copy-from-company, onboarding step);
- per-company entitlement enforcement for sections, folders, depth, custom fields, filing rules, and an organization quota for templates;
- versioning: every change is a numbered, immutable revision with before/after and a snapshot, plus audit events; non-destructive restore with dry run;
- distribution contract `upload-config` (ETag/304) for Desktop and Mobile.

Nothing from the out-of-scope list was built (no upload/storage, OCR, AI, accounting, VAT, reconciliation, Excel, Desktop, Mobile, sync).

## 2. Existing architecture reused

Organizations/companies/memberships/assignments, `Requires` dependency and the pure policy engine (extended with four keys), RLS context and the ADR-024 policy pattern, `company_configurations` as the version owner (no parallel settings table), `EntitlementService` module and definitions/overrides/plan data from Phase 2, the append-only audit writer, RFC 9457 errors, the BFF, design tokens and UI kit, the OpenAPI/client pipeline and breaking-change check, the test fixtures and Playwright/axe setup. No identity, tenant, permission or subscription model was duplicated.

## 3. Database tables and migration

Migration `0006_workspace` (forward-only): new `document_kinds`, `workspace_nodes`, `document_categories`, `custom_field_definitions`, `custom_field_options`, `custom_field_applicability`, `naming_rules`, `filing_rules`, `workspace_permissions`, `workspace_templates`, `workspace_template_versions`, `workspace_revisions`; extended `company_configurations`; replaced `app.measure_entitlement`. Composite three-column foreign keys, forced RLS, least-privilege grants (revisions insert-only). Applied from migration 0001 on every test run and in `make e2e`. Details: [`../workspace/WORKSPACE_DATA_MODEL.md`](../workspace/WORKSPACE_DATA_MODEL.md).

## 4. API endpoints

53 new route handlers in `routers/workspace.py` (list in [`../firm-control-center/API_REFERENCE.md`](../firm-control-center/API_REFERENCE.md#workspace-endpoints-phase-4)); `POST /orgs/{org}/companies` gained an optional `workspace` field; `GET /reference/document-kinds`. `make openapi-check`: up to date; `check_openapi_breaking` vs `HEAD`: **0 breaking**. (The regenerated `openapi.json`/`schema.d.ts` diffs are large because many schemas were added.)

## 5. Workspace Builder frontend

`/companies/{id}/workspace` with eight tabs (Structure, Categories, Custom fields, Naming rules, Filing rules, Permissions, Templates, History), `/workspace-templates`, a Workspace step in the company wizard, and a link from the company’s Configuration tab; navigation item “Workspace Templates”. Details and interaction rules: [`../workspace/WORKSPACE_BUILDER.md`](../workspace/WORKSPACE_BUILDER.md).

## 6. Hierarchical structure capabilities

Sections (roots) and folders at any depth up to the plan limit (technical ceiling 12); stable `id` and immutable `key`; sibling-name uniqueness; cycle prevention (move validation + database path CHECKs); move with subtree re-pathing; stable reordering; duplicate (structure only); archive cascade with exact restore; delete only when never referenced.

## 7. Custom metadata capabilities

Ten types; declarative validation (no regex); options archived not deleted; immutable key and type; scope to categories/folders; typed value validation; reference fields limited to same-company nodes/categories. Stored values wait for documents (Phase 6) — see limitations.

## 8. Naming-rule capabilities

Tokens (built-in + active custom field keys), fallbacks, financial-year token from the company’s start month, sanitization, reserved Windows names, path-traversal rejection, unique-name protection, deterministic fallback, original filename returned separately, live preview.

## 9. Filing-rule capabilities

Conditions on document kind, category, source, filename and custom fields with eight operators; target folder/category validated against the company and live state; deterministic priority/tie-break; static analysis (duplicate priority, shadowed rules); reorder; dry-run evaluate; rules switch off when their target is archived. No document is filed or moved.

## 10. Template capabilities

Eight Artify starters (General Business, Trading, Construction, Professional Services, Manufacturing, Retail, Hospitality, Blank), organization templates (save from a company or a definition), immutable versions, deep-copy apply with new ids (`add` or confirmed `replace`-by-archive), dry run with plan-limit fit report, copy-from-company, onboarding choice. Independence is tested. Not built: review-differences-and-merge for older template versions.

## 11. Permission model

Four role keys + node-level allow/deny engine ([`../workspace/WORKSPACE_PERMISSIONS.md`](../workspace/WORKSPACE_PERMISSIONS.md)): administrators never locked out, read-only capped at view, nearest grant wins downward, deny beats allow per level, `restricted` mode, 404 for hidden nodes, hidden parents not disclosed. Grants cannot target administrators.

## 12. Versioning and audit

`config_version` + immutable `workspace_revisions` (changes with before/after, full snapshot, actor) + `audit_events` (`workspace.*`); optional `base_version` concurrency check (409); restore with dry run, non-destructive, atomic, itself versioned. [`../workspace/WORKSPACE_VERSIONING.md`](../workspace/WORKSPACE_VERSIONING.md).

## 13. Subscription integration

Backend-only enforcement in `entitlements.py` (no limit constants except the technical depth ceiling). Per-company: `limit.workspace_sections_per_company`, `limit.folders_per_company`, `limit.folder_depth`, `limit.custom_fields_per_company`, new `limit.filing_rules_per_company`; organization: new `limit.workspace_templates`. Only increases beyond a limit are refused (402 with key/limit/current/requested); lowering a limit never deletes or blocks editing, archiving or reading; bulk operations are checked on their result and roll back atomically. The builder shows current usage against limits. Super Admin’s catalog now marks these as enforced. ADR-044.

## 14. Security testing

See [`../workspace/SECURITY_TEST_RESULTS.md`](../workspace/SECURITY_TEST_RESULTS.md): company/tenant isolation (parents, moves, category/rule/field references, RLS at table level), permission inheritance/deny/restricted/hidden names, read-only ceiling, configuration needs company-level `configure`, route authorization table test, names/paths safety, no executable validation, immutable history, optimistic concurrency, read-only inactive companies.

## 15. Automated test results (all executed in this session)

| Suite | Command | Result |
|---|---|---|
| API + database (real PostgreSQL, database rebuilt from migrations 0001→0006) | `ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6399/0 uv run --project services/api pytest services/api/tests` | **303 passed, 0 failed** (Phase 3: 207; +69 pure-engine tests in `test_p4_engine.py`, +27 in `test_p4_workspace.py`). Two Phase 2/3 tests were updated because their premise changed (a new company now has workspace tables, with zero rows; workspace limits are now “enforced”). |
| Lint, format, boundaries, types | ruff check, ruff format --check (86 files), import-linter (2 contracts kept), mypy `--strict` (53 files), eslint, `tsc --noEmit` | clean |
| API contract | `export_openapi.py --check`, `check_openapi_breaking.py git:HEAD …` | up to date; **0 breaking** |
| Web unit / build | vitest; production `next build` (inside `make e2e`) | 6 passed; succeeded |
| Browser end-to-end | `E2E_CHROMIUM_PATH=/opt/pw-browsers/chromium infrastructure/dev/e2e.sh` | **32 passed** (31 before + `workspace.spec.ts`) |
| Accessibility | axe-core WCAG 2.0–2.2 A/AA in light and dark on the builder’s empty state, populated structure, archive dialog, filing rules, permissions, template gallery and the templates page, plus `/workspace-templates` in the all-pages scan | 0 violations (the first run found a real label-in-name defect on the move buttons; fixed) |
| Dependency audit | `make audit` | no known vulnerabilities (Python and JavaScript) |
| Regression | all Phase 1–3 API tests (inside the 303) and Super Admin / Control Center / auth e2e specs | passed |

**Not executed:** Docker Compose smoke test, `make hooks` pre-commit run, the RLS benchmark, GitHub CI for this change (it can only run after a push; nothing was pushed to a PR), a Vercel preview redeploy. No mutation-testing pass on the new modules, no load test with large trees, no penetration test, no screen-reader review.

## 16. Known issues and limitations (nothing hidden)

1. **Deviations from the Phase 0 design / `PHASE_4.md`** (ADR-041…046): no multi-operation change sets (each change is atomic and versioned instead); integer `position` instead of fractional sort keys; no workflow definitions/approval rules; no Intake Inbox (TS-WS-12 not built); no device change-feed record (clients poll `upload-config` with ETag); no `document_field_values` table or type-matching trigger (Phase 6); no template “review differences and merge” flow; no drag-and-drop; no Mobile preview tab; template role presets for permissions not included.
2. **Document-bound guarantees are not yet testable**: rename/archive/delete semantics are proven at node level (flag, stable ids, RESTRICT reference via a stand-in table); full document-level verification of TS-WS-03/04 is a Phase 6 acceptance condition, as `PHASE_4.md` states. `workspace.impact()` reports `documents: 0` and must read `documents` in Phase 6. Category re-mapping with derived financial records cannot be refused yet.
3. **Pre-Phase-4 plans lack two limit keys** (`limit.filing_rules_per_company`, `limit.workspace_templates`): creating filing rules or organization templates is denied (default deny) until Super Admin adds them. The development plan in `seed.py` includes them.
4. **Subscription & Usage page** still shows the workspace limits as “Not metered” (it lists organization-level measurements); per-company usage is shown in the builder.
5. **Hidden-node rules are API-level.** There is no document RLS mirroring yet (Phase 6). A node hidden from a member is hidden in the workspace/upload-config responses and by 404 on direct access; naming/filing/history/permission lists require `configure`/`manage_permissions`.
6. **`base_version` is optional**; clients that omit it get serialized last-write-wins. The builder always sends it.
7. **Revisions store a full snapshot each time.** Fine at tested sizes; large workspaces with many edits need a compaction policy. Restore does not restore permission grants or the access mode, and cannot recreate things that are never hard-deleted by design.
8. **Archived template cannot be applied again**; there is no “unarchive template”.
9. **Filing rule priorities are not unique by constraint** (a warning and a deterministic tie-break instead).
10. **Reference fields** can point only to workspace nodes or categories of the same company; vendor/customer/cost-centre registers do not exist.
11. **Field validation editor** in the UI exposes length, min/max and decimal places only (the API also accepts date ranges, masks, currency lists, item counts).
12. **Client-side (company role) users have no workspace access**; Mobile will need role keys when it arrives.
13. **English only** in the new screens (ADR-040 still applies); **no moderated usability sessions** with non-technical accountants were run (a PHASE_4.md checklist item — open).
14. Free-form node `config`/category `processing_settings` JSON (size-capped, 8 KB for nodes) is stored but nothing reads it yet.
15. Concurrent-writer serialization relies on `SELECT … FOR UPDATE` of the configuration row; it was not stress-tested.
16. Test-isolation note: the new entitlement tests use `entitlement_overrides` through the existing helper; the suite shares one database per run as before.

## 17. Manual configuration required

- Add `limit.filing_rules_per_company` and `limit.workspace_templates` to any plan created in Super Admin before this release (§16.3). Existing section/folder/depth/custom-field limits already exist on plans and are now enforced per company (a plan with `0` or a small value will now block growth).
- Nothing else: no new settings, secrets or services. Run `make db-reset` (or `alembic upgrade head` + `artify-api seed`) so the document kinds, permissions, entitlement definitions and Artify starter templates are synchronized.

## 18. Steps to test the complete Workspace Builder

```bash
make install && make hooks
make db-reset                                  # migrations 0001->0006 + seed
uv run --project services/api uvicorn artify_api.main:app --port 8000
cd apps/web && ARTIFY_API_URL=http://localhost:8000/api/v1 pnpm dev      # http://localhost:3000
# sign up, verify via the link in the API console, sign in, enrol TOTP
# Create company (note the new Workspace step: blank / template / copy)
# Companies -> your company -> Configuration -> Open the workspace builder
#   Structure: add section, add subfolder, rename, Move…, Move up/down, duplicate, archive (see impact), show archived, restore
#   Categories: add one mapped to “Bank statement”; Custom fields: add “Bank”; Naming rules: pattern {Company}_{Bank}_{Period}, preview
#   Filing rules: add one for Bank statement -> your folder; use “Test your rules”
#   Permissions (owner): switch to Restricted; invite/assign an accountant and grant View on one section; sign in as them
#   Templates: apply “Construction” to a second company (fits-plan check first); Save as template; Copy from another company
#   History: open versions, preview a restore
make check                                     # lint, types, OpenAPI drift, API tests, docs check, web tests + build
ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6379/0 make test-api
E2E_CHROMIUM_PATH=/path/to/chromium make e2e   # includes workspace.spec.ts
make client                                    # after any API change
```

Sandbox note: PostgreSQL and Redis stop when the sandbox restarts; restart them before tests (`LOCAL_SETUP_GUIDE.md` §7–8).

## 19. Integration readiness

**Phase 5 (Desktop):** ready — `upload-config` gives the permitted, active, hierarchical target list with `version` and ETag; node `name` is already a safe Windows folder name; ids are stable across renames/moves; offline behaviour is specified in [`../workspace/FUTURE_INTEGRATION_CONTRACTS.md`](../workspace/FUTURE_INTEGRATION_CONTRACTS.md). Open: D-7 (Explorer-side edits) and a change-feed push (polling works).

**Phase 6 (documents):** ready with explicit obligations listed in the contracts document: RESTRICT foreign keys to `workspace_nodes`/`document_categories`, document counts in `impact()`/`delete_node`, `document_field_values` using the existing validators, `original_filename` stored separately with the naming engine, filing via `evaluate_rules`, document RLS mirroring of grants, and the document-level halves of TS-WS-03/04/11.

**Before Phase 5 the owner may want to decide:** (a) whether multi-operation change sets are required for the Builder before documents exist, (b) whether the Intake Inbox and workflow/approval definitions belong in Phase 4 or move to Phases 6/8, (c) whether to run the moderated usability sessions now, (d) whether client-side roles need workspace read access for Mobile (Phase 11), (e) Arabic/RTL for signed-in screens (still ADR-040).
