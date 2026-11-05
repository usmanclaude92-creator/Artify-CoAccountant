# Notification Delivery

How a domain event becomes something a person sees. **In-app notifications and e-mail are implemented and tested against a stub mailer; push notifications to phones are not.** Code: `notifications.py`, `event_tasks.py`, `routers/notifications.py`, migration 0015. Tests: `test_p12_notifications.py` (7), `sync_test.dart`.

## Flow

1. A feature writes a domain event to the transactional outbox in the same transaction as the change (assignment, review request, closing sent back or closed).
2. A dispatcher moves pending events to the worker: `artify-api dispatch-outbox` (to Celery) or `artify-api dispatch-outbox --inline` (handled in the process, no broker needed); `--loop N` repeats. Docker Compose runs the Celery variant.
3. `event_tasks.handle_event` runs in the event's own tenant context, resolves the recipients, and creates one `notifications` row per person (unique per event and person — redelivery never duplicates), then sends one e-mail if the person allows it and has a verified address. The outcome is stored: `sent`, `failed` or `skipped`. A failed e-mail does not fail the event and is not retried silently.
4. The apps list `GET /notifications` (header organization), mark read, and set the e-mail preference (`/notifications/preferences`).

## Rules

* Text is generic and fixed per event type plus an entity reference. **No names, amounts or document content** in a notification or an e-mail (tested: a task title typed by a user never appears).
* The person who caused an event is not notified of it.
* Row-level security: a person sees only their own rows in the active organization; the worker, which has no user, creates rows and sets delivery state; a user context cannot change delivery state; rows cannot be rewritten or deleted.
* Event types with a recipient today: `bookkeeping.task.assigned`, `bookkeeping.task.review_requested`, `bookkeeping.closing.sent_back`, `bookkeeping.closing.closed`. Other outbox events are acknowledged as before.

## Not built

Push to phones (needs a provider account, APNs/FCM keys and device-token handling); digest e-mails; per-event-type preferences; a Control Center notification centre; e-mail bounce handling; sending e-mail through a real provider was never executed here (SMTP code exists from Phase 1 and was only exercised with the memory mailer); retry of failed e-mails.
