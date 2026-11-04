# Platform Monitoring (Super Admin: Overview, Usage, System Health)

What the Super Admin reports, where each number comes from, and — as important — what it does **not** know. Code: `routers/platform_ops.py`, `platform_usage.py`, `metrics.py`. Tests: `test_p2_ops.py`.

## 1. Rule

Every figure is the result of a query on real data. A quantity the product cannot measure yet is returned as `available: false` with a reason and rendered as **"Not available"**; it is never zero, never an estimate and never a placeholder.

## 2. Overview metrics

| Metric | Source | Status |
|---|---|---|
| Organizations (total, by type, by status) | `organizations` | real; test compares with the database |
| Companies (total, by status) | view `platform_company_overview` | real |
| Registered / active users | view `platform_user_totals` | real |
| Subscriptions by status (all six statuses) | `subscriptions` | real |
| Organization growth, last 12 months | `organizations.created_at` | real |
| Subscription changes by month (new, status, plan) | `subscription_events` | real |
| Recent registrations; recent subscription changes; recent administrative activity | `organizations`, `subscription_events`, `platform_audit_events` | real; sections appear only for roles holding the matching read permission |
| Collected revenue, last 30 days | `billing_payments` (net of refunds), per currency | real **only when an administrator has recorded payments**; otherwise "No payment has been recorded. No billing provider is integrated." |
| Background jobs (pending, failed, dispatched, oldest pending age) | view `platform_outbox_overview` | real (transactional outbox) |
| Documents processed / awaiting processing | — | **NOT AVAILABLE** (documents arrive in Phase 5–7) |
| Storage consumption | — | **NOT AVAILABLE** (object storage arrives in Phase 6) |
| AI processing | — | **NOT AVAILABLE** |
| Usage trend | `usage_records` | "not available" until metered records exist |

**Alerts** (the Alerts button and the banner list) are computed from real conditions: failed outbox events (critical), past-due subscriptions, pending support requests, organizations over or approaching a plan limit (scans at most 500 organizations; a larger fleet needs a materialized rollup — noted for Phase 12).

## 3. Usage & Limits

See `SUBSCRIPTION_AND_ENTITLEMENT_IMPLEMENTATION.md` §6. Accuracy rests on the usage counters; a test creates companies in two tenants and checks the figures, the remaining allowance, the percentage, the near-limit flag, tenant isolation and equality with the customer's own entitlement endpoint.

## 4. System Health

`GET /platform/health` runs these checks **from the API process that serves the request**:

| Check | How | Honest limits |
|---|---|---|
| API | the request itself | says nothing about other replicas |
| Database | `SELECT 1` as the platform role, with latency | one connection, one moment |
| Redis | a real `PING` with a 1-second timeout (rate limiter and task broker) | reports `down` when unreachable — including on a laptop with no Redis |
| Outbox queue | counts and oldest pending age; `degraded` if any failed or the oldest pending event is older than 5 minutes ("is the dispatcher running?") | proves backlog, not that workers are alive |
| Workers | **unknown** — worker heartbeats are not instrumented | |
| Object storage | **not configured** (Phase 6) | |
| Backups | **not configured** — no backup system is reported on | |
| Error counts | in-process counters of 2xx/3xx/4xx/5xx since this process started | per process; resets on restart; not a metric store |
| Version / uptime | package metadata; process start time | |

The page always shows the **environment** (`local`, `ci`, `staging`, `production`) with a sentence describing it, and states that **there is no external monitoring, alerting or on-call integration**. Nothing here should be read as production observability.

## 5. Not built (deliberately, per the brief)

No third-party monitoring, tracing or log aggregation; no uptime history; no queue depth by task type; no worker/Celery inspection; no storage or backup integration; no per-tenant error rates. Phase 12 owns production observability.
