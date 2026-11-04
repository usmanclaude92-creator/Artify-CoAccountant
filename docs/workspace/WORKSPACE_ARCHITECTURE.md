# Workspace Architecture (as built in Phase 4)

Status: implemented and tested. The design intent is in [`../architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md`](../architecture/WORKSPACE_CUSTOMIZATION_ARCHITECTURE.md); this document records what the code actually does and where it deliberately differs.

## Principle

Every company owns an independent workspace. Nothing seeds a default structure; a new company has **zero** sections, folders, categories, fields and rules (tested). Two companies in one firm can have 4 and 10 sections (tested) and changing one never touches the other.

## Two layers that must not be confused

| Layer | Owned by | Examples | Where |
|---|---|---|---|
| **Organization layer** (customer words) | the company | section "Supplier Payments", folder "Bank of Muscat", category "Muscat Bank Monthly Statements", field "Cost Center" | `workspace_nodes`, `document_categories`, `custom_field_definitions` |
| **Accounting layer** (Artify words) | Artify | document kind `bank_statement` → accounting domain `banking`, processing type `statement_extraction` | `document_kinds` (reference data) |

A category **must** map to a document kind (`document_kind NOT NULL` + foreign key + API validation). Folder names never carry accounting meaning. The domain is fixed by the kind; the processing type may be the kind's default or `reference_only` (store without processing).

## Components

| Module | Role | Imports |
|---|---|---|
| `workspace_engine.py` (pure) | document-kind catalog; node-name safety; custom field definition/value validation; naming-rule parser/renderer; filing-rule validation/evaluation/analysis; allow/deny access evaluation | no database, no web framework (import-linter contract) |
| `workspace_templates_data.py` (pure) | template definition schema (`TemplateDefinition`) and the Artify system templates | engine |
| `workspace.py` | per-company serialization lock, optimistic version, snapshot + revision + audit (`commit`), node tree operations, access loading | audit, entitlements, errors |
| `workspace_config.py` | categories, fields (+options, applicability), naming rules, filing rules, permission grants | workspace |
| `workspace_templates.py` | export a company to a definition, apply a definition (deep copy), template storage, revision restore | workspace_config |
| `routers/workspace.py` | the HTTP API and per-action authorization | all of the above |

Layers are enforced by `import-linter` (see `services/api/pyproject.toml`).

## Writing a change (all mutations)

1. `Requires("workspace.read", company=True)` resolves membership, MFA, organization status and company reach (404 outside scope).
2. The router checks the **node-level** action (`workspace_engine.decide`) — see [`WORKSPACE_PERMISSIONS.md`](WORKSPACE_PERMISSIONS.md).
3. `begin_change` locks `company_configurations` `FOR UPDATE` (serializes writers per company), requires the company to be `active` (inactive/archived → 409), and compares the optional `base_version` (stale → 409 `workspace_version_conflict` with the current version).
4. The service changes rows and records `before`/`after` per entity.
5. `commit` enforces entitlement **increases** (402), bumps `config_version`, stores an immutable `workspace_revisions` row (changes + full snapshot) and writes an `audit_events` row `workspace.<action>`.

Everything happens in the request transaction: an error anywhere rolls back the data, the version bump, the revision and the audit row.

## Deviations from the Phase 0 design (honest list)

| Design said | Built | Why / consequence |
|---|---|---|
| Change sets (`POST …/change-sets`, `dry_run`, pending-change summary) | Individual atomic mutations; each bumps the version; dry run exists for template apply, copy and revision restore; node archive reports impact and needs confirmation | Simpler contract for the first builder; a multi-operation change set is not available. Bulk operations (template apply, duplicate, restore) are atomic. |
| Fractional `sort_key` | Integer `position`, renumbered for the affected sibling list | Reordering writes one row per sibling instead of one row. Fine for realistic sizes. |
| `workspace_sections` + `workspace_folders` tables | One adjacency table `workspace_nodes` (`node_type` section/folder) with `depth` and `path_ids` | One recursive model; sections are roots. |
| Workflow definitions, stages, approval rules | **Not built** | Out of the brief’s Phase 4 list; belongs with the review/approval phase. |
| Intake Inbox system node | **Not built** | Needs document intake (Phase 6). |
| `document_field_values` + type-matching trigger | **Not built** | Needs `documents` (Phase 6). The value validators exist and are exposed through `fields/validate-values`. |
| Change-feed record for devices | **Not built**; clients poll `GET …/upload-config` (version + ETag) | There is no change-feed in the repository yet. |
| Drag-and-drop | **Not built**; Move…, Move up/down buttons (keyboard friendly) | Brief: “if feasible”. |
| Template role presets for permissions | Not included (permissions are user/role bound and are never copied) | Security. |

## What is not here (by design)

No document upload/storage, OCR, AI, accounting entries, VAT, reconciliation, Excel, Desktop or Mobile. Filing rules and naming rules are **configuration and validation only**: nothing is filed, moved or renamed.
