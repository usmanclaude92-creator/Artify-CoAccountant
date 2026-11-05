# Approval Workflow (Phase 8)

States: `draft → awaiting_review → awaiting_approval → approved`, plus `rejected`, `reversed` (and `posted`, reserved for a later phase; no endpoint sets it).

| Action | Who (permission) | Rules |
|---|---|---|
| prepare / edit | `accounting.prepare` | drafts only; the last editor becomes the "preparer" |
| submit | `accounting.prepare` | refused while any **blocking** issue exists; goes to review (two-level policy) or approval |
| review-approve | `accounting.review` | two-level only; not the preparer (when separation is on) |
| approve | `accounting.approve` | a **person** (`actor_type = user`); not the preparer when separation is on; not the reviewer when two-level distinct; above the approval limit also `accounting.approve_high`; recalculated at approval time; no blocking issue; soft differences need `acknowledge` + a written note |
| reject | reviewer or approver | reason required |
| request correction | reviewer or approver | returns to draft with the note |
| revise | `accounting.prepare` | reopens a rejected transaction as a draft |
| reverse / amend | `accounting.reverse` | approved only; reason; open period |

## Company policy (`PUT /accounting/policy`)
`separate_preparer_approver` (default on), `levels` (1 or 2), `distinct_levels`, `approval_limit` (base currency). Two levels and limits need `feature.advanced_approval`. The database trigger enforces preparer ≠ approver using the same policy, so a manipulated request cannot bypass it.

## Guarantees
* Every transition is appended to `acct_approvals` (action, from/to status, actor, note, time); the trail is never rewritten.
* `approve` rows are rejected by the database unless the actor is a user with an id; approved transactions without `approved_by` cannot exist.
* Permissions are checked on the server on every route; hidden objects answer 404.
* The Windows app only shows buttons the server reports as allowed (`can`), and the server re-checks every call.
