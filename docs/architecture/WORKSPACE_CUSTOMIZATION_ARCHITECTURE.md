# Workspace Structure & Customization Architecture

This is a foundational capability (brief §4, §6). **No part of the system may hard-code a list of sections, folders or categories.** Document processing, Mobile categories, Windows folders, filing, permissions and exports all consume the Company's configured workspace.

Related: [`ACCOUNTING_DATA_MAPPING.md`](ACCOUNTING_DATA_MAPPING.md) (what categories *mean*), [`WORKFLOW_AND_APPROVALS.md`](WORKFLOW_AND_APPROVALS.md), [`STORAGE_AND_SYNC_ARCHITECTURE.md`](STORAGE_AND_SYNC_ARCHITECTURE.md), [`../database/ENTITY_CATALOG.md`](../database/ENTITY_CATALOG.md).

## 1. Concept model

```
Company ─1──1─ workspace_configurations (version n)
                 ├─ workspace_sections ─┐  top level; as many as entitlement allows
                 │    └─ workspace_folders (nested, parent_folder_id; depth ≤ limit.folder_depth)
                 ├─ document_categories      each → exactly one document_kind (Artify-controlled)
                 │    └─ category ↔ section/folder "allowed placement" and default
                 ├─ custom_field_definitions (+ options) with applicability to categories
                 ├─ filing_rules
                 ├─ workflow_definitions (see WORKFLOW_AND_APPROVALS)
                 ├─ workspace_section_permissions
                 └─ workspace_configuration_versions (immutable snapshots)
Intake Inbox: one system-reserved, non-deletable node per Company
```

Everything above carries `organization_id` and `company_id`. Nothing is shared between Companies; templates are *copied* in (§6).

## 2. Stable identity and naming

- Every node has an immutable `id` (UUIDv7) and an immutable machine `key` (slug, unique within the Company and node type; generated from the initial name, editable only by creating a new node).
- `display_name` (and its localized variants, see D-9) is freely renamable.
- Nothing internal references a display name: documents → `section_id`/`folder_id`/`category_id`; Excel mappings → field ids/keys; permissions → section ids; sync → ids.
- At filing time the document stores `filed_path_snapshot` (text) for audit readability even if the structure later changes.

## 3. Hierarchy

- `workspace_sections`: ordered with `sort_key` (fractional indexing: reorder changes one row, not N).
- `workspace_folders`: adjacency list (`parent_folder_id`) belonging to one section; `depth` and a materialized `path_ids` array are maintained on write for fast subtree queries and to enforce `limit.folder_depth`. Moving a folder revalidates depth and cycles inside the change set.
- Sibling name uniqueness is enforced among *non-archived* siblings (case-insensitive) to keep local Windows folders unambiguous.
- Different Companies have different counts and shapes by construction: Company A: 4 sections; Company B: 10 sections with 3-level folder trees; both are plain rows scoped by `company_id`.

## 4. Editing model: change sets, versions, preview

All workspace edits are **change sets**: an ordered list of operations submitted in one request.

Operations: `section.create|rename|move|archive|restore`, `folder.create|rename|move|archive|restore`, `category.create|update|archive|restore`, `field.create|update|archive|restore`, `rule.create|update|delete`, `permission.set`, `template.apply`.

```
POST /companies/{id}/workspace/change-sets?dry_run=true   → validation + impact analysis, nothing persisted
POST /companies/{id}/workspace/change-sets                → atomic apply
```

- **Atomic**: all operations succeed or none do.
- **Optimistic concurrency**: the request carries `base_version`; a stale base returns `409 workspace_version_conflict` with the current version so the UI can rebase.
- **Entitlements** are checked for the *resulting* counts inside the transaction (`EntitlementService`), not per-click in the UI.
- **Impact analysis** (dry run) returns counts: documents affected, filing rules referencing a node, Excel mappings referencing a field, Desktop devices with local mirrors that will rename folders, section permissions affected, and any validation errors/warnings. Archiving a section shows exactly how many documents and rules it touches.
- **Result**: `workspace_configurations.version` increments; an immutable snapshot (`workspace_configuration_versions`: JSONB snapshot + operations + author + timestamp) is stored; a `workspace.changed` record is written to the Company change feed; audit events are written.
- **Preview**: the Control Center renders the proposed tree from the dry-run response (resulting tree + mobile upload-config + local folder preview) before the user confirms.

## 5. Rename, archive, restore — never destroying documents

| Action | Behavior |
|---|---|
| Rename section/folder/category | Updates `display_name` only. Documents unaffected. Desktop mirrors rename folders via change feed. Excel mappings unaffected (id-based). |
| Move folder | Updates parent/path; documents move with it logically; object storage keys unchanged (ADR-011); Desktop mirror moves directories. |
| Archive section/folder/category | Sets `archived_at`. Existing documents remain filed there and remain viewable/searchable/exportable. The node disappears from pickers and Mobile upload-config; new filing is blocked (`409 section_archived`). Archiving a section archives its folders by cascade *as archived flags*, not deletes. |
| Restore | Clears `archived_at` (name collision handled by suffix proposal in dry run). |
| Delete | Only allowed for a node with **zero** references ever (never held a document, no rules, no mappings, no snapshots referencing it as live). Otherwise only archive is offered. Foreign keys are `ON DELETE RESTRICT`. |
| Reorder | `sort_key` update only. |
| Intake Inbox | Cannot be archived or deleted. |

## 6. Templates and presets

Sources: **Artify system templates** (platform-owned; "General Business", "Trading Company", "Construction Company", "Professional Services", "Manufacturing", "Retail" — starting points, authored in Phase 4) and **Organization templates** (firm-owned "Custom Accounting Firm Template" and any others).

