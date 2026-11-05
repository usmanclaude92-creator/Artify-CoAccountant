# Phase 10 — Daily Bookkeeping and Closing

**Status: DELIVERED FOR REVIEW** (see [PHASE_10_COMPLETION_REPORT.md](PHASE_10_COMPLETION_REPORT.md) and `docs/bookkeeping/`). The owner's Phase 10 brief superseded the scope below where they differ; items of this plan that the brief did not require (period lock/reopen UI, bulk operations, digest emails, dashboard enrichment, `daily_summaries`) were **not** built.

## Objectives

Provide the accountant's daily operating rhythm on top of the engines built so far: a daily workflow, exception handling, daily summaries and closing, and period close controls — configured per Company.

## Functional scope

- Daily workspace (Desktop): today's intake by stage, items needing attention (blocking findings, duplicates, unfiled documents, failed processing), assignments, SLA indicators.
- Daily closing: checklist per Company (configurable steps: all documents reviewed, entries approved, bank statements reconciled to configured tolerance, exports completed), sign-off by an authorized human, `daily_summaries` generation and history.
- Period management UI: open/close/lock `financial_periods` with controls and reopen approval.
- Bulk operations with safeguards (bulk file, bulk assign, bulk export of approved entries) and audit.
- Notifications and digest emails (configurable).
- Overview Dashboard enrichment with real processing and closing data (Control Center module 1).
- Daily transaction reports via the Phase 9 report engine.

## Technical implementation

Closing-checklist definitions (Company-scoped configuration), summary generation jobs (deterministic, regenerable), closing state machine with human sign-off (ADR-009 applies), dashboards using precomputed aggregates, scheduling via Celery beat with per-Company time zones.

## Database changes

Extends `daily_summaries`; creates closing checklist definition/instance tables; period-change history; notification preference additions.

## API requirements

`/companies/{id}/daily/summary`, `…/daily/close` (+ checklist), `…/periods/{id}/close|lock|reopen`, `…/bulk/*`, `/orgs/{id}/overview`.

## Dependencies

Phases 8 and 9; Phase 3 notification framework.

## Security requirements

Closing and reopening are privileged, separated duties where configured, and audited; bulk actions respect section permissions and per-item entitlement/state checks; time-zone handling explicit (no implicit server-local dates).

## Testing requirements

Executable: TS-ACC-11 (period control, UI-level), closing state-machine tests, bulk-operation authorization matrix, time-zone boundary tests, summary regeneration determinism, E2E daily cycle on synthetic Company data.

## Acceptance criteria

1. A full synthetic day (intake → review → approval → export → close) runs end-to-end per Company with its own configuration.
2. Closing cannot complete with unmet mandatory checklist items unless an authorized override with reason is recorded.
3. Locked periods reject new entries and approvals.
4. Dashboards show only real computed data.

## Completion checklist

- [ ] Checklist configuration documented
- [ ] `PHASE_10_COMPLETION_REPORT.md`

## Known risks

Differing bookkeeping routines per firm (keep checklist configurable, not opinionated); time zones; bulk-action mistakes; scope creep into full period-end accounting.
