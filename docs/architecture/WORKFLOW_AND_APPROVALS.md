# Workflows and Approvals

Companies configure their own processing workflow, and approvals always require an authorized human.

## 1. Model

- `workflow_definitions` (Company-scoped, versioned): name, applicable categories/sections, status.
- `workflow_stages`: `name` (customer-chosen), `stage_type` (Artify-defined semantic type), `sort_key`, optional SLA.
- `workflow_transitions`: `from_stage → to_stage`, `required_permission`, `allowed_actor_types`, `approval_level`, optional guard conditions (e.g. "all required fields present", "validation has no blocking findings").
- `approval_rules`: for `approval` stages — one or more ordered levels, each satisfied by a role or named user; optional amount thresholds (Decimal), optional separation of duties (submitter ≠ approver).
- `workflow_instances`: current stage of each document/record plus the definition **version** it runs under (in-flight items finish on the version they started; new items use the latest).
- `workflow_history`: append-only: from/to stage, `actor_type`, `actor_id`, reason/comment, timestamp.

### Stage types

`intake`, `queued`, `processing`, `review`, `correction`, `approval`, `approved`, `exported`, `archived`.

Default stage names for a new definition: Uploaded (`intake`) → Awaiting Processing (`queued`) → Processing (`processing`) → Awaiting Review (`review`) → Needs Correction (`correction`) → Approved (`approved`) → Exported (`exported`) → Archived (`archived`). These are a default template. Companies add stages (e.g. "Manager Approval" of type `approval`), rename, or reorder subject to validation: every definition must contain at least one `intake`, one `review`, one `approved`; the path to `approved` must pass through all configured approval levels.

## 2. Hard rules

1. **No silent approvals.** A transition into a stage of type `approved` (and any `approval` level) requires `actor_type = 'user'`, an authenticated user holding the transition's permission and section permission, and satisfaction of the separation-of-duties rule when configured. Enforced in the service layer and by a database trigger on `workflow_history` and on `accounting_entries.state` (ADR-009).
2. **AI and automation may**: ingest, enqueue, process, move a document to `review`, attach suggestions, and (if the Company enables) move through non-approval stages. They may **not** approve, post, export on behalf of an approver, or edit reviewed values.
3. **Every transition is recorded** with actor, time, from/to, and reason; reversals (`correction`) require a reason.
4. **Approved items are immutable.** Corrections create a new version/reversal and re-enter the workflow.
5. **Assignment.** Rules assign reviewers/approvers by role, Company assignment (primary/secondary/reviewer), section, or round-robin; unassigned items surface in the Processing Monitor.
6. **Notifications.** Transitions fire domain events consumed by the notification service (in-app, email; push for Mobile) per recipient preferences.

## 3. Relationship to accounting entries

Document workflow state and accounting-entry state are separate but linked: an entry cannot reach `approved` unless its source document's workflow is at (or beyond) its configured approval, and an `exported` entry moves its documents to `exported` stage types. See `ACCOUNTING_DATA_MAPPING.md`.

## 4. Versioning and change safety

Changing a definition creates a new version; the Control Center shows impact (items in each stage). Removing a stage that holds items requires a mapping of those items to another stage within the same change; stages are archived, not deleted, once used.
