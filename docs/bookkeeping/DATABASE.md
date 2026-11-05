# Database (migration 0012)

All tables are company-owned (`organization_id`, `company_id`, composite FK to `companies`), have RLS enabled **and forced** with the same company-scope policy as Phases 8–9, and are granted to `artify_app` only (the app role has no `BYPASSRLS`).

| Table | Purpose | App-role rights |
| --- | --- | --- |
| `bk_policies` | per-company policy (one row, lazily created) | select, insert, update |
| `bk_checklists`, `bk_checklist_items` | company checklists (archive, never delete) | select, insert, update |
| `bk_checklist_completions` | manual ticks per item and day (unique) | select, insert, update, delete (blocked on a closed day by trigger) |
| `bk_tasks` | tasks | select, insert, update (status graph by trigger; no delete) |
| `bk_task_assignments`, `bk_task_comments` | append-only | select, insert |
| `bk_closing_sessions` | closings (state machine trigger, CHECKs for submit/close/reopen/independence) | select, insert, update |
| `bk_validation_runs` | every validation run (immutable) | select, insert |
| `bk_exceptions` | one per fingerprint and day; overrides need person, reason, time | select, insert, update (blocked on a closed day) |
| `bk_closing_approvals` | trail; approve/close/reopen/override/submit/send-back must be `actor_type = user` | select, insert (immutable) |
| `bk_closing_snapshots` | immutable payload with database-computed `sha256` | select, insert (immutable) |
| `bk_closing_exports` | closing ↔ `xl_exports` link | select, insert |
| `bk_sync_reports` | device-declared sync state per day | select, insert, update |
| `bk_audit_events` | domain history (immutable) | select, insert |

Triggers: `app.bk_guard_session`, `app.bk_guard_task`, `app.bk_guard_closed_day` (exceptions, completions), `app.bk_guard_transaction_day` (on `acct_transactions`), `app.bk_snapshot_hash`, `app.forbid_mutation` on snapshots, approvals, runs and audit events. Helper `app.bk_day_closed(company, date)`.
Money never appears in these tables as a column; snapshots store amounts as JSON strings built from `NUMERIC` values.
Migration 0012 is forward-only; restoring means a fresh database or a backup.
