# Document Security Test Results (Phase 6)

All results below are from commands run against this repository (Linux sandbox, real PostgreSQL 16 with forced RLS, a real running API for the live test) unless marked *CI*. Test data is synthetic (`services/api/tests/samples.py`); no real documents were used. **Nothing here was run on Windows by hand.**

## Where the tests are

| Layer | Files | What it proves |
|---|---|---|
| File validation, storage, scanner client | `services/api/tests/test_p6_validation.py` (11) | every format accepted by content; corrupt, empty, unsupported, executable, renamed-executable, disguised, extension/content mismatch, compression-bomb and unsafe-name files rejected; keys opaque and traversal impossible; staging exact, resumable and non-overwriting; clamd protocol (clean / infected / unavailable) against a **fake** clamd server |
| Upload, integrity, workspace, metadata, search, duplicates | `test_p6_documents.py` (30) | byte-exact round trip; resume at the server's offset; checksum mismatch; cancel; idempotent init; storage failure keeps data and retries; **a database failure after storing removes the object**; reconciliation; two companies with different structures; folder rename/move/archive/delete rules; versions; metadata; naming/filing; search; duplicates inside one company only and without leaking hidden references |
| Security, authorization, entitlements | `test_p6_security.py` (20, including the two added by the mutation review) | see below |
| Desktop core | `apps/desktop/core/tests/{documents,mirror}.rs` (14 + 8) | confirmed-only completion; resume; retry/back-off; restart recovery; duplicate decision; quarantine; offline; cancel; corrupted download discarded; no second download; closed operation set; path traversal; no overwrite; reconcile moves |
| Desktop against a real API | `apps/desktop/core/tests/live_backend.rs` (2 new) | a 5 MiB multi-chunk upload, SHA-256-identical download and preview, duplicate refusal and decision, a mislabelled file refused by the real server, an accountant reaching only the assigned company's documents (typing another company's ids or document ids returns 404/403) |
| UI | `apps/desktop/src/documents.test.tsx`, `lib/*.test.ts`; `apps/web/e2e/documents.spec.ts` | destinations limited to folders the server allows; no path ever shown; required details block queueing; refused files explained; offline keeps the file; duplicate choice; quota message says nothing was deleted; permitted actions only; non-previewable files go to the default app; Control Center views and accessibility |

## Security scenarios in `test_p6_security.py`

Other organizations cannot touch any document endpoint (18 routes × two organization claims → 403/404, nothing leaked, stored object untouched) · an accountant sees only assigned companies and swapped ids give 404 (including another company's document through this company's path, content, patch and upload) · **an owner (not limited by RLS) also gets 404 for another company's document through this company's path** · folder permissions apply immediately (deny hides list, counts, search, content, versions, events, direct id and upload; revoke is effective at once; restricted mode) · who may upload / change / remove / purge by role · suspended members and revoked devices lose access at once · uploads belong to the person who started them · hostile file and folder names cannot reach storage or display · scanning is only claimed when it ran · storage quota enforced on the backend and never deletes data · concurrent uploads cannot oversubscribe the quota · file-size and batch limits come from the plan and only lower the central ceiling · monthly document quota counts documents, not versions · a suspended subscription blocks uploads but not reading · usage reporting · usage visible only with `entitlements.read` · only a **removed** document can be permanently deleted · every important action is recorded · batches.

## Mutation review (executed)

Each row: one control was disabled by editing the source, the three Phase 6 backend test files were run, the source was restored. "killed" = a test failed (good).

| Disabled control | Result |
|---|---|
| list hides folders the user cannot see | killed |
| counts hide invisible folders | **survived at first** (vacuous assertion) → test fixed → killed |
| legal hold blocks purge | killed |
| purge needs the typed document name | killed |
| purge needs the retention period | killed |
| purge needs the organization permission | killed |
| only a removed document can be purged | **survived at first** (no test) → test added → killed |
| storage keys are validated | killed |
| content inspection rejects bad files | killed |
| upload offset is server-authoritative | killed |
| no more data than declared | killed |
| storage quota checked at completion | killed |
| document quota checked at completion | killed |
| storage quota checked at upload start | killed |
| duplicates are looked up inside the company | killed |
| duplicate reference only if the folder is visible | killed |
| company storage totals need the `configure` action (added after a review found the leak) | killed |
| documents are loaded inside the company (query filter removed) | **survived — equivalent mutant**: forced RLS and the node-visibility check (computed for the requested company) still answer 404, which a dedicated owner-level test confirms. The query filter is kept as a third layer |

Not mutation-tested: the immutability triggers (covered by direct SQL tests that try to update/delete as the application role), the clamd client (only a fake server), Desktop controls (covered by the Rust and UI tests above but not mutated).

## Defect found by this review and fixed

`GET /companies/{id}/documents/storage` returned company-wide totals, a by-type breakdown and upload counts for **all** folders to anyone who could open the company, including counts that include folders hidden from them. It now requires the company-level `configure` action (managers); the web Documents tab asks for it only for managers; a test asserts 403 for an accountant.

## Not tested

Real ClamAV; a real object store; concurrency beyond the quota race test; very large files (the biggest test file is a few MiB; the live test uses 5 MiB); disk-full behaviour; Windows runtime behaviour; load/performance; penetration testing by a third party.

## Counts and run results

* Backend, full suite (real PostgreSQL + Redis): **425 passed, 0 failed** in 170 s. Of these, the Phase 6 files hold **115 cases** (`test_p6_validation` 46, `test_p6_documents` 49, `test_p6_security` 20). One earlier full run failed a Phase 2 dashboard-count test (`test_dashboard_numbers_match_the_database…`, 125 ≠ 126 organizations): another test process was using the same database at the time; the test passes alone and in the clean full run above. It is a pre-existing test that compares two reads of a shared table and is sensitive to concurrent runs.
* Desktop Rust core: **100 passed**, 7 live tests (ignored in the default run) **7 passed against a real API and PostgreSQL** (`make desktop-live`).
* Desktop UI: 75 passed (14 in `documents.test.tsx`, 11 in the two new `lib/*.test.ts` files).
* Web e2e (Playwright, Chromium): **33 passed** (includes `documents.spec.ts` and the accessibility audit of the new pages).
* `pip-audit` and `pnpm audit --prod`: no known vulnerabilities.
