# Checklists and tasks

## Checklists
* Default templates (`bk_templates_data.py`: "Daily bookkeeping", "Cash and bank day-end") are **copied** into a company (`POST …/checklists/from-template/{key}`); `source_template_key` is provenance only. After the copy nothing links the company's list to the template or to any other company.
* A company can create lists, rename them, reorder items, add items, mark items mandatory, assign an item to a role (`assigned_role`, validated against the system roles), attach an automatic rule, and archive lists/items (nothing is deleted).
* Ticking: `POST …/checklist-items/{id}/complete?business_date=`. Needs `bookkeeping.work`; the item's role must match the person's role (or the person manages checklists); automatic items cannot be ticked by hand; future days and closed days are refused. Un-ticking: the person who ticked it, or a reviewer/manager.
* Automatic rules are keys evaluated from data, never titles: `documents_processed`, `transactions_decided`, `no_open_critical`, `sync_clean` (no report is not "clean").
* A mandatory item that is not done produces the `CHECKLIST_MANDATORY_OPEN` error in validation.

## Tasks
Statuses: new, assigned, in progress, awaiting information, awaiting review, approved, rejected, completed, cancelled (graph in `bk_core.TASK_TRANSITIONS` and `app.bk_guard_task`). A task may require review and may block closing; it has a business date, due date, priority, a free-text category chosen by the company (nothing depends on it), an optional named reviewer, optional subject (document, transaction, exception or closing of the same company), assignee, comments (append-only), assignment history (append-only) and audit history.
* Who may move it: the assignee or a task manager (`bookkeeping.tasks.manage`); only a manager cancels or reassigns; approving/rejecting needs `bookkeeping.close.review`.
* **No self-approval:** where the company requires independence (default), the person who asked for the review cannot approve or reject it (application check and a CHECK on the row). Switching it off is a company policy decision.
* An assignee must be an active member who can work in the company; a person from another organization is refused.
* Notifications use the existing transactional outbox (`bookkeeping.task.assigned`, `…review_requested`, `bookkeeping.closing.submitted|sent_back|closed|reopened`); payloads carry ids and dates, never amounts. Delivery channels are those that already exist; no new third-party service was added.
