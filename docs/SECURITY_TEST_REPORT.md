# Security Test Report (Phase 12)

An internal, honest account of what security testing has been **executed** against this repository and what has not. It is **not** an independent penetration test and must not be presented as one. An independent test remains a prerequisite for any external pilot (audit H-5).

## 1. Scope and method

Code at the Phase 12 branch head, tested on a single development machine against a local PostgreSQL 16 and Redis, synthetic data only. Evidence is automated tests that ran and passed, dependency scanners, and code reading. No tester outside the development team, no production-like infrastructure, no network-level testing.

## 2. Tests executed (all automated, all in the API suite unless noted)

| Area | What the tests prove | Where |
|---|---|---|
| Tenant isolation | Two organizations, two companies each: every list/get/update route returns 404 for the other tenant's ids; composite foreign keys reject cross-tenant references; the application role has no `BYPASSRLS`; RLS is forced. Repeated per phase for each new table. | `test_isolation_api.py`, `test_database_rls.py`, `test_p4_*`, `test_p6_*` … `test_p12_sync.py` |
| Authorization | Role × permission matrix per route; hidden folders return 404, forbidden 403; platform admins cannot read customer business data without a support grant. | `test_p2_authz.py`, `test_p4_authz_guards.py`, `test_p2_*`, `test_p3_*`, [ROLE_AND_PERMISSION_MATRIX](security/ROLE_AND_PERMISSION_MATRIX.md) |
| Authentication | Password policy, lockout and rate limits, MFA, session rotation and revocation, refresh-token reuse. | `test_auth.py`, `test_ratelimit.py` |
| Immutability | Triggers refuse updates to stored binaries, approved records, closing snapshots, exports, even with the application bypassed (tests switch triggers off to simulate tampering and show the hash check detects it). | `test_p6`, `test_p8`, `test_p9`, `test_p10` |
| Upload safety | Type sniffing, size limits, archive/macro refusal for workbooks, path-traversal names, EICAR-style test pattern via the scanner interface (stub scanner). | `test_p6_*`, `test_p9_*` |
| Injection | Parameterised SQL everywhere; filter/sort fields are whitelists (reports are governed datasets, not SQL). Covered by route tests with hostile input; not by a fuzzer. | `test_p8`, `test_p9` |
| Money and approvals | AI cannot approve; approvals need `actor_type = user` plus permission; maker–checker enforced in application and database. | `test_p7`, `test_p8`, `test_p10` |
| Sync feed (Phase 12) | Feed and preflight never reveal other companies, other organizations, forbidden record kinds or hidden folders; a device clock cannot move a cursor; malformed cursors are 422. | `test_p12_sync.py` |
| Browser | Security headers present; axe accessibility checks; sign-in/up flows. | Playwright suite (last run Phase 10: 33 passed) |
| Dependencies | `pip-audit` and `pnpm audit --prod`: no known vulnerabilities at the Phase 10/11 runs. Re-run before release. | tooling |

The API suite result at the Phase 12 head is in the [completion report](development/PHASE_12_COMPLETION_REPORT.md) §18.

## 3. Response headers (API)

`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, and `Strict-Transport-Security` when served over HTTPS (set in `main.py`). The web app sets its own CSP in `next.config.ts`. CORS is an allow-list from settings.

## 4. Not tested

* **Independent penetration test** — not done.
* Dynamic scanning (OWASP ZAP/Burp), fuzzing, load-based abuse, DoS and slow-request testing.
* TLS configuration, WAF, network segmentation, cloud IAM — no production environment exists.
* Real antivirus engine (ClamAV) and real S3-compatible storage behaviour.
* `cargo audit` (not installable here), SBOM generation, container image scanning.
* Mobile: root/jailbreak detection, certificate pinning (neither implemented), reverse engineering of the app, device storage inspection.
* Desktop: installer tampering, DLL search order, update-channel signing (none exists).
* Secrets rotation drill and incident-response exercise.
* Social engineering, phishing of administrators, insider scenarios beyond what RLS and audit tests model.

## 5. Findings from this review

| Id | Finding | Severity | Status |
|---|---|---|---|
| S-1 | No external penetration test. | High | Open |
| S-2 | Audit log is trigger-protected but not hash-chained or externally anchored. | Medium | Open |
| S-3 | No certificate pinning or root detection on mobile. | Medium | Open (documented in [mobile/SECURITY_AND_PRIVACY](mobile/SECURITY_AND_PRIVACY.md)) |
| S-4 | Rate-limit values never tuned under load. | Medium | Open |
| S-5 | Notifications not delivered, so security-relevant alerts (new device, failed sign-ins) reach users only through the in-app audit views. | Medium | Open |
| S-6 | No Phase 12 security defect was found in the new sync routes (isolation, permission and cursor tests pass). | — | Closed |

## 6. Encryption — what is and is not protected

* **In transit:** TLS is required by the clients (the desktop app refuses non-HTTPS except localhost; mobile requires HTTPS). TLS termination is a deployment concern and is untested here.
* **At rest, today:** relies on whatever the hosting platform provides for the database volume and the object store (platform-managed). The application stores passwords as salted hashes, refresh tokens hashed, and sealed local caches on desktop; mobile keeps tokens in the OS keystore.
* **Per-tenant envelope encryption (decision D-4): not implemented.** Document bytes and database rows are not encrypted with keys that belong to a tenant. Therefore **this system does not protect customer data from a database administrator, a cloud administrator or anyone holding a database backup and its platform key.** Platform-admin access to customer data is limited by application policy (support grants) and by RLS for the application role — not by cryptography.
* If D-4 is decided in favour of per-tenant keys: envelope encryption of stored objects first (data key per object, wrapped by a key per organization in a KMS), then selected sensitive columns; database-level admin access would still read unencrypted operational columns unless those are encrypted too. The schema keeps `encryption_key_ref` hooks for this. Nothing of this is built.

## 7. Release gate

Before a pilot: independent penetration test complete with no open Critical/High; dependency scans re-run clean or triaged; D-4 decided and, if per-tenant, implemented; production TLS/WAF/IAM reviewed; incident-response and secret-rotation drills done.
