# Document Naming and Filing

Phase 4 lets each company define **naming rules** and **filing rules**; Phase 6 *uses* them, without hard-coding any folder or category.

## Suggest, don't decide

`POST /companies/{company_id}/documents/suggest` with `filename`, optional `category_id`, `node_id`, `field_values`, `document_date`. The response is computed from the company's configuration and what the user already supplied:

* **Filing**: the active filing rules are evaluated against the attributes (source, file name, category, document kind, custom values). The winning rule (by priority) proposes a target folder and category; every other matching rule is listed so the user can see why. A rule is offered only if the caller may **upload** to its target folder; otherwise it is not mentioned.
* **Naming**: the pattern of the category's naming rule (or the company default) is rendered with the values given. Built-in tokens: company name/code, category, document type (kind), document date, plus custom field keys. If any token has no value, the result carries **warnings and `complete = false`** and *no pretend name* is invented.
* **Fields**: the custom fields that apply to this folder/category, so the client can ask for them.

The user always decides: the Desktop pre-fills the destination, category and document name from the suggestion and lets the user change them. If no rule matches, the file keeps its **original file name** and the user picks the folder.

## What is stored

* `display_name`: what the user accepted (default: the file name). Unique per folder among active/archived documents, case-insensitive; a clash on upload or move gets a numbered variant (`name (2).pdf`) via the same `ensure_unique_name` used by Phase 4.
* `filing_rule_id`: only if that rule really targets the chosen folder (otherwise dropped).
* `kind_at_filing`: the Artify kind of the category at filing time. Later processing keys off this and the category mapping — **never** off a folder or category name.
* `filed_path_snapshot`: a readable path for audit and explanation only.

## Moving and renaming

`POST /documents/{id}/move` (new folder and optional category; both validated, `upload` needed on the destination and on the source) and `PATCH` (rename). The extension must stay allowed for the target category (`allowed_file_types`). Moving never touches the bytes. A folder move/rename in the workspace does not change documents at all (ids); the local mirror is realigned by `mirror_reconcile`.

## Archived or inactive targets

Documents cannot be filed into an archived or inactive folder or an archived category (`409 target_unavailable` for the folder, `422 invalid_category` for the category). The Desktop's destination list offers only active folders the server says the user may upload to.

## Empty workspace

With no folders there is nowhere to file; the Desktop explains that an administrator configures the workspace in the Control Center. No default folders are created.
