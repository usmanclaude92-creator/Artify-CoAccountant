# Phase 4 — Workspace Structure & Customization Engine

**Status: NOT STARTED.** This phase establishes the configurable model that Phases 5–11 depend on. It must be complete and stable before any document management or AI work.

## Objectives

Implement per-Company configurable workspaces: sections, nested folders, document categories mapped to document kinds, custom fields, workflows and approvals, filing rules, section permissions, templates, change sets with versioning, and the distribution APIs for Desktop and Mobile.

## Functional scope

- Workspace Structure Builder (Control Center module 6): tree editor, rename/reorder/move/archive/restore, drag-and-drop with keyboard alternatives, three previews (Control Center, Mobile, Windows folders), pending change summary, impact analysis, history.
- Categories with mandatory document-kind mapping; `document_kinds` reference data (catalog from `ACCOUNTING_DATA_MAPPING.md`).
- Custom field definitions (all types), options, applicability, validation, defaults, visibility.
- Workflow definitions: stages, transitions, approval rules, defaults; versioning.
- Filing rules (propose/auto-file) with dry-run test.
- Section permissions.
- Templates: Artify system templates (General Business, Trading, Construction, Professional Services, Manufacturing, Retail), Organization templates, save-as-template, apply (deep copy), diff-and-merge update review.
- Default workspace offered at Company creation (optional, editable).
- Intake Inbox (system node).
- `upload-config` and full `workspace` APIs with ETag/version, change-feed records for workspace changes.
- Entitlements: `limit.workspace_sections_per_company`, `folder_depth`, `folders_per_company`, `custom_fields_per_company` enforced on change-set totals.

## Technical implementation

`workspace` domain module (change-set processor with operation handlers, validators, impact analyzer, snapshotter), materialized path/depth maintenance, fractional `sort_key`, dynamic field validators (Pydantic models built from definitions), template engine (schema-validated JSON definition; apply = replay with id remapping), policy integration for section permissions (incl. RLS section grants), change-feed writer, Builder UI (`apps/web`), `workspace-customization-engine` and `multi-company-configuration` skills.

## Database changes

Creates: `workspace_configurations`, `workspace_sections`, `workspace_folders`, `document_categories`, `document_kinds`, `custom_field_definitions`, `custom_field_options`, `custom_field_applicability`, `workflow_definitions`, `workflow_stages`, `workflow_transitions`, `approval_rules`, `filing_rules`, `workspace_section_permissions`, `workspace_templates`, `workspace_template_versions`, `workspace_configuration_versions`, `company_change_log` (workspace records). `document_field_values` is **not** created here (it references `documents`, which arrives in Phase 6); Phase 4 fixes its contract in the field-definition validators and ships the type-matching trigger function so Phase 6 only attaches it. All with RLS and composite FKs.

## API requirements

`GET /companies/{id}/workspace`, `GET …/workspace/upload-config`, `POST …/workspace/change-sets` (+ `dry_run`), `GET …/workspace/versions`, `GET|POST …/workspace/templates/apply`, `GET /orgs/{id}/workspace-templates` (+ versions), `POST /orgs/{id}/workspace-templates` (save-as), field/category/rule/permission read endpoints, `GET /reference/document-kinds`.

## Dependencies

Phase 3 complete (Companies, roles, entitlements UI); D-9 (localized names) considered; Phase 1 change-feed conventions.

## Security requirements

Company-scoped RLS on every table; template apply cannot cross Companies; regex validation engine is ReDoS-safe; field definitions cannot carry executable content; permission changes audited; section grants reflected in RLS; archived nodes unreachable for new filing.

## Testing requirements

Executable: TS-WS-01 through TS-WS-12; TS-ISO-03 (workspace parts); TS-ENT-05; property tests for sort keys and tree invariants (no cycles, depth limits); migration test; builder E2E and a11y; moderated usability sessions with non-technical accountants.

## Acceptance criteria

1. Two Companies in one firm have different structures (4 vs 10 sections) and neither affects the other.
2. Rename/archive/restore semantics are proven at node level: archive is a flag, hard delete is rejected for referenced nodes (verified with a test-only table holding a RESTRICT foreign key to the node, since `documents` does not exist yet). Full document-level verification of TS-WS-03/04 happens in Phase 6, and Phase 6 cannot be accepted without it.
3. Applying a template creates an independent copy; later template edits change nothing.
4. Categories cannot be used without a document kind.
5. Workspace change sets are atomic, versioned, and entitlement-checked.
6. Mobile upload-config returns only permitted, non-archived targets; no hard-coded categories anywhere.

## Completion checklist

- [ ] All TS-WS scenarios executed and recorded
- [ ] Usability sessions done; findings addressed or logged
- [ ] Skills created: `workspace-customization-engine`, `multi-company-configuration`
- [ ] Docs updated if the model changed
- [ ] `PHASE_4_COMPLETION_REPORT.md`

## Known risks

Builder UX complexity; combinatorial permission interactions; pressure to take shortcuts with fixed categories; template-update merge semantics; retroactive tightening of required fields.
