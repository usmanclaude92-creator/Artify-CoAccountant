# Audit Log Integrity

A tamper-evident layer over the tenant audit log. **Implemented and tested; it detects changes by someone who cannot also recompute the whole chain, and it does not stop a database owner who can.** Code: migration 0016, `artify-api seal-audit`, `GET /orgs/{org}/audit-integrity`. Tests: `test_p12_audit_chain.py` (6).

## Design

* `audit_events` already refuses updates, deletes and truncates (triggers). A **sealer job — not the request path, so requests are not slowed or serialized** — appends one row per event to `audit_seals`: per-organization `seq` (1, 2, 3…), `prev_hash`, and `row_hash = sha256(prev_hash || canonical text of the event)`. The canonical text covers id, organization, company, actor type and id, action, entity, summary and the UTC timestamp.
* The sealer is a `SECURITY DEFINER` function (`app.audit_seal`) executable only by the dispatcher role; the request role cannot call it, and `audit_seals` is append-only (triggers) and visible to a tenant only for its own organization.
* `app.audit_verify(org)` recomputes the chain under the caller's row-level security and reports the first problem: `sequence_gap` (a seal was removed), `chain_broken`, `event_missing` (an event was deleted) or `event_modified`.
* `GET /orgs/{org}/audit-integrity` (permission `audit.read`) returns `ok`, how many events were checked, the first bad sequence, the **head hash** and `unsealed_recent` — events of the last 7 days the sealer has not reached yet. Those are not yet protected, and the endpoint says so.

## Anchors: keeping the head outside the database

`artify-api audit-heads [--out FILE]` prints (or appends) one JSON line per chain — every organization and the platform chain (`organization_id` null) — with the newest sequence number and hash. **Ship these lines to a store the database operator cannot rewrite** (append-only bucket in another account, a signed e-mail, a ticket). `artify-api audit-check-anchors FILE` later checks each exported anchor against the database: a missing seal means the newest history was cut off (`seal missing`), a different hash means it was rewritten (`hash differs`); exit code 1 on any mismatch. Tests cut off the newest seal — which `audit-integrity` alone cannot see — and rewrite a seal; both are caught by the anchor check. The functions behind it (`app.audit_heads`, `app.audit_seal_at`) are executable only by the dispatcher role.

## The platform audit log

`platform_audit_events` has its own single chain (`platform_audit_seals`, `app.platform_audit_seal`, `app.platform_audit_verify`), sealed by the same `seal-audit` job; `GET /platform/audit-integrity` (permission `platform.audit.read`) verifies it. Tested: a modified platform event is detected; customers cannot reach the endpoint.

## Running it

`artify-api seal-audit` once, or `--loop 30` (it seals tenant and platform chains); Docker Compose runs an `audit-sealer` service and, for development only, writes heads to `/tmp`. Monitor `artify_audit_unsealed_events` / `artify_audit_oldest_unsealed_seconds` (see `/metrics`), run `audit-integrity` and `audit-check-anchors` on a schedule, and **schedule `audit-heads` to an external store — scheduling and the external store are the operator's to set up; nothing here does it for you.**

## What it does not do

* A database owner who can rewrite `audit_events` **and** recompute every later seal can still forge history *unless* the exported anchors were kept out of that person's reach. With anchors, forging requires reproducing every exported hash, which is not possible after the fact.
* Events with no organization (pre-tenant security events) are not sealed.
* Events written during the last sealer interval are unprotected until sealed.
* Tested on a development database only; sealing speed on millions of events was not measured.
