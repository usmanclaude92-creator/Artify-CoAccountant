# Desktop workbench and Control Center

## Windows app (`apps/desktop`)
* Page **Daily Bookkeeping** (`src/screens/bookkeeping.tsx`): Today (overview, findings, document queue, transactions), Tasks, Checklists, Closing (six-stage wizard with exceptions, validate, summary, submit, approve and close, send back, reopen, cancel, history), Daily report (approved totals, cash and bank), Period readiness. The business day is chosen by date; the company's time zone is shown.
* `core/src/bk.rs` + `src-tauri/src/bk_commands.rs`: one allow-listed passthrough `bk_call(op, ids, query, body)`. The interface can only name an operation from a fixed table; UUIDs, `YYYY-MM-DD` dates, template keys and query keys are validated; writes take no filters (except the day when ticking an item); the body travels unchanged; the server's refusal (with its code) is shown as is. The desktop validates, approves, closes and overrides nothing.
* Uses the existing design system (`@artify/ui`, existing CSS classes); status is always text plus colour.
* Synchronisation: the app can only *declare* its local sync state (`sync_report`); it is displayed as "declared by the device".

## Firm Control Center (`apps/web`)
* **Daily Closing** page (`/bookkeeping`, `bookkeeping.monitor`): per-company closing status for a chosen day, open errors, open and overdue tasks, recent unclosed days. Status and counts only; no transaction, document or amount; no button that closes, reopens or overrides.
