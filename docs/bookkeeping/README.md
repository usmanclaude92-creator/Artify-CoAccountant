# Daily Bookkeeping and Closing (Phase 10)

Status: **delivered for review.** Phase 11 has not been started by this phase. See [the completion report](../development/PHASE_10_COMPLETION_REPORT.md) for what is and is not verified.

The accountant's daily rhythm on top of the Phase 4–9 engines: a daily workbench, tasks, company-specific checklists, a deterministic daily validation, a six-stage closing with independent approval, immutable closing snapshots, daily Excel reports through the Phase 9 engine, cash and bank daily summaries, a period-readiness overview and a Firm Control Center overview.

| Document | Content |
| --- | --- |
| [VALIDATION_ENGINE.md](VALIDATION_ENGINE.md) | every check, severity, result ladder, exceptions, acknowledge/override |
| [CLOSING_WORKFLOW.md](CLOSING_WORKFLOW.md) | stages, statuses, independence, snapshots, reopening, closed-day protection |
| [CHECKLISTS_AND_TASKS.md](CHECKLISTS_AND_TASKS.md) | company checklists (copied templates), automatic rules, task statuses and review |
| [PERMISSIONS_AND_POLICY.md](PERMISSIONS_AND_POLICY.md) | permissions per role, per-company policy, time zones |
| [DATABASE.md](DATABASE.md) | tables, constraints, triggers, RLS, grants |
| [API.md](API.md) | routes and error codes |
| [DESKTOP_AND_CONTROL_CENTER.md](DESKTOP_AND_CONTROL_CENTER.md) | Windows workbench, allow-listed core, Control Center page |

Decisions: ADR-076 (business day, workflow, policy), ADR-077 (snapshots and reopening), ADR-078 (visibility, declared sync state, reuse of Excel).

## What it deliberately is not
Not a bank reconciliation, not a VAT return, not an automatic period lock, not a sync engine, not a third-party posting. No country tax rule is built in: tax checks only compare what the company configured with what was recorded (the Phase 8 Oman tax pack stays unverified, and an unverified tax code used today is reported as a warning).
