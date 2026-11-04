# Document Search and Filters

`GET /companies/{company_id}/documents` — same authorization as everything else: only documents in folders the caller may **view** are searched or counted (hidden folders contribute nothing, not even to totals).

| Parameter | Meaning |
|---|---|
| `q` | case-insensitive substring of the display name, the original file name, **or any text custom-field value** (LIKE with escaped wildcards; a trigram index backs the name) |
| `node_id`, `include_subfolders` | one folder; `include_subfolders` (default `true`) adds all folders below it (uses the node's stored path ids) |
| `category_id` | one category |
| `status` | `active` (default), `archived`, `deleted` (removed documents, same folder visibility) |
| `family` | `pdf`, `image`, `excel`, `word` or `text` (each maps to a fixed list of extensions) |
| `ext` | exact extension |
| `uploaded_by`, `created_from`, `created_to` | who and when |
| `field=key:value` (repeatable, up to 5) | custom-field filter: text equality (case-insensitive), numeric equality (so `3` matches `3.000000`), or date equality |
| `sort` / `order` | `name`, `created` (default), `updated`, `size`, `type`; ties broken by id for stable paging |
| `limit` / `offset` | paging; the response has `total` |

`GET /documents/counts` returns per-folder counts for active and archived documents (the Desktop tree shows them) and totals — again only for visible folders.

## Search never reveals

A folder the user cannot view is filtered **in the query** (`d.node_id = ANY(:visible)`), not after the fact, so counts, totals and paging cannot leak existence. A direct `node_id` that is hidden answers 404. Search text is never logged.

## Performance

Verified on a small synthetic data set only; no benchmark at scale was run in this phase. Indexes: `(company_id, node_id, status)`, `(company_id, created_at DESC, id)`, trigram on `lower(display_name)`, `(company_id, field_id, value_text)` for field filters. Full-text content search (OCR text) is a later phase.

## Desktop

All Documents: folder tree with counts, search box, type filter, sort. Archived Documents: the same screen with `status=archived`. Large result sets show "first N of M" and ask the user to narrow the search.
