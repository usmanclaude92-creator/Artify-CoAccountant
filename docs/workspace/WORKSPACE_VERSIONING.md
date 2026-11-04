# Versioning, History and Audit

## Version

`company_configurations.config_version` starts at 1 (“nothing configured”). Every successful mutation increments it by exactly one inside the locked transaction and writes `workspace_revisions(version, action, changes, snapshot, actor_id)`. A rolled-back request leaves no gap.

`base_version` (body, or query for DELETE) is optional on every mutation: a stale value returns **409 `workspace_version_conflict`** with `current_version`; the builder reloads and asks the user to repeat. Desktop/Mobile read the version from `GET …/upload-config`.

## What a revision holds

- `changes`: list of `{action, entity_type, entity_id, before, after}` (e.g. `node.moved` with the old/new parent).
- `snapshot`: the company’s whole configuration at that version — nodes, categories, fields, options, applicability, naming rules, filing rules, permissions, access mode.
- The application role can only `SELECT` and `INSERT` revisions (tested): history cannot be rewritten through the app.

Audit: each change also writes `audit_events` `workspace.<action>` (visible in the company History tab and organization Activity page) and `GET …/workspace/audit`.

## Restore

`POST …/revisions/{version}/restore` (`dry_run` supported). Non-destructive semantics:

- items created after the revision are **archived**; items archived after it are **restored**; renames, moves, edits and re-ordering are reverted; hard-deleted nodes are re-created with their original ids;
- fields keep their key and type; options and applicability are reset to the snapshot; categories and rules return to their recorded properties;
- permissions and the access mode are **not** restored;
- the restore runs in one savepoint-protected transaction; a clash (duplicate names/settings that cannot be reconciled) returns 409 and nothing changes;
- a restore is itself a new version (`restored`), so it can be undone.

Limits: restore does not enforce plan limits beyond the normal increase check; it cannot recreate hard-deleted categories/fields (they are never hard-deleted); snapshots are stored per revision (size grows with workspace size × changes — an archival/compaction policy is future work).
