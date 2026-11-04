# Local Storage Integration (Windows)

The cloud is the source of truth. The local folder is a *derived mirror* plus on-demand copies, built on the Phase 5 storage foundation (configured root, per-company folder chosen once, safe names).

## Folder tree

`mirror::plan` turns the workspace delivered by the server into relative paths keyed by **node id**: sanitised Windows-safe segments, case-insensitive uniqueness among siblings (a clash gets `name (xxxxxx)` from the node id, deterministic and order-independent), archived nodes and their descendants omitted, nodes with an unknown parent treated as roots. `mirror::apply` makes the disk match:

| Event | Behaviour |
|---|---|
| New node | Directory created |
| Renamed or moved node | The **same directory is renamed/moved** (children and saved files follow; saved-file records are re-pointed) |
| Target name already exists locally | Reported as a conflict; nothing overwritten; the node keeps its previous folder |
| Archived or vanished node | Local folder and files **left untouched** (counted as `orphaned`) |
| Drive or root missing | `storage_unavailable` **before** anything is created; the root is never recreated implicitly |
| Path too long | Not created; reported in `too_long` |

Nothing is ever deleted by the mirror.

## Files

*Save a copy / Open* downloads the document into its node's folder: streamed to a `.part` file, checksum verified against the server's, renamed without overwriting (`name (2).pdf` if the name exists). An identical, already saved copy of the same version is reused. If the user edited a local copy, the next save creates a new file instead of replacing it. A damaged download is discarded and nothing is left behind.

## Changing the storage root

Phase 5 rules still apply: an overlapping root is refused and existing files are never moved or deleted. After a change, folders and copies are created under the new root on demand; the old ones stay where they were (documented behaviour; open decision D-7 about Explorer edits stays open).

## Not in this phase

No bidirectional sync and no detection of changes made in Explorer; no bulk "download everything" (full offline sync is a later phase); "Show in folder" and "Open" use Windows Explorer / the default program and are **not verifiable on Linux**.

## Upload queue persistence

Table `upload_queue` (migration 2 of the local SQLite) keeps the item with its sealed private fields (path, name, metadata). After a crash `queue_recover` puts *preparing/uploading* items back to *waiting*; the registered server session is reused, bytes the server already holds are not resent, an expired session is replaced by a new one, and a file that changed or vanished is reported (`source_changed`, `source_missing`) instead of being uploaded. Signing out keeps unsent items; a different user never sees them (`user_id` scoping).
