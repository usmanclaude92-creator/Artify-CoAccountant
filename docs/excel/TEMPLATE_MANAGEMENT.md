# Template Management

* **Per company.** A template belongs to one company; nothing is shared or copied across companies. "Duplicate" makes a new template in the *same* company with its own file copy and a draft copy of the mapping.
* **Categories** are free text (suggestions: purchase register, sales register, VAT, bank, trial balance, P&L, balance sheet, management report, custom report). Behaviour never depends on the category.
* **Status:** `draft` (no active mapping) → `active` (activating a valid mapping) ⇄ `inactive`; `archived` (kept, hidden; export history stays).
* **Versions are immutable.** Uploading a new file for a template creates version *n+1* with its SHA-256, size and change note; earlier versions stay downloadable. The previous mapping is copied as a *draft* against the new version so it can be re-checked; the active mapping keeps pointing at its own version. Old exports record the version/mapping they used.
* **Delete vs archive.** A template that was never used (no activated mapping, no export) can be deleted; otherwise it can only be archived.
* **Limits (EntitlementService):** `limit.excel_templates_per_company`, `limit.excel_template_versions_per_template`, `quota.storage_bytes` (templates and exports are billable storage). Reaching a limit blocks *new* items; nothing is deleted. Archiving frees a template slot.
* **Permissions:** `excel.read` (view), `excel.templates.manage` (upload, mapping, status), `excel.export`, `excel.export_unapproved`, `excel.reports.configure` (company), `excel.monitor` (organization overview). Assistants/read-only roles can look but not export or download.
* **Audit:** upload, version, mapping saved/activated, status changes, duplicate, delete, downloads and exports are audited with ids and checksums, never workbook content.
