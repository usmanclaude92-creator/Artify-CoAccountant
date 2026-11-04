# Automatic Filing Rules (configuration foundation)

This delivers rule storage, validation, conflict analysis and a dry-run evaluator. Since Phase 6 the evaluator is called by `POST /documents/suggest` to *propose* a folder and category; the user decides and nothing is filed automatically ([`../documents/DOCUMENT_NAMING_AND_FILING.md`](../documents/DOCUMENT_NAMING_AND_FILING.md)).

## Rule

`name`, `description`, `priority` (lower wins), `conditions` (1–20, all must match), target section/folder (required), optional target category, active flag, archive/restore, created/updated by.

## Conditions

`field`: `document_kind`, `category_id`, `source` (`mobile|desktop|web|email`), `filename`, or `custom:<field key>` (must be an active custom field of the company). `operator`: `equals`, `not_equals`, `in`, `contains`, `starts_with`, `ends_with`, `is_present`, `is_empty`. Comparison is case-insensitive. Document kinds and sources are validated against their catalogs; `category_id` values must be categories of the company. There is no catch-all-by-accident: at least one condition is required.

## Resolution (deterministic)

`evaluate_rules` sorts by `(priority, created_at, id)`; the first rule whose conditions all match wins; the full ordered list of matches is returned and `ambiguous` is set when matching rules would file to different places. Rules whose target is archived/inactive are excluded from evaluation and are switched off automatically when their target is archived.

## Validation and analysis

- Targets and categories must belong to the same company (composite foreign keys + API checks) and must be active and not archived (409 otherwise).
- `GET …/filing-rules/analysis` reports `duplicate_priority` and `shadowed` (a rule that can never win because an earlier rule with a subset of its conditions files elsewhere).
- `POST …/filing-rules/reorder` assigns priorities 10, 20, 30… in the given order (must list every live rule once).
- `POST …/filing-rules/validate` checks a draft without saving; `POST …/filing-rules/evaluate` runs the dry-run.

## Limit

`limit.filing_rules_per_company` (backend enforced). AI-driven conditions (extraction confidence, vendor match) are deliberately not part of Phase 4.
