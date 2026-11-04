# Integration Contracts for Later Phases

## Stable identifiers

Nodes, categories, fields and rules are referenced by **uuid**; `key` is a stable machine name (immutable). Display names may change at any time. Documents (Phase 6) must store `node_id`/`category_id` plus a `filed_path_snapshot`, never a path or name as the reference.

## Phase 5 – Windows Desktop and Phase 11 – Mobile: `GET /companies/{id}/workspace/upload-config`

Response: `{company_id, version, targets[], categories[], fields[]}` filtered for the caller: active, non-archived nodes on which the caller has `view` **and** `upload`; categories that are active and whose folder is among the targets (or have none); active fields with options. `ETag` + `If-None-Match` → **304**. Hierarchy: `targets[].parent_id` (null for roots or when the parent is not a target), `position`, `depth`. Windows folder names are the node `name` (already filesystem-safe by the name rules; `display_name` is for screens only). An empty workspace returns empty lists — clients must never fall back to a built-in list.

Offline devices: a device holds `version`; on reconnect it re-fetches. An upload addressed to a node that was archived, made inactive, or hidden meanwhile must be refused by the document API with a structured conflict (Phase 6) and the device re-fetches `upload-config`, offering the nearest valid target. Renamed nodes keep their id, so a stale local name does not misfile. Moves keep the id; the device remaps its local mirror from the new tree. Explorer-side edits are D-7 (open).

`GET /companies/{id}/workspace` is the full builder view (adds management data and per-node `can` flags).

## Phase 6 – documents

- `documents.node_id → workspace_nodes(organization_id, company_id, id)` **ON DELETE RESTRICT**; `category_id` likewise; add the documents count to `workspace.impact()` and `delete_node`.
- Attach `document_field_values` to the field definitions; validate with `workspace_engine.validate_values` and the applicable-fields query (`workspace_config.applicable_fields`); reference values use `ref_exists_fn`.
- Store `original_filename` separately; generate names with `parse_pattern` + `render_name` + `ensure_unique_name`; the deterministic fallback is `fallback_name`.
- Mirror `workspace_permissions` into document RLS (allowed node set in the transaction context).
- Filing: call `evaluate_rules` (`workspace_config.rule_defs`) and file only into active, non-archived targets; archived/inactive targets are already excluded.
- Category re-mapping to another document kind must be refused once financial records derive from the category (accounting-mapping rule 4); not enforceable until records exist.

## Phase 7/8 – AI and accounting

Classification suggestions choose among the company’s **categories**; the accounting engine reads `document_categories.document_kind` / `accounting_domain` / `processing_type` and never folder names. `reference_only` categories must never create financial records.

## Phase 9 – Excel

Custom field **keys** and ids are the stable mapping targets (key is immutable; labels are not).

## Distribution of change

Today clients poll `upload-config` with the ETag. A push/change-feed record (Phase 0 design) is not implemented; `workspace_revisions` is the source from which one can be derived.
