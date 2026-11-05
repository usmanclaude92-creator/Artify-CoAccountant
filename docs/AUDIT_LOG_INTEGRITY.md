# Audit Log Integrity

A tamper-evident layer over the tenant audit log. **Implemented and tested; it detects changes by someone who cannot also recompute the whole chain, and it does not stop a database owner who can.** Code: migration 0016, `artify-api seal-audit`, `GET /orgs/{org}/audit-integrity`. Tests: `test_p12_audit_chain.py` (6).

## Design

* `audit_events` already refuses updates, deletes and truncates (triggers). A **sealer job — not the request path, so requests are not slowed or serialized** — appends one row per event to `audit_seals`: per-organization `seq` (1, 2, 3…), `prev_hash`, and `row_hash = sha256(prev_hash || canonical text of the event)`. The canonical text covers id, organization, company, actor type and id, action, entity, summary and the UTC timestamp.
* The sealer is a `SECURITY DEFINER` function (`app.audit_seal`) executable only by the dispatcher role; the request role cannot call it, and `audit_seals` is append-only (triggers) and visible to a tenant only for its own organization.
* `app.audit_verify(org)` recomputes the chain under the caller's row-level security and reports the first problem: `sequence_gap` (a seal was removed), `chain_broken`, `event_missing` (an event was deleted) or `event_modified`.
* `GET /orgs/{org}/audit-integrity` (permission `audit.read`) returns `ok`, how many events were checked, the first bad sequence, the **head hash** and `unsealed_recent` — events of the last 7 days the sealer has not reached yet. Those are not yet protected, and the endpoint says so.

## Running it

`artify-api seal-audit` once, or `--loop 30`; Docker Compose runs an `audit-sealer` service. Monitor `unsealed_recent` (alert when it grows) and run the integrity check on a schedule.

## What it does not do

* A database owner who can rewrite `audit_events` **and** recompute every later seal can forge history. Removing the newest seals is invisible unless the head hash was copied somewhere that person cannot reach. **Exporting `head_hash` regularly to an external store (a separate account's bucket, a ticket, a signed e-mail) is the missing control and is not built.**
* Events with no organization (pre-tenant security events) and the platform audit log (`platform_audit_events`) are not sealed.
* Events written during the last sealer interval are unprotected until sealed.
* Tested on a development database only; sealing speed on millions of events was not measured.
