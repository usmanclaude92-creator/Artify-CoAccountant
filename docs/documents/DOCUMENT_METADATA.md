# Document Metadata

Two layers, as everywhere in Artify: **customer words** (the company's own categories and fields from Phase 4) and **Artify words** (document kinds and fixed system attributes).

## System attributes (fixed)

`display_name`, `original_filename`, `extension`, `mime_type` (from the content), `size_bytes`, `sha256`, `scan_status`, `source` (`desktop`/`web`/`api`/`mobile`/`inbox`), `created_by` / `created_at`, `updated_by` / `updated_at`, `status`, `legal_hold`, `row_version`, version number, `kind_at_filing` (the Artify document kind of the category when the document was filed — a snapshot used for later processing), `filed_path_snapshot` (the human-readable path at filing time, for explanation only), `filing_rule_id`.

## Configurable fields (Phase 4 definitions)

Custom fields are defined per company with a stable `key` and a type: `short_text`, `long_text`, `number`, `decimal`, `currency`, `date`, `dropdown`, `multi_select`, `boolean`, `reference` (to a workspace node or a category). A field applies everywhere, or only to listed categories/folders (a folder covers its subfolders). Values for a document are stored in `document_field_values`:

* normalised **by the Phase 4 validator** (the same one that validates configuration; no regex, no code);
* money/decimals as **exact strings in JSON and `NUMERIC(28,6)` in the database** — never floats; currency values keep amount and 3-letter currency;
* referenced by field **id** (and key), never by label;
* required fields are enforced when the document is created (`require_complete`), optional ones are validated only if given.

`PATCH /documents/{id}` with `field_values` changes values (audited as `metadata_changed` with the changed keys). An archived field keeps its stored values but is not asked for any more.

**Never invent missing data.** A value the user did not give is simply absent (no default is silently stored unless the field definition has an explicit default). The Desktop shows required fields as required and refuses to queue until they are filled; it never fills them.

## Source and provenance

`source` says where the document came from. Phase 6 only creates documents from user uploads; there is no AI-derived metadata. When later phases add extracted values, they will be stored as *suggestions* separate from these user-confirmed fields (invariant 6).

## Desktop

Import: the detail fields for the chosen folder/category are rendered by type (text, number, decimal, currency amount + code, date, dropdown, multi-select, yes/no, reference pickers). Document view → *Details* shows system attributes and stored field values.

## Not in this phase

Editing field values from the Desktop details tab (the API supports it; the UI shows values read-only), bulk metadata edit, metadata-based retention rules, OCR text.
