# Production Deployment Guide

A runbook for the **first** staging and production deployments. Nothing here has been executed against real infrastructure: no production environment exists, hosting is undecided (D-3), and **no production deployment may be made without the owner's explicit authorization.** What is verified is stated in each section; the rest is the plan.

## 1. Deployable units

| Unit | Artifact | Verified here |
|---|---|---|
| API | `services/api/Dockerfile` (non-root user, health check on `/api/v1/health`), command `uvicorn artify_api.main:app` | Image builds in CI configuration; compose smoke test (`make compose-smoke`) runs it. Not run in this session. |
| Worker | same image, Celery command | Not run against a real broker here (the in-process background mode is what tests use). |
| Migrations | same image, `alembic upgrade head` as a one-shot job with the migrate role | Run on every test run against local PostgreSQL 16. |
| Web (Control Center, Super Admin) | Next.js app in `apps/web` | Built and tested in e2e; the Vercel demo is not a production design. |
| Desktop / Mobile | [WINDOWS_RELEASE_GUIDE](WINDOWS_RELEASE_GUIDE.md), [ANDROID_RELEASE_GUIDE](ANDROID_RELEASE_GUIDE.md), [IOS_RELEASE_GUIDE](IOS_RELEASE_GUIDE.md) | Not built. |

## 2. Required infrastructure

PostgreSQL 16 (managed, with PITR), Redis (rate limits; required in staging/production), an S3-compatible bucket with versioning, an SMTP provider, optionally ClamAV (`scanner_backend=clamav`), an OCR runtime (Tesseract in the image or a worker image), TLS termination in front of the API and web app. Database roles: `artify_app` (no `BYPASSRLS`), platform, dispatcher, migrate/definer — passwords generated per environment; `infrastructure/database/dev_roles.sql` is **development only**.

## 3. Settings that are enforced at start-up

With `ARTIFY_ENVIRONMENT=staging|production` the API refuses to start (`settings.py`) unless: JWT secrets and the MFA key are not the development values; `mailer_backend=smtp` with a host; `web_base_url` is `https://`; `storage_backend=s3` with a bucket; `rate_limit_backend=redis`; database URLs contain no development password. This is verified by reading the validator; a test run with `ARTIFY_ENVIRONMENT=production` and defaults has not been added to the suite.

Also set: `cors_origins` (explicit list), `processing_mode=celery`, `log_format=json`, `understanding_provider` (`rules` unless an AI provider contract and key exist), pool sizes after load testing. Secrets come from the platform secret store, never from the repository or image.

## 3a. Processes and settings added in Phase 12

* **Outbox dispatcher** — `artify-api dispatch-outbox --loop 2` (Celery) or `--inline` (no broker); without it no notification or e-mail is ever created.
* **Audit sealer** — `artify-api seal-audit --loop 30`; alert when `unsealed_recent` of `GET /orgs/{org}/audit-integrity` keeps growing; run the integrity check on a schedule and **copy the `head_hash` to a store outside the database**.
* **Upload staging** — `storage_tmp_dir` must be a persistent volume **shared by all API replicas** (resumable uploads); `upload_chunk_max_bytes` (default 16 MiB) is a technical request guard. Clean-up of abandoned staging files happens when a session is cancelled, fails, completes or is expired by the storage reconcile job.
* **Migrations 0013–0016** (conflicts, resumable uploads, notifications, audit chain) are additive; the sealing function is owned by the definer role and executable only by the dispatcher role — verify the grants after restoring a database onto a new server.

## 4. Order of deployment

1. Provision database, Redis, bucket, SMTP; create roles. 2. Run migrations as a one-shot job; confirm `alembic current` equals head. 3. Deploy API and worker; check `/api/v1/health` and `/api/v1/health/ready`. 4. Create the first platform administrator with the CLI (`artify-api create-platform-admin`) — never ship a default account. 5. Deploy the web app with its API base URL configured (see `apps/web` settings). 6. Smoke test: sign in, create an organization and company, upload a synthetic document, run OCR, create and approve a transaction, export, close a day. 7. Register monitoring and alerts (§7). 8. Take the first backup and perform the restore drill ([BACKUP_AND_DISASTER_RECOVERY](BACKUP_AND_DISASTER_RECOVERY.md)).

## 5. Migrations

Migrations are forward-only SQL files applied in one transaction each. Therefore: back up first; apply to a restored copy of production before production; use expand/contract (add nullable columns and new tables first, switch code, remove later) so the previous API version keeps working during a rolling deploy; there is no automated rollback — a bad migration is fixed by a new forward migration or a restore.

## 6. Release and rollback

Tag the commit; build one image per tag; deploy staging; run the full suite and the restore drill; promote the same image to production. Rollback of application code = redeploy the previous image, valid only if the migration was expand-only. Keep the previous two images.

## 7. Monitoring, logging and alerting (provider-independent)

What exists in the product: JSON logs with request ids (`log_format=json`); `GET /api/v1/health` (liveness) and `/health/ready` (readiness); request counters by status class (`metrics.py`) surfaced in the Super Admin "System health" page together with database latency and outbox backlog ([development/PLATFORM_MONITORING](development/PLATFORM_MONITORING.md)); platform alerts for failed jobs, past-due subscriptions and organizations near limits.

What does **not** exist: metrics export (Prometheus/OpenTelemetry), tracing, dashboards, alert delivery (paging/email), uptime probes, log shipping, a status page. Minimum to add before a pilot: ship JSON logs to a central store; scrape or poll health and the counters; alert on 5xx rate, readiness failures, outbox backlog age, worker queue depth, database connections/replication lag/disk, storage errors, certificate expiry and failed backups. Choose the tools after D-3.

## 8. Operational checklists

* **Secrets rotation**: JWT secrets (invalidates sessions), MFA key (needs re-encryption procedure — not built), SMTP, database passwords, S3 keys. A rotation drill has not been performed.
* **Capacity**: the measured read-path latencies in the [completion report](development/PHASE_12_COMPLETION_REPORT.md) §11 come from one process on a development machine; do not size production from them.
* **Support access**: platform staff reach customer data only through audited, time-limited support grants ([security/SUPPORT_ACCESS_POLICY](security/SUPPORT_ACCESS_POLICY.md)).
* **Data residency / retention**: undecided (D-3, D-8).
