# Document Search, Filtering and Pagination

`GET /companies/{c}/documents` — all filters are optional and combine with AND.

| Parameter | Meaning |
|---|---|
| `node_id`, `include_subfolders` | One folder, optionally with its subtree (only nodes the caller may view) |
| `category_id`, `extension`, `source`, `created_by`, `import_batch_id` | Exact match |
| `status` | `active` (default), `archived`, `deleted` (deleted only where the caller may delete) |
| `q` | Case-insensitive substring of display name or original file name; `%`/`_` are literal |
| `min_size`, `max_size`, `created_from`, `created_to`, `sha256` | Ranges and checksum |
| `field_key` + `field_value` | Exact match on one custom field |
| `sort` (`name`, `created`, `updated`, `size`, `type`) + `order` | Whitelisted; ties broken by id |
| `limit` (1–200, default 50), `offset` | Pagination; `total` is returned |

Visibility is applied in SQL as `node_id = ANY(visible nodes)`, so hidden documents are neither listed nor counted. `GET …/documents/counts` returns per-node counts (own and including subfolders) for tree badges. Archived/deleted counts are only shown where permitted.

Indexes: trigram GIN on lower(name) and lower(original name), `(company_id, node_id, status, created_at)`, `(company_id, content_sha256)`, metadata value index.

**Not included:** searching inside document text (that needs OCR, a later phase) and fuzzy matching.
