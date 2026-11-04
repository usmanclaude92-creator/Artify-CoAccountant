# Local Storage Integration (Desktop)

Builds on [`../desktop/LOCAL_STORAGE.md`](../desktop/LOCAL_STORAGE.md): the user chooses one **storage root** (Settings → Storage); each company gets its own subfolder (`<root>/<company folder>`). Phase 6 fills it.

## The local copy is a copy

The cloud is official. A local file is a *convenience copy* of a specific version, recorded in the sealed SQLite table `local_files` (document id, version id, node id, relative path, SHA-256, size). The original files the user imported stay where they were; nothing is moved or deleted.

## Folder structure comes from the cloud tree

The relative path of a local copy is `<company folder>/<section>/<folder>/…/<file name>`, derived **from the cached workspace tree by node id** (`mirror::node_segments`). No section or folder name is written in the code. If the document's folder is not in the cached tree yet, the core refreshes the workspace once; if it is still missing the copy is refused (`workspace_unavailable`) rather than guessed.

## Rules

* **Never overwrite.** A name clash gets ` (2)`, ` (3)`… (case-insensitive, as NTFS). `unique_in_dir` checks the directory; `place_file` renames the finished download into place.
* **Verified.** Downloads go to a temp file inside the app's data folder, the SHA-256 is computed while streaming and compared with the server's `X-Content-SHA256` and the document's stored hash; a mismatch deletes the temp file and records nothing (`checksum_mismatch`).
* **Not twice.** If a copy of the same version already exists and the file is there, nothing is downloaded and the existing path is returned (`already_present`).
* **Paths are validated.** `resolve_under(root, rel)` rejects empty, absolute, `.`/`..`, backslash or drive-letter segments and builds the path one segment at a time (so separators are the platform's own). Names are sanitised for Windows (reserved names, trailing dots/spaces, forbidden characters, length).
* **Webview supplies no path.** Files to upload are referred to by token; the file to open is chosen by the core from its own records.

## Reconciling after the cloud structure changes

`mirror_reconcile` plans moves from `local_files` against the *current* tree (a folder was renamed/moved ⇒ the local file's expected path changed) and **renames** files (never copies, never overwrites the destination; existing conflicts are reported). Missing local files (drive unplugged, user deleted) are reported as *missing* and never silently recreated. Settings → Storage → **Match folders to the cloud** runs it.

## Download policy

`mirror_policy` (a validated setting): `on_demand` (default: only documents the user saves or opens), `recent` (last 30 days), `all`. **Download now** fetches what the policy asks for and is not yet local, bounded to 25 files per run so a big company never blocks the app. There is no automatic background mass-download in this release.

## Open and preview

*Open in default app* downloads (when a storage root is usable, as a normal local copy; otherwise into a temp folder that is cleaned at the next sign-in) and hands the path to `explorer.exe` (Windows) without a shell. The in-app **viewer** receives the bytes through the core (up to 30 MiB), verified, as a `blob:` URL; images get zoom/fit/rotate/print, PDFs use the web view's built-in viewer.

## Sign-out, session end and revoked devices

These wipe the cached organizations, companies and workspaces of that user (Phase 5 behaviour) but **keep queued uploads and the local-copy bookkeeping**: unsent work is never discarded silently, and without the bookkeeping the next sign-in would not recognise files already saved and would save a second copy. All queue and mirror rows are per user; another person signing in on the same PC does not see them. The files themselves stay in the company folders on disk. Queued uploads cannot send anything until the user signs in again; at sign-in, uploads that were interrupted are put back in the queue. (No code path currently deletes pending uploads on purpose; a "discard my queue" action is a future decision for the owner.)

## Not done in this phase

No file watching; no two-way sync; no conflict resolution beyond "never overwrite"; no offline *creation* of documents beyond the upload queue; no mobile.
