---
name: workspace-customization-engine
description: Use when changing workspace sections/folders, document categories, custom metadata fields, naming rules, filing rules, workspace permissions, templates or workspace history in the Artify repo (Phase 4 code in services/api workspace*.py and routers/workspace.py, UI in apps/web components/cc/workspace). Covers stable ids, archive-not-delete, category-to-document-kind mapping, versioning and the extension points for Phases 5-11.
---

# Workspace customization engine

Read first: `docs/workspace/WORKSPACE_ARCHITECTURE.md`, then the topic doc in `docs/workspace/`.

## Rules that must not break
1. **No hard-coded structure.** Nothing seeds sections, folders or categories. A company with zero rows must behave correctly. Templates are data that users choose.
2. **Reference by id/key, never by display name.** `key` is immutable (nodes, categories, fields). Renames change `name`/`display_name` only.
3. **Archive, don't delete.** Nodes/categories/fields/rules get `archived_at`; options are archived; hard delete of a node only when nothing ever referenced it (RESTRICT FKs). Archiving a node cascades with one timestamp and switches rules targeting it off.
4. **Two layers.** Customer words (nodes, categories, fields) vs Artify words (`document_kinds`, accounting domain, processing type). A category always maps to a kind; processing logic must key off the kind, never a folder or category name.
5. **Templates are copied, never linked.** `apply_definition` creates new ids; `applied_template_id` is provenance only. `replace` archives.
6. **Every mutation** = `begin_change` (lock + company must be `active` + optional `base_version`) → change rows and call `scope.record(...)` → `commit` (entitlement increases, version bump, revision, audit). Never write workspace tables outside this path.
7. **Limits live in `entitlements.COMPANY_MEASURES`/plans**, enforced only in `commit`/`enforce_company_limits`. No limit constants (except the technical `MAX_DEPTH_HARD`).
8. **Authorization is two-stage.** Route: `Requires("workspace.read", company=True)`; handler: `access(ctx).require(action, node_id)`. Hidden nodes → 404, forbidden → 403. Pure logic lives in `workspace_engine.decide`.
9. **Config is validated by the pure engine** (`workspace_engine.py`): no regex, no code in field validation; naming patterns are parsed, never evaluated.

## Adding a feature
- New table → follow `multi-tenant-saas` (composite FKs incl. the 3-column `(organization_id, company_id, id)` references, forced RLS, grants) and add it to the snapshot SQL (`workspace._SNAP_SQL`) and, if restorable, to `workspace_templates.restore_revision`.
- New operation → extend the service module, add the router endpoint with the right node action, add the audit/revision record, extend `test_p4_workspace.py` (two-company isolation + permissions + entitlement case) and `make client`.
- Clients: contract in `docs/workspace/FUTURE_INTEGRATION_CONTRACTS.md`; keep `upload-config` filtered by `view`+`upload`, ETag-able.

## Tests to extend
`test_p4_engine.py` (pure), `test_p4_workspace.py` (API + DB), `apps/web/e2e/workspace.spec.ts` (UI + axe).
