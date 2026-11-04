# Document Metadata

Definitions come from the company's Phase 4 configuration (`custom_field_definitions`, applicability by category and folder, options, validation, required flag). This phase stores **values** in `document_metadata_values` and enforces the definitions.

* On registration the values are validated with the same engine as `POST …/workspace/fields/validate-values`: required fields, types, option membership, decimal places, references. Problems return 422 with a per-field list.
* Values are applied when the document is created. `PATCH` accepts a metadata patch: unknown or non-applicable keys are rejected, `null` clears a value, and clearing a required value is refused.
* Money-like values stay **strings** (`"10.50"`), never floats.
* Values of fields that stop applying after a move or category change are **kept**, flagged `applies: false` in the details, and are not validated until they apply again.
* Search by one custom field: `GET …/documents?field_key=cost_center&field_value=ops` (exact match).
* Built-in attributes (name, original file name, type, size, dates, checksum, source, uploader, version, status) are columns on `documents`; nothing here knows any document category.

Tests: `test_categories_file_types_and_custom_metadata`.
