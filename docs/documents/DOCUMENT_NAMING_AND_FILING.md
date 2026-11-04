# Naming and Filing Integration

`POST …/documents/suggest` runs the company's Phase 4 engines and returns an **advisory** answer; nothing is filed or renamed.

* **Filing**: `evaluate_rules(rule_defs, attributes)` with attributes `filename`, `source`, `document_kind`/`category_id` (the kind is read from the category mapping, never from names) and any attributes the caller adds. The winner is the lowest priority number; `ambiguous` is true when matches disagree. A target the caller cannot upload to is not suggested.
* **Naming**: the active naming rule for the category, else the company-wide rule, rendered by `render_name`; with no rule the deterministic fallback `<kind>_<date|undated>_<suffix>.<ext>` is used. The name is made unique within the target folder (`_2`, `_3`…) so nothing is overwritten, and `renamed_to_avoid_overwrite` says so.
* Accepting a suggestion is an ordinary upload; the client may pass `filing_rule_id`, which the server verifies exists in the company and stores on the document (`documents.filing_rule_id`) for traceability.
* At creation the server also resolves name clashes itself (`unique_display_name` under a per-folder advisory lock plus a partial unique index), so concurrent imports cannot overwrite each other.

AI never chooses a destination in Phase 6.

Tests: `test_filing_and_naming_suggestions_are_deterministic_and_advisory`.
