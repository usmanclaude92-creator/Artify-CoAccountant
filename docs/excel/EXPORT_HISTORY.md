# Export History and Traceability

Every export is a permanent record: template, version, mapping number, parameters, unapproved flag, validation issues at creation, status/progress, filename, SHA-256, size, per-sheet row counts, fidelity notes, who/when, attempts and (when failed) a reason.

* **Immutable:** database triggers forbid changing a `completed`/`failed` export and any delete; the application role has no `DELETE` on exports or export rows and no `UPDATE` on template versions or export rows. `DELETE` on templates, versions and mappings exists only so a **never-used** template can be removed; the service refuses it once a mapping was activated or an export exists (a database-level guard for that rule is not implemented). Tested at the grant level (`has_table_privilege`) and at the trigger level.
* **Traceability:** `xl_export_rows` links each written row to its transaction and journal, sheet and row number. `GET …/exports/{id}/transactions` lists them; `GET …/transactions/{id}/exports` answers "in which exports did this transaction appear?" (the repeat warning uses the same links).
* **Compare:** `GET …/exports/compare?a=&b=` lists differences in version, mapping, parameters, row counts, checksum and the number of transactions in both.
* **Re-export** needs a reason and creates a new record; it never overwrites the first.
* **Retention:** exports and their files are kept; they count towards storage. No automatic expiry exists yet (open decision D-8).
