# Local Storage

## Root location

The user chooses the root folder (Settings → Storage; native folder dialog opened by Rust). The path is stored per signed-in user. Nothing assumes the Windows Documents folder. Requirements checked before adoption (`storage::validate_new_root`): absolute path, no `..`, not a drive root, exists, is a directory, **writable** (a probe file is created and removed), free space ≥ 100 MB (warning below 2 GB), and **no overlap** with the previous root (neither nested nor containing). OneDrive locations produce a warning. Changing the location **never moves or deletes files**; the old folder stays as it was.

## Status at any time

`ok` · `missing` (folder or drive not available — never silently recreated) · `not_a_directory` · `not_writable` · `low_space`. The status, path and free space are shown in Settings.

## Company folders

`company_dir` creates `<root>/<Company name>/`: name sanitized for Windows (illegal characters, control characters, trailing dots/spaces, `.`/`..`, reserved device names such as `CON`, `LPT9.log` → `LPT9_.log`, length ≤ 80), made unique case-insensitively per user (`Name`, then `Name (xxxxxxxx)` from the company id). The name is chosen **once and stored**, so renaming a company in the Control Center never orphans local data. Creation never overwrites: an existing file with that name is an error; an existing folder is reused untouched. The resulting path must stay inside the root (symlink escapes are refused) and below 160 characters, leaving room for documents under the 260-character Windows limit.

## Separation

Each company has its own folder; the database, caches and settings are keyed by Windows user profile (app data) and by signed-in user. Different Windows users have separate databases and roots. The frontend can never pass an arbitrary path to a file operation: only `storage_set_root` (validated) receives a path.

## Phase 6 additions

Document copies, the folder mirror, the download policy and path rules are in [`../documents/LOCAL_STORAGE_INTEGRATION.md`](../documents/LOCAL_STORAGE_INTEGRATION.md).

## Not built (Phase 7 and later)

Two-way file sync, the `_Inbox` watched folder, long-path (`\\?\`) handling beyond the length guard, Explorer-edit detection (D-7).
