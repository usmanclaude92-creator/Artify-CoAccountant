# API (prefix `/api/v1/companies/{company_id}/bookkeeping`)

Every route declares a permission (company scope: another company or organization answers 404). Stable error codes appear in `code` (`day_closed`, `closing_exists`, `validation_required`, `validation_stale`, `critical_failure`, `errors_open`, `independence_required`, `role_not_allowed`, `reason_required`, `explanation_required`, `hard_check`, `period_closed`, `invalid_status`, `future_date`, `stale_version`, …).

| Area | Routes |
| --- | --- |
| Policy | `GET/PUT /policy` |
| Checklists | `GET /checklist-templates`, `GET/POST /checklists`, `POST /checklists/from-template/{key}`, `PATCH /checklists/{id}`, `POST /checklists/{id}/items`, `PUT /checklists/{id}/order`, `PATCH /checklist-items/{id}`, `POST/DELETE /checklist-items/{id}/complete?business_date=` |
| Tasks | `GET/POST /tasks`, `GET/PATCH /tasks/{id}`, `POST /tasks/{id}/assign`, `…/transition`, `…/comments` |
| Day | `GET /days/{date}` (overview), `…/validation` (dry run, stores nothing), `…/documents`, `…/transactions`, `…/exceptions`, `…/report`, `…/cash-bank`, `…/snapshots` |
| Sync | `POST /sync-reports` (a device **declares** its state) |
| Exceptions | `POST /exceptions/{id}/acknowledge`, `…/override` |
| Closings | `GET/POST /closings`, `GET /closings/{id}`, `POST …/validate`, `…/stage`, `GET …/summary`, `POST …/submit`, `…/send-back`, `…/close`, `…/reopen`, `…/cancel`, `GET …/snapshot`, `GET/POST …/exports`, `POST …/exports/{export}/regenerate` |
| Readiness | `GET /readiness?period_id=` or `date_from`/`date_to` |
| Organization | `GET /api/v1/bookkeeping/overview?business_date=` (`bookkeeping.monitor`, header-scoped): status and counts only |

The OpenAPI contract and TypeScript client are regenerated with `make client`.
