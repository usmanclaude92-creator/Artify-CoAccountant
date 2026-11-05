# Permissions and company policy

| Permission | firm_owner | practice_manager | accountant | assistant_accountant | read_only | staff / document_operator |
| --- | --- | --- | --- | --- | --- | --- |
| `bookkeeping.read` | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| `bookkeeping.work` | ✔ | ✔ | ✔ | ✔ | – | – |
| `bookkeeping.tasks.manage` | ✔ | ✔ | ✔ | – | – | – |
| `bookkeeping.checklists.manage` | ✔ | ✔ | ✔ | – | – | – |
| `bookkeeping.close.prepare` (start, validate, submit, cancel) | ✔ | ✔ | ✔ | ✔ | – | – |
| `bookkeeping.close.review` (acknowledge, review tasks, send back) | ✔ | ✔ | ✔ | – | – | – |
| `bookkeeping.close.approve` (approve and close) | ✔ | ✔ | ✔ | – | – | – |
| `bookkeeping.close.reopen` | ✔ | ✔ | – | – | – | – |
| `bookkeeping.close.override` | ✔ | ✔ | – | – | – | – |
| `bookkeeping.policy.manage` | ✔ | ✔ | – | – | – | – |
| `bookkeeping.monitor` (organization overview) | ✔ | ✔ | – | – | – | – |

Every role holding `bookkeeping.read` also holds `accounting.read` (test-enforced). Roles are the system roles; custom per-company roles do not exist yet, so a company cannot GRANT a role more than the table above. It can **restrict** (below).

## Per-company policy (`bk_policies`, `PUT …/policy`)
* `require_independent_approval` (default on): the closer cannot be the submitter. The value in force at submit time is stored on the closing.
* `require_independent_task_review` (default on).
* `action_roles`: for any of `prepare`, `review`, `approve`, `reopen`, `override`, a list of role keys; if present, the person's role must be in the list in addition to holding the permission (e.g. only practice managers reopen). "Approve" and "close" are one action (approve and close) and are not separately configurable.
* `severity_overrides`: validation code → severity (integrity checks refuse).
* `override_min_chars` (5–500, default 10): minimum explanation length.
* `timezone`: IANA name; empty = the organization's zone.
Policy changes are audited and use optimistic concurrency (`base_row_version`). Company A's policy never affects company B.

## Overrides and reopening
Override needs `bookkeeping.close.override`, the company's `override` role restriction and an explanation; it is recorded on the exception, in the closing trail and the audit log. Reopening needs `bookkeeping.close.reopen`, the `reopen` role restriction, a reason, and an open accounting period.
