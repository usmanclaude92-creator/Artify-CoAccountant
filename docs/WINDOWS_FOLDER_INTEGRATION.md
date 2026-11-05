# Windows Folder Integration

How the desktop app presents a company's workspace as folders on a Windows disk. Code: `apps/desktop/core/src/mirror.rs`, `storage.rs`, `docs.rs`. Deeper background: [desktop/LOCAL_STORAGE.md](desktop/LOCAL_STORAGE.md), [documents/LOCAL_STORAGE_INTEGRATION.md](documents/LOCAL_STORAGE_INTEGRATION.md). **Verified only by unit tests of the pure Rust core on Linux; never run on Windows, with OneDrive, antivirus or real long-path settings.**

## 1. Source of truth

The folder tree is **derived** from the workspace the server delivers for the company. There are no built-in folder names. A company with an empty workspace gets an empty tree. Each local folder is tied to a workspace node by its **stable id**; the display name is only a label.

## 2. What the mirror does

| Server change | Local effect |
|---|---|
| New node | Folder created (parent first). |
| Renamed or moved node | The existing local folder is renamed or moved; saved files go with it. |
| Archived or vanished node | Local folder **left untouched**; nothing is deleted. |
| Two siblings with the same name (case-insensitive) | The second gets a suffix from the end of its node id, deterministically. |
| Illegal Windows characters, reserved names (`CON`, `LPT9`…), trailing dots/spaces | Sanitized per segment. |
| Drive unavailable | Everything stops before a single directory is created. |
| Path longer than the safe limit | Reported; not created. |

Documents are copied on demand, with checksum verification, under a unique name; an existing file is never overwritten.

## 3. Rules the brief asked for and where they stand

| Rule | Status |
|---|---|
| Local structure comes from the actual workspace configuration | Implemented. |
| Stable node ids, not names | Implemented (rename test). |
| Checksums for documents | Implemented for downloads (SHA-256 compared with the server's). |
| Never delete a cloud document because a local file is missing | Holds by construction: the app has no call that deletes a server document from a missing local file, and the mirror never reads the disk to decide what exists. |
| Detect files dropped into a local folder and import them | **Not implemented.** Import is explicit (drag-and-drop/file picker into the queue). |
| Detect edits made in Explorer to a downloaded copy | **Not implemented** (D-7 stays open). |
| Two-way sync / bulk offline copies / placeholders | **Not implemented.** |
| Multi-user machines | Separate databases, roots and credential entries per Windows user (by design; not exercised on Windows). |
| OneDrive-managed folder | Warning at root selection; behaviour under OneDrive's file-on-demand not tested. |
| Long paths, Unicode names | Segment sanitizer and a 160-character budget under the 260-character limit are unit-tested; no Windows matrix run. |

## 4. Test matrix still to run on Windows

Windows 10 and 11, standard user and administrator; local NTFS, a second drive, a USB drive removed mid-run, a OneDrive folder, a network share; names with Arabic text, emoji and trailing dots; a 240-character path; locked file (open in Excel); antivirus quarantine of a downloaded file; two Windows users on one PC; rename storm (50 renames in one sync).