- A template is a versioned, schema-validated definition document (`workspace_template_versions.definition` JSONB): sections, folders, categories (with document-kind mapping), fields, rules, workflow, section permission *role* presets.
- **Apply = deep copy.** Applying a template version to a Company is a `template.apply` operation inside a change set: it creates new nodes with **new ids** in the Company. The Company stores `applied_from_template_version_id` as **provenance only**; no foreign key drives behavior, and no query joins from a Company node back to a template for runtime behavior.
- **No live dependency.** Editing or publishing a new version of a template changes nothing in any Company.
- **Template updates are explicit.** If the template later gets a new version, the Control Center may *offer* "review differences" which computes a diff between the Company's current workspace and the new template version and lets an authorized user choose operations to merge via a normal change set. Nothing is ever pushed automatically.
- **Apply to non-empty workspace:** default mode "add" (adds nodes with collision suffixes), never overwrites. "Replace" is permitted only on an empty workspace (no documents) and is audited.
- Creating a template from an existing Company workspace is supported ("save as template"); it strips Company-specific data (permissions bound to users, references to vendors, etc.).
- Template content is validated against the target Company's entitlements before apply (dry run shows the shortfall).

## 7. Default workspace

New Companies may be offered Artify's recommended default (or none). The default is a normal template applied by the same mechanism: editable, optional, replaceable, and never referenced by the processing engine. A Company with an entirely different structure behaves identically.

## 8. Custom fields and metadata

Definition (`custom_field_definitions`, Company-scoped):

| Property | Notes |
|---|---|
| `key` (immutable), `label` | Label is localized/renamable |
| `data_type` | `text`, `number`, `date`, `currency`, `dropdown`, `boolean`, `reference`, `multi_select` (limited and justified: dropdown option sets such as multiple projects) |
| `required` | Optionally per applicable category |
| `validation` | Declarative: min/max length, regex (length-bounded, ReDoS-safe engine), numeric range, decimal places, date range, allowed options |
| `default_value` | Type-checked against the definition |
| `visibility` / permissions | Which roles/sections may view/edit the value |
| `applies_to` | Set of categories (and optionally sections) where it appears |
| `reference_target` | For `reference` type: `vendor`, `customer`, `chart_account`, `cost_center` list, or another Company-defined option set |

Storage (ADR-007): `document_field_values(organization_id, company_id, document_id, field_id, value_text | value_number | value_date | value_bool | value_ref_id, currency_code, field_definition_version)` — one row per (document, field); multi-select stores one row per selected option. Unique on `(document_id, field_id, option_id)`. Indexes support filtering/reporting by `(company_id, field_id, value_*)`.

Validation: the API builds a validator from the definition (Pydantic dynamic model) and rejects invalid values; the DB constraints enforce "exactly one value column populated, and it matches the field's declared type" via a trigger that reads the definition. Changing a field's type after values exist is forbidden (create a new field and migrate); making a field required applies to new submissions and surfaces existing gaps as warnings.

## 9. Filing rules

`filing_rules`: ordered, deterministic conditions → actions. Conditions: source (mobile/desktop/email-in later), uploader role, category suggestion from extraction (above a confidence threshold), vendor match, filename pattern, custom-field value. Actions: set section/folder/category, set field values, assign reviewer. A rule that depends on AI output produces a **proposal** (document sits in `review` with a suggested filing) unless the Company explicitly enabled auto-file for a high-confidence classification; auto-filing never approves anything and is always visible in history. Conflicts resolve by explicit priority; dry-run test against sample documents is available.

## 10. Section permissions

`workspace_section_permissions`: grants of `view`, `upload`, `edit_metadata`, `review`, `approve`, `export`, `manage` to roles or users **per section** (optionally per folder subtree). Evaluated in the policy engine after role permissions and Company scope, and mirrored into RLS policies for documents (RLS checks the allowed section set from the transaction context). Default for a new section: inherits the Company's role-based defaults. Deny is implicit (absence of grant), not an override list, to keep evaluation auditable.

## 11. Config distribution to clients

See [`APPLICATION_ARCHITECTURE.md`](APPLICATION_ARCHITECTURE.md#5-how-desktop-and-mobile-retrieve-and-apply-the-workspace-configuration). Summary: version + ETag, permission-trimmed, change-feed notification, structured `409` for stale targets, Desktop local mirror derived from the same tree.

## 12. Workspace Structure Builder (Control Center UX requirements)

For non-technical accountants:

- Tree editor with inline rename, add section/folder, drag-and-drop reorder/move **plus** keyboard and button alternatives (accessibility).
- Side panel for the selected node: category mapping (with plain-language explanation of what the mapped accounting kind means), fields, permissions, rules, document count.
- Live preview tabs: "Control Center tree", "What staff see on Mobile", "Windows folders".
- Draft-and-confirm: edits accumulate client-side as a pending change set with a summary and impact analysis; **Apply** commits atomically; history shows versions and who changed what, with restore-from-version implemented as a new change set (never rewriting history).
- Clear inline messages when an entitlement limit would be exceeded, naming the limit and what the plan allows.
- Template gallery: preview, apply (with diff preview), save-as-template.

## 13. Invariants (tested in Phase 4; scenarios in `TEST_SCENARIOS.md`)

1. No query path reads a section/category/field by display name for business logic.
2. A change set is all-or-nothing and version-checked.
3. A node referenced by any document can never be hard-deleted.
4. Applying a template creates new ids; subsequent template edits do not alter Companies.
5. Workspace data for Company A is unreachable and unmodifiable from Company B's context (RLS + composite FKs + API scope).
6. Every category used by a Document has a document-kind mapping; unmapped categories cannot be activated.
