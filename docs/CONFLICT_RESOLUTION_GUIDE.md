# Conflict Resolution Guide

What a "conflict" is in this system, what the server does today, and the rules every client must follow. **Status: the server records, verifies and keeps an immutable, audited history of conflicts and their resolution; the desktop and phone apps and the Control Center list and resolve them; open conflicts feed Daily Closing validation. Automatic reporting exists only for the desktop's new-version uploads.** Tests: `test_p12_conflicts.py` (6), `core/tests/sync.rs`, `sync.test.tsx`, `sync_test.dart`.

## 1. Where conflicts come from

A conflict is a write based on a copy that is no longer the latest one, or a write to something that became locked. Two causes only:

* **Stale write** — the client held revision *N*, the server is at *N+1* or later.
* **Locked target** — the record was approved, posted, exported, its day was closed, or its period was closed after the client last looked.

## 2. What the server does

| Mechanism | Behaviour | Where |
|---|---|---|
| `base_row_version` on update requests | If it differs from the stored `row_version` the request is refused with HTTP 409; nothing is changed. | documents, accounting configuration/accounts/transactions, bookkeeping policy/tasks/checklists |
| `Idempotency-Key` | A retried identical request returns the first result; the same key with a different body is 422. | all mutating routes that accept it |
| `POST /sync/check` | Returns `current`, `stale`, `locked` or `missing` with a reason before the client tries to write. | [SYNCHRONIZATION_ARCHITECTURE](SYNCHRONIZATION_ARCHITECTURE.md) |
| `POST /sync/conflicts` | The client reports a refused or doomed write; **the server verifies it with the check above** (a current item is `422 no_conflict`; an invisible one 404) and stores the revision it held. Idempotent per item and device. | `sync_conflicts` (migration 0013) |
| `GET /sync/conflicts`, `GET /sync/status` | The caller's open or resolved conflicts for records still visible to them; the server's own count of open conflicts. | |
| `POST /sync/conflicts/{id}/resolve` | Records `keep_server`, `apply_local`, `merged` or `discarded` with resolver, time and note; audit event; a resolved row can no longer change. `apply_local`/`merged` need a note of at least 5 characters, are refused (`409 locked`) for a locked record and return `next_base_revision`. | |
| Database triggers | Approved/posted records, closed days, closing snapshots and stored binaries cannot be changed by any request, whatever the client sends. | migrations 0007–0012 |

A write without `base_row_version` is accepted as last-writer-wins on that field set. Clients that edit data offline **must** send the revision they started from.

## 3. Rules by data type

| Data | Rule | Who may resolve |
|---|---|---|
| Original document file | Never overwritten. A changed local file is a **new version**; the old version stays. No conflict is possible. | uploader |
| Document metadata (name, category, fields) | `base_row_version`; on 409 show server value and local value side by side; the user picks one or edits; the choice is saved as a new revision. | anyone with edit rights on that folder |
| Workspace structure | Server-authoritative, versioned (`base_version`); clients never push structure. A local rename of a mirrored folder is **not** sent anywhere. | workspace administrators |
| Extracted data / review decisions | Review status is per field; a field already approved by a person is not changed by a re-run (suggestions are stored beside it). | reviewer |
| Transactions and journal | Draft: `base_row_version`. Approved/posted: **no resolution** — correct by reversal or a new version through the accounting workflow. | accountant with approval rights |
| Daily closing | Stage changes are server state machine moves; a stale move is refused. Closed: reopen workflow only. | closing approvers |
| Exports | Immutable. A repeat is a new export with a reason. | n/a |
| Settings and policies | `base_row_version`; last decision shown to the user, no merge. | holders of the settings permission |

## 4. Client rules

1. Never silently overwrite server data and never silently discard a local change. A 409 or `stale` moves the item to **Conflict Detected** and keeps the local copy.
2. Never retry a 403/409/422 automatically; only network errors and 5xx are retried, with the same idempotency key.
3. Offline approvals do not exist: an approval is requested online, by a person, and the server decides.
4. Show the user what differs, who changed the server copy and when (from the record's own audit trail), and offer only the choices the rule above allows.

## 5. Who may decide

The person who found the conflict, or anyone holding `sync.resolve` (firm owner, practice administrator, practice manager, accountant), and only while they can still see the record. A colleague with neither gets 403 on the decision but may read the conflict. The decision never writes the record: the write goes through the normal endpoint, with its own permission, `base_row_version` and idempotency checks.

## 6. What is still missing

* Only the desktop's new-version upload preflights and reports automatically (see [SYNCHRONIZATION_ARCHITECTURE §7](SYNCHRONIZATION_ARCHITECTURE.md)); other write paths rely on `base_row_version` refusals and the Synchronization page.
* No merge editor and no side-by-side field comparison; the summaries stored with a conflict are small and client-supplied (limited to 4 KB, never trusted for decisions).
* The Control Center page lists and decides conflicts per company; there is no firm-wide cross-company view.
* Open conflicts now feed Daily Closing validation (`SYNC_SERVER_CONFLICTS`, company-wide, severity error, policy-adjustable).
