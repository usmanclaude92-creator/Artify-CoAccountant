# Backup and Disaster Recovery

What protects the data, what has actually been tested, and what has not. **A backup that has never been restored is not evidence.** This document separates targets from measurements.

## 1. What must be recoverable

| Asset | Where it lives | Recoverable from |
|---|---|---|
| Relational data (tenants, workspace, accounting, closings, audit, exports metadata) | PostgreSQL | Logical dump or physical backup + WAL (PITR) |
| Document binaries, export workbooks | Object storage (S3-compatible; local disk in development) | Bucket versioning / replication / provider backup |
| Secrets and keys | Deployment secret store | Secret-store backup (not in the database, not in the repo) |
| Redis | Rate-limit counters and caches only | Nothing — safely rebuilt |
| Clients' local data | Desktop sealed cache, mobile queue | Re-sync from the server; unsent queue items on a lost device are lost |

A restore of the database without the matching object-store state leaves dangling document rows; both must be restored to the same point, and the document checksum (`sha256`) lets the check find any mismatch.

## 2. Targets (NOT measurements)

| Item | Target | Status |
|---|---|---|
| RPO, database | ≤ 15 minutes (continuous WAL archiving) | **Not measured.** No WAL archiving is configured anywhere. |
| RPO, object storage | ≤ 24 hours (versioning + replication) | Not configured. |
| RTO, core API | ≤ 4 hours | Not measured. |
| Restore drill | Quarterly, with RLS re-verification | First scripted drill below; no schedule exists. |

Source: [security/SECURITY_ARCHITECTURE.md](security/SECURITY_ARCHITECTURE.md) §Backup. They remain targets until a drill on production-like infrastructure reports numbers.

## 3. Restore check that WAS executed

`infrastructure/ops/restore_check.sh <source-db-url>` dumps the source database (`pg_dump -Fc`), restores it into a scratch database, then compares, for every table, the row count and an md5 over the ordered rows. It exits non-zero on any difference and drops the scratch database.

Run on 2026-11-05 against the development test database (`artify_test`, built from migrations 0001–0012 plus the API test data left by the last test):

| Measure | Result |
|---|---|
| Tables compared | 121 |
| Rows compared | ≈ 2,115 |
| Source database size | 18 MB |
| Dump time / file size | 0.3 s / 888 KB |
| Restore time | 1.9 s |
| Outcome | **PASS — every table identical after restore** |

**What this does and does not show.** It shows the dump and restore tools work on this schema (partitions, triggers, enums, forced RLS policies came back) and that content survives byte-for-byte for the tables compared. It does **not** show: production-size timing (this database is tiny, so these times say nothing about RTO), point-in-time recovery, restore of an encrypted or off-site backup, role and grant restoration on a fresh server (the script restores into the same server with the same roles), object-store restore, or behaviour under a failed-over replica.

The script does not re-run the RLS isolation tests against the restored database. Do this manually in any drill: restore, then run `pytest tests/test_isolation_api.py tests/test_database_rls.py` with `ARTIFY_TEST_SKIP_RESET=1` against it, or an equivalent two-tenant probe.

## 4. Backup design to implement (not built)

1. Managed PostgreSQL with automated daily base backups and continuous WAL archiving to a bucket in a second zone/region (D-3 decides provider and residency). Retention per D-8.
2. Object storage with versioning on, deletion protection and replication; the application never deletes binaries physically except through the audited purge workflow.
3. Backup credentials separate from application credentials; restore access limited to named operators and logged.
4. Encryption of backups with a key held outside the database host. Be explicit with customers about D-4: platform-managed keys do not protect against operators of the platform.
5. Per-tenant export (all documents + data for one organization) as the basis for account closure and for single-tenant restore; not built.

## 5. Disaster scenarios and the current honest answer

| Scenario | What happens today |
|---|---|
| Database corruption / bad migration | Restore from last dump taken by the operator. Migrations are forward-only; there is no automated down-migration. Rehearse every migration on a restored copy first. |
| Accidental deletion of a document | Soft delete; purge is a separate confirmed action; originals are immutable. Restore by un-deleting before purge. After purge only a backup helps. |
| Region/zone outage | No multi-zone deployment exists. |
| Lost laptop (desktop) | Sealed cache plus credential store; revoke the device/session in the Control Center; data re-syncs on a new machine. |
| Lost phone | Revoke sessions; tokens live in the OS keystore; the unsent queue on the phone is lost. |
| Ransomware on the database host | Depends entirely on off-host, immutable backups, which do not exist yet. |

## 6. Drill procedure (to run on staging)

1. Record start time. Take the latest base backup; restore to a new instance. 2. Replay WAL to a chosen timestamp. 3. Restore the matching object-store version. 4. Run migrations `current` check, the isolation tests, and `restore_check.sh`-style table comparison against the pre-drill checksums where available. 5. Start the API against it; sign in; open a document; verify the closing snapshot hashes with the verify endpoints. 6. Record end time; **RTO = end − start; RPO = failure time − last restored transaction time.** Write both into the drill report.
