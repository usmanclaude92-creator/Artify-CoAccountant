# Custom Metadata Fields

Definitions live in `custom_field_definitions` (+ options + applicability). **No columns are added per field.** Values are not stored yet: `document_field_values` arrives with `documents` in Phase 6, which must attach the validators documented here.

## Types

`short_text` (≤255), `long_text` (≤10,000), `number` (whole), `decimal` (string), `currency` (`{"amount": "12.50", "currency": "OMR"}`), `date` (`YYYY-MM-DD`), `dropdown`, `multi_select`, `boolean`, `reference` (to a `workspace_node` or `document_category` **of the same company** — vendor/customer registers do not exist yet).

Money and decimals are strings, never JSON floats (a float value is rejected).

## Validation (declarative only)

| Type | Allowed `validation` keys |
|---|---|
| short_text | `min_length`, `max_length`, `mask` (A = letter, 9 = digit, * = any, other characters literal) |
| long_text | `min_length`, `max_length` |
| number | `min`, `max` |
| decimal | `min`, `max`, `decimal_places` (0–6) |
| currency | `min`, `max`, `decimal_places`, `allowed_currencies` |
| date | `min_date`, `max_date` |
| multi_select | `min_items`, `max_items` |

Unknown keys are rejected. **There is no regular-expression support and no executable content**: masks are matched in linear time, so there is no ReDoS surface. A default value must itself be valid.

## Rules

- `key` is generated from the label (or supplied), unique per company, **immutable**; `data_type` is immutable. A company can reuse the same key independently of other companies.
- Labels are unique among non-archived fields; options are unique per field.
- Removing an option **archives** it; archiving a field keeps the definition and (later) stored values. Nothing is discarded silently.
- Applicability: no rows → applies to every document; otherwise to the listed categories and/or folders (a folder scope covers its subfolders). `GET …/fields?category_id=&node_id=` returns the applicable active fields.
- `POST …/fields/validate-values` validates a value set for a category/folder: unknown keys, missing required fields and invalid values are reported as problems; nothing is stored.
- Changing `required` or validation affects new submissions; existing data is never rewritten (no data exists yet).

## Limits

`limit.custom_fields_per_company` (non-archived fields) is enforced on the backend.
