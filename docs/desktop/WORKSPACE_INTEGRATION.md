# Workspace Integration (Phase 4 contract)

The desktop app displays the company's **actual** workspace, as configured in the Firm Control Center, filtered by the server for this user. Nothing is hard-coded: no category, section or folder name exists in the app's code, tests use per-test data.

## Source

`GET /companies/{id}/workspace` (with `If-None-Match`). Response fields used: `version`, `workspace_state`, `nodes[]` (`id`, `parent_id`, `node_type`, `key`, `name`, `display_name`, `description`, `position`, `depth`, `is_active`, `archived_at`, `can{}`), `categories[]`. Hidden nodes are not delivered; a node whose parent is hidden arrives re-rooted (`parent_id` null, `detached`).

## Validation before caching (`workspace.rs::parse`)

Rejected (never cached, previous cache kept, event recorded): wrong company id in the answer, version < 1, duplicate or empty node ids, unnamed nodes, a `parent_id` that was not delivered, cycles. An empty node list is valid ("not configured").

## Version handling

- ETag revalidation: unchanged ⇒ 304 ⇒ only the sync timestamp moves.
- A newer version replaces the cache; a response with an **older** version than the cache is ignored (`stale_response`), so a stale replica can never roll the cache back.
- Changes are described **by stable node id**, never by name: added, renamed, moved, archived, restored, no-longer-visible. Example summary: "Workspace updated: 1 added, 1 renamed, 1 archived." It becomes a notification and, if the workspace is open, a banner.
- The open screen keeps its selection by id; if the selected node vanished the user is told.

## Rendering

Hierarchical tree (sections open one level by default; expand/collapse each, expand all, collapse all), search that keeps the path to every match, "Show archived items" (archived are hidden by default, marked when shown, and flagged as unable to receive new documents), inactive items marked, details panel (type, status, description, subfolders, mapped categories, **the user's own permissions on that item** from `can{}`, stable identifier), refresh, loading/error/empty states, "saved copy" banner when the server cannot confirm.

## Permissions

The app shows only what the server delivered. A user restricted to some sections sees only those. The app does not re-implement or cache the permission engine; `can{}` is displayed, not enforced locally.

## Local folders

"Prepare local company folder" creates only the company's folder under the configured root ([`LOCAL_STORAGE.md`](LOCAL_STORAGE.md)). The per-folder directory tree is built by Phase 6 when a document is saved locally: it is derived from this same cached tree **by node id**, and a folder rename/move in the cloud is applied to local copies as a rename by *Match folders to the cloud* ([`../documents/LOCAL_STORAGE_INTEGRATION.md`](../documents/LOCAL_STORAGE_INTEGRATION.md)).

## Tested

4-section, 10-section/nested and a third differently shaped company (Rust and UI tests); live against a real API (4 vs 10 sections, archived node, empty company); version change with rename detected by id; stale/invalid/other-company responses; restricted users; offline fallback; revoked access (404 not papered over by the cache).
