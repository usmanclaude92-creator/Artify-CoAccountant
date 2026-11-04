# Duplicate Detection

## Rule

A duplicate is an **exact byte match**: the same SHA-256 as a **non-purged version** of an **active or archived** document **in the same company**. Removed (soft-deleted) documents do not count; other companies and organizations are never compared (a duplicate in another tenant is invisible by construction). Fuzzy / near-duplicate detection (same invoice scanned twice) is Phase 8.

## When it is checked

1. **Desktop, before upload**: the core hashes the file while queueing (streaming SHA-256) and sends it with the reservation.
2. **Server, at completion**: the authoritative check, on the hash the server computed itself, so a client cannot bypass it by lying or omitting the hash. `POST /documents/check-duplicate` lets a client ask in advance.

## Policy

`duplicate_policy` on the upload: **`block`** (default) — the upload fails with `duplicate_detected` and the staged data is kept, so the user can decide without re-sending; **`allow_copy`** — the document is stored as a separate document and, when the identical document is in a folder the user can view, an event `duplicate_kept` and a row in `document_duplicate_candidates` (`kept_separate`, immutable) record the decision and who made it.

## What the caller learns

The response says that a duplicate exists. The **reference** (document id, name, status, folder path) is included **only if the caller may view that document's folder**; otherwise `document: null` — the user learns "an identical document exists" and nothing about where.

## Desktop

The queue shows *Failed — a document with identical content already exists* with three actions: **Show the existing document** (the check-duplicate call; the answer respects visibility), **Add it anyway** (retry with `allow_copy`) and **Do not add it** (remove the entry). The import form can pre-choose the policy per file.

## Limits

Different bytes ⇒ not a duplicate, even if visually identical. An edited copy of the same invoice is a different file. Hash collisions are not handled specially (SHA-256).
